import json
import re
import time
import traceback
import xml.dom.minidom
import xml.etree.ElementTree as ET

from openai import OpenAI
from pydantic_core.core_schema import nullable_schema

from .prompts import (
    actor_prompt_template,
    available_tools,
    chapter_selection_prompt,
    logic_prompt,
    normative_prompt,
    normative_logic_prompt,
    reflection_prompt_template,
    reviewer_prompt,
    system_prompt,
    vision_verify_prompt,
    local_chapter_review_prompt,
    global_logic_review_prompt,
)


def clean_xml_string(xml_str):
    cleaned = "".join(char for char in xml_str if char.isprintable() or char.isspace())
    return cleaned


class DocAgent:
    def __init__(
        self,
        doc_reader,
        model_id="deepseek-chat",
        temperature=0.0,
        max_tokens=8192,
        api_key=None,
        base_url="https://api.deepseek.com",
        tool_call_wait_time=10,
    ):
        self.doc_reader = doc_reader
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.tool_call_wait_time = tool_call_wait_time
        # 用于在逻辑审查过程中暂存每章摘要，供全局阶段直接读取
        self.logic_memory = []

        # ✅ 新增：跨章节共享的事实存储（用于细粒度冲突检测）
        self.fact_store = {
            "entities": {},  # 实体：人名、机构、角色等
            "numbers": {},  # 数值：指标、参数、统计数据等
            "dates": {},  # 时间：日期、时间线
            "claims": [],  # 论断：重要观点和结论
        }

    def _extract_plain_text(self, char_limit=6000):
        """Extract plain text segments for lightweight review."""
        texts = []
        for elem in self.doc_reader.root.iter():
            if elem.text and elem.tag in ["Paragraph", "Title", "Caption"]:
                t = elem.text.strip()
                if t:
                    texts.append(t)
            if sum(len(x) for x in texts) > char_limit:
                break
        combined = "\n".join(texts)
        return combined[:char_limit]

    def _run_simple_review(self, prompt_template):
        outline_xml = self.get_outline()
        body_text = self._extract_plain_text()
        messages = [
            {"role": "system", "content": prompt_template},
            {
                "role": "user",
                "content": f"大纲：\n{outline_xml}\n\n正文片段：\n{body_text}\n\n请按约定输出 JSON。",
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=1500,  # Increased to allow for thinking process
                temperature=0.0,  # 降低温度确保输出稳定
            )
            raw_content = response.choices[0].message.content

            # Extract thinking and json
            thinking = ""
            thinking_match = re.search(
                r"<thinking>(.*?)</thinking>", raw_content, re.DOTALL
            )
            if thinking_match:
                thinking = thinking_match.group(1).strip()

            return {"raw": raw_content, "thinking": thinking}
        except Exception as e:
            print(traceback.format_exc())
            return {"raw": "", "thinking": "", "error": str(e)}

    def _parse_json(self, raw_content):
        """Helper to safely extract and parse JSON from LLM response."""
        try:
            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_str = raw_content[start : end + 1]
                parsed = json.loads(json_str)
                return parsed
            return json.loads(raw_content)
        except Exception as e:
            print(f"[Error] JSON parse failed: {str(e)[:200]}")
            print(f"[Error] Raw content preview: {raw_content[:500]}...")
            return {"issues": []}

    def _find_page_by_quote(self, quote_text, min_len=10):
        """
        Try to locate a quote in the XML tree and return its page_num.
        Fallback: None if not found.
        """
        if not quote_text:
            return None
        qt = quote_text.strip()
        if len(qt) < min_len:
            return None

        for node in self.doc_reader.root.iter():
            if node.text and qt[: min(50, len(qt))] in node.text:
                # Prefer explicit page_num attr
                if node.get("page_num"):
                    return node.get("page_num")
                # If the node is inside a Section, try to read its start_page_num
                parent = node
                while parent is not None:
                    if parent.tag == "Section" and parent.get("start_page_num"):
                        return parent.get("start_page_num")
                    parent = (
                        parent.getparent() if hasattr(parent, "getparent") else None
                    )
        return None

    def _find_page_by_fuzzy_quote(self, quote_text, threshold=0.6, max_len=200):
        """
        Fuzzy match quote_text against all nodes' text to guess page_num.
        Returns page_num (str) or None.
        """
        if not quote_text:
            return None
        qt = quote_text.strip()
        from difflib import SequenceMatcher

        best_score = 0
        best_page = None
        for node in self.doc_reader.root.iter():
            if not node.text:
                continue
            cand = node.text[:max_len]
            score = SequenceMatcher(None, qt, cand).ratio()
            if score > best_score and score >= threshold:
                # pick page from node or its Section
                page = node.get("page_num")
                if not page:
                    parent = node
                    while parent is not None:
                        if parent.tag == "Section" and parent.get("start_page_num"):
                            page = parent.get("start_page_num")
                            break
                        parent = (
                            parent.getparent() if hasattr(parent, "getparent") else None
                        )
                if page:
                    best_score = score
                    best_page = page
        return best_page

    def select_top_sections(self, max_sections=8):
        """
        Use LLM to select top-level (or important) sections from Outline XML.
        Returns a list of section_id strings.
        """
        outline_xml = self.get_outline()
        messages = [
            {"role": "system", "content": chapter_selection_prompt},
            {
                "role": "user",
                "content": f'以下是论文大纲（XML）。请只输出最重要的大章节 section_id 列表（最多 {max_sections} 个），用 JSON 数组表示，如 ["1", "2", "3"].\n\n{outline_xml}',
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=500,
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            data = self._parse_json(raw)
            if isinstance(data, list):
                return [str(x) for x in data][:max_sections]
            if isinstance(data, dict) and "sections" in data:
                return [str(x) for x in data.get("sections", [])][:max_sections]
        except Exception as e:
            print(f"[SectionSelect] failed: {e}")
        # fallback: take top-level section ids from doc_reader
        top_ids = []
        for child in self.doc_reader.root:
            if child.tag == "Section":
                top_ids.append(child.get("section_id"))
            if len(top_ids) >= max_sections:
                break
        return top_ids

    def _needs_vision_verification(self, issue):
        """Determine if an issue needs vision verification (e.g. missing sections, page numbers)."""
        suggestion = issue.get("suggestion", "")
        issue_type = issue.get("issue_type", "")
        # Keywords that suggest a parser failure might be the cause
        keywords = ["编号", "缺少", "丢失", "不连续", "页码", "不一致", "章节"]

        # 兼容中英文 issue_type
        if issue_type in ["Format", "规范性"] and any(
            k in suggestion for k in keywords
        ):
            return True
        return False

    def verify_with_vision(self, issue):
        """
        Verify a normative issue using Vision Model.
        Returns (is_false_positive, reason_str).
        """
        page = issue.get("page")
        if not page or not str(page).isdigit():
            return False, "无法验证：缺少有效页码"

        page_num = int(float(page))
        suggestion = issue.get("suggestion", "")

        # DEBUG: Check calculated path
        index_string = "%04d" % (int(page_num) - 1)
        expected_path = (
            self.doc_reader.data_path + "/page_images/page_" + index_string + ".png"
        )
        print(f"[Verification] Debug: Issue Page={page} -> Path={expected_path}")

        print(
            f"[Verification] Checking page {page_num} for issue: {suggestion[:30]}..."
        )

        try:
            media_type, base64_img, error = self.doc_reader.get_page_image(page_num)
            if error:
                return False, f"无法加载图片: {error}"

            prompt = vision_verify_prompt.format(issue_description=suggestion)

            # Determine client (reuse logic from run_vision_review or simplify)
            # Assuming Qwen/Dashscope for vision
            client = self.client
            model_id = "qwen3-vl-flash"  # Default for verification

            # Construct message
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

            import os

            vision_api_key = os.getenv("DASHSCOPE_API_KEY")
            if not vision_api_key:
                print("[Verification] Skipped: DASHSCOPE_API_KEY not found.")
                return False, "缺少 Vision API Key"

            from openai import OpenAI

            vision_client = OpenAI(
                api_key=vision_api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

            response = vision_client.chat.completions.create(
                model="qwen3-vl-flash",
                messages=messages,
                max_tokens=500,
                temperature=0.0,  # 降低温度确保视觉验证结果稳定
            )

            res_json = self._parse_json(response.choices[0].message.content)
            is_false_positive = res_json.get("is_false_positive", False)
            reason = res_json.get("reason", "无理由")

            # 兜底修正：通过 reason 的语义判断真实意图，纠正 JSON 字段可能的错误
            # 如果 reason 明确表示"问题属实/确实缺失/确实存在问题"，则应该是 False（不是误报）
            true_issue_markers = [
                "问题属实",
                "确实缺失",
                "确实没有",
                "确实不存在",
                "确实错误",
                "实际缺失",
                "内容缺失",
            ]
            # 如果 reason 明确表示"误报/不成立/解析器遗漏/实际存在"，则应该是 True（是误报）
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
                is_false_positive = False  # 问题真实存在
                print(
                    f"[Verification] Issue is REAL (corrected by reason): {reason[:60]}..."
                )
            elif any(m in reason for m in false_positive_markers):
                is_false_positive = True  # 问题是误报
                print(
                    f"[Verification] False positive detected (corrected by reason): {reason[:60]}..."
                )

            return is_false_positive, reason

        except Exception as e:
            print(f"[Verification] Failed: {e}")
            return False, str(e)

    def run_normative_review(self):
        """规范性审查，不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Normative Review...")
        res = self._run_simple_review(normative_prompt)

        # Debug: 显示原始输出的前500字符
        print(f"[Debug] Normative raw output preview: {res.get('raw', '')[:500]}...")

        # Parse initial issues
        data = self._parse_json(res["raw"])
        initial_issues = data.get("issues", [])
        verified_issues = []

        print(f"[Agent] Initial Normative Issues: {len(initial_issues)}")
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
                is_fp, reason = self.verify_with_vision(issue)
                if not is_fp:
                    verified_issues.append(issue)
                    verification_log += f"- ✅ **保留 Issue**: `{issue.get('suggestion', '')[:40]}...`\n  - *视觉核查结果*: 问题属实或无法排除。({reason})\n"
                    print(
                        f"[Verification] ✅ Issue kept: {issue.get('suggestion', '')[:40]}..."
                    )
                else:
                    verification_log += f"- ❌ **移除误报 (False Positive)**: `{issue.get('suggestion', '')[:40]}...`\n  - *视觉核查结果*: 页面截图显示该内容实际存在，系解析器遗漏。({reason})\n"
                    print(f"[Verification] ❌ Issue removed as false positive")
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

        # Update JSON in raw response (hacky but keeps compatibility)
        data["issues"] = verified_issues
        res["raw"] = json.dumps(data, ensure_ascii=False, indent=2)  # Update raw json

        print(f"[Agent] Verified Normative Issues: {len(verified_issues)}")
        if verified_issues:
            print(
                f"[Debug] Sample verified issue: {verified_issues[0].get('suggestion', '')[:60]}..."
            )
        else:
            print(f"[Warning] No verified issues remaining after vision verification!")
        return res

    def _extract_chapter_facts(self, chapter_content, chapter_info):
        """
        从章节内容中提取细粒度事实（用于跨章节冲突检测）

        Args:
            chapter_content: 章节内容（XML字符串）
            chapter_info: 章节信息字典（包含title, section_id, page等）

        Returns:
            dict: {"entities": [...], "numbers": [...], "dates": [...], "claims": [...]}
        """
        print(
            f"[Fact Extraction] Extracting facts from: {chapter_info.get('title', 'Unknown')}"
        )

        fact_extraction_prompt = """
你是一个精确的事实提取专家。请从以下章节内容中提取关键事实，用于后续的跨章节一致性验证。

请提取以下类型的事实：

1. **实体（Entities）**：人名、角色、机构、公司等
   - 示例：{"type": "人物角色", "key": "甲方", "value": "张三"}
   - 示例：{"type": "机构", "key": "项目单位", "value": "XX科技有限公司"}

2. **数值（Numbers）**：性能指标、实验数据、统计数字等
   - 示例：{"type": "性能指标", "key": "准确率", "value": 95.5, "unit": "%"}
   - 示例：{"type": "实验数据", "key": "样本数量", "value": 1000, "unit": "个"}

3. **时间（Dates）**：日期、时间节点、时间段等
   - 示例：{"type": "时间节点", "key": "项目启动", "value": "2023年3月"}

4. **重要论断（Claims）**：关键结论、核心观点（限5条最重要的）
   - 示例：{"claim": "算法A在准确率上优于算法B", "type": "比较结论"}

**注意**：
- 只提取明确的事实，不要推断
- 保留原文上下文片段（用于定位）
- 如果某类事实不存在，返回空数组

输出JSON格式：
{
  "entities": [
    {"type": "人物角色", "key": "甲方", "value": "张三", "context": "根据合同约定，甲方为张三"}
  ],
  "numbers": [
    {"type": "性能指标", "key": "准确率", "value": 95.5, "unit": "%", "context": "实验结果显示准确率达到95.5%"}
  ],
  "dates": [
    {"type": "时间节点", "key": "项目启动", "value": "2023年3月", "context": "项目于2023年3月正式启动"}
  ],
  "claims": [
    {"claim": "算法A优于算法B", "type": "比较结论", "context": "综合实验结果表明，算法A在各项指标上均优于算法B"}
  ]
}
"""

        # 限制内容长度（避免超token）
        content_snippet = chapter_content[:8000]

        messages = [
            {"role": "system", "content": fact_extraction_prompt},
            {
                "role": "user",
                "content": f"章节标题：{chapter_info.get('title', 'Unknown')}\n\n章节内容：\n{content_snippet}\n\n请提取关键事实。",
            },
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model_id, messages=messages, max_tokens=4096, temperature=0.0
            )
            raw_response = response.choices[0].message.content
            facts = self._parse_json(raw_response)

            print(
                f"[Fact Extraction] Extracted: {len(facts.get('entities', []))} entities, "
                f"{len(facts.get('numbers', []))} numbers, "
                f"{len(facts.get('dates', []))} dates, "
                f"{len(facts.get('claims', []))} claims"
            )

            return facts

        except Exception as e:
            print(f"[Fact Extraction] Failed: {e}")
            return {"entities": [], "numbers": [], "dates": [], "claims": []}

    def _store_facts(self, facts, chapter_info):
        """
        将提取的事实存储到 self.fact_store 中

        Args:
            facts: _extract_chapter_facts 返回的字典
            chapter_info: 章节信息（用于标注来源）
        """
        chapter_label = f"{chapter_info.get('title', 'Unknown')} (第{chapter_info.get('start_page_num', '?')}页)"

        # 存储实体
        for entity in facts.get("entities", []):
            key = entity.get("key")  # 如"甲方"
            if key:
                if key not in self.fact_store["entities"]:
                    self.fact_store["entities"][key] = []
                self.fact_store["entities"][key].append(
                    {
                        "value": entity.get("value"),
                        "type": entity.get("type"),
                        "context": entity.get("context", ""),
                        "source": chapter_label,
                        "page": chapter_info.get("start_page_num"),
                    }
                )

        # 存储数值
        for number in facts.get("numbers", []):
            key = number.get("key")  # 如"准确率"
            if key:
                if key not in self.fact_store["numbers"]:
                    self.fact_store["numbers"][key] = []
                self.fact_store["numbers"][key].append(
                    {
                        "value": number.get("value"),
                        "unit": number.get("unit", ""),
                        "type": number.get("type"),
                        "context": number.get("context", ""),
                        "source": chapter_label,
                        "page": chapter_info.get("start_page_num"),
                    }
                )

        # 存储时间
        for date in facts.get("dates", []):
            key = date.get("key")
            if key:
                if key not in self.fact_store["dates"]:
                    self.fact_store["dates"][key] = []
                self.fact_store["dates"][key].append(
                    {
                        "value": date.get("value"),
                        "context": date.get("context", ""),
                        "source": chapter_label,
                        "page": chapter_info.get("start_page_num"),
                    }
                )

        # 存储论断
        for claim in facts.get("claims", []):
            self.fact_store["claims"].append(
                {
                    "claim": claim.get("claim"),
                    "type": claim.get("type"),
                    "context": claim.get("context", ""),
                    "source": chapter_label,
                    "page": chapter_info.get("start_page_num"),
                }
            )

    def _detect_fact_conflicts(self):
        """
        检测 fact_store 中的冲突

        Returns:
            list: 冲突问题列表
        """
        print("[Fact Conflict Detection] Analyzing cross-chapter conflicts...")
        conflicts = []

        # 1. 检测实体冲突（如"甲方"在不同章节有不同的值）
        for entity_key, occurrences in self.fact_store["entities"].items():
            if len(occurrences) > 1:
                # 检查是否有不同的值
                unique_values = set(occ["value"] for occ in occurrences if occ["value"])
                if len(unique_values) > 1:
                    conflicts.append(
                        {
                            "issue_type": "逻辑性-实体冲突",
                            "severity": "High",
                            "section": "跨章节",
                            "page": occurrences[0]["page"],
                            "quote": f"'{entity_key}' 在不同位置有不同的值：{', '.join(unique_values)}",
                            "suggestion": (
                                f"'{entity_key}' 的信息在文档中不一致。"
                                f"出现位置："
                                + "; ".join(
                                    [
                                        f"{occ['source']}为'{occ['value']}'"
                                        for occ in occurrences[:3]
                                    ]
                                )
                            ),
                        }
                    )

        # 2. 检测数值冲突（如"准确率"在不同章节有明显差异）
        for metric_key, occurrences in self.fact_store["numbers"].items():
            if len(occurrences) > 1:
                values = [
                    occ["value"]
                    for occ in occurrences
                    if isinstance(occ["value"], (int, float))
                ]
                if len(values) > 1:
                    # 检查数值差异（允许5%的误差范围）
                    max_val = max(values)
                    min_val = min(values)
                    if max_val > 0 and (max_val - min_val) / max_val > 0.05:
                        conflicts.append(
                            {
                                "issue_type": "逻辑性-数值冲突",
                                "severity": "High",
                                "section": "跨章节",
                                "page": occurrences[0]["page"],
                                "quote": f"'{metric_key}' 在不同位置有不同的数值",
                                "suggestion": (
                                    f"'{metric_key}' 的数值在文档中不一致（范围：{min_val}-{max_val}）。"
                                    f"出现位置："
                                    + "; ".join(
                                        [
                                            f"{occ['source']}为{occ['value']}{occ.get('unit', '')}"
                                            for occ in occurrences[:3]
                                        ]
                                    )
                                ),
                            }
                        )

        # 3. 检测时间冲突（简单检查是否有明显的时序矛盾）
        # TODO: 可以扩展时间线排序验证

        print(f"[Fact Conflict Detection] Found {len(conflicts)} conflicts")
        return conflicts

    def run_logic_review(self):
        """逻辑审查，不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Logic Review...")
        return self._run_simple_review(logic_prompt)

    def run_hierarchical_logic_review(self):
        """
        层次化逻辑审查 (Map-Reduce)。
        1. 将文档分割为章节。
        2. Map: 审查每个章节（局部逻辑 + 摘要）。
        3. Reduce: 基于摘要进行全局一致性审查。
        """
        print("[Agent] Starting Hierarchical Logic Review...")
        # 重置逻辑内存
        self.logic_memory = []

        # ✅ 重置事实存储（用于细粒度冲突检测）
        self.fact_store = {"entities": {}, "numbers": {}, "dates": {}, "claims": []}
        print("[Fact Store] Initialized for cross-chapter conflict detection")

        # Step 0: Let LLM select top/important sections
        top_sections = self.select_top_sections(max_sections=8)
        print(f"[Logic] Selected sections: {top_sections}")

        # Build chapters list from selected section_ids (fallback to top-level if empty)
        chapters = []
        use_ids = top_sections if top_sections else []
        if not use_ids:  # fallback to top-level children
            for child in self.doc_reader.root:
                if child.tag == "Section" and child.get("section_id"):
                    use_ids.append(child.get("section_id"))
                    if len(use_ids) >= 8:
                        break

        for sid in use_ids:
            try:
                sec_root = self.doc_reader.get_section_content(sid)
            except Exception:
                continue
            # Extract title from Heading or Title
            title_text = f"Section {sid}"
            for node in sec_root:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text
                    break
            # Serialize subtree to text (XML string)
            content_xml = ET.tostring(sec_root, encoding="unicode", method="xml")
            chapters.append(
                {
                    "section_id": sid,
                    "title": title_text,
                    "content_xml": content_xml,
                    "start_page_num": sec_root.get("start_page_num"),
                }
            )

        if not chapters:
            print("[Logic] No chapters found, aborting logic review.")
            return {
                "raw": json.dumps({"issues": []}),
                "thinking": "[Error] No chapters to review",
            }

        print(f"[Logic] Will review {len(chapters)} selected chapters.")

        map_results = []
        full_thinking_log = "=== 分章节审查 (Local Review) ===\n"

        # Step 2: Map
        for i, chap in enumerate(chapters):
            print(f"[Logic] Reviewing Chapter {i+1}: {chap['title']}")

            # Use XML content directly; limit length if huge
            content_snippet = chap["content_xml"][:12000]

            messages = [
                {"role": "system", "content": local_chapter_review_prompt},
                {
                    "role": "user",
                    "content": (
                        f"章节标题：{chap['title']}\n"
                        f"章节XML内容：\n{content_snippet}\n\n"
                        "请按约定输出 JSON，必须包含 chapter_summary。"
                        "注意直接使用 XML 节点中的 page_num，如果节点没有 page_num 再用章节 start_page_num 兜底，不要猜测页码。"
                    ),
                },
            ]

            try:
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=messages,
                    max_tokens=8192,
                    temperature=0.0,  # 收紧温度，保证格式稳定
                )
                raw_res = response.choices[0].message.content

                # 【日志1】打印原始响应（前500字符）
                print(f"[Logic Debug] Chapter {i+1} Raw Response (first 500 chars):")
                print(raw_res[:500])
                print("---")

                data = self._parse_json(raw_res)

                # Collect thinking
                thinking_match = re.search(
                    r"<thinking>(.*?)</thinking>", raw_res, re.DOTALL
                )
                thinking = thinking_match.group(1).strip() if thinking_match else ""

                full_thinking_log += (
                    f"\n#### Chapter {i+1}: {chap['title']}\n{thinking}\n"
                )

                # 提取章节摘要
                chapter_summary = (
                    data.get("chapter_summary") if isinstance(data, dict) else ""
                )

                # 【兜底】如果摘要为None或空，使用兜底文本
                if not chapter_summary or chapter_summary == "None":
                    chapter_summary = f"[摘要解析失败] 第{i+1}章《{chap['title']}》的摘要未能正确生成，可能因为模型输出不完整或JSON格式错误。"
                    print(
                        f"[Logic WARNING] Chapter {i+1} 摘要为空或None，已使用兜底文本"
                    )

                # 【日志2】打印解析出的摘要
                print(
                    f"[Logic Debug] Chapter {i+1} Parsed Summary: '{chapter_summary}'"
                )
                print(f"[Logic Debug] Summary Length: {len(chapter_summary)}")

                # 写入逻辑内存
                memory_entry = {
                    "section_id": chap.get("section_id"),
                    "title": chap["title"],
                    "summary": chapter_summary,
                }
                self.logic_memory.append(memory_entry)

                # 【日志3】验证写入逻辑内存后的内容
                print(
                    f"[Logic Debug] Stored in logic_memory: section_id={memory_entry['section_id']}, title={memory_entry['title']}, summary_len={len(memory_entry['summary'])}"
                )

                # ✅ 新增：提取并存储细粒度事实（用于跨章节冲突检测）
                try:
                    chapter_facts = self._extract_chapter_facts(
                        chapter_content=chap["content_xml"],
                        chapter_info={
                            "title": chap["title"],
                            "section_id": chap.get("section_id"),
                            "start_page_num": chap.get("start_page_num"),
                        },
                    )
                    self._store_facts(
                        chapter_facts,
                        chapter_info={
                            "title": chap["title"],
                            "start_page_num": chap.get("start_page_num"),
                        },
                    )
                except Exception as fact_error:
                    print(f"[Fact Extraction] Failed for chapter {i+1}: {fact_error}")
                    # 事实提取失败不影响主流程

                map_results.append(
                    {
                        "title": chap["title"],
                        "summary": chapter_summary,
                        "issues": data.get("issues", []),
                        "start_page_num": chap.get("start_page_num"),
                        "section_id": chap.get("section_id"),
                    }
                )

            except Exception as e:
                print(f"[Logic] Failed to review chapter {i+1}: {e}")
                # 【兜底】即使失败也要加入logic_memory，避免全局阶段缺失章节
                fallback_summary = f"[审查失败] 第{i+1}章《{chap['title']}》审查过程中出现异常：{str(e)}"

                self.logic_memory.append(
                    {
                        "section_id": chap.get("section_id"),
                        "title": chap["title"],
                        "summary": fallback_summary,
                    }
                )
                print(
                    f"[Logic Debug] Exception fallback: Added to logic_memory with error summary"
                )

                map_results.append(
                    {
                        "title": chap["title"],
                        "summary": fallback_summary,
                        "issues": [],
                        "start_page_num": chap.get("start_page_num"),
                        "section_id": chap.get("section_id"),
                    }
                )

        # 为缺失页码的章节级 issue 填充章节起始页
        for res in map_results:
            start_page = res.get("start_page_num")
            if start_page:
                for issue in res["issues"]:
                    if not issue.get("page"):
                        issue["page"] = start_page

        # Step 3: Reduce
        print("[Logic] Starting Global Reduction...")

        # 【日志4】在全局阶段开始前，打印逻辑内存的完整内容
        print("\n[Logic Debug] === Logic Memory Content ===")
        print(f"[Logic Debug] Total entries in logic_memory: {len(self.logic_memory)}")
        for idx, mem_entry in enumerate(self.logic_memory):
            print(f"[Logic Debug] Entry {idx+1}:")
            print(f"  - section_id: {mem_entry.get('section_id')}")
            print(f"  - title: {mem_entry.get('title')}")
            print(
                f"  - summary: {mem_entry.get('summary')[:100]}..."
                if len(mem_entry.get("summary", "")) > 100
                else f"  - summary: {mem_entry.get('summary')}"
            )
        print("[Logic Debug] =============================\n")

        # Construct global context from summaries（优先使用逻辑内存中的摘要）
        global_context = ""
        title_page_map = {}
        section_page_map = {}
        mem_list = self.logic_memory if self.logic_memory else map_results
        for i, res in enumerate(mem_list):
            global_context += f"【章节 {i+1}】{res.get('title','未知章节')}\n摘要：{res.get('summary','无摘要')}\n\n"
        # 依然从 map_results 构建页码索引
        for res in map_results:
            if res.get("start_page_num"):
                title_page_map[res["title"]] = res["start_page_num"]
            if res.get("section_id") and res.get("start_page_num"):
                section_page_map[res["section_id"]] = res["start_page_num"]
        fallback_page = list(title_page_map.values())[0] if title_page_map else None

        # 【日志5】打印将要传递给全局LLM的global_context
        print("\n[Logic Debug] === Global Context (sent to LLM) ===")
        print(global_context)
        print("[Logic Debug] =====================================\n")

        messages = [
            {
                "role": "system",
                "content": global_logic_review_prompt.format(
                    global_context=global_context
                ),
            },
            {"role": "user", "content": "请基于以上各章摘要进行全局逻辑一致性检查。"},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=8192,
                temperature=0.0,  # 降低温度确保全局逻辑审查结果稳定
            )
            raw_res = response.choices[0].message.content

            # 【日志6】打印全局LLM原始响应（前1000字符）
            print(f"[Logic Debug] Global LLM Raw Response (first 1000 chars):")
            print(raw_res[:1000])
            print("---")

            global_data = self._parse_json(raw_res)

            # Collect global thinking
            thinking_match = re.search(
                r"<thinking>(.*?)</thinking>", raw_res, re.DOTALL
            )
            global_thinking = thinking_match.group(1).strip() if thinking_match else ""

            full_thinking_log += (
                f"\n=== 全局一致性审查 (Global Review) ===\n{global_thinking}\n"
            )

            # Merge issues
            all_issues = []
            # Local issues
            for res in map_results:
                all_issues.extend(res["issues"])
            # Global issues
            all_issues.extend(global_data.get("issues", []))

            # ✅ 新增：细粒度事实冲突检测
            print(
                "\n[Fact Conflict Detection] Starting cross-chapter fact verification..."
            )
            fact_conflicts = self._detect_fact_conflicts()
            all_issues.extend(fact_conflicts)
            print(
                f"[Fact Conflict Detection] Added {len(fact_conflicts)} conflict issues\n"
            )

            # 为无页码的 issue 做兜底填充（优先按章节标题/section_id匹配其起始页，再尝试quote匹配，否则用首章节页码）
            for issue in all_issues:
                if not issue.get("page"):
                    sec_title = issue.get("section")
                    sec_id = issue.get("section_id") or issue.get("section")
                    if sec_title and sec_title in title_page_map:
                        issue["page"] = title_page_map[sec_title]
                    elif sec_id and sec_id in section_page_map:
                        issue["page"] = section_page_map[sec_id]
                    else:
                        # 试图通过 quote 在 XML 中查找所在页
                        quote_page = self._find_page_by_quote(issue.get("quote"))
                        if not quote_page:
                            quote_page = self._find_page_by_fuzzy_quote(
                                issue.get("quote"), threshold=0.6
                            )
                        if quote_page:
                            issue["page"] = quote_page
                        elif fallback_page:
                            issue["page"] = fallback_page

            return {
                "raw": json.dumps({"issues": all_issues}, ensure_ascii=False, indent=2),
                "thinking": full_thinking_log,
            }

        except Exception as e:
            print(f"[Logic] Global reduction failed: {e}")
            return {
                "raw": json.dumps({"issues": []}),
                "thinking": full_thinking_log
                + "\n=== 全局一致性审查 (Global Review) ===\n"
                + f"[Error] 全局分析失败：{e}",
            }

    def run_vision_review(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        include_page_image=False,
    ):
        """
        视觉审查（Vision），遍历文档中的图片进行检查。
        输出语言：中文
        """
        results = []

        # 1. 定义中文的 System Prompt，覆盖导入的默认 prompt
        # 确保包含 <thinking> 标签的要求，以便后续解析
        vision_system_prompt_cn = """
    你是一个专业的学术论文视觉审查助手。你的任务是审查论文中的图片及其上下文。
    
    【核心原则】
    - 只关注"图片是否支持正文/标题的论述"。
    - 如果图片内容和正文/标题不冲突、不矛盾，则视为"无问题"。
    - 忽略所有与"图文一致性"无关的视觉瑕疵（如清晰度、美观度、水印、边框等）。

    【输入材料】
    我会提供：
    1. 【目标图片】（裁剪图）：需要审查的核心对象。
    2. 【三页连续截图】：前一页、当前页、后一页（按顺序提供）。
    3. 【文档流文本】：参考信息（可能不准确，仅供参考）。

    【三步审查流程】
    
    **步骤1：视觉定位（找到图片在哪一页）**
    - 首先查看【目标图片】的视觉特征（形状、内容、颜色等）
    - 然后依次浏览三张页面截图，找到【目标图片】出现在哪一页
    - 记录：图片位于【前一页/当前页/后一页】中的哪一页
    
    **步骤2：依次解析三页内容（按前→中→后顺序）**
    对于每一页，提取以下信息：
    - 是否包含目标图片？
    - 页面上是否有与该图片相关的Caption（如"图 2-5 XXX"）？
    - 页面上是否有与该图号相关的正文描述？
    
    **重点**：
    - Caption通常紧贴图片下方或上方，是小号黑体字
    - 正文描述可能在图片前后，会引用图号（如"如图2-5所示..."）
    - Caption和正文描述可能跨页（图在前一页末尾，Caption在当前页开头）
    
    **步骤3：综合判断图文一致性**
    基于你从三页截图中提取的真实信息：
    - Caption的真实内容是什么？（以截图为准，忽略文档流错误）
    - 正文对该图的描述是什么？
    - 图片内容是否与Caption和正文描述一致？

    【严厉禁止】
    - 禁止评论图片清晰度、分辨率、字号大小。
    - 禁止评论图片是否美观、配色是否合理。
    - 禁止评论水印、Logo、装饰元素。
    - 如果图片与论文学术内容无关（如装饰/Logo/二维码），直接忽略，issues返回空。

    【输出格式】
    1. 在 <thinking> 标签中，按照三步流程详细记录你的分析过程：
       ```
       【步骤1：定位】
       目标图片的视觉特征：...
       在三页中的位置：第X页
       
       【步骤2：依次解析】
       前一页（Page N-1）：...
       当前页（Page N）：看到目标图片，旁边Caption是"图X-X ..."，正文提到...
       后一页（Page N+1）：...
       
       【步骤3：综合判断】
       真实Caption：...
       图片内容：...
       一致性分析：...
       ```
    
    2. 然后输出 JSON：{"issues": [...]}
       - 如果一致，issues为空数组
       - 如果不一致，提供具体的issue描述
    
    3. **请务必使用中文进行回复。**
    """

        # Determine client to use
        client = self.client
        using_qwen = "qwen" in vision_model_id.lower()
        if using_qwen:
            key = vision_api_key or self.client.api_key
            if key is None:
                raise ValueError(
                    "vision_api_key is required when using Qwen vision models. "
                    "Please set DASHSCOPE_API_KEY or pass --vision-api-key."
                )
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=vision_base_url or self.client.base_url,
            )
        elif vision_api_key or vision_base_url:
            from openai import OpenAI

            client = OpenAI(
                api_key=vision_api_key or self.client.api_key,
                base_url=vision_base_url or self.client.base_url,
            )

        image_info_map = {}
        parent_map = {c: p for p in self.doc_reader.root.iter() for c in p}

        for elem in self.doc_reader.root.iter("Image"):
            img_id = elem.get("image_id")
            page_num = elem.get("page_num")
            caption_text = ""
            context_text = []
            # Heuristic regex to detect captions in nearby paragraphs when extractor missed them
            # 修复：支持带连字符的图号，如 "图 2-5"、"Figure 3-2" 等
            caption_pattern = re.compile(
                r"^(figure|fig\.?|图)\s*[\d\-\.]+", re.IGNORECASE
            )

            # 1. Get Caption
            for child in elem:
                if child.tag == "Caption" and child.text:
                    caption_text = child.text

            # 2. Get Context（优化：减少混入其他图片描述）
            parent = parent_map.get(elem)
            if parent:
                try:
                    children = list(parent)
                    idx = children.index(elem)
                    # 优先提取图片前面的2个段落和后面的1个段落，减少混入风险
                    start_idx = max(0, idx - 2)
                    end_idx = min(len(children), idx + 2)

                    for i in range(start_idx, end_idx):
                        node = children[i]
                        if node.tag == "Paragraph" and node.text:
                            text = node.text.strip()
                            # 如果段落中包含其他图号（且不是当前图号），跳过以避免混淆
                            # 提取当前caption中的图号（如果有）
                            current_fig_num = None
                            if caption_text:
                                fig_match = re.search(r"图\s*([\d\-\.]+)", caption_text)
                                if fig_match:
                                    current_fig_num = fig_match.group(1)

                            # 如果该段落包含不同的图号，可能是其他图的描述，需谨慎
                            other_fig_match = re.search(r"图\s*([\d\-\.]+)", text)
                            if other_fig_match and current_fig_num:
                                other_fig_num = other_fig_match.group(1)
                                # 如果图号不同，标记一下但仍然包含（因为可能是正文引用）
                                if other_fig_num != current_fig_num:
                                    text = f"[⚠️可能引用其他图] {text}"

                            # If no caption captured yet, try to detect from nearby paragraphs
                            if not caption_text and caption_pattern.match(text):
                                caption_text = text
                            context_text.append(text)
                        elif node.tag == "Heading" and node.text:
                            context_text.append(f"[Heading: {node.text}]")
                except ValueError:
                    pass

            context_str = "\n".join(context_text)

            if img_id:
                image_info_map[img_id] = {
                    "page_num": page_num,
                    "caption": caption_text,
                    "context": context_str,
                }

        # Process images
        count = 0
        total_images = len(self.doc_reader.image_path_dict)
        process_limit = min(max_images, total_images)
        print(
            f"[Agent] Found {total_images} images, will review first {process_limit} images."
        )

        for img_id, filename in self.doc_reader.image_path_dict.items():
            if count >= max_images:
                break

            media_type, base64_img, error = self.doc_reader.get_image(img_id)
            if error:
                print(f"Error loading image {img_id}: {error}")
                continue

            meta = image_info_map.get(
                img_id, {"page_num": "?", "caption": "Unknown", "context": ""}
            )

            print(
                f"[Agent] [Vision] Reviewing image {img_id} (Page {meta['page_num']}): {meta['caption'][:30]}..."
            )

            # Optionally attach the full page image to help the model see captions/footers
            # New Strategy: Attach Previous Page, Current Page, Next Page to handle cross-page captions
            page_image_block = []
            if include_page_image and str(meta["page_num"]).isdigit():
                current_page = int(float(meta["page_num"]))
                # Fetch Previous, Current, Next pages
                pages_to_fetch = [current_page - 1, current_page, current_page + 1]
                page_labels = ["前一页", "当前页", "后一页"]

                for idx, p_num in enumerate(pages_to_fetch):
                    if p_num < 1 or p_num > self.doc_reader.num_page:
                        continue
                try:
                    page_media_type, page_base64_img, page_err = (
                        self.doc_reader.get_page_image(p_num)
                    )
                    if not page_err:
                        # 明确标注页面顺序：前一页、当前页、后一页
                        label = (
                            page_labels[idx]
                            if idx < len(page_labels)
                            else f"Page {p_num}"
                        )
                        page_image_block.append(
                            {
                                "type": "text",
                                "text": f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n【{label}：Page {p_num}】\n请仔细查看本页是否包含目标图片，以及是否有相关的Caption或正文描述。\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                            }
                        )
                        page_image_block.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{page_media_type};base64,{page_base64_img}"
                                },
                            }
                        )
                except Exception as e:
                    print(
                        f"[Agent] [Vision] Failed to attach page image for page {p_num}: {e}"
                    )

            # Construct message for Vision Model
            messages = [
                {"role": "system", "content": vision_system_prompt_cn},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 【图文一致性审查任务】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📎 **文档流参考信息**（可能不准确，仅供参考）：
- 图片ID: {img_id}
- 预估页码: {meta['page_num']}
- 提取的Caption: {meta['caption']}
- 上下文片段: {meta['context'][:150]}{'...' if len(meta['context']) > 150 else ''}

⚠️ **警告**：上述信息由PDF解析器提取，可能存在图号混乱、Caption错误、context混入其他图片描述等问题。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **请严格按照三步流程进行审查**：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**第1步：视觉定位**
- 下方第一张图是【目标图片】，记住它的视觉特征
- 在后续的三张页面截图中，找到该图片出现在哪一页

**第2步：依次解析三页内容（重要！）**
按照【前一页 → 当前页 → 后一页】的顺序，逐页提取：
- 该页是否包含目标图片？
- 是否有Caption（如"图 2-5 Focus结构"）？
- 是否有正文描述（如"如图2-5所示..."）？

**第3步：综合判断**
- 汇总从三页中提取的真实Caption和描述
- 判断图片内容是否与Caption、正文一致
- 以你从页面截图中看到的为准，而不是文档流信息

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

👇 **下方是目标图片和三页连续截图**

【目标图片】：""",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_img}"
                            },
                        },
                    ]
                    + page_image_block,
                },
            ]

            try:
                response = client.chat.completions.create(
                    model=vision_model_id,
                    messages=messages,
                    max_tokens=1000,
                    temperature=0.0,  # 降低温度确保视觉审查结果稳定
                )
                raw_content = response.choices[0].message.content

                # Debug: 如果是image 7，显示完整输出
                if img_id == "7":
                    print(f"[Debug] Image 7 raw output:\n{raw_content}\n")

                # Extract thinking and json
                thinking = ""
                thinking_match = re.search(
                    r"<thinking>(.*?)</thinking>", raw_content, re.DOTALL
                )
                if thinking_match:
                    thinking = thinking_match.group(1).strip()

                results.append(
                    {
                        "image_id": img_id,
                        "page": meta["page_num"],
                        "caption": meta["caption"],
                        "raw": raw_content,
                        "thinking": thinking,
                    }
                )
                count += 1

            except Exception as e:
                print(f"Vision review failed for image {img_id}: {e}")
                results.append({"image_id": img_id, "error": str(e)})

        return results

    def run_normative_logic_review(self):
        """Lightweight normative + logic review without tool calls; returns JSON string."""
        outline_xml = self.get_outline()
        body_text = self._extract_plain_text()
        messages = [
            {"role": "system", "content": normative_logic_prompt},
            {
                "role": "user",
                "content": f"大纲：\n{outline_xml}\n\n正文片段：\n{body_text}\n\n请按约定输出 JSON。",
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=800,
                temperature=0.0,  # 降低温度确保审查结果稳定
            )
            return response.choices[0].message.content
        except Exception as e:
            print(traceback.format_exc())
            return json.dumps(
                {"issues": [], "error": f"normative_logic_review_failed: {e}"}
            )

    def get_outline(self):

        outline = self.doc_reader.get_outline_root()

        xml_string = ET.tostring(outline, encoding="unicode", method="xml")
        xml_string = clean_xml_string(xml_string)
        dom = xml.dom.minidom.parseString(xml_string)
        xml_string = (
            dom.toprettyxml(indent="  ", newl="\n")
            .split("\n", 1)[1]
            .replace("&quot;", "")
        )
        return xml_string

    def run_actor(self, question, memory, tools=available_tools):
        xml_string = self.get_outline()
        initial_prompt = actor_prompt_template.format(
            document_outline=xml_string, question=question, memory=memory
        )

        initial_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_prompt},
        ]
        final_response, messages = self.run_agent(initial_messages, tools=tools)
        return final_response, messages

    def run_reviewer(
        self,
        initial_messages,
        initial_prompt=reviewer_prompt,
        tools=available_tools,
        extract_regex=r"<final_result>(.*)</final_result>",
    ):

        messages = []

        for item in initial_messages:
            # remove id, token_usage
            if "model" in item:  # from assistant
                messages.append(item["choices"][0]["message"])

            else:  # others
                messages.append(item)

        messages.append({"role": "user", "content": initial_prompt})

        final_response, messages = self.run_agent(
            messages, tools=tools, extract_regex=extract_regex
        )
        return final_response, messages

    def run_reflection(
        self,
        initial_messages,
        memory,
        tools=available_tools,
        extract_regex=r"<updated_guideline>(.*)</updated_guideline>",
    ):

        initial_prompt = reflection_prompt_template.format(memory=memory)

        messages = []

        for item in initial_messages:
            # remove id, token_usage
            if "model" in item:  # from assistant
                messages.append(item["choices"][0]["message"])

            else:  # others
                messages.append(item)

        messages.append({"role": "user", "content": initial_prompt})

        memory_new, messages_memory = self.run_agent(
            messages, tools=tools, extract_regex=extract_regex
        )
        return memory_new, messages_memory

    def run_agent(
        self,
        initial_messages,
        tools,
        extract_regex=r"<final_result>(.*)</final_result>",
        max_num_tool=10,
        max_round=10,
    ):

        messages = initial_messages
        messages_full = messages.copy()

        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=tools,
                tool_choice="auto",
            )

            # limit the number of tools called in one turn
            if (
                response.choices[0].message.tool_calls
                and len(response.choices[0].message.tool_calls) > max_num_tool
            ):
                response.choices[0].message.tool_calls = response.choices[
                    0
                ].message.tool_calls[:max_num_tool]

            messages_full.append(response.to_dict())
            messages.append(response.choices[0].message)

            # tools are callled
            num_round = 0
            while response.choices[0].message.tool_calls:
                # Wait to reduce rate limit errors
                time.sleep(self.tool_call_wait_time)

                # LLM can call multiple functions in one turn
                tool_response_tool, tool_response_user = [], []
                for tool_call in response.choices[0].message.tool_calls:
                    tool_response = self.get_reply_for_tool(
                        {
                            "type": "tool_use",
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "input": json.loads(tool_call.function.arguments),
                        }
                    )
                    if len(tool_response) > 1:  # tool reply with image
                        tool_response_tool.append(tool_response[0])
                        tool_response_user.extend(tool_response[1:])
                    else:
                        tool_response_tool.extend(tool_response)
                # tool calls must follow by tool response
                messages.extend(tool_response_tool + tool_response_user)
                messages_full.extend(tool_response_tool + tool_response_user)

                if num_round >= max_round:
                    tool_choice = "none"
                    print("Exceed max_round, stop calling tools")
                else:
                    tool_choice = "auto"
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    tools=tools,
                    tool_choice=tool_choice,
                )

                # limit the number of tools called in one turn
                if (
                    response.choices[0].message.tool_calls
                    and len(response.choices[0].message.tool_calls) > max_num_tool
                ):
                    response.choices[0].message.tool_calls = response.choices[
                        0
                    ].message.tool_calls[:max_num_tool]
                messages_full.append(response.to_dict())
                messages.append(response.choices[0].message)
                num_round += 1

            match_result = re.search(
                extract_regex, response.choices[0].message.content, re.DOTALL
            )
            if match_result is not None:
                final_response = match_result.group(1)
            else:
                final_response = response.choices[0].message.content

            return final_response.strip(), messages_full

        except Exception as e:
            print(traceback.format_exc())
            return str(e), messages_full

    def package_content(self, item, tool_use_id=None, image_content=None):
        if image_content is not None:  # tool reply with text and image
            if "deepseek" in self.model_id.lower():
                # DeepSeek-V3 is text-only, so we skip the image and provide a placeholder
                content = f"{item}\n[Note: An image was retrieved by the tool but is not displayed here because the current model is text-only.]"
                return [
                    {"role": "tool", "content": content, "tool_call_id": tool_use_id}
                ]

            content = [{"type": "text", "text": item}]
            for item in image_content:
                media_type, base64_image = item
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{base64_image}"
                        },
                    }
                )
            # As of Nov 2024, GPT-4o doesn't support tool response with image, therefore we package image in user message
            return [
                {
                    "role": "tool",
                    "content": "The result from tool is returned in the following user message:",
                    "tool_call_id": tool_use_id,
                },
                {"role": "user", "content": content, "tool_call_id": tool_use_id},
            ]
        else:  # tool only reply with text
            content = item
            return [{"role": "tool", "content": content, "tool_call_id": tool_use_id}]

    def get_reply_for_tool(self, item, max_search_results=24, max_page_images=20):

        if item["type"] == "tool_use":
            tool_use_id = item["id"]
            if item["name"] == "search":
                keyword = item["input"]["keyword"]
                search_root = self.doc_reader.search(keyword)
                if len(search_root) == 0:
                    result_text = f"We didn't find any section or paragraph that contains the keyword {keyword}"

                else:
                    if len(search_root) > max_search_results:
                        for subelement in search_root[max_search_results:]:
                            search_root.remove(subelement)

                        result_text = f"We found {str(len(search_root))} results that contain the keyword {keyword}. To shorten response, the first {max_search_results} results are listed below:\n"
                    else:
                        result_text = f"We found {str(len(search_root))} results that contain the keyword {keyword}, listed below:\n"
                    xml_string = ET.tostring(
                        search_root, encoding="unicode", method="xml"
                    )
                    xml_string = clean_xml_string(xml_string)
                    dom = xml.dom.minidom.parseString(xml_string)
                    xml_string = dom.toprettyxml(indent="  ", newl="\n").split("\n", 1)[
                        1
                    ]
                    result_text = result_text + xml_string

                return self.package_content(result_text, tool_use_id=tool_use_id)

            elif item["name"] == "get_section_content":
                section_id = str(item["input"]["section_id"])
                if section_id not in self.doc_reader.section_dict.keys():
                    result_text = f"The section_id {section_id} is not presented in the document, here is the full list of available section_id: {list(self.doc_reader.section_dict.keys())}. Please try again."

                else:
                    section_root = self.doc_reader.get_section_content(section_id)

                    xml_string = ET.tostring(
                        section_root, encoding="unicode", method="xml"
                    )
                    xml_string = clean_xml_string(xml_string)
                    dom = xml.dom.minidom.parseString(xml_string)
                    xml_string = dom.toprettyxml(indent="  ", newl="\n").split("\n", 1)[
                        1
                    ]
                    if len(xml_string) > 30000:
                        xml_string = (
                            xml_string[:30000]
                            + "\n...The content is too long. Try to get the content in sub sections."
                        )
                        result_text = (
                            f"Here is the text content of Section {section_id}:\n"
                            + xml_string
                        )
                    else:
                        result_text = (
                            f"Here is the full text content of Section {section_id}:\n"
                            + xml_string
                        )

                return self.package_content(result_text, tool_use_id=tool_use_id)

            elif item["name"] == "get_page_images":
                start_page_num = int(item["input"]["start_page_num"])

                end_page_num = int(item["input"]["end_page_num"]) + 1
                result_text = ""
                if start_page_num < 1:
                    result_text = (
                        result_text + "The start_page_num cannot be smaller than 1. "
                    )
                elif start_page_num > self.doc_reader.num_page:
                    result_text = (
                        result_text
                        + f"The start_page_num cannot be greater than max_page_num {str(self.doc_reader.num_page)}. "
                    )
                if end_page_num < 1:
                    result_text = (
                        result_text + "The end_page_num cannot be smaller than 1. "
                    )
                elif end_page_num > self.doc_reader.num_page:
                    result_text = (
                        result_text
                        + f"The end_page_num cannot be greater than max_page_num {str(self.doc_reader.num_page)}. "
                    )

                if len(result_text) > 0:
                    return self.package_content(
                        result_text + "Please try again",
                        tool_use_id=tool_use_id,
                    )

                else:
                    image_content = []
                    # end_page_num is included
                    for page_num in range(
                        start_page_num,
                        min(end_page_num + 1, start_page_num + max_page_images + 1),
                    ):
                        media_type, base64_image, error = (
                            self.doc_reader.get_page_image(page_num)
                        )
                        if error is not None:
                            raise Exception(
                                f"Error in extracting page_image {str(page_num)}: {str(error)}"
                            )
                        image_content.append([media_type, base64_image])
                    if end_page_num > start_page_num + max_page_images:
                        result_text = f"Here are the page images for page {str(start_page_num)} to page {str(start_page_num+max_page_images)}, as the number of page images exceeds the maximum limit of {str(max_page_images)}"
                    else:
                        result_text = f"Here are the page images for page {str(start_page_num)} to page {str(end_page_num)}"
                    return self.package_content(
                        result_text,
                        tool_use_id=tool_use_id,
                        image_content=image_content,
                    )

            elif item["name"] == "get_image":
                image_id = str(item["input"]["image_id"])
                if image_id not in self.doc_reader.image_path_dict:
                    result_text = f"The image_id {image_id} is not presented in the document, here is the full list of available image_id: {list(self.doc_reader.image_path_dict.keys())}. Please try again"

                    return self.package_content(result_text, tool_use_id=tool_use_id)

                else:
                    media_type, base64_image, error = self.doc_reader.get_image(
                        image_id
                    )
                    if error is not None:
                        raise Exception(
                            f"Error in extracting image {str(image_id)}: {str(error)}"
                        )
                    result_text = f"Here is the image content for image_id {image_id}"

                    return self.package_content(
                        result_text,
                        tool_use_id=tool_use_id,
                        image_content=[[media_type, base64_image]],
                    )

            elif item["name"] == "get_table_image":
                table_id = str(item["input"]["table_id"])
                if table_id not in self.doc_reader.table_image_path_dict:
                    result_text = f"The table {table_id} doesn't have a corresponding image, here is the full list of table_id that companies an image: {list(self.doc_reader.table_image_path_dict.keys())}. Please try again."

                    return self.package_content(result_text, tool_use_id=tool_use_id)

                else:
                    media_type, base64_image, error = self.doc_reader.get_table_image(
                        table_id
                    )
                    if error is not None:
                        raise Exception(
                            f"Error in extracting image for table {str(table_id)}: {str(error)}"
                        )
                    result_text = f"Here is the image content for table_id {table_id}"

                    return self.package_content(
                        result_text,
                        tool_use_id=tool_use_id,
                        image_content=[[media_type, base64_image]],
                    )

            else:
                result_text = (
                    "Tool "
                    f"{item['name']}"
                    " is not valid, here is the list of available tools:"
                    " [search, get_section_content, get_page_images, get_image, get_table_image]."
                    " Please try again."
                )
                return self.package_content(result_text, tool_use_id=tool_use_id)
