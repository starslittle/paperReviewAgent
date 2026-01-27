from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

from openai import OpenAI

from .prompts import normative_prompt, vision_verify_prompt


class NormativeAgent:
    def __init__(self, doc_agent: Any):
        """
        初始化规范性审查Agent
        
        Args:
            doc_agent: DocAgent实例，提供公共功能（LLM调用、JSON解析、文档读取等）
        """
        self.doc_agent = doc_agent

    def _needs_vision_verification(self, issue):
        """判断问题是否需要视觉验证（如章节丢失、页码错误）"""
        suggestion = issue.get("suggestion", "")
        issue_type = issue.get("issue_type", "")
        # 可能由解析器错误导致的关键词
        keywords = ["编号", "缺少", "丢失", "不连续", "页码", "不一致", "章节"]

        # 兼容中英文 issue_type
        if issue_type in ["Format", "规范性"] and any(
            k in suggestion for k in keywords
        ):
            return True
        return False

    def verify_with_vision(self, issue):
        """
        使用视觉模型验证规范性问题
        
        Returns:
            tuple: (is_real, reason_str)
                - is_real: True=问题真实存在, False=误报, None=无法验证
                - reason_str: 验证理由
        """
        page = issue.get("page")
        if not page or not str(page).isdigit():
            return None, "无法验证：缺少有效页码"

        page_num = int(float(page))
        suggestion = issue.get("suggestion", "")

        # DEBUG: Check calculated path
        index_string = "%04d" % (int(page_num) - 1)
        expected_path = (
            self.doc_agent.doc_reader.data_path + "/page_images/page_" + index_string + ".png"
        )
        print(f"[Verification] Debug: Issue Page={page} -> Path={expected_path}")

        print(
            f"[Verification] Checking page {page_num} for issue: {suggestion[:30]}..."
        )

        try:
            media_type, base64_img, error = self.doc_agent.doc_reader.get_page_image(page_num)
            if error:
                return None, f"无法加载图片: {error}"

            prompt = vision_verify_prompt.format(issue_description=suggestion)

            # 使用DashScope视觉模型
            vision_api_key = os.getenv("DASHSCOPE_API_KEY")
            if not vision_api_key:
                print("[Verification] Skipped: DASHSCOPE_API_KEY not found.")
                return None, "缺少 Vision API Key"

            vision_client = OpenAI(
                api_key=vision_api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_img}"
                            },
                        },
                    ],
                }
            ]

            response = vision_client.chat.completions.create(
                model="qwen3-vl-flash",
                messages=messages,
                max_tokens=500,
                temperature=0.0,
            )

            res_json = self.doc_agent._parse_json(response.choices[0].message.content)
            is_real = res_json.get("is_real")
            if is_real is None:
                is_real = not res_json.get("is_false_positive", False)
            reason = res_json.get("reason", "无理由")

            # 兜底修正：通过 reason 的语义判断真实意图
            true_issue_markers = [
                "问题属实",
                "确实缺失",
                "确实没有",
                "确实不存在",
                "确实错误",
                "实际缺失",
                "内容缺失",
            ]
            false_positive_markers = [
                "误报",
                "误判",
                "不成立",
                "解析器遗漏",
                "PDF解析器遗漏",
                "实际存在",
                "清晰显示",
                "完整存在",
                "格式正确",
                "编号完整",
            ]

            # 优先根据语义判断
            if any(m in reason for m in true_issue_markers):
                is_real = True
                print(
                    f"[Verification] Issue is REAL (corrected by reason): {reason[:60]}..."
                )
            elif any(m in reason for m in false_positive_markers):
                is_real = False
                print(
                    f"[Verification] False positive detected (corrected by reason): {reason[:60]}..."
                )

            return is_real, reason

        except Exception as e:
            print(f"[Verification] Failed: {e}")
            return None, str(e)

    def _run_simple_review(self, prompt_template):
        """运行简单审查（使用DocAgent的公共方法）"""
        outline_xml = self.doc_agent.get_outline()
        body_text = self.doc_agent._extract_plain_text()
        messages = [
            {"role": "system", "content": prompt_template},
            {
                "role": "user",
                "content": f"大纲：\n{outline_xml}\n\n正文片段：\n{body_text}\n\n请按约定输出 JSON。",
            },
        ]
        try:
            response = self.doc_agent._call_llm(messages, max_tokens=1500, temperature=0.0)
            raw_content = response.choices[0].message.content
            return {"raw": raw_content, "thinking": self.doc_agent._extract_thinking(raw_content)}
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return {"raw": "", "thinking": "", "error": str(e)}

    def run_normative_review(self):
        """规范性审查主逻辑"""
        print("[NormativeAgent] Starting Normative Review...")
        res = self._run_simple_review(normative_prompt)
        res["raw_original"] = res.get("raw", "")

        # Debug: 显示原始输出的前500字符
        print(f"[Debug] Normative raw output preview: {res.get('raw', '')[:500]}...")

        # Parse initial issues
        data = self.doc_agent._parse_json(res["raw"])
        initial_issues = data.get("issues", [])
        verified_issues = []

        print(f"[NormativeAgent] Initial Normative Issues: {len(initial_issues)}")
        if len(initial_issues) == 0:
            print(f"[Warning] 规范性审查未发现任何问题，这可能不正常。请检查模型输出。")

        verification_log = "\n\n### 👁️ 视觉验证环节 (Visual Verification)\n"
        verification_log += "针对潜在的 PDF 解析误差（如章节丢失、页码错误），Agent 调用了视觉模型对原始页面进行了二次核查：\n\n"
        has_verification = False

        for issue in initial_issues:
            if self._needs_vision_verification(issue):
                has_verification = True
                print(
                    f"[Verification] Checking issue: {issue.get('suggestion', '')[:60]}..."
                )
                is_real, reason = self.verify_with_vision(issue)
                if is_real is None:
                    verified_issues.append(issue)
                    verification_log += f"- ❌ **保留 Issue**: `{issue.get('suggestion', '')[:40]}...`\n  - *视觉核查结果*: 无法验证，默认保留。({reason})\n"
                    print(
                        f"[Verification] ⚠️ Issue kept (unverifiable): {issue.get('suggestion', '')[:40]}..."
                    )
                elif is_real:
                    verified_issues.append(issue)
                    verification_log += f"- ❌ **保留 Issue**: `{issue.get('suggestion', '')[:40]}...`\n  - *视觉核查结果*: 问题属实或无法排除。({reason})\n"
                    print(
                        f"[Verification] ❌ Issue kept: {issue.get('suggestion', '')[:40]}..."
                    )
                else:
                    verification_log += f"- ✅ **移除误报 (False Positive)**: `{issue.get('suggestion', '')[:40]}...`\n  - *视觉核查结果*: 页面截图显示该内容实际存在，系解析器遗漏。({reason})\n"
                    print(f"[Verification] ✅ Issue removed as false positive")
            else:
                verified_issues.append(issue)
                print(
                    f"[Verification] Issue skipped (no verification needed): {issue.get('suggestion', '')[:60]}..."
                )

        if has_verification:
            # Append log to thinking
            current_thinking = res.get("thinking", "")
            if not current_thinking:
                current_thinking = "（无初始思考过程）"
            res["thinking"] = current_thinking + verification_log

        # Update JSON in raw response
        data["issues"] = verified_issues
        res["raw"] = json.dumps(data, ensure_ascii=False, indent=2)

        print(f"[NormativeAgent] Verified Normative Issues: {len(verified_issues)}")
        if verified_issues:
            print(
                f"[Debug] Sample verified issue: {verified_issues[0].get('suggestion', '')[:60]}..."
            )
        else:
            print(f"[Warning] No verified issues remaining after vision verification!")
        return res

    def run(self) -> Dict[str, Any]:
        """运行规范性审查并返回标准格式结果"""
        print("[NormativeAgent] start")
        res = self.run_normative_review()
        raw = res.get("raw", "")
        raw_original = res.get("raw_original", "")
        thinking = res.get("thinking", "")
        print(
            f"[NormativeAgent] raw_length={len(raw) if isinstance(raw, str) else 'n/a'}"
        )
        if isinstance(raw_original, str) and raw_original:
            json_match = re.search(r"<json>(.*?)</json>", raw_original, flags=re.DOTALL)
            if json_match:
                print("[NormativeAgent] json_block_begin")
                print(f"<json>{json_match.group(1).strip()}</json>")
                print("[NormativeAgent] json_block_end")
        elif isinstance(raw, str):
            json_match = re.search(r"<json>(.*?)</json>", raw, flags=re.DOTALL)
            if json_match:
                print("[NormativeAgent] json_block_begin")
                print(f"<json>{json_match.group(1).strip()}</json>")
                print("[NormativeAgent] json_block_end")
        parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}
        if isinstance(parsed, dict):
            issues = parsed.get("issues", [])
            if not isinstance(issues, list):
                print(f"[NormativeAgent] issues_not_list type={type(issues).__name__}")
                parsed["issues"] = []
            else:
                for issue in issues:
                    if isinstance(issue, dict):
                        issue["issue_type"] = "规范性"
            print(f"[NormativeAgent] issues_count={len(parsed.get('issues', []))}")
        else:
            print(f"[NormativeAgent] parsed_not_dict type={type(parsed).__name__}")
            parsed = {"issues": []}
        return {
            "raw": raw,
            "parsed": parsed,
            "thinking": thinking,
            "errors": [],
        }
