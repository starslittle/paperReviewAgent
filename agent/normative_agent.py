from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Dict, Optional, Tuple

from .prompts import normative_prompt, vision_verify_prompt


class NormativeAgent:
    """
    规范性审查 Agent (Normative Review)

    【职责范围】：
    - ✅ 论文结构：必需部分是否齐全、顺序是否符合规范
    - ✅ 章节编号：是否连续（1, 1.1, 1.2）、格式是否统一（第一章 vs 1 绪论）
    - ✅ 图表编号：是否规范（图1-1, 表2-1）、是否连续
    - ✅ 引用格式：引用格式是否统一（[1] vs (张三, 2023)）、参考文献格式
    - ✅ 页面格式：页码、页眉页脚格式
    - ✅ 摘要关键词：中英文摘要是否齐全、关键词数量
    - ⚠️ 篇幅比例：仅检查结构失衡（头重脚轻），不评价内容

    【禁止触碰】：
    - ❌ 内容质量：论文内容是否正确、充分、深入
    - ❌ 逻辑正确性：论证是否严密、前后是否一致
    - ❌ 语言学术性：是否口语化、用词是否规范（由 LogicAgent 负责）
    - ❌ 标题内容：标题措辞是否准确（仅检查标题格式如断行、编号）

    【审查方式】：
    - 一次性审查（大纲 + 正文片段前 6000 字）
    - 覆盖范围：⚠️ 仅前 6000 字内容 + 完整大纲结构
    - 审查起点：从「摘要」开始（与 LogicAgent、VisionAgent 对齐）
    - 视觉验证：已关闭（不再对问题做视觉二次核查）

    【输出】：
    - issue_type: 固定为 "规范性"
    - 判断标准："能否用格式规范手册客观判定"
    """

    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent

    def run(self) -> Dict[str, Any]:
        return self.run_normative_review()

    def _needs_vision_verification(self, issue: Dict[str, Any]) -> bool:
        """判断是否需要视觉核查（如章节缺失、页码错误等）。"""
        suggestion = issue.get("suggestion", "")
        issue_type = issue.get("issue_type", "")
        keywords = ["编号", "缺少", "丢失", "不连续", "页码", "不一致", "章节"]
        if issue_type in ["Format", "规范性"] and any(
            k in suggestion for k in keywords
        ):
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

    def _deduplicate_issues(self, issues: list) -> list:
        """
        去重 issues，避免滑动窗口重复检测相同问题。

        去重策略：
        1. 基于 suggestion 的相似度（编辑距离 < 30% 认为是重复）
        2. 基于 section + quote 的精确匹配
        3. 优先保留 severity 更高的问题

        Args:
            issues: 待去重的 issue 列表

        Returns:
            去重后的 issue 列表
        """
        if not issues:
            return []

        print(f"[Deduplication] Starting with {len(issues)} issues")

        deduplicated = []
        seen_signatures = set()

        for issue in issues:
            if not isinstance(issue, dict):
                print(f"[Deduplication] Skip non-dict issue: {type(issue).__name__}")
                continue
            suggestion = issue.get("suggestion", "")
            section = issue.get("section", "")
            quote = issue.get("quote", "")
            severity = issue.get("severity", "Low")

            # 策略 1: 精确匹配（section + quote）
            exact_signature = f"{section}||{quote}"
            if exact_signature in seen_signatures:
                print(f"[Deduplication] Skipping exact duplicate: {suggestion[:40]}...")
                continue

            # 策略 2: 相似度匹配（suggestion 编辑距离）
            is_duplicate = False
            for existing_issue in deduplicated:
                existing_suggestion = existing_issue.get("suggestion", "")
                similarity = self._calculate_similarity(suggestion, existing_suggestion)

                # 相似度 > 70% 认为是重复
                if similarity > 0.7:
                    print(
                        f"[Deduplication] Similar issue found (sim={similarity:.2f}): {suggestion[:40]}..."
                    )

                    # 如果新问题严重程度更高，替换旧问题
                    severity_order = {"High": 3, "Medium": 2, "Low": 1}
                    new_severity_score = severity_order.get(severity, 0)
                    existing_severity_score = severity_order.get(
                        existing_issue.get("severity", "Low"), 0
                    )

                    if new_severity_score > existing_severity_score:
                        print("[Deduplication] Replacing with higher severity issue")
                        deduplicated.remove(existing_issue)
                        seen_signatures.discard(
                            f"{existing_issue.get('section', '')}||{existing_issue.get('quote', '')}"
                        )
                    else:
                        is_duplicate = True
                    break

            if not is_duplicate:
                deduplicated.append(issue)
                seen_signatures.add(exact_signature)

        print(f"[Deduplication] Completed: {len(deduplicated)} unique issues")
        return deduplicated

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """
        计算两个字符串的相似度（基于编辑距离）。

        Returns:
            相似度分数 (0.0 ~ 1.0)，1.0 表示完全相同
        """
        if not s1 or not s2:
            return 0.0

        # 简化版编辑距离（Levenshtein Distance）
        len1, len2 = len(s1), len(s2)

        # 如果长度差异过大，直接判定为不相似
        if abs(len1 - len2) / max(len1, len2) > 0.5:
            return 0.0

        # 使用动态规划计算编辑距离
        dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

        for i in range(len1 + 1):
            dp[i][0] = i
        for j in range(len2 + 1):
            dp[0][j] = j

        for i in range(1, len1 + 1):
            for j in range(1, len2 + 1):
                if s1[i - 1] == s2[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1]
                else:
                    dp[i][j] = min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]) + 1

        edit_distance = dp[len1][len2]
        max_len = max(len1, len2)

        # 相似度 = 1 - (编辑距离 / 最大长度)
        similarity = 1.0 - (edit_distance / max_len)

        return similarity

    def _parse_bbox_attr(
        self, bbox_value: Optional[str]
    ) -> Optional[Tuple[float, float, float, float]]:
        if not bbox_value:
            return None
        if isinstance(bbox_value, (list, tuple)) and len(bbox_value) >= 4:
            return (
                float(bbox_value[0]),
                float(bbox_value[1]),
                float(bbox_value[2]),
                float(bbox_value[3]),
            )
        raw = str(bbox_value).strip()
        if not raw:
            return None
        try:
            if raw.startswith("[") and raw.endswith("]"):
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, (list, tuple)) and len(parsed) >= 4:
                    return (
                        float(parsed[0]),
                        float(parsed[1]),
                        float(parsed[2]),
                        float(parsed[3]),
                    )
        except Exception:
            pass
        parts = [p for p in raw.replace(" ", "").split(",") if p]
        if len(parts) < 4:
            return None
        try:
            return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
        except Exception:
            return None

    def _check_media_caption_positions(self) -> list:
        issues = []
        root = self.doc_agent.doc_reader.root
        margin = 2.0

        def _get_section_title(page_num: Optional[str]) -> str:
            if not page_num:
                return ""
            section_info = self.doc_agent.doc_reader.find_section_by_page(page_num)
            if section_info:
                return section_info.get("title", "")
            return ""

        def _find_alt_text(elem):
            for child in list(elem):
                if child.tag == "Alt_Text" and child.text:
                    return child
            return None

        # 表格：标题应在表格上方（caption.bottom < table.top）
        for table in root.iter("Table"):
            table_bbox = self._parse_bbox_attr(table.get("bbox"))
            if not table_bbox:
                continue
            alt_node = _find_alt_text(table)
            if not alt_node:
                continue
            caption_bbox = self._parse_bbox_attr(alt_node.get("bbox"))
            if not caption_bbox:
                continue
            table_top = table_bbox[1]
            caption_bottom = caption_bbox[3]
            if caption_bottom >= table_top - margin:
                page_num = table.get("page_num", "")
                issues.append(
                    {
                        "issue_type": "规范性",
                        "severity": "Medium",
                        "section": _get_section_title(page_num),
                        "page": page_num,
                        "quote": (alt_node.text or "").strip(),
                        "suggestion": "表题应位于表格上方，请调整表题位置。",
                    }
                )

        # 图片：标题应在图片下方（caption.top > image.bottom）
        for image in root.iter("Image"):
            image_bbox = self._parse_bbox_attr(image.get("bbox"))
            if not image_bbox:
                continue
            alt_node = _find_alt_text(image)
            if not alt_node:
                continue
            caption_bbox = self._parse_bbox_attr(alt_node.get("bbox"))
            if not caption_bbox:
                continue
            image_bottom = image_bbox[3]
            caption_top = caption_bbox[1]
            if caption_top <= image_bottom + margin:
                page_num = image.get("page_num", "")
                issues.append(
                    {
                        "issue_type": "规范性",
                        "severity": "Medium",
                        "section": _get_section_title(page_num),
                        "page": page_num,
                        "quote": (alt_node.text or "").strip(),
                        "suggestion": "图题应位于图片下方，请调整图题位置。",
                    }
                )

        if issues:
            print(f"[NormativeAgent] Caption position issues: {len(issues)}")
        return issues

    def _check_citation_superscript_position(self, max_issues: int = 20) -> list:
        """
        规则检查：正文引用标识应使用右上角上标形式。
        对纯文本 XML 而言，若检测到普通行内 [n] 引用（如“扩展[17]。”）则提示为可能未上标。
        """
        issues = []
        root = self.doc_agent.doc_reader.root
        citation_pattern = re.compile(r"\[(\d+(?:\s*[-,，]\s*\d+)*)\]")

        # 与其他 Agent 对齐：从摘要章节开始扫描
        abstract_idx = self.doc_agent._get_abstract_start_section_index()
        top_sections = [c for c in list(root) if c.tag == "Section"]
        if abstract_idx is not None:
            top_sections = top_sections[abstract_idx:]

        def _get_section_title(section):
            for child in list(section):
                if child.tag in ["Heading", "Title"] and (child.text or "").strip():
                    return (child.text or "").strip()
            return section.get("section_id", "未知章节")

        for section in top_sections:
            section_title = _get_section_title(section)
            for node in section.iter():
                if node.tag not in {"Paragraph", "Heading", "Title"}:
                    continue
                text = (node.text or "").strip()
                if not text:
                    continue
                # 过滤页眉页脚类文本，降低误报
                page_num = node.get("page_num")
                if self.doc_agent._is_header_footer(text, page_num):
                    continue

                for match in citation_pattern.finditer(text):
                    quote = text
                    marker = match.group(0)
                    issues.append(
                        {
                            "issue_type": "规范性",
                            "severity": "Medium",
                            "section": section_title,
                            "page": page_num or section.get("start_page_num", "N/A"),
                            "quote": quote,
                            "suggestion": (
                                f"检测到引用标识 {marker} 为普通行内形式。"
                                "按论文格式规范，引用标识建议置于右上角上标形式（如“...扩展[17]”中的[17]应以上标显示）。"
                            ),
                        }
                    )
                    if len(issues) >= max_issues:
                        print(
                            f"[NormativeAgent] Citation superscript issues capped at {max_issues}"
                        )
                        return issues

        if issues:
            print(f"[NormativeAgent] Citation superscript issues: {len(issues)}")
        return issues

    def run_normative_review(self) -> Dict[str, Any]:
        """
        规范性审查（滑动窗口版本）。审查起点与 LogicAgent、VisionAgent 对齐：均从「摘要」开始。
        使用滑动窗口策略覆盖全文，每次窗口 6000 字，overlap 1000 字避免断句。
        """
        print(
            "[Agent] Starting Normative Review with Sliding Window (from 摘要, aligned with Logic/Vision)..."
        )

        # 配置滑动窗口参数
        WINDOW_SIZE = 6000
        OVERLAP = 1000

        all_issues = []
        all_thinking = []
        window_count = 0
        char_offset = 0

        while True:
            window_count += 1
            print(
                f"\n[Agent] === Window {window_count}: offset={char_offset}, size={WINDOW_SIZE} ==="
            )

            # 调用滑动窗口审查
            res = self.doc_agent._run_simple_review(
                normative_prompt,
                from_abstract=True,
                char_offset=char_offset,
                char_limit=WINDOW_SIZE,
            )

            window_info = res.get("window_info", {})
            actual_start = window_info.get("start", char_offset)
            actual_end = window_info.get("end", char_offset + WINDOW_SIZE)
            is_end = window_info.get("is_end", False)

            print(
                f"[Agent] Window actual range: {actual_start} ~ {actual_end}, is_end={is_end}"
            )

            # 解析当前窗口的 issues
            data = self.doc_agent._parse_json(res.get("raw", ""))

            # 处理两种可能的返回格式：{"issues": [...]} 或 [{...}, {...}]
            if isinstance(data, list):
                window_issues = data
            elif isinstance(data, dict):
                window_issues = data.get("issues", [])
            else:
                window_issues = []

            print(f"[Agent] Window {window_count} found {len(window_issues)} issues")

            # 记录 thinking
            thinking = res.get("thinking", "")
            if thinking:
                all_thinking.append(
                    f"=== Window {window_count} ({actual_start}~{actual_end}) ===\n{thinking}"
                )

            # 收集 issues（添加窗口标记）
            for issue in window_issues:
                if isinstance(issue, dict):
                    issue["_window"] = window_count
                    issue["_window_range"] = f"{actual_start}~{actual_end}"
                    all_issues.append(issue)
                elif isinstance(issue, list):
                    for sub in issue:
                        if isinstance(sub, dict):
                            sub["_window"] = window_count
                            sub["_window_range"] = f"{actual_start}~{actual_end}"
                            all_issues.append(sub)
                        else:
                            print(
                                f"[Agent] Skip non-dict nested issue: {type(sub).__name__}"
                            )
                else:
                    print(f"[Agent] Skip non-dict issue: {type(issue).__name__}")

            # 判断是否结束
            if is_end:
                print(f"[Agent] Reached end of document at window {window_count}")
                break

            # 计算下一个窗口的起始位置（有 overlap）
            char_offset = actual_end - OVERLAP

            # 防止死循环（最多 20 个窗口，约 12 万字）
            if window_count >= 20:
                print(
                    "[Warning] Reached max window count (20), stopping to prevent infinite loop"
                )
                break

        print(
            f"\n[Agent] Sliding window review completed: {window_count} windows, {len(all_issues)} total issues (before deduplication)"
        )

        # 追加规则检测：表题在表上方、图题在图下方
        rule_issues = self._check_media_caption_positions()
        if rule_issues:
            all_issues.extend(rule_issues)

        # 追加规则检测：引用标识应使用右上角上标
        citation_issues = self._check_citation_superscript_position()
        if citation_issues:
            all_issues.extend(citation_issues)

        # 去重（在步骤 3 实现）
        deduplicated_issues = self._deduplicate_issues(all_issues)

        print(f"[Agent] After deduplication: {len(deduplicated_issues)} issues")

        # 构造返回结果
        combined_thinking = "\n\n".join(all_thinking)

        # 为每个 issue 强制设置 issue_type 为 "规范性"
        for issue in deduplicated_issues:
            issue["issue_type"] = "规范性"
            # 移除临时字段
            issue.pop("_window", None)
            issue.pop("_window_range", None)

        final_data = {"issues": deduplicated_issues}
        raw = json.dumps(final_data, ensure_ascii=False, indent=2)

        print(
            f"[Agent] Normative Issues (final, no vision verification): {len(deduplicated_issues)}"
        )
        if deduplicated_issues:
            print(
                f"[Debug] Sample issue: {deduplicated_issues[0].get('suggestion', '')[:60]}..."
            )

        # 输出调试信息
        print(f"[NormativeAgent] raw_length={len(raw)}")
        print(f"[NormativeAgent] issues_count={len(deduplicated_issues)}")

        return {
            "raw": raw,
            "parsed": final_data,
            "thinking": combined_thinking,
            "errors": [],
        }
