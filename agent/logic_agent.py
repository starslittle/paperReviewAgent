from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .prompts import (
    global_logic_review_prompt,
    local_chapter_review_prompt,
    local_chapter_review_retry_prompt,
    logic_prompt,
)


class LogicAgent:
    """
    逻辑性审查 Agent (Logic Review)

    【职责范围】：
    - ✅ 论证逻辑：论证跳跃、前后矛盾、论据不足
    - ✅ 语言学术性：口语化表达（"我觉得"、"超级"、"很"等）、用词不规范
    - ✅ 内容一致性：摘要vs结论、方法vs实验、标题vs内容
    - ✅ 连贯性：段落衔接、章节过渡
    - ✅ 内容充分性：工作量、创新点、实验数据

    【不负责】：
    - ❌ 格式编号：章节编号、图表编号（由 NormativeAgent 负责）
    - ❌ 引用格式：参考文献格式（由 NormativeAgent 负责）
    - ❌ 页面格式：页码、页眉页脚（由 NormativeAgent 负责）

    【审查方式】：
    - Map-Reduce：分章节局部审查 + 全局一致性检查
    - 覆盖范围：全文（每章 12000 字）
    - 审查起点：从「摘要」开始（与 NormativeAgent、VisionAgent 对齐）

    【输出】：
    - issue_type: "逻辑性" | "语言" | "连贯性"
    - 细粒度事实抽取（实体/数值/时间/论断）→ fact_store
    - 跨章节冲突检测（数值差异 > 5%）
    """

    ROLE_WEIGHTS = {
        "RESULT": 1.0,
        "METHOD": 0.9,
        "DESIGN": 0.8,
        "BACKGROUND": 0.3,
        "CONCLUSION": 0.6,
    }

    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent
        self.logic_memory: List[Dict[str, Any]] = []
        self.fact_store = {"entities": {}, "numbers": {}, "dates": {}, "claims": []}

    def run(self) -> Dict[str, Any]:
        res = self.run_hierarchical_logic_review()
        raw = res.get("raw", "")
        thinking = res.get("thinking", "")
        parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}

        # 处理两种可能的返回格式：{"issues": [...]} 或 [{...}, {...}]
        if isinstance(parsed, list):
            issues = parsed
            parsed = {"issues": issues}
        elif isinstance(parsed, dict):
            issues = parsed.get("issues", [])
            if not isinstance(issues, list):
                parsed["issues"] = []
        else:
            parsed = {"issues": []}

        return {
            "raw": raw,
            "parsed": parsed,
            "thinking": thinking,
            "errors": [],
        }

    def run_logic_review(self) -> Dict[str, Any]:
        """逻辑审查，不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Logic Review...")
        return self.doc_agent._run_simple_review(logic_prompt)

    def _extract_chapter_facts(self, chapter_content, chapter_info):
        """
        从章节内容中提取细粒度事实（用于跨章节冲突检测）
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
   - **重要**：不要提取参考文献中的期刊卷期号、期次、页码等引用信息（如"第52卷"、"第3期"、"pp.123-456"等）

3. **时间（Dates）**：日期、时间节点、时间段等
   - 示例：{"type": "时间节点", "key": "项目启动", "value": "2023年3月"}

4. **重要论断（Claims）**：关键结论、核心观点（限5条最重要的）
   - 示例：{"claim": "算法A在准确率上优于算法B", "type": "比较结论"}

**注意**：
- 只提取明确的事实，不要推断
- 保留原文上下文片段（用于定位）
- 如果某类事实不存在，返回空数组
- **重要**：跳过参考文献区域的卷期号、期次等引用格式信息

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

        content_snippet = chapter_content[:8000]

        messages = [
            {"role": "system", "content": fact_extraction_prompt},
            {
                "role": "user",
                "content": f"章节标题：{chapter_info.get('title', 'Unknown')}\n\n章节内容：\n{content_snippet}\n\n请提取关键事实。",
            },
        ]

        try:
            response = self.doc_agent._call_llm(
                messages, max_tokens=4096, temperature=0.0
            )
            raw_response = response.choices[0].message.content
            facts = self.doc_agent._parse_json(raw_response)

            # 确保返回字典格式
            if not isinstance(facts, dict):
                facts = {"entities": [], "numbers": [], "dates": [], "claims": []}

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
        """将提取的事实存储到 fact_store 中。"""
        chapter_label = f"{chapter_info.get('title', 'Unknown')} (第{chapter_info.get('start_page_num', '?')}页)"

        for entity in facts.get("entities", []):
            key = entity.get("key")
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

        for number in facts.get("numbers", []):
            key = number.get("key")
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
        """检测 fact_store 中的冲突。"""
        print("[Fact Conflict Detection] Analyzing cross-chapter conflicts...")
        conflicts = []

        def _verify_entity_conflict(entity_key, occurrences):
            """用 LLM 复核实体冲突，确认是否为真正矛盾。"""
            try:
                sample = []
                for occ in occurrences[:6]:
                    sample.append(
                        {
                            "value": occ.get("value"),
                            "source": occ.get("source"),
                            "context": occ.get("context", "")[:200],
                        }
                    )

                prompt = """
你是学术文本一致性审查助手。请判断以下“同一实体键”的不同表述是否构成真正冲突。

规则：
1) 如果只是“别名/简称/同一公司不同写法”或“上下文不同但可兼容”，则不算冲突。
2) 只有在明确相互矛盾（同一实体键被赋予不同且不可兼容的值）时，才算冲突。
3) 请基于上下文判断是否可兼容。

请输出严格 JSON：
{"is_conflict": true/false, "reason": "..."}
"""
                messages = [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"entity_key": entity_key, "occurrences": sample},
                            ensure_ascii=False,
                        ),
                    },
                ]
                response = self.doc_agent._call_llm(
                    messages, max_tokens=800, temperature=0.0
                )
                raw = response.choices[0].message.content
                data = self.doc_agent._parse_json(raw)
                # 确保返回字典格式
                if not isinstance(data, dict):
                    return True, ""
                if "is_conflict" in data:
                    return bool(data.get("is_conflict")), data.get("reason", "")
            except Exception as e:
                error_text = str(e)
                if "Insufficient Balance" in error_text or "402" in error_text:
                    print(
                        "[Fact Conflict Verification] Skipped (insufficient balance for verification)"
                    )
                    return False, ""
                print(f"[Fact Conflict Verification] Failed: {e}")
            return True, ""

        # 1. 检测实体冲突（如"甲方"在不同章节有不同的值）- 已关闭

        # 2. 检测数值冲突（如"准确率"在不同章节有明显差异）
        # 排除参考文献引用信息（期刊卷期、期次、页码等）
        reference_keywords = ["卷", "期", "页", "volume", "issue", "pp", "p.", "页码"]

        for metric_key, occurrences in self.fact_store["numbers"].items():
            # 检查是否为参考文献引用信息
            is_reference_info = any(
                keyword in metric_key.lower()
                or keyword in occ.get("context", "").lower()
                for keyword in reference_keywords
                for occ in occurrences
            )

            # 如果是参考文献引用信息，跳过检测
            if is_reference_info:
                print(
                    f"[Fact Conflict Detection] Skipping reference info: '{metric_key}'"
                )
                continue

            if len(occurrences) > 1:
                values = [
                    occ["value"]
                    for occ in occurrences
                    if isinstance(occ["value"], (int, float))
                ]
                if len(values) > 1:
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

        print(f"[Fact Conflict Detection] Found {len(conflicts)} conflicts")
        return conflicts

    def _normalize_claim_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = text.lower().strip()
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`]", "", text)
        return text

    def _has_claim_overlap(self, core_claims, fact_claims) -> bool:
        if not isinstance(core_claims, list) or not isinstance(fact_claims, list):
            return False
        normalized_core = [self._normalize_claim_text(c) for c in core_claims if c]
        normalized_fact = [
            self._normalize_claim_text(c.get("claim", "")) for c in fact_claims if c
        ]
        normalized_core = [c for c in normalized_core if c]
        normalized_fact = [c for c in normalized_fact if c]
        if not normalized_core or not normalized_fact:
            return False
        for core in normalized_core:
            for fact in normalized_fact:
                if core in fact or fact in core:
                    return True
        return False

    def _is_logic_skeleton_stable(self, logic_skeleton, stability_check) -> bool:
        if not isinstance(logic_skeleton, dict):
            return False
        chapter_role = logic_skeleton.get("chapter_role")
        core_claims = logic_skeleton.get("core_claims")
        if (
            not chapter_role
            or not isinstance(core_claims, list)
            or len(core_claims) < 1
        ):
            return False
        if isinstance(stability_check, dict):
            if stability_check.get("is_stable") is False:
                return False
        return True

    def _build_global_context(self, mem_list):
        global_context = ""
        for i, res in enumerate(mem_list):
            logic_skeleton = res.get("logic_skeleton") or {}
            core_claims = logic_skeleton.get("core_claims") or []
            if not core_claims:
                continue
            chapter_role = logic_skeleton.get("chapter_role") or "UNKNOWN"
            base_weight = self.ROLE_WEIGHTS.get(chapter_role, 0.6)
            confidence = res.get("confidence", "HIGH")
            weight = base_weight * (0.5 if confidence == "LOW" else 1.0)
            global_context += f"【章节 {i+1}】{res.get('title','未知章节')}\n"
            global_context += f"【章节角色】{chapter_role}\n"
            global_context += f"【权重】{weight}\n"
            global_context += "【核心论断】\n"
            for claim in core_claims:
                global_context += f"- {claim}\n"
            dependencies = logic_skeleton.get("dependencies") or []
            outputs = logic_skeleton.get("outputs") or []
            if dependencies:
                global_context += "【依赖】\n"
                for dep in dependencies:
                    global_context += f"- {dep}\n"
            if outputs:
                global_context += "【产出】\n"
                for out in outputs:
                    global_context += f"- {out}\n"
            global_context += f"【稳定性】{confidence}\n\n"
        return global_context

    def _get_outermost_section_ids(self) -> List[str]:
        """
        获取真实章节的顶层Section ID（结合 level 属性和 Heading 标签）
        - 只选择 level="1" 的顶层Section
        - 必须包含真实 <Heading> 标签
        - 从「摘要」开始
        - 排除目录、封面、承诺等非内容Section
        """
        section_ids = []
        abstract_start_index = None

        # 第一遍：找到摘要的位置
        for idx, child in enumerate(self.doc_agent.doc_reader.root):
            if child.tag != "Section":
                continue

            # 检查是否包含真实标题
            title_text = None
            for node in child:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text.strip()
                    break

            if title_text:
                normalized = title_text.lower().replace(" ", "")
                if any(key in normalized for key in ["摘要", "abstract"]):
                    abstract_start_index = idx
                    break

        # 黑名单：排除这些Section（即使有Heading和level=1）
        SKIP_TITLES = [
            "目录",
            "目 录",
            "封面",
            "诚信承诺",
            "致谢",
            "contents",
            "tableofcontents",
        ]

        # 第二遍：收集真实的Level 1章节
        for idx, child in enumerate(self.doc_agent.doc_reader.root):
            if child.tag != "Section" or not child.get("section_id"):
                continue

            # 跳过摘要之前的Section
            if abstract_start_index is not None and idx < abstract_start_index:
                continue

            # 检查 level 属性
            level = child.get("level")
            if level != "1":
                continue

            # 检查是否包含真实标题
            has_heading = False
            title_text = None
            for node in child:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text.strip()
                    has_heading = True
                    break

            # 必须有Heading才算章节
            if not has_heading:
                continue

            # 排除黑名单中的Section
            if title_text:
                normalized_title = title_text.lower().replace(" ", "")
                if any(skip in normalized_title for skip in SKIP_TITLES):
                    print(f"[Logic] Skip non-content section: {title_text}")
                    continue

            # 通过所有检查，加入章节列表
            section_ids.append(child.get("section_id"))

        return section_ids

    def run_hierarchical_logic_review(self) -> Dict[str, Any]:
        """
        层次化逻辑审查 (Map-Reduce)。
        """
        print("[Agent] Starting Hierarchical Logic Review...")
        self.logic_memory = []
        self.fact_store = {"entities": {}, "numbers": {}, "dates": {}, "claims": []}
        print("[Fact Store] Initialized for cross-chapter conflict detection")

        top_sections = self._get_outermost_section_ids()
        print(f"[Logic] Selected outermost sections: {top_sections}")

        chapters = []
        use_ids = top_sections if top_sections else []
        if not use_ids:
            print("[Logic] No outermost sections found, fallback to LLM selection.")
            use_ids = self.doc_agent.select_top_sections(
                max_sections=8, skip_front_matter=True
            )

        for sid in use_ids:
            try:
                sec_root = self.doc_agent.doc_reader.get_section_content(sid)
            except Exception:
                continue
            title_text = f"Section {sid}"
            for node in sec_root:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text
                    break
            filtered_root = self.doc_agent._filter_header_footer_from_section(sec_root)
            content_xml = ET.tostring(filtered_root, encoding="unicode", method="xml")
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

        for i, chap in enumerate(chapters):
            print(f"[Logic] Reviewing Chapter {i+1}: {chap['title']}")
            content_snippet = chap["content_xml"][:12000]

            user_content = (
                f"章节标题：{chap['title']}\n"
                f"章节XML内容：\n{content_snippet}\n\n"
                "请按约定输出 JSON，必须包含 local_summary、logic_skeleton、stability_check。"
                "注意直接使用 XML 节点中的 page_num，如果节点没有 page_num 再用章节 start_page_num 兜底，不要猜测页码。"
            )

            try:

                def _run_local_review(system_prompt):
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ]
                    response = self.doc_agent._call_llm(
                        messages, max_tokens=8192, temperature=0.0
                    )
                    raw = response.choices[0].message.content
                    data = self.doc_agent._parse_json(raw)
                    thinking_match = re.search(
                        r"<thinking>(.*?)</thinking>", raw, re.DOTALL
                    )
                    thinking = thinking_match.group(1).strip() if thinking_match else ""
                    return raw, data, thinking

                raw_res, data, thinking = _run_local_review(local_chapter_review_prompt)

                print(f"[Logic Debug] Chapter {i+1} Raw Response (first 500 chars):")
                print(raw_res[:500])
                print("---")
                if not isinstance(data, dict):
                    data = {}

                logic_skeleton = data.get("logic_skeleton") or {}
                stability_check = data.get("stability_check") or {}
                if not self._is_logic_skeleton_stable(logic_skeleton, stability_check):
                    print(
                        f"[Logic WARNING] Chapter {i+1} logic_skeleton unstable, retrying once"
                    )
                    raw_res, data, thinking = _run_local_review(
                        f"{local_chapter_review_prompt}\n\n{local_chapter_review_retry_prompt}"
                    )
                    if not isinstance(data, dict):
                        data = {}
                    logic_skeleton = data.get("logic_skeleton") or {}
                    stability_check = data.get("stability_check") or {}
                low_confidence = not self._is_logic_skeleton_stable(
                    logic_skeleton, stability_check
                )
                if low_confidence:
                    logic_skeleton["confidence"] = "LOW"

                full_thinking_log += (
                    f"\n#### Chapter {i+1}: {chap['title']}\n{thinking}\n"
                )

                local_summary = (
                    data.get("local_summary") if isinstance(data, dict) else ""
                )
                if not local_summary or local_summary == "None":
                    local_summary = f"[摘要解析失败] 第{i+1}章《{chap['title']}》的摘要未能正确生成，可能因为模型输出不完整或JSON格式错误。"
                    print(
                        f"[Logic WARNING] Chapter {i+1} local_summary 为空或None，已使用兜底文本"
                    )

                print(f"[Logic Debug] Chapter {i+1} Parsed Summary: '{local_summary}'")
                print(f"[Logic Debug] Summary Length: {len(local_summary)}")

                memory_entry = {
                    "section_id": chap.get("section_id"),
                    "title": chap["title"],
                    "local_summary": local_summary,
                    "logic_skeleton": logic_skeleton,
                    "confidence": "LOW" if low_confidence else "HIGH",
                }
                self.logic_memory.append(memory_entry)

                print(
                    f"[Logic Debug] Stored in logic_memory: section_id={memory_entry['section_id']}, title={memory_entry['title']}, confidence={memory_entry['confidence']}"
                )

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
                    core_claims = logic_skeleton.get("core_claims") or []
                    if core_claims and not self._has_claim_overlap(
                        core_claims, chapter_facts.get("claims", [])
                    ):
                        logic_skeleton["confidence"] = "LOW"
                        low_confidence = True
                except Exception as fact_error:
                    print(f"[Fact Extraction] Failed for chapter {i+1}: {fact_error}")

                map_results.append(
                    {
                        "title": chap["title"],
                        "local_summary": local_summary,
                        "logic_skeleton": logic_skeleton,
                        "confidence": "LOW" if low_confidence else "HIGH",
                        "issues": data.get("issues", []),
                        "start_page_num": chap.get("start_page_num"),
                        "section_id": chap.get("section_id"),
                    }
                )

            except Exception as e:
                print(f"[Logic] Failed to review chapter {i+1}: {e}")
                fallback_summary = f"[审查失败] 第{i+1}章《{chap['title']}》审查过程中出现异常：{str(e)}"

                self.logic_memory.append(
                    {
                        "section_id": chap.get("section_id"),
                        "title": chap["title"],
                        "local_summary": fallback_summary,
                        "logic_skeleton": {},
                        "confidence": "LOW",
                    }
                )
                print(
                    "[Logic Debug] Exception fallback: Added to logic_memory with error summary"
                )

                map_results.append(
                    {
                        "title": chap["title"],
                        "local_summary": fallback_summary,
                        "logic_skeleton": {},
                        "confidence": "LOW",
                        "issues": [],
                        "start_page_num": chap.get("start_page_num"),
                        "section_id": chap.get("section_id"),
                    }
                )

        for res in map_results:
            start_page = res.get("start_page_num")
            if start_page:
                for issue in res["issues"]:
                    if not issue.get("page"):
                        issue["page"] = start_page

        print("[Logic] Starting Global Reduction...")

        print("\n[Logic Debug] === Logic Memory Content ===")
        print(f"[Logic Debug] Total entries in logic_memory: {len(self.logic_memory)}")
        for idx, mem_entry in enumerate(self.logic_memory):
            print(f"[Logic Debug] Entry {idx+1}:")
            print(f"  - section_id: {mem_entry.get('section_id')}")
            print(f"  - title: {mem_entry.get('title')}")
            print(
                f"  - local_summary: {mem_entry.get('local_summary')[:100]}..."
                if len(mem_entry.get("local_summary", "")) > 100
                else f"  - local_summary: {mem_entry.get('local_summary')}"
            )
            print(f"  - confidence: {mem_entry.get('confidence')}")
        print("[Logic Debug] =============================\n")

        title_page_map = {}
        section_page_map = {}
        mem_list = self.logic_memory if self.logic_memory else map_results
        global_context = self._build_global_context(mem_list)
        for res in map_results:
            if res.get("start_page_num"):
                title_page_map[res["title"]] = res["start_page_num"]
            if res.get("section_id") and res.get("start_page_num"):
                section_page_map[res["section_id"]] = res["start_page_num"]
        fallback_page = list(title_page_map.values())[0] if title_page_map else None

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
            response = self.doc_agent._call_llm(
                messages, max_tokens=8192, temperature=0.0
            )
            raw_res = response.choices[0].message.content

            print("[Logic Debug] Global LLM Raw Response (first 1000 chars):")
            print(raw_res[:1000])
            print("---")

            global_data = self.doc_agent._parse_json(raw_res)

            # 处理两种可能的返回格式：{"issues": [...]} 或 [{...}, {...}]
            if isinstance(global_data, list):
                global_issues = global_data
            elif isinstance(global_data, dict):
                global_issues = global_data.get("issues", [])
            else:
                global_issues = []

            thinking_match = re.search(
                r"<thinking>(.*?)</thinking>", raw_res, re.DOTALL
            )
            global_thinking = thinking_match.group(1).strip() if thinking_match else ""

            full_thinking_log += (
                f"\n=== 全局一致性审查 (Global Review) ===\n{global_thinking}\n"
            )

            all_issues = []
            for res in map_results:
                all_issues.extend(res["issues"])
            all_issues.extend(global_issues)

            print(
                "\n[Fact Conflict Detection] Starting cross-chapter fact verification..."
            )
            fact_conflicts = self._detect_fact_conflicts()
            all_issues.extend(fact_conflicts)
            print(
                f"[Fact Conflict Detection] Added {len(fact_conflicts)} conflict issues\n"
            )

            for issue in all_issues:
                if not issue.get("page"):
                    sec_title = issue.get("section")
                    sec_id = issue.get("section_id") or issue.get("section")
                    if sec_title and sec_title in title_page_map:
                        issue["page"] = title_page_map[sec_title]
                    elif sec_id and sec_id in section_page_map:
                        issue["page"] = section_page_map[sec_id]
                    else:
                        quote_page = self.doc_agent._find_page_by_quote(
                            issue.get("quote")
                        )
                        if not quote_page:
                            quote_page = self.doc_agent._find_page_by_fuzzy_quote(
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

    # NOTE: run_normative_logic_review() has been removed
    # This method is deprecated as normative and logic reviews have been separated
    # into NormativeAgent and LogicAgent respectively.
