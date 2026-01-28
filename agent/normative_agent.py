from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

from .prompts import normative_prompt, vision_verify_prompt


class NormativeAgent:
    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent

    def run(self) -> Dict[str, Any]:
        return self.run_normative_review()

    def _needs_vision_verification(self, issue: Dict[str, Any]) -> bool:
        """判断是否需要视觉核查（如章节缺失、页码错误等）。"""
        suggestion = issue.get("suggestion", "")
        issue_type = issue.get("issue_type", "")
        keywords = ["编号", "缺少", "丢失", "不连续", "页码", "不一致", "章节"]
        if issue_type in ["Format", "规范性"] and any(k in suggestion for k in keywords):
            return True
        return False

    def verify_with_vision(self, issue: Dict[str, Any]) -> Tuple[Optional[bool], str]:
        """
        使用视觉模型验证规范性问题是否为误报。
        返回 (is_real, reason)，is_real=None 表示无法验证。
        """
        page = issue.get("page")
        if not page or not str(page).isdigit():
            return None, "无法验证：缺少有效页码"

        page_num = int(float(page))
        suggestion = issue.get("suggestion", "")

        index_string = "%04d" % (int(page_num) - 1)
        expected_path = (
            self.doc_agent.doc_reader.data_path
            + "/page_images/page_"
            + index_string
            + ".png"
        )
        print(f"[Verification] Debug: Issue Page={page} -> Path={expected_path}")
        print(
            f"[Verification] Checking page {page_num} for issue: {suggestion[:30]}..."
        )

        try:
            media_type, base64_img, error = self.doc_agent.doc_reader.get_page_image(
                page_num
            )
            if error:
                return None, f"无法加载图片: {error}"

            prompt = vision_verify_prompt.format(issue_description=suggestion)
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

            vision_api_key = os.getenv("DASHSCOPE_API_KEY")
            if not vision_api_key:
                print("[Verification] Skipped: DASHSCOPE_API_KEY not found.")
                return None, "缺少 Vision API Key"

            from openai import OpenAI

            vision_client = OpenAI(
                api_key=vision_api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
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

    def run_normative_review(self) -> Dict[str, Any]:
        """规范性审查，不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Normative Review...")
        res = self.doc_agent._run_simple_review(normative_prompt)
        res["raw_original"] = res.get("raw", "")

        print(f"[Debug] Normative raw output preview: {res.get('raw', '')[:500]}...")

        data = self.doc_agent._parse_json(res["raw"])
        initial_issues = data.get("issues", [])
        abstract_start_page = None
        for section in self.doc_agent.doc_reader.root:
            if section.tag != "Section":
                continue
            title_text = ""
            for node in section:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text
                    break
            if abstract_start_page is None and title_text:
                if re.search(r"(摘要|abstract|摘\s*要)", title_text, re.IGNORECASE):
                    abstract_start_page = section.get("start_page_num")
                    break
        if abstract_start_page:
            try:
                start_page_num = int(float(abstract_start_page))
            except Exception:
                start_page_num = None
            if start_page_num is not None:
                initial_issues = [
                    issue
                    for issue in initial_issues
                    if not issue.get("page")
                    or (
                        str(issue.get("page", "")).isdigit()
                        and int(float(issue.get("page"))) >= start_page_num
                    )
                ]
        verified_issues = []

        print(f"[Agent] Initial Normative Issues: {len(initial_issues)}")
        if len(initial_issues) == 0:
            print("[Warning] 规范性审查未发现任何问题，这可能不正常。请检查模型输出。")

        verification_log = "\n\n### 👁️ 视觉验证环节 (Visual Verification)\n"
        verification_log += (
            "针对潜在的 PDF 解析误差（如章节丢失、页码错误），Agent 调用了视觉模型对原始页面进行了二次核查：\n\n"
        )
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
                    verification_log += (
                        f"- ❌ **保留 Issue**: `{issue.get('suggestion', '')[:40]}...`\n"
                        f"  - *视觉核查结果*: 无法验证，默认保留。({reason})\n"
                    )
                    print(
                        f"[Verification] ⚠️ Issue kept (unverifiable): {issue.get('suggestion', '')[:40]}..."
                    )
                elif is_real:
                    verified_issues.append(issue)
                    verification_log += (
                        f"- ❌ **保留 Issue**: `{issue.get('suggestion', '')[:40]}...`\n"
                        f"  - *视觉核查结果*: 问题属实或无法排除。({reason})\n"
                    )
                    print(
                        f"[Verification] ❌ Issue kept: {issue.get('suggestion', '')[:40]}..."
                    )
                else:
                    verification_log += (
                        f"- ✅ **移除误报 (False Positive)**: `{issue.get('suggestion', '')[:40]}...`\n"
                        f"  - *视觉核查结果*: 页面截图显示该内容实际存在，系解析器遗漏。({reason})\n"
                    )
                    print("[Verification] ✅ Issue removed as false positive")
            else:
                verified_issues.append(issue)
                print(
                    f"[Verification] Issue skipped (no verification needed): {issue.get('suggestion', '')[:60]}..."
                )

        if has_verification:
            current_thinking = res.get("thinking", "")
            if not current_thinking:
                current_thinking = "（无初始思考过程）"
            res["thinking"] = current_thinking + verification_log

        data["issues"] = verified_issues
        res["raw"] = json.dumps(data, ensure_ascii=False, indent=2)

        print(f"[Agent] Verified Normative Issues: {len(verified_issues)}")
        if verified_issues:
            print(
                f"[Debug] Sample verified issue: {verified_issues[0].get('suggestion', '')[:60]}..."
            )
        else:
            print("[Warning] No verified issues remaining after vision verification!")

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
