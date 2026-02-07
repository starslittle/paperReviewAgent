import copy
import json
import re
import time
import traceback
import xml.dom.minidom
import xml.etree.ElementTree as ET
from typing import Optional, Union

from openai import OpenAI

from .prompts import (
    actor_prompt_template,
    available_tools,
    chapter_selection_prompt,
    reflection_prompt_template,
    reviewer_prompt,
    system_prompt,
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
        # 页眉页脚特征：含“第X页/页码/学院/…”或排版页码如 “- 28 -”
        self._header_footer_regex = re.compile(
            r"(第\s*\d+\s*页|页码|学院|专业|指导教师|学校|论文|本科|毕业|\d{4}\s*年|^\s*-\s*\d+\s*-\s*$)",
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
            if elem.tag not in [
                "Paragraph",
                "Heading",
                "Title",
                "Caption",
                "Header",
                "Footer",
            ]:
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

    def _get_abstract_start_section_index(self):
        """
        返回第一个标题为「摘要/Abstract」的顶层 Section 的索引（从 0 计）。
        用于与 LogicAgent、VisionAgent 对齐：三个 Agent 均从「摘要」开始审查。
        若未找到则返回 None（表示不跳过任何内容）。
        """
        for idx, child in enumerate(self.doc_reader.root):
            if child.tag != "Section":
                continue
            title_text = ""
            for node in child:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text.strip()
                    break
            if title_text and re.search(
                r"(摘要|abstract|摘\s*要)", title_text, re.IGNORECASE
            ):
                return idx
        return None

    def _extract_plain_text(self, char_limit=6000):
        """Extract plain text segments for lightweight review."""
        texts = []
        for elem in self.doc_reader.root.iter():
            if elem.text and elem.tag in [
                "Paragraph",
                "Title",
                "Caption",
                "Header",
                "Footer",
            ]:
                t = elem.text.strip()
                if t and not self._is_header_footer(t, elem.get("page_num")):
                    texts.append(t)
            if sum(len(x) for x in texts) > char_limit:
                break
        combined = "\n".join(texts)
        return combined[:char_limit]

    def _extract_plain_text_from_abstract(self, char_limit=6000, char_offset=0):
        """
        仅从「摘要」及之后的章节抽取正文（与 LogicAgent/VisionAgent 起点对齐）。

        Args:
            char_limit: 窗口大小（字符数）
            char_offset: 起始偏移量（跳过前 N 个字符）

        Returns:
            tuple: (text_snippet, actual_start, actual_end, is_end_of_doc)
                - text_snippet: 抽取的正文片段
                - actual_start: 实际开始位置（字符数）
                - actual_end: 实际结束位置（字符数）
                - is_end_of_doc: 是否已到达文档末尾
        """
        abstract_idx = self._get_abstract_start_section_index()
        all_texts = []  # 先收集全部文本

        for idx, child in enumerate(self.doc_reader.root):
            if child.tag != "Section":
                continue
            if abstract_idx is not None and idx < abstract_idx:
                continue
            for elem in child.iter():
                if elem.text and elem.tag in [
                    "Paragraph",
                    "Title",
                    "Caption",
                    "Header",
                    "Footer",
                ]:
                    t = elem.text.strip()
                    if t and not self._is_header_footer(t, elem.get("page_num")):
                        all_texts.append(t)

        combined = "\n".join(all_texts)
        total_len = len(combined)

        # 处理偏移量和窗口
        actual_start = min(char_offset, total_len)
        actual_end = min(char_offset + char_limit, total_len)
        is_end_of_doc = actual_end >= total_len

        text_snippet = combined[actual_start:actual_end]

        return text_snippet, actual_start, actual_end, is_end_of_doc

    def _call_llm(self, messages, max_tokens=None, temperature=None, **kwargs):
        """公共方法：调用LLM API"""
        return self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
            **kwargs,
        )

    def _extract_thinking(self, raw_content):
        """从响应中提取thinking部分"""
        thinking_match = re.search(
            r"<thinking>(.*?)</thinking>", raw_content, re.DOTALL
        )
        return thinking_match.group(1).strip() if thinking_match else ""

    def _run_simple_review(
        self, prompt_template, from_abstract=False, char_offset=0, char_limit=6000
    ):
        """
        from_abstract: 若为 True，正文片段仅从「摘要」及之后抽取，与 Logic/Vision Agent 起点对齐。
        char_offset: 正文片段的起始偏移量（用于滑动窗口）
        char_limit: 正文片段的窗口大小

        Returns:
            dict: {
                "raw": LLM 返回的原始 JSON 字符串,
                "thinking": 提取的思考过程,
                "window_info": {"start": int, "end": int, "is_end": bool}  # 窗口信息
            }
        """
        outline_xml = self.get_outline()

        if from_abstract:
            body_text, actual_start, actual_end, is_end = (
                self._extract_plain_text_from_abstract(
                    char_limit=char_limit, char_offset=char_offset
                )
            )
            window_info = {"start": actual_start, "end": actual_end, "is_end": is_end}
        else:
            body_text = self._extract_plain_text(char_limit=char_limit)
            window_info = {"start": 0, "end": len(body_text), "is_end": True}

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
            return {
                "raw": raw_content,
                "thinking": self._extract_thinking(raw_content),
                "window_info": window_info,
            }
        except Exception as e:
            print(traceback.format_exc())
            return {
                "raw": "",
                "thinking": "",
                "error": str(e),
                "window_info": window_info,
            }

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
            if cleaned and cleaned[-1] not in "}]":
                print("[Warning] JSON appears to be truncated, attempting to fix...")

                # 尝试闭合未完成的字符串
                if cleaned.count('"') % 2 != 0:
                    # 奇数个引号，说明有未闭合的字符串
                    cleaned += '"'

                # 尝试闭合数组和对象
                # 计算需要闭合的括号
                open_braces = cleaned.count("{") - cleaned.count("}")
                open_brackets = cleaned.count("[") - cleaned.count("]")

                # 按相反顺序添加闭合括号
                for _ in range(open_brackets):
                    cleaned += "]"
                for _ in range(open_braces):
                    cleaned += "}"

                print(
                    f"[Fix] Attempted to close {open_brackets} brackets and {open_braces} braces"
                )

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
        in_string = False
        escape_next = False
        current_string = []

        for char in json_str:
            if escape_next:
                current_string.append(char)
                escape_next = False
                continue

            if char == "\\":
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
            elif char in "\n\r\t" and in_string:
                # 在字符串内，将换行符和制表符替换为空格
                current_string.append(" ")
            elif char == "\n" and not in_string:
                # 在 JSON 结构中，直接保留（会被 JSON 解析器忽略）
                current_string.append(char)
            else:
                current_string.append(char)

        return "".join(current_string)

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

    def select_top_sections(self, max_sections=8, skip_front_matter=True):
        """
        Use LLM to select top-level (or important) sections from Outline XML.
        Returns a list of section_id strings.

        Args:
            max_sections: Maximum number of sections to select
            skip_front_matter: Whether to skip front matter (cover, commitment, etc.)
        """
        outline_xml = self.get_outline()
        messages = [
            {"role": "system", "content": chapter_selection_prompt},
            {
                "role": "user",
                "content": f'以下是论文大纲（XML）。请只输出最重要的大章节 section_id 列表（最多 {max_sections} 个），跳过封面、诚信承诺等非学术内容，但必须包含目录（非常重要的环节）。从摘要开始选择，用 JSON 数组表示，如 ["5", "7", "8", "9"]（摘要+目录+正文）.\n\n{outline_xml}',
            },
        ]
        try:
            response = self._call_llm(messages, max_tokens=500, temperature=0.0)
            raw = response.choices[0].message.content
            data = self._parse_json(raw)
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
                # 首先找到目录章节（通常包含"目 录"或"目录"）
                toc_section = None
                for child in self.doc_reader.root:
                    if child.tag == "Section":
                        section_id = child.get("section_id")
                        try:
                            sec_root = self.doc_reader.get_section_content(section_id)
                            title_text = f"Section {section_id}"
                            for node in sec_root:
                                if node.tag in ["Heading", "Title"] and node.text:
                                    title_text = node.text
                                    break
                            if any(
                                keyword in title_text for keyword in ["目 录", "目录"]
                            ):
                                toc_section = section_id
                                break
                        except Exception:
                            continue

                # 过滤掉典型的非学术章节（根据标题特征）
                filtered_sections = []
                for sid in selected_sections:
                    try:
                        sec_root = self.doc_reader.get_section_content(sid)
                        title_text = f"Section {sid}"
                        for node in sec_root:
                            if node.tag in ["Heading", "Title"] and node.text:
                                title_text = node.text
                                break

                        # 跳过封面、诚信承诺等非学术内容，但保留摘要、目录等重要内容
                        skip_keywords = [
                            "封面",
                            "诚信",
                            "承诺",
                            "签名",
                            "杭州電子科技大学",
                        ]
                        keep_keywords = ["目 录", "目录", "摘要", "abstract", "摘 要"]
                        if not any(
                            keyword in title_text for keyword in skip_keywords
                        ) or any(keyword in title_text for keyword in keep_keywords):
                            filtered_sections.append(sid)
                    except Exception:
                        continue

                # 确保目录被包含（如果找到了目录章节）
                if toc_section and toc_section not in filtered_sections:
                    filtered_sections.insert(0, toc_section)  # 插入到开头

                return filtered_sections[:max_sections]

            return selected_sections

        except Exception as e:
            print(f"[SectionSelect] failed: {e}")

        # fallback: take top-level section ids from doc_reader, skip front matter but keep important sections
        top_ids = []
        toc_section = None

        # 首先找到目录章节
        for child in self.doc_reader.root:
            if child.tag == "Section":
                section_id = child.get("section_id")
                try:
                    title_text = f"Section {section_id}"
                    for node in child:
                        if node.tag in ["Heading", "Title"] and node.text:
                            title_text = node.text
                            break
                    if any(keyword in title_text for keyword in ["目 录", "目录"]):
                        toc_section = section_id
                        break
                except Exception:
                    continue

        for child in self.doc_reader.root:
            if child.tag == "Section":
                section_id = child.get("section_id")
                if skip_front_matter:
                    # 基于section_id和内容跳过前面的非学术部分，但保留摘要和目录
                    try:
                        sid_int = int(section_id)
                        if sid_int <= 4:  # 跳过前4个section（封面相关）
                            # 但要检查是否是摘要、目录等重要内容
                            title_text = f"Section {section_id}"
                            for node in child:
                                if node.tag in ["Heading", "Title"] and node.text:
                                    title_text = node.text
                                    break
                            keep_keywords = [
                                "目 录",
                                "目录",
                                "摘要",
                                "abstract",
                                "摘 要",
                            ]
                            if not any(
                                keyword in title_text for keyword in keep_keywords
                            ):
                                continue
                    except (ValueError, TypeError):
                        pass
                top_ids.append(section_id)
            if len(top_ids) >= max_sections:
                break

        # 确保目录被包含在结果中
        if toc_section and toc_section not in top_ids:
            top_ids.insert(0, toc_section)  # 插入到开头

        return top_ids[:max_sections]

    def run_normative_review(self):
        """规范性审查代理包装（逻辑由 NormativeAgent 负责）。"""
        from .normative_agent import NormativeAgent

        return NormativeAgent(self).run_normative_review()

    def run_logic_review(self):
        """逻辑审查代理包装（逻辑由 LogicAgent 负责）。"""
        from .logic_agent import LogicAgent

        return LogicAgent(self).run_logic_review()

    def run_hierarchical_logic_review(self):
        """层次化逻辑审查代理包装（逻辑由 LogicAgent 负责）。"""
        from .logic_agent import LogicAgent

        return LogicAgent(self).run_hierarchical_logic_review()

    def run_vision_review(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        include_page_image: bool = True,
        parallel=None,
        max_workers=None,
    ):
        """图文一致性审查代理包装（逻辑由 VisionAgent 负责）。"""
        from .vision_agent import VisionAgent

        return VisionAgent(self).run_vision_review(
            vision_model_id=vision_model_id,
            max_images=max_images,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            include_page_image=include_page_image,
            parallel=parallel,
            max_workers=max_workers,
        )

    def run_vision_review_parallel(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_workers=3,
    ):
        """并行图文审查代理包装（逻辑由 VisionAgent 负责）。"""
        from .vision_agent import VisionAgent

        return VisionAgent(self).run_vision_review_parallel(
            vision_model_id=vision_model_id,
            max_images=max_images,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            max_workers=max_workers,
        )

    # NOTE: run_normative_logic_review() has been removed
    # This method is deprecated as normative and logic reviews have been separated
    # into NormativeAgent and LogicAgent respectively.
    # Use run_normative_review() and run_logic_review() separately instead.

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
                response = self._call_llm(
                    messages,
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
