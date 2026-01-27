from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict

from .prompts import (
    chapter_selection_prompt,
    global_logic_review_prompt,
    local_chapter_review_prompt,
)


class LogicAgent:
    def __init__(self, doc_agent: Any):
        """
        初始化逻辑性审查Agent
        
        Args:
            doc_agent: DocAgent实例，提供公共功能（LLM调用、JSON解析、文档读取等）
        """
        self.doc_agent = doc_agent
        # 逻辑内存：存储每章摘要，供全局阶段使用
        self.logic_memory = []
        # 事实存储：用于跨章节冲突检测
        self.fact_store = {
            "entities": {},  # 实体：人名、机构、角色等
            "numbers": {},  # 数值：指标、参数、统计数据等
            "dates": {},  # 时间：日期、时间线
            "claims": [],  # 论断：重要观点和结论
        }

    def select_top_sections(self, max_sections=8, skip_front_matter=True):
        """
        使用LLM选择重要章节
        
        Args:
            max_sections: 最大章节数
            skip_front_matter: 是否跳过前置内容
            
        Returns:
            list: section_id列表
        """
        outline_xml = self.doc_agent.get_outline()
        messages = [
            {"role": "system", "content": chapter_selection_prompt},
            {
                "role": "user",
                "content": f'以下是论文大纲（XML）。请只输出最重要的大章节 section_id 列表（最多 {max_sections} 个），跳过封面、诚信承诺等非学术内容，但必须包含目录（非常重要的环节）。从摘要开始选择，用 JSON 数组表示，如 ["5", "7", "8", "9"]（摘要+目录+正文）.\n\n{outline_xml}',
            },
        ]
        try:
            response = self.doc_agent._call_llm(messages, max_tokens=500, temperature=0.0)
            raw = response.choices[0].message.content
            data = self.doc_agent._parse_json(raw)
            if isinstance(data, list):
                selected_sections = [str(x) for x in data][:max_sections]
            elif isinstance(data, dict) and "sections" in data:
                selected_sections = [str(x) for x in data.get("sections", [])][
                    :max_sections
                ]
            else:
                selected_sections = []

            # 如果启用了跳过前置内容，则过滤掉前面的非学术部分，但确保包含重要章节
            if skip_front_matter and selected_sections:
                # 简单策略：保留从第一个数字section_id开始的所有section
                filtered = []
                for sid in selected_sections:
                    try:
                        # 如果section_id是数字，保留
                        int(sid)
                        filtered.append(sid)
                    except ValueError:
                        # 非数字section_id可能是封面等，跳过
                        continue
                if filtered:
                    selected_sections = filtered[:max_sections]

            return selected_sections
        except Exception as e:
            print(f"[Logic] Failed to select sections: {e}")
            # Fallback: 返回前几个顶级section
            selected = []
            for child in self.doc_agent.doc_reader.root:
                if child.tag == "Section" and child.get("section_id"):
                    selected.append(child.get("section_id"))
                    if len(selected) >= max_sections:
                        break
            return selected

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
            response = self.doc_agent._call_llm(messages, max_tokens=4096, temperature=0.0)
            raw_response = response.choices[0].message.content
            facts = self.doc_agent._parse_json(raw_response)

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

        # 存储数值
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

        def _verify_entity_conflict(entity_key, occurrences):
            """用 LLM 复核实体冲突，确认是否为真正矛盾"""
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
你是学术文本一致性审查助手。请判断以下"同一实体键"的不同表述是否构成真正冲突。

规则：
1) 如果只是"别名/简称/同一公司不同写法"或"上下文不同但可兼容"，则不算冲突。
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
                response = self.doc_agent._call_llm(messages, max_tokens=800, temperature=0.0)
                raw = response.choices[0].message.content
                data = self.doc_agent._parse_json(raw)
                if isinstance(data, dict) and "is_conflict" in data:
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

        # 检测数值冲突（如"准确率"在不同章节有明显差异）
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

        print(f"[Fact Conflict Detection] Found {len(conflicts)} conflicts")
        return conflicts

    def run_hierarchical_logic_review(self):
        """
        层次化逻辑审查 (Map-Reduce)。
        1. 将文档分割为章节。
        2. Map: 审查每个章节（局部逻辑 + 摘要）。
        3. Reduce: 基于摘要进行全局一致性审查。
        """
        print("[LogicAgent] Starting Hierarchical Logic Review...")
        # 重置逻辑内存和事实存储
        self.logic_memory = []
        self.fact_store = {"entities": {}, "numbers": {}, "dates": {}, "claims": []}
        print("[Fact Store] Initialized for cross-chapter conflict detection")

        # Step 0: 选择重要章节
        top_sections = self.select_top_sections(max_sections=8, skip_front_matter=True)
        print(f"[Logic] Selected sections (skipping front matter): {top_sections}")

        # 构建章节列表
        chapters = []
        use_ids = top_sections if top_sections else []
        if not use_ids:  # fallback to top-level children
            for child in self.doc_agent.doc_reader.root:
                if child.tag == "Section" and child.get("section_id"):
                    use_ids.append(child.get("section_id"))
                    if len(use_ids) >= 8:
                        break

        for sid in use_ids:
            try:
                sec_root = self.doc_agent.doc_reader.get_section_content(sid)
            except Exception:
                continue
            # 提取标题
            title_text = f"Section {sid}"
            for node in sec_root:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text
                    break
            # 序列化为XML字符串，过滤页眉页脚
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

        # Step 2: Map - 审查每个章节
        for i, chap in enumerate(chapters):
            print(f"[Logic] Reviewing Chapter {i+1}: {chap['title']}")

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
                response = self.doc_agent._call_llm(messages, max_tokens=8192, temperature=0.0)
                raw_res = response.choices[0].message.content

                print(f"[Logic Debug] Chapter {i+1} Raw Response (first 500 chars):")
                print(raw_res[:500])
                print("---")

                data = self.doc_agent._parse_json(raw_res)

                # 提取thinking
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

                if not chapter_summary or chapter_summary == "None":
                    chapter_summary = f"[摘要解析失败] 第{i+1}章《{chap['title']}》的摘要未能正确生成，可能因为模型输出不完整或JSON格式错误。"
                    print(
                        f"[Logic WARNING] Chapter {i+1} 摘要为空或None，已使用兜底文本"
                    )

                print(
                    f"[Logic Debug] Chapter {i+1} Parsed Summary: '{chapter_summary}'"
                )

                # 写入逻辑内存
                memory_entry = {
                    "section_id": chap.get("section_id"),
                    "title": chap["title"],
                    "summary": chapter_summary,
                }
                self.logic_memory.append(memory_entry)

                # 提取并存储细粒度事实
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
                fallback_summary = f"[审查失败] 第{i+1}章《{chap['title']}》审查过程中出现异常：{str(e)}"

                self.logic_memory.append(
                    {
                        "section_id": chap.get("section_id"),
                        "title": chap["title"],
                        "summary": fallback_summary,
                    }
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

        # Step 3: Reduce - 全局一致性审查
        print("[Logic] Starting Global Reduction...")

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

        # 构建全局上下文
        global_context = ""
        title_page_map = {}
        section_page_map = {}
        mem_list = self.logic_memory if self.logic_memory else map_results
        for i, res in enumerate(mem_list):
            global_context += f"【章节 {i+1}】{res.get('title','未知章节')}\n摘要：{res.get('summary','无摘要')}\n\n"
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
            response = self.doc_agent._call_llm(messages, max_tokens=8192, temperature=0.0)
            raw_res = response.choices[0].message.content

            print(f"[Logic Debug] Global LLM Raw Response (first 1000 chars):")
            print(raw_res[:1000])
            print("---")

            global_data = self.doc_agent._parse_json(raw_res)

            # 提取全局thinking
            thinking_match = re.search(
                r"<thinking>(.*?)</thinking>", raw_res, re.DOTALL
            )
            global_thinking = thinking_match.group(1).strip() if thinking_match else ""

            full_thinking_log += (
                f"\n=== 全局一致性审查 (Global Review) ===\n{global_thinking}\n"
            )

            # 合并issues
            all_issues = []
            # 局部issues
            for res in map_results:
                all_issues.extend(res["issues"])
            # 全局issues
            all_issues.extend(global_data.get("issues", []))

            # 细粒度事实冲突检测
            print(
                "\n[Fact Conflict Detection] Starting cross-chapter fact verification..."
            )
            fact_conflicts = self._detect_fact_conflicts()
            all_issues.extend(fact_conflicts)
            print(
                f"[Fact Conflict Detection] Added {len(fact_conflicts)} conflict issues\n"
            )

            # 为无页码的 issue 做兜底填充
            for issue in all_issues:
                if not issue.get("page"):
                    sec_title = issue.get("section")
                    sec_id = issue.get("section_id") or issue.get("section")
                    if sec_title and sec_title in title_page_map:
                        issue["page"] = title_page_map[sec_title]
                    elif sec_id and sec_id in section_page_map:
                        issue["page"] = section_page_map[sec_id]
                    else:
                        quote_page = self.doc_agent._find_page_by_quote(issue.get("quote"))
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

    def run(self) -> Dict[str, Any]:
        """运行逻辑性审查并返回标准格式结果"""
        res = self.run_hierarchical_logic_review()
        raw = res.get("raw", "")
        thinking = res.get("thinking", "")
        parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}
        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            parsed["issues"] = []
        return {
            "raw": raw,
            "parsed": parsed,
            "thinking": thinking,
            "errors": [],
        }
