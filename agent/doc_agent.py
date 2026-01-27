import copy
import json
import re
import time
import traceback
import xml.dom.minidom
import xml.etree.ElementTree as ET
from typing import Optional, Union, Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from openai import OpenAI

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
    vision_description_prompt,
    text_analysis_prompt,
    text_claim_prompt,
    image_evidence_prompt,
    context_fitness_prompt,
    judge_prompt,
)


def clean_xml_string(xml_str):
    cleaned = "".join(char for char in xml_str if char.isprintable() or char.isspace())
    return cleaned


class DocAgent:
    def __init__(
        self,
        doc_reader,
        model_id="deepseek-v3.2",
        temperature=0.0,
        max_tokens=8192,
        api_key=None,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        tool_call_wait_time=10,
    ):
        self.doc_reader = doc_reader
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        # 如果没有提供api_key，使用DASHSCOPE_API_KEY
        if api_key is None:
            import os
            api_key = os.getenv("DASHSCOPE_API_KEY")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.tool_call_wait_time = tool_call_wait_time
        self._header_footer_index = None
        self._header_footer_pages = {}
        self._header_footer_regex = re.compile(
            r"(第\s*\d+\s*页|页码|学院|专业|指导教师|学校|论文|本科|毕业|\d{4}\s*年)",
            re.IGNORECASE,
        )

    def _normalize_header_footer_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_header_footer_index(
        self, top_bottom_n: int = 2, repeat_threshold: int = 3
    ):
        if self._header_footer_index is not None:
            return

        per_page_texts = {}
        for elem in self.doc_reader.root.iter():
            if elem.tag not in ["Paragraph", "Heading", "Title", "Caption", "Header", "Footer"]:
                continue
            if not elem.text:
                continue
            page_num = elem.get("page_num")
            if not page_num:
                continue
            try:
                page_int = int(float(page_num))
            except Exception:
                continue
            norm = self._normalize_header_footer_text(elem.text)
            if not norm:
                continue
            per_page_texts.setdefault(page_int, []).append(norm)

        top_bottom_by_page = {}
        for page, texts in per_page_texts.items():
            if not texts:
                continue
            top = texts[:top_bottom_n]
            bottom = texts[-top_bottom_n:] if len(texts) > top_bottom_n else texts
            top_bottom_by_page[page] = set(top + bottom)

        counts = {}
        for page, texts in per_page_texts.items():
            for norm in set(texts):
                counts[norm] = counts.get(norm, 0) + 1

        repeated_texts = {t for t, c in counts.items() if c >= repeat_threshold}

        regex_matches = set()
        for norm in counts.keys():
            if self._header_footer_regex.search(norm):
                regex_matches.add(norm)

        header_footer_texts = set(repeated_texts)
        header_footer_texts.update(regex_matches)

        self._header_footer_index = header_footer_texts
        self._header_footer_pages = top_bottom_by_page

    def _is_header_footer(
        self, text: str, page_num: Optional[Union[str, int]] = None
    ) -> bool:
        if not text:
            return False
        self._build_header_footer_index()
        norm = self._normalize_header_footer_text(text)
        if not norm:
            return False
        if norm in self._header_footer_index:
            return True
        if page_num:
            try:
                page_int = int(float(page_num))
            except Exception:
                page_int = None
            if page_int and norm in self._header_footer_pages.get(page_int, set()):
                if self._header_footer_regex.search(norm):
                    return True
        return False

    def _filter_header_footer_from_section(
        self, section_root: ET.Element
    ) -> ET.Element:
        filtered = copy.deepcopy(section_root)
        for parent in filtered.iter():
            for child in list(parent):
                if child.tag in ["Header", "Footer"]:
                    parent.remove(child)
                    continue
                if child.tag not in ["Paragraph", "Heading", "Title", "Caption"]:
                    continue
                if not child.text:
                    continue
                page_num = child.get("page_num") or parent.get("page_num")
                if self._is_header_footer(child.text, page_num):
                    parent.remove(child)
        return filtered

    def _extract_plain_text(self, char_limit=6000):
        """Extract plain text segments for lightweight review."""
        texts = []
        for elem in self.doc_reader.root.iter():
            if elem.text and elem.tag in ["Paragraph", "Title", "Caption", "Header", "Footer"]:
                t = elem.text.strip()
                if t and not self._is_header_footer(t, elem.get("page_num")):
                    texts.append(t)
            if sum(len(x) for x in texts) > char_limit:
                break
        combined = "\n".join(texts)
        return combined[:char_limit]

    def _call_llm(self, messages, max_tokens=None, temperature=None, **kwargs):
        """公共方法：调用LLM API"""
        return self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
            **kwargs
        )

    def _extract_thinking(self, raw_content):
        """从响应中提取thinking部分"""
        thinking_match = re.search(r"<thinking>(.*?)</thinking>", raw_content, re.DOTALL)
        return thinking_match.group(1).strip() if thinking_match else ""

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
            response = self._call_llm(messages, max_tokens=1500, temperature=0.0)
            raw_content = response.choices[0].message.content
            return {"raw": raw_content, "thinking": self._extract_thinking(raw_content)}
        except Exception as e:
            print(traceback.format_exc())
            return {"raw": "", "thinking": "", "error": str(e)}

    def _parse_json(self, raw_content):
        """Helper to safely extract and parse JSON from LLM response."""
        try:
            if not raw_content:
                return {"issues": []}

            # 提取 <json> 标签内容
            json_block = re.search(r"<json>(.*?)</json>", raw_content, re.DOTALL)
            if json_block:
                raw_content = json_block.group(1).strip()

            # 兜底处理：存在未闭合的 <thinking> 时，直接截断
            if "<thinking>" in raw_content and "</thinking>" not in raw_content:
                raw_content = raw_content.split("<thinking>", 1)[0].strip()

            # 移除 <thinking> 标签
            cleaned = re.sub(
                r"<thinking>.*?</thinking>", "", raw_content, flags=re.DOTALL
            ).strip()

            # 移除 markdown 代码块标记
            cleaned = re.sub(
                r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE
            ).strip()

            if not cleaned:
                return {"issues": []}

            # 若没有任何 JSON 起始符号，直接返回空结果
            if "{" not in cleaned and "[" not in cleaned:
                return {"issues": []}

            # 尝试修复截断的JSON
            # 检查是否被截断（最后一个字符不是 } 或 ]）
            if cleaned and cleaned[-1] not in '}]':
                print("[Warning] JSON appears to be truncated, attempting to fix...")

                # 尝试闭合未完成的字符串
                if cleaned.count('"') % 2 != 0:
                    # 奇数个引号，说明有未闭合的字符串
                    cleaned += '"'

                # 尝试闭合数组和对象
                # 计算需要闭合的括号
                open_braces = cleaned.count('{') - cleaned.count('}')
                open_brackets = cleaned.count('[') - cleaned.count(']')

                # 按相反顺序添加闭合括号
                for _ in range(open_brackets):
                    cleaned += ']'
                for _ in range(open_braces):
                    cleaned += '}'

                print(f"[Fix] Attempted to close {open_brackets} brackets and {open_braces} braces")

            # 移除字符串值中的换行符和特殊字符（改进版）
            # 使用更强大的方法：先识别 JSON 字符串边界
            cleaned = self._clean_json_strings(cleaned)

            # 尝试解析
            decoder = json.JSONDecoder()
            for candidate in [cleaned, raw_content]:
                for idx in range(len(candidate)):
                    if candidate[idx] not in "{[":
                        continue
                    try:
                        obj, _ = decoder.raw_decode(candidate[idx:])
                        return obj
                    except Exception:
                        continue

            # 最后尝试直接解析
            return json.loads(cleaned)

        except Exception as e:
            print(f"[Error] JSON parse failed: {str(e)[:200]}")
            print(f"[Error] Raw content preview: {raw_content[:500]}...")
            return {"issues": []}

    def _clean_json_strings(self, json_str):
        """
        清理 JSON 字符串中的换行符和特殊字符
        保留 JSON 结构，只处理字符串值内部的内容
        """
        result = []
        in_string = False
        escape_next = False
        current_string = []

        for char in json_str:
            if escape_next:
                current_string.append(char)
                escape_next = False
                continue

            if char == '\\':
                current_string.append(char)
                escape_next = True
            elif char == '"':
                current_string.append(char)
                if in_string:
                    # 字符串结束
                    in_string = False
                else:
                    # 字符串开始
                    in_string = True
            elif char in '\n\r\t' and in_string:
                # 在字符串内，将换行符和制表符替换为空格
                current_string.append(' ')
            elif char == '\n' and not in_string:
                # 在 JSON 结构中，直接保留（会被 JSON 解析器忽略）
                current_string.append(char)
            else:
                current_string.append(char)

        return ''.join(current_string)

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

    def run_logic_review(self):
        """逻辑审查，不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Logic Review...")
        return self._run_simple_review(logic_prompt)

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
            response = self._call_llm(messages, max_tokens=800, temperature=0.0)
            return response.choices[0].message.content
        except Exception as e:
            print(traceback.format_exc())
            return json.dumps(
                {"issues": [], "error": f"normative_logic_review_failed: {e}"}
            )

    # ==================== 以下方法已移出到各Agent中，保留仅为向后兼容 ====================
    # 这些方法现在应该通过对应的Agent调用，而不是直接调用DocAgent的方法
    # ================================================================================
    
    def _extract_chapter_facts(self, chapter_content, chapter_info):
        """
        【已移出】此方法已移至LogicAgent中
        """
        raise NotImplementedError("此方法已移至LogicAgent，请通过LogicAgent调用")

    def _store_facts(self, facts, chapter_info):
        """
        【已移出】此方法已移至LogicAgent中
        """
        raise NotImplementedError("此方法已移至LogicAgent，请通过LogicAgent调用")

    def _detect_fact_conflicts(self):
        """
        【已移出】此方法已移至LogicAgent中
        """
        raise NotImplementedError("此方法已移至LogicAgent，请通过LogicAgent调用")

    def run_hierarchical_logic_review(self):
        """
        【已移出】此方法已移至LogicAgent中
        """
        raise NotImplementedError("此方法已移至LogicAgent，请通过LogicAgent调用")

    def _build_figure_unit(self, img_id: str, image_info: Dict, base64_img: str, media_type: str) -> Optional[Dict]:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _extract_reference_texts(self, img_id: str, caption: str, section_info: Dict) -> List[str]:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _extract_context_around_image(self, page_num: int, section_info: Dict) -> tuple:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _extract_text_claims(self, figure_unit: Dict) -> List[Dict]:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _analyze_image_evidence(self, client, vision_model_id: str, figure_unit: Dict) -> Optional[Dict]:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _analyze_context_fitness(self, figure_unit: Dict, image_evidence: Dict) -> Dict:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _judge_consistency(
        self,
        text_claims: List[Dict],
        image_evidence: Dict,
        context_fitness: Dict,
        figure_unit: Dict
    ) -> Dict:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def _analyze_image_text_consistency_structured(
        self, figure_unit: Dict, client, vision_model_id: str
    ) -> Dict:
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def run_vision_review(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        parallel=None,
        max_workers=None,
    ):
        """
        【已移出】此方法已移至VisionAgent中
        """
        raise NotImplementedError("此方法已移至VisionAgent，请通过VisionAgent调用")

    def run_vision_review_parallel(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_workers=3,
    ):
        """
        【已废弃】并行版本已移除，请使用串行版本 run_vision_review()
        
        此方法保留仅为向后兼容，实际会抛出异常提示使用串行版本。
        """
        raise NotImplementedError(
            "并行模式已废弃。请使用串行模式 run_vision_review()，"
            "它使用6步结构化流程：Figure Unit构建 → Text Claims抽取 → "
            "Image Evidence分析 → Context Fitness分析 → Judge裁决 → 格式化输出"
        )

    def get_outline(self):
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
            response = self._call_llm(messages, max_tokens=4096, temperature=0.0)
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
                response = self._call_llm(messages, max_tokens=800, temperature=0.0)
                raw = response.choices[0].message.content
                data = self._parse_json(raw)
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

        # 1. 检测实体冲突（如"甲方"在不同章节有不同的值）- 已关闭

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

    def run_normative_logic_review(self):
        """
        Step 1: 构建Figure Unit（核心数据结构）
        
        Args:
            img_id: 图片ID
            image_info: 图片信息字典，包含 page_num, caption, context 等
            base64_img: 图片的base64编码
            media_type: 图片媒体类型
        
        Returns:
            FigureUnit 字典，如果构建失败返回 None
        """
        page_num = image_info.get('page_num')
        caption = image_info.get('caption', '')
        context = image_info.get('context', '')
        
        # 1. 查找所属章节
        section_info = self.doc_reader.find_section_by_page(page_num)
        if not section_info:
            print(f"[Figure Unit] ✗ 图片 {img_id}: 未找到所属章节 (页码: {page_num})")
            return None
        
        # 2. 提取章节全文（XML格式）
        section_elem = section_info.get('section_elem')
        if section_elem is not None:
            section_xml = ET.tostring(section_elem, encoding='unicode', method='xml')
        else:
            section_xml = ""
        
        # 3. 搜索引用文本
        reference_texts = self._extract_reference_texts(img_id, caption, section_info)
        
        # 4. 提取上下文（图片前后的段落）
        context_before, context_after = self._extract_context_around_image(page_num, section_info)
        
        figure_unit = {
            "figure_id": img_id,
            "chapter_id": section_info.get('section_id', ''),
            "chapter_title": section_info.get('title', ''),
            "caption": caption,
            "image": {
                "img_id": img_id,
                "base64_img": base64_img,
                "media_type": media_type,
                "page_num": page_num
            },
            "reference_texts": reference_texts,
            "local_context": section_xml,
            "context_before": context_before,
            "context_after": context_after
        }
        
        print(f"[Figure Unit] ✓ 图片 {img_id} 构建完成")
        print(f"  → 章节: {figure_unit['chapter_title']}")
        print(f"  → 引用文本数量: {len(reference_texts)}")
        
        return figure_unit
    
    def _extract_reference_texts(self, img_id: str, caption: str, section_info: Dict) -> List[str]:
        """提取正文中引用该图片的文本片段"""
        reference_texts = []
        section_elem = section_info.get('section_elem')
        if not section_elem:
            return reference_texts
        
        # 从caption中提取图片编号（如 "图4.2" -> "4.2"）
        figure_num_match = re.search(r'[图圖](\d+\.?\d*)', caption)
        if figure_num_match:
            figure_num = figure_num_match.group(1)
        else:
            # 尝试从img_id中提取
            figure_num_match = re.search(r'(\d+\.?\d*)', img_id)
            figure_num = figure_num_match.group(1) if figure_num_match else None
        
        if not figure_num:
            return reference_texts
        
        # 搜索引用模式
        patterns = [
            rf'[如如]图[圖圖]?\s*{re.escape(figure_num)}[所示]',
            rf'见图[圖圖]?\s*{re.escape(figure_num)}',
            rf'Figure\s+{re.escape(figure_num)}',
            rf'图[圖圖]?\s*{re.escape(figure_num)}[显示示]',
        ]
        
        # 在章节文本中搜索
        section_text = ''.join(section_elem.itertext())
        for pattern in patterns:
            matches = re.finditer(pattern, section_text, re.IGNORECASE)
            for match in matches:
                # 提取匹配位置前后的上下文（各50字符）
                start = max(0, match.start() - 50)
                end = min(len(section_text), match.end() + 50)
                context = section_text[start:end].strip()
                if context and context not in reference_texts:
                    reference_texts.append(context)
        
        return reference_texts
    
    def _extract_context_around_image(self, page_num: int, section_info: Dict) -> tuple:
        """提取图片前后的段落文本作为上下文"""
        context_before = ""
        context_after = ""
        
        section_elem = section_info.get('section_elem')
        if not section_elem:
            return context_before, context_after
        
        # 查找包含该页码的段落
        paragraphs = []
        for elem in section_elem.iter():
            if elem.tag == "Paragraph" and elem.text:
                elem_page = elem.get("page_num")
                if elem_page:
                    try:
                        elem_page_int = int(float(elem_page))
                        paragraphs.append((elem_page_int, elem.text))
                    except (ValueError, TypeError):
                        continue
        
        # 找到图片所在页码附近的段落
        try:
            page_int = int(float(page_num))
        except (ValueError, TypeError):
            return context_before, context_after
        
        # 提取图片前的段落（最多3段）
        before_paragraphs = [
            p[1] for p in paragraphs 
            if p[0] <= page_int and p[0] >= page_int - 1
        ][-3:]
        context_before = "\n".join(before_paragraphs)
        
        # 提取图片后的段落（最多3段）
        after_paragraphs = [
            p[1] for p in paragraphs 
            if p[0] > page_int and p[0] <= page_int + 1
        ][:3]
        context_after = "\n".join(after_paragraphs)
        
        return context_before, context_after
    
    def _extract_text_claims(self, figure_unit: Dict) -> List[Dict]:
        """
        Step 2: 抽取文本主张（Text Claim Agent）
        
        职责：从章节文本中抽取可被图像验证的结构化主张
        """
        # 构建输入
        chapter_text = figure_unit['local_context']
        reference_texts = figure_unit['reference_texts']
        caption = figure_unit['caption']
        
        # 如果章节文本过长，截断（保留前15000字符）
        if len(chapter_text) > 15000:
            chapter_text = chapter_text[:15000] + "\n[内容已截断...]"
        
        messages = [
            {"role": "system", "content": text_claim_prompt},
            {
                "role": "user",
                "content": f"""请从以下章节文本中抽取可被图像验证的结构化主张。

【章节标题】{figure_unit['chapter_title']}

【图片标题】{caption}

【章节内容】
{chapter_text}

【图片引用文本】
{chr(10).join(reference_texts) if reference_texts else "（未找到明确的图片引用）"}

请按照要求输出JSON格式的主张列表。"""
            }
        ]
        
        try:
            print(f"[Text Claim Agent] 正在抽取文本主张 (图片: {figure_unit['figure_id']})...")
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            claims_data = self._parse_json_from_response(raw_content)
            claims = claims_data.get("claims", [])
            
            print(f"[Text Claim Agent] ✓ 抽取到 {len(claims)} 个文本主张")
            for i, claim in enumerate(claims, 1):
                print(f"  → C{i}: {claim.get('type', 'unknown')} - {claim.get('assertion', '')[:50]}")
            
            return claims
            
        except Exception as e:
            print(f"[Text Claim Agent] ✗ 抽取失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _analyze_image_evidence(self, client, vision_model_id: str, figure_unit: Dict) -> Optional[Dict]:
        """
        Step 3: 分析图像证据能力（Image Evidence Agent）
        
        职责：分析图片"客观上"能支持哪些类型的事实
        
        这是原来 _extract_vision_description 的重构版本
        """
        img_data = figure_unit['image']
        caption = figure_unit['caption']
        
        messages = [
            {"role": "system", "content": image_evidence_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"请分析以下图片的证据能力。\n\n图片标题：{caption}\n\n请按照要求输出JSON格式的证据能力描述。"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img_data['media_type']};base64,{img_data['base64_img']}"
                        }
                    }
                ]
            }
        ]
        
        try:
            print(f"[Image Evidence Agent] 正在分析图像证据能力 (图片: {figure_unit['figure_id']})...")
            response = client.chat.completions.create(
                model=vision_model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            evidence_data = self._parse_json_from_response(raw_content)
            
            # 完整打印视觉大模型生成的evidence内容
            print(f"\n{'='*80}")
            print(f"[Image Evidence Agent] 证据能力完整输出 (图片 {figure_unit['figure_id']})")
            print(f"{'='*80}")
            print(json.dumps(evidence_data, ensure_ascii=False, indent=2))
            print(f"{'='*80}\n")
            
            print(f"[Image Evidence Agent] ✓ 证据能力分析完成")
            print(f"  → 图片类型: {evidence_data.get('image_type', 'unknown')}")
            capabilities = evidence_data.get('evidence_capabilities', {})
            enabled = [k for k, v in capabilities.items() if v]
            print(f"  → 支持的证据类型: {', '.join(enabled) if enabled else '无'}")
            
            return evidence_data
            
        except Exception as e:
            print(f"[Image Evidence Agent] ✗ 分析失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _analyze_context_fitness(self, figure_unit: Dict, image_evidence: Dict) -> Dict:
        """
        Step 4: 分析章节-图像适配性（Context Agent）
        
        职责：判断图片在该章节中的适配性
        """
        chapter_title = figure_unit['chapter_title']
        chapter_context = figure_unit['local_context']
        image_type = image_evidence.get('image_type', 'unknown')
        
        # 如果章节文本过长，截断
        if len(chapter_context) > 10000:
            chapter_context = chapter_context[:10000] + "\n[内容已截断...]"
        
        messages = [
            {"role": "system", "content": context_fitness_prompt},
            {
                "role": "user",
                "content": f"""请分析以下图片在该章节中的适配性。

【章节标题】{chapter_title}

【章节内容摘要】
{chapter_context[:5000]}

【图片类型】{image_type}

请按照要求输出JSON格式的适配性分析结果。"""
            }
        ]
        
        try:
            print(f"[Context Agent] 正在分析章节适配性 (图片: {figure_unit['figure_id']})...")
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            fitness_data = self._parse_json_from_response(raw_content)
            
            print(f"[Context Agent] ✓ 适配性分析完成")
            print(f"  → 适配性: {fitness_data.get('fitness', 'unknown')}")
            print(f"  → 图片角色: {fitness_data.get('figure_role', 'unknown')}")
            
            return fitness_data
            
        except Exception as e:
            print(f"[Context Agent] ✗ 分析失败: {e}")
            # 返回默认值
            return {
                "chapter_intent": "unknown",
                "figure_role": "unknown",
                "fitness": "medium",
                "reason": f"分析失败: {str(e)}"
            }
    
    def _judge_consistency(
        self,
        text_claims: List[Dict],
        image_evidence: Dict,
        context_fitness: Dict,
        figure_unit: Dict
    ) -> Dict:
        """
        Step 5: 裁决（Judge Agent）
        
        职责：基于所有结构化信息，做出最终判断
        """
        # 构建输入
        claims_json = json.dumps(text_claims, ensure_ascii=False, indent=2)
        evidence_json = json.dumps(image_evidence, ensure_ascii=False, indent=2)
        fitness_json = json.dumps(context_fitness, ensure_ascii=False, indent=2)
        
        messages = [
            {"role": "system", "content": judge_prompt},
            {
                "role": "user",
                "content": f"""请基于以下结构化信息，对图片 {figure_unit['figure_id']} 进行图文一致性裁决。

【文本主张列表】
{claims_json}

【图像证据能力】
{evidence_json}

【章节适配性】
{fitness_json}

【图片信息】
- 图片ID: {figure_unit['figure_id']}
- 图片标题: {figure_unit['caption']}
- 章节: {figure_unit['chapter_title']}
- 引用文本数量: {len(figure_unit['reference_texts'])}

请按照要求输出JSON格式的裁决结果。"""
            }
        ]
        
        try:
            print(f"[Judge Agent] 正在进行最终裁决 (图片: {figure_unit['figure_id']})...")
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            verdict = self._parse_json_from_response(raw_content)
            
            print(f"[Judge Agent] ✓ 裁决完成")
            print(f"  → 裁决结果: {verdict.get('verdict', 'unknown')}")
            print(f"  → 支持的主张: {len(verdict.get('supported_claims', []))}")
            print(f"  → 不支持的主张: {len(verdict.get('unsupported_claims', []))}")
            print(f"  → 发现问题数: {len(verdict.get('issues', []))}")
            
            return verdict
            
        except Exception as e:
            print(f"[Judge Agent] ✗ 裁决失败: {e}")
            import traceback
            traceback.print_exc()
            # 返回默认值
            return {
                "figure_id": figure_unit['figure_id'],
                "verdict": "unknown",
                "supported_claims": [],
                "unsupported_claims": [c.get('claim_id', '') for c in text_claims],
                "placement_fitness": context_fitness.get('fitness', 'medium'),
                "issues": [
                    {
                        "type": "analysis_error",
                        "severity": "Medium",
                        "description": f"裁决过程出错: {str(e)}",
                        "suggestion": "请检查输入数据或重新分析"
                    }
                ]
            }
    
    def _parse_json_from_response(self, raw_content: str) -> Dict:
        """从LLM响应中解析JSON（简化版，复用_parse_json的核心逻辑）"""
        # 复用_parse_json的逻辑，但返回空字典而不是{"issues": []}
        result = self._parse_json(raw_content)
        # 如果_parse_json返回的是{"issues": []}格式，尝试提取实际内容
        if result == {"issues": []} and raw_content:
            # 快速尝试：直接查找JSON对象
            start = raw_content.find('{')
            end = raw_content.rfind('}')
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw_content[start:end+1])
                except json.JSONDecodeError:
                    pass
        return result if result else {}
    
    # ==================== 保留原有方法以保持兼容性 ====================
    
    def _extract_vision_description(
        self, client, vision_model_id, img_id, base64_img, media_type, caption
    ):
        """
        使用视觉大模型（Vision Model）提取图片的结构化描述。
        
        这是图文审查流程的第一步：视觉大模型负责"看懂"图片内容，将其转换为结构化的文本描述。
        这个描述将作为"memory"传递给文本大模型进行深度分析。
        
        【视觉大模型的作用】
        - 输入：图片（base64编码）+ 图片标题（Caption）
        - 输出：结构化的JSON描述，包含：
          * image_type: 图片类型（流程图、数据图、示意图等）
          * main_elements: 主要元素列表
          * key_information: 关键信息描述
          * text_content: 图片中的文字内容
          * colors_and_visual: 颜色和视觉特征
          * initial_assessment: 初步评估（caption_match, confidence, reason）
        
        【Memory传递机制】
        - 返回的 vision_description (dict) 将作为"memory"传递给文本大模型
        - 文本大模型基于这个描述和章节内容进行深度图文一致性分析
        - 这种设计避免了文本大模型直接处理图片，提高了效率和准确性

        Args:
            client: OpenAI客户端（用于调用视觉模型API，如Qwen-VL）
            vision_model_id: 视觉模型ID（如 "qwen3-vl-flash"）
            img_id: 图片ID（用于日志和错误追踪）
            base64_img: 图片的base64编码（直接传递给视觉模型）
            media_type: 图片媒体类型（如 "image/png", "image/jpeg"）
            caption: 图片标题（帮助视觉模型理解图片的上下文）

        Returns:
            dict: 包含结构化描述的字典，格式如下：
                {
                    "image_type": "流程图",
                    "main_elements": ["元素1", "元素2"],
                    "key_information": "关键信息描述",
                    "text_content": "图片中的文字",
                    "colors_and_visual": "视觉特征",
                    "initial_assessment": {
                        "caption_match": true,
                        "confidence": 0.8,
                        "reason": "判断理由"
                    }
                }
            如果提取失败则返回 None
        """
        messages = [
            {"role": "system", "content": vision_description_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"请提取以下图片的结构化描述。\n\n图片标题：{caption}\n\n请按照要求输出JSON格式的结构化描述。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{base64_img}"},
                    },
                ],
            },
        ]

        try:
            response = client.chat.completions.create(
                model=vision_model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()

            # 解析JSON输出
            # 方法1: 尝试从markdown代码块中提取
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL)
            if json_match:
                try:
                    raw_content = json_match.group(1)
                except IndexError:
                    pass  # 如果没有捕获组，继续尝试其他方法
            
            # 方法2: 尝试找到第一个 { 到最后一个 } 之间的内容（最可靠的方法）
            start = raw_content.find('{')
            end = raw_content.rfind('}')
            if start != -1 and end != -1 and end > start:
                raw_content = raw_content[start:end+1]
            else:
                # 方法3: 尝试使用正则表达式匹配JSON对象（处理嵌套）
                json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw_content, re.DOTALL)
                if json_match:
                    raw_content = json_match.group(0)

            # 尝试解析JSON
            try:
                description = json.loads(raw_content)
            except json.JSONDecodeError as json_err:
                # 如果解析失败，尝试清理内容后再次解析
                # 移除可能的注释和多余空白
                cleaned = re.sub(r'//.*?$', '', raw_content, flags=re.MULTILINE)
                cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
                try:
                    description = json.loads(cleaned)
                except json.JSONDecodeError:
                    raise ValueError(f"Failed to parse JSON: {json_err}. Raw content: {raw_content[:200]}")
            
            # 完整打印视觉大模型生成的memory内容
            print(f"\n{'='*80}")
            print(f"[视觉大模型] Memory内容完整输出 (图片 {img_id})")
            print(f"{'='*80}")
            print(json.dumps(description, ensure_ascii=False, indent=2))
            print(f"{'='*80}\n")
            
            return description
        except Exception as e:
            print(f"[Vision Description] Failed to extract description for image {img_id}: {e}")
            return None

    def _analyze_image_text_consistency_structured(
        self, figure_unit: Dict, client, vision_model_id: str
    ) -> Dict:
        """
        结构化图文一致性分析（新方法）
        
        使用6步结构化流程：
        1. Figure Unit 构建（已完成，作为输入）
        2. Text Claim Agent（文本主张抽取）
        3. Image Evidence Agent（图像证据能力建模）
        4. Context Agent（章节-图像适配性分析）
        5. Judge Agent（裁决）
        6. 格式化输出
        
        Args:
            figure_unit: Figure Unit字典
            client: 视觉模型客户端
            vision_model_id: 视觉模型ID
        
        Returns:
            包含分析结果的字典
        """
        print(f"\n{'='*80}")
        print(f"[结构化审查] 开始处理图片 {figure_unit['figure_id']}")
        print(f"{'='*80}\n")
        
        # Step 2: 抽取文本主张
        text_claims = self._extract_text_claims(figure_unit)
        
        # Step 3: 分析图像证据能力
        image_evidence = self._analyze_image_evidence(client, vision_model_id, figure_unit)
        if not image_evidence:
            return {
                "img_id": figure_unit['figure_id'],
                "error": "Failed to analyze image evidence",
                "parsed": {"issues": []},
                "thinking": "图像证据能力分析失败"
            }
        
        # Step 4: 分析章节适配性
        context_fitness = self._analyze_context_fitness(figure_unit, image_evidence)
        
        # Step 5: 裁决
        judge_verdict = self._judge_consistency(
            text_claims, image_evidence, context_fitness, figure_unit
        )
        
        # Step 6: 格式化输出（转换为现有格式）
        issues = []
        for issue in judge_verdict.get('issues', []):
            issues.append({
                "issue_type": "图文一致性",
                "severity": issue.get('severity', 'Medium'),
                "section": figure_unit.get('chapter_title', ''),
                "page": figure_unit['image'].get('page_num'),
                "image_id": figure_unit['figure_id'],
                "caption": figure_unit.get('caption', ''),  # 添加图表名称
                "quote": issue.get('description', ''),
                "suggestion": issue.get('suggestion', '')
            })
        
        # 构建thinking（包含所有中间结果）
        thinking_parts = [
            "### 📊 结构化分析流程",
            "",
            "#### Step 1: Figure Unit",
            f"- 图片ID: {figure_unit['figure_id']}",
            f"- 章节: {figure_unit['chapter_title']}",
            f"- 引用文本数量: {len(figure_unit['reference_texts'])}",
            "",
            "#### Step 2: Text Claims",
            json.dumps(text_claims, ensure_ascii=False, indent=2),
            "",
            "#### Step 3: Image Evidence",
            json.dumps(image_evidence, ensure_ascii=False, indent=2),
            "",
            "#### Step 4: Context Fitness",
            json.dumps(context_fitness, ensure_ascii=False, indent=2),
            "",
            "#### Step 5: Judge Verdict",
            json.dumps(judge_verdict, ensure_ascii=False, indent=2),
        ]
        
        thinking = "\n".join(thinking_parts)
        
        print(f"\n{'='*80}")
        print(f"[结构化审查] ✓ 图片 {figure_unit['figure_id']} 处理完成")
        print(f"{'='*80}\n")
        
        return {
            "thinking": thinking,
            "parsed": {"issues": issues},
            "raw": json.dumps(judge_verdict, ensure_ascii=False),
            # 保留中间结果用于调试
            "figure_unit": figure_unit,
            "text_claims": text_claims,
            "image_evidence": image_evidence,
            "context_fitness": context_fitness,
            "judge_verdict": judge_verdict
        }
    
    def _analyze_image_text_consistency(
        self, img_id, vision_description, section_info, caption, context
    ):
        """
        【已废弃】使用旧方法进行图文一致性分析
        
        保留此方法以保持向后兼容，但内部会尝试使用新的结构化方法
        """
        # 这个方法现在主要用于向后兼容
        # 新的流程应该使用 _analyze_image_text_consistency_structured
        return {"thinking": "使用旧方法（已废弃）", "parsed": {"issues": []}}

    def run_vision_review(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        parallel=None,  # 已废弃，保留参数以保持兼容性
        max_workers=None,  # 已废弃，保留参数以保持兼容性
    ):
        """
        图文一致性审查（结构化串行版本）：
        使用6步结构化流程：
        1. Figure Unit构建
        2. Text Claims抽取
        3. Image Evidence分析
        4. Context Fitness分析
        5. Judge裁决
        6. 格式化输出

        输出语言：中文

        Args:
            vision_model_id: 视觉模型ID
            max_images: 最大处理图片数
            vision_api_key: 视觉模型API密钥
            vision_base_url: 视觉模型API Base URL
            parallel: 已废弃，不再支持并行模式
            max_workers: 已废弃，不再支持并行模式
        """

        results = []

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
                            if self._is_header_footer(
                                text, node.get("page_num") or page_num
                            ):
                                continue
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
                            heading_text = node.text.strip()
                            if self._is_header_footer(
                                heading_text, node.get("page_num") or page_num
                            ):
                                continue
                            context_text.append(f"[Heading: {heading_text}]")
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
            f"[Agent] 发现 {total_images} 张图片，将审查前 {process_limit} 张（结构化串行模式）"
        )
        print(f"  → 处理流程: Step 1 (Figure Unit构建) → Step 2 (Text Claims抽取) → Step 3 (Image Evidence分析) → Step 4 (Context Fitness分析) → Step 5 (Judge裁决) → Step 6 (格式化输出)")

        for img_id, filename in self.doc_reader.image_path_dict.items():
            if count >= max_images:
                break

            media_type, base64_img, error = self.doc_reader.get_image(img_id)
            if error:
                print(f"[Error] Failed to load image {img_id}: {error}")
                continue

            meta = image_info_map.get(
                img_id, {"page_num": "?", "caption": "Unknown", "context": ""}
            )

            print(
                f"[Agent] 分析图片 {img_id} (第 {meta['page_num']} 页): {meta['caption'][:30]}..."
            )

            try:
                # Step 1: 构建Figure Unit
                print(f"[Step 1/6] [Figure Unit] 构建图片 {img_id} 的分析单元...")
                figure_unit = self._build_figure_unit(
                    img_id=img_id,
                    image_info=meta,
                    base64_img=base64_img,
                    media_type=media_type
                )
                
                if not figure_unit:
                    print(f"[Error] 无法构建Figure Unit，跳过图片 {img_id}")
                    results.append({
                        "image_id": img_id,
                        "page": meta["page_num"],
                        "caption": meta["caption"],
                        "error": "Failed to build figure unit",
                    })
                    count += 1
                    continue
                
                # Step 2-6: 执行结构化分析流程
                text_analysis = self._analyze_image_text_consistency_structured(
                    figure_unit=figure_unit,
                    client=client,
                    vision_model_id=vision_model_id
                )
                
                # 格式化输出（兼容现有格式）
                # 确保text_analysis中的issues也包含caption信息
                text_issues = text_analysis.get("parsed", {}).get("issues", [])
                for issue in text_issues:
                    if isinstance(issue, dict):
                        if not issue.get("caption"):
                            issue["caption"] = meta["caption"]
                
                results.append({
                    "image_id": img_id,
                    "page": meta["page_num"],
                    "caption": meta["caption"],
                    "raw": text_analysis.get("raw", ""),
                    "thinking": text_analysis.get("thinking", ""),
                    "text_analysis": text_analysis.get("parsed", {"issues": text_issues}),
                    "section": figure_unit.get("chapter_title", ""),
                    # 保留中间结果用于调试
                    "figure_unit": figure_unit,
                    "text_claims": text_analysis.get("text_claims", []),
                    "image_evidence": text_analysis.get("image_evidence", {}),
                    "judge_verdict": text_analysis.get("judge_verdict", {}),
                })
                
                count += 1

            except Exception as e:
                print(f"[Error] Analysis failed for image {img_id}: {e}")
                import traceback
                traceback.print_exc()
                results.append({"image_id": img_id, "error": str(e)})

        # 返回结果
        print(f"\n[Agent] ✅ 完成 {count} 张图片的结构化分析")
        print(f"  → 所有图片均使用6步结构化流程：Figure Unit → Text Claims → Image Evidence → Context Fitness → Judge → 格式化输出")
        print(f"  → 发现问题: 共 {sum(len(r.get('text_analysis', {}).get('issues', [])) for r in results if r.get('text_analysis'))} 个图文一致性问题")
        return results

    def run_vision_review_parallel(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_workers=3,
    ):
        """
        【已废弃】并行版本已移除，请使用串行版本 run_vision_review()
        
        此方法保留仅为向后兼容，实际会抛出异常提示使用串行版本。
        """
        raise NotImplementedError(
            "并行模式已废弃。请使用串行模式 run_vision_review()，"
            "它使用6步结构化流程：Figure Unit构建 → Text Claims抽取 → "
            "Image Evidence分析 → Context Fitness分析 → Judge裁决 → 格式化输出"
        )

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
            response = self._call_llm(messages, max_tokens=800, temperature=0.0)
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
            response = self._call_llm(
                messages, 
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=tools,
                tool_choice="auto"
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
                response = self._call_llm(
                    messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    tools=tools,
                    tool_choice=tool_choice
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
            # 注意：文本模型通过DASHSCOPE API调用，支持deepseek-v3.2等模型
            if "deepseek" in self.model_id.lower() or "qwen" in self.model_id.lower():
                # 文本模型不支持图片输入，跳过图片并提供占位符
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
