from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .modification_advisor import (
    build_llm_instruction,
    build_suggestion_prompt,
    decide_modification_target,
)
from .prompts import argument_role_prompt, image_capacity_prompt, local_stage_alignment_prompt
from .stage_resolver import (
    StageResolver,
    is_metric_chart,
    is_method_diagram,
    normalize_image_type,
)


@dataclass
class TextReference:
    sentence: str
    figure_id: str


@dataclass
class FigureNode:
    figure_id: str
    page_num: Optional[int]
    caption: str
    section_id: str
    section_title: str
    section_elem: Optional[ET.Element] = None
    references: List[TextReference] = field(default_factory=list)
    image_scope_text: str = ""
    scope_confidence: float = 0.0
    decision_trace: List[str] = field(default_factory=list)


@dataclass
class RoleAssignment:
    role: str
    confidence: float
    evidence_sentence: str
    raw: Optional[Dict[str, Any]] = None


@dataclass
class StageAlignment:
    expected_stage: Optional[str]
    actual_stage: str
    status: str
    severity: Optional[str]
    message: str
    stage_mismatch: bool = False
    mismatch_severity: Optional[str] = None
    vote_breakdown: Optional[Dict[str, Any]] = None
    image_type: Optional[str] = None


@dataclass
class ImageCapacity:
    role: str
    sufficient: bool
    reason: str
    raw: Optional[Dict[str, Any]] = None


@dataclass
class FigureReviewResult:
    figure_id: str
    page: Optional[int]
    caption: str
    section: str
    media_kind: str
    role_assignment: RoleAssignment
    stage_alignment: StageAlignment
    image_capacity: ImageCapacity
    issues: List[Dict[str, Any]] = field(default_factory=list)
    image_scope_text: str = ""
    scope_confidence: float = 0.0
    consistency_result: str = "CONSISTENT"
    modification_target: str = ""
    modification_suggestion: str = ""
    decision_trace: List[str] = field(default_factory=list)
    raw_debug: Dict[str, Any] = field(default_factory=dict)


class VisionAgent:
    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent
        self.doc_reader = doc_agent.doc_reader
        self.client = doc_agent.client
        self.model_id = doc_agent.model_id
        self.stage_resolver = StageResolver()

    def _load_media_image(self, media_kind: str, media_id: str):
        if media_kind == "table":
            return self.doc_reader.get_table_image(media_id)
        return self.doc_reader.get_image(media_id)

    def _infer_image_type(
        self,
        figure_node: FigureNode,
        role_assignment: RoleAssignment,
        image_capacity: Optional[ImageCapacity],
    ) -> Optional[str]:
        raw_type = None
        if image_capacity and isinstance(image_capacity.raw, dict):
            raw_type = image_capacity.raw.get("image_type") or image_capacity.raw.get("type")
        context_text = " ".join(
            [
                figure_node.caption or "",
                figure_node.section_title or "",
                role_assignment.evidence_sentence or "",
            ]
        )
        return normalize_image_type(raw_type, context_text)

    def _is_explanatory_reference(self, figure_node: FigureNode, role_assignment: RoleAssignment) -> bool:
        sentence = self._select_reference_sentence(figure_node, role_assignment)
        if not sentence:
            return False
        pattern = r"(如图|如\s*fig|如\s*figure|图\s*\d+|figure\s*\d+).*?(显示|表明|说明|可见|展示)"
        return re.search(pattern, sentence, re.IGNORECASE) is not None

    def _has_generalization_language(self, text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        keywords = [
            "鲁棒",
            "泛化",
            "泛化能力",
            "稳定性强",
            "效果好",
            "表现更佳",
            "显著提升",
            "明显优于",
            "更优",
            "更好",
            "优势明显",
            "robust",
            "generalization",
            "generalisation",
            "outperforms",
            "significantly improves",
            "superior",
            "state-of-the-art",
            "sota",
        ]
        return any(k in lowered for k in keywords)

    def _build_generalization_issue(
        self,
        figure_node: FigureNode,
        role_assignment: RoleAssignment,
        image_type: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not is_metric_chart(image_type):
            return None
        if role_assignment.role not in ["RESULT_CLAIM", "COMPARISON_CLAIM"]:
            return None
        reference_sentence = self._select_reference_sentence(figure_node, role_assignment)
        if not self._has_generalization_language(reference_sentence):
            return None
        return {
            "issue_type": "EVIDENCE_GENERALIZATION",
            "severity": "Low",
            "section": figure_node.section_title,
            "page": figure_node.page_num,
            "image_id": figure_node.figure_id,
            "quote": reference_sentence,
            "suggestion": (
                "建议限定结论表述范围，使其仅基于图中展示的指标或区间，"
                "避免将局部指标趋势泛化为整体鲁棒性或全面优势。"
            ),
        }

    def run(
        self,
        vision_model_id: str,
        vision_api_key: Optional[str],
        vision_base_url: Optional[str],
        include_page_image: bool = True,
        parallel: bool = True,
        max_workers: int = 3,
    ) -> Dict[str, Any]:
        res_list = self.run_vision_review(
            vision_model_id=vision_model_id,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            parallel=None,
            max_workers=None,
        )

        issues: List[Dict[str, Any]] = []
        for res in res_list:
            if isinstance(res, dict):
                issues.extend([iss for iss in res.get("issues", []) if isinstance(iss, dict)])

        return {
            "raw": res_list,
            "parsed": {"issues": issues},
            "errors": [],
        }

    def _build_figure_node(self, img_id: str, image_info: Dict) -> Optional[FigureNode]:
        page_num = image_info.get("page_num")
        caption = image_info.get("caption", "")

        section_info = image_info.get("section_info")
        if not section_info:
            section_info = self.doc_reader.find_section_by_page(page_num)
        if not section_info:
            print(f"[Figure Node] ✗ 图片 {img_id}: 未找到所属章节 (页码: {page_num})")
            return None

        references = self._extract_reference_sentences(
            img_id, caption, section_info.get("section_elem")
        )

        figure_node = FigureNode(
            figure_id=img_id,
            page_num=int(page_num) if page_num is not None else None,
            caption=caption,
            section_id=section_info.get("section_id", ""),
            section_title=section_info.get("title", ""),
            section_elem=section_info.get("section_elem"),
            references=references,
        )

        print(f"[Figure Node] ✓ 图片 {img_id} 构建完成")
        print(f"  → 章节: {figure_node.section_title}")
        print(f"  → 引用句数量: {len(references)}")

        return figure_node

    def _build_table_node(self, table_id: str, table_info: Dict) -> Optional[FigureNode]:
        page_num = table_info.get("page_num")
        caption = table_info.get("caption", "")

        section_info = table_info.get("section_info")
        if not section_info:
            section_info = self.doc_reader.find_section_by_page(page_num)
        if not section_info:
            print(f"[Table Node] ✗ 表格 {table_id}: 未找到所属章节 (页码: {page_num})")
            return None

        references = self._extract_table_reference_sentences(
            table_id, caption, section_info.get("section_elem")
        )

        table_node = FigureNode(
            figure_id=table_id,
            page_num=int(page_num) if page_num is not None else None,
            caption=caption,
            section_id=section_info.get("section_id", ""),
            section_title=section_info.get("title", ""),
            section_elem=section_info.get("section_elem"),
            references=references,
        )

        print(f"[Table Node] ✓ 表格 {table_id} 构建完成")
        print(f"  → 章节: {table_node.section_title}")
        print(f"  → 引用句数量: {len(references)}")

        return table_node

    def _find_section_by_element(
        self, elem: ET.Element, parent_map: Dict[ET.Element, ET.Element]
    ) -> Optional[Dict[str, Any]]:
        curr = elem
        while curr in parent_map:
            curr = parent_map[curr]
            if curr.tag == "Section":
                title_text = "Unknown Section"
                for node in curr:
                    if node.tag == "Heading" and node.text:
                        title_text = node.text
                        break
                return {
                    "section_id": curr.get("section_id", ""),
                    "title": title_text,
                    "start_page_num": curr.get("start_page_num"),
                    "end_page_num": curr.get("end_page_num"),
                    "section_elem": curr,
                }
        return None

    def _extract_reference_sentences(
        self, img_id: str, caption: str, section_elem: Optional[ET.Element]
    ) -> List[TextReference]:
        if section_elem is None:
            return []

        figure_nums = self._get_figure_numbers(img_id, caption)
        if not figure_nums:
            return []

        patterns = []
        for figure_num in figure_nums:
            num_pattern = self._build_figure_number_pattern(figure_num)
            patterns.extend(
                [
                    rf"(?:[如见及与和]|以及|参见|参看|详见|在|由)?\s*图[圖图]?\s*{num_pattern}",
                    rf"Figure\s*{num_pattern}",
                    rf"Fig\.?\s*{num_pattern}",
                ]
            )
        combined = "".join(section_elem.itertext())
        sentences = self._split_sentences(combined)
        references: List[TextReference] = []

        for sentence in sentences:
            for pattern in patterns:
                if re.search(pattern, sentence, re.IGNORECASE):
                    cleaned = sentence.strip()
                    if cleaned:
                        references.append(TextReference(sentence=cleaned, figure_id=img_id))
                    break

        return references

    def _extract_table_reference_sentences(
        self, table_id: str, caption: str, section_elem: Optional[ET.Element]
    ) -> List[TextReference]:
        if section_elem is None:
            return []

        table_nums = self._get_table_numbers(table_id, caption)
        if not table_nums:
            return []

        patterns = []
        for table_num in table_nums:
            num_pattern = self._build_table_number_pattern(table_num)
            patterns.extend(
                [
                    rf"(?:[如见及与和]|以及|参见|参看|详见|在|由)?\s*表\s*{num_pattern}",
                    rf"Table\s*{num_pattern}",
                    rf"Tab\.?\s*{num_pattern}",
                ]
            )
        combined = "".join(section_elem.itertext())
        sentences = self._split_sentences(combined)
        references: List[TextReference] = []

        for sentence in sentences:
            for pattern in patterns:
                if re.search(pattern, sentence, re.IGNORECASE):
                    cleaned = sentence.strip()
                    if cleaned:
                        references.append(TextReference(sentence=cleaned, figure_id=table_id))
                    break

        return references

    def _split_sentences(self, text: str) -> List[str]:
        if not text:
            return []
        parts = re.split(r"([。！？.!?])", text)
        sentences = []
        for i in range(0, len(parts) - 1, 2):
            sentence = (parts[i] + parts[i + 1]).strip()
            if sentence:
                sentences.append(sentence)
        if len(parts) % 2 == 1 and parts[-1].strip():
            sentences.append(parts[-1].strip())
        return sentences

    def _extract_scope_keywords(self, caption_text: str, image_capacity: ImageCapacity) -> List[str]:
        raw_text = caption_text or ""
        keywords: List[str] = []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", raw_text):
            keywords.append(token)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}", raw_text):
            keywords.append(token)
        if image_capacity and isinstance(image_capacity.raw, dict):
            for key in ["keywords", "entities", "labels"]:
                vals = image_capacity.raw.get(key)
                if isinstance(vals, list):
                    for item in vals:
                        if isinstance(item, str) and len(item) >= 2:
                            keywords.append(item)
        deduped = []
        for k in keywords:
            if k not in deduped:
                deduped.append(k)
        return deduped

    def _contains_figure_or_table_ref(self, sentence: str) -> bool:
        if not sentence:
            return False
        return bool(re.search(r"(图|圖|figure|fig\.?|表|table|tab\.?)\s*[\d\-\.]+", sentence, re.IGNORECASE))

    def _is_heading_like(self, sentence: str) -> bool:
        if not sentence:
            return False
        text = sentence.strip()
        if re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*章", text):
            return True
        if re.match(r"^\d+(\.\d+)*\s+\S", text):
            return True
        if re.match(r"^[（(]\d+[)）]\s*\S", text):
            return True
        return False

    def _is_topic_switch(self, sentence: str) -> bool:
        if not sentence:
            return False
        return bool(
            re.search(r"(综上|因此|本节小结|本章小结|小结|总结|结论)", sentence)
        )

    def _build_image_semantic_scope(
        self,
        figure_node: FigureNode,
        caption_text: str,
        image_capacity: ImageCapacity,
    ) -> Tuple[str, float, List[str]]:
        if not figure_node.section_elem:
            return "", 0.0, ["missing_section_elem"]

        combined = "".join(figure_node.section_elem.itertext())
        sentences = self._split_sentences(combined)
        if not sentences:
            return "", 0.0, ["empty_section_text"]

        figure_nums = self._get_figure_numbers(figure_node.figure_id, caption_text)
        patterns = []
        for figure_num in figure_nums:
            num_pattern = self._build_figure_number_pattern(figure_num)
            patterns.extend(
                [
                    rf"(?:[如见及与和]|以及|参见|参看|详见|在|由)?\s*图[圖图]?\s*{num_pattern}",
                    rf"Figure\s*{num_pattern}",
                    rf"Fig\.?\s*{num_pattern}",
                ]
            )
        ref_indices = []
        for idx, sentence in enumerate(sentences):
            if any(re.search(p, sentence, re.IGNORECASE) for p in patterns):
                ref_indices.append(idx)
        ref_idx = ref_indices[0] if ref_indices else 0

        keywords = self._extract_scope_keywords(caption_text, image_capacity)
        trace: List[str] = []

        scope_indices = [ref_idx]
        # Backward anchor
        i = ref_idx - 1
        while i >= 0:
            s = sentences[i]
            if self._is_heading_like(s) or self._contains_figure_or_table_ref(s) or self._is_topic_switch(s):
                trace.append(f"backward_stop_at:{s[:20]}")
                break
            scope_indices.insert(0, i)
            i -= 1

        # Forward extension
        forward_triggers = (
            "但是",
            "并且",
            "同时",
            "其中",
            "此外",
            "具体而言",
            "如下",
            "即",
        )
        no_keyword_streak = 0
        for j in range(ref_idx + 1, len(sentences)):
            s = sentences[j]
            if self._is_heading_like(s) or self._contains_figure_or_table_ref(s) or self._is_topic_switch(s):
                trace.append(f"forward_stop_at:{s[:20]}")
                break
            trigger = None
            for t in forward_triggers:
                if s.strip().startswith(t):
                    trigger = t
                    break
            hit_ratio = 0.0
            if keywords:
                hits = self._keyword_hits(s, keywords)
                hit_ratio = hits / max(len(keywords), 1)
            if trigger:
                scope_indices.append(j)
                trace.append(f"forward_extension_triggered_by:'{trigger}'")
                if keywords:
                    trace.append(f"keyword_overlap:{hit_ratio:.2f}")
                no_keyword_streak = 0 if hit_ratio >= 0.3 else no_keyword_streak + 1
            else:
                if keywords and hit_ratio >= 0.3:
                    scope_indices.append(j)
                    trace.append(f"keyword_overlap:{hit_ratio:.2f}")
                    no_keyword_streak = 0
                else:
                    no_keyword_streak += 1
            if no_keyword_streak >= 2:
                trace.append("forward_stop:keyword_miss_streak")
                break

        scope_text = "\n".join([sentences[i] for i in scope_indices if sentences[i].strip()])
        scope_confidence = 0.6
        if len(scope_indices) > 1:
            scope_confidence += 0.1
        if any("forward_extension_triggered_by" in t for t in trace):
            scope_confidence += 0.1
        if any(t.startswith("keyword_overlap") and float(t.split(":")[1]) >= 0.3 for t in trace if ":" in t):
            scope_confidence += 0.1
        scope_confidence = max(0.0, min(1.0, scope_confidence))

        return scope_text, scope_confidence, trace

    def _get_figure_numbers(self, img_id: str, caption: str) -> List[str]:
        numbers: List[str] = []
        if caption:
            for match in re.finditer(
                r"(?:图|圖|figure|fig\.?)\s*"
                r"(\d+(?:\s*[-–—\.]\s*\d+)*)(?:\s*[\(（]?\s*([a-zA-Z])\s*[\)）]?)?",
                caption,
                re.IGNORECASE,
            ):
                num = match.group(1) or ""
                suffix = match.group(2) or ""
                normalized = self._normalize_figure_num(num, suffix)
                if normalized:
                    numbers.append(normalized)

        if not numbers and img_id:
            for match in re.finditer(r"(\d+(?:[-–—\.]\d+)*)", img_id):
                normalized = self._normalize_figure_num(match.group(1), "")
                if normalized:
                    numbers.append(normalized)

        return list(dict.fromkeys(numbers))

    def _get_table_numbers(self, table_id: str, caption: str) -> List[str]:
        numbers: List[str] = []
        if caption:
            for match in re.finditer(
                r"(?:表|table|tab\.?)\s*"
                r"(\d+(?:\s*[-–—\.]\s*\d+)*)(?:\s*[\(（]?\s*([a-zA-Z])\s*[\)）]?)?",
                caption,
                re.IGNORECASE,
            ):
                num = match.group(1) or ""
                suffix = match.group(2) or ""
                normalized = self._normalize_figure_num(num, suffix)
                if normalized:
                    numbers.append(normalized)

        if not numbers and table_id:
            for match in re.finditer(r"(\d+(?:[-–—\.]\d+)*)", table_id):
                normalized = self._normalize_figure_num(match.group(1), "")
                if normalized:
                    numbers.append(normalized)

        return list(dict.fromkeys(numbers))

    def _normalize_figure_num(self, num: str, suffix: str) -> str:
        cleaned = re.sub(r"\s+", "", num)
        cleaned = cleaned.replace("–", "-").replace("—", "-").replace("－", "-")
        cleaned = cleaned.replace("．", ".")
        if suffix:
            cleaned = f"{cleaned}{suffix.lower()}"
        return cleaned

    def _build_figure_number_pattern(self, num: str) -> str:
        base = num
        if base and base[-1].isalpha():
            base = base[:-1]

        parts = re.split(r"[-\.]", base) if base else []
        if not parts:
            return re.escape(num)

        joiner = r"\s*[-–—\.]\s*"
        core = joiner.join([re.escape(p) for p in parts if p])
        suffix_pattern = r"(?:\s*[\(（]?\s*[a-zA-Z]\s*[\)）]?)?"
        return core + suffix_pattern

    def _build_table_number_pattern(self, num: str) -> str:
        return self._build_figure_number_pattern(num)

    def _classify_role(
        self, references: List[TextReference], scope_text: str = ""
    ) -> RoleAssignment:
        if not references and not scope_text:
            return RoleAssignment(
                role="UNKNOWN",
                confidence=0.0,
                evidence_sentence="",
                raw={"error": "no_reference_sentence"},
            )

        if scope_text:
            ref_text = scope_text.strip()
        else:
            ref_text = "\n".join([f"- {r.sentence}" for r in references])
        messages = [
            {"role": "system", "content": argument_role_prompt},
            {"role": "user", "content": f"图片引用语义作用域如下：\n{ref_text}"},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=512,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            parsed = self._parse_json_from_response(raw_content)
            evidence_sentence = str(parsed.get("evidence_sentence", ""))
            if not evidence_sentence and scope_text:
                evidence_sentence = scope_text
            return RoleAssignment(
                role=str(parsed.get("role", "UNKNOWN")),
                confidence=float(parsed.get("confidence", 0.0)),
                evidence_sentence=evidence_sentence,
                raw=parsed,
            )
        except Exception as e:
            return RoleAssignment(
                role="UNKNOWN",
                confidence=0.0,
                evidence_sentence="",
                raw={"error": str(e)},
            )

    def _map_section_stage(self, section_title: str) -> str:
        title = (section_title or "").lower()
        if any(k in title for k in ["introduction", "background", "绪论", "引言", "背景"]):
            return "PROBLEM"
        if any(k in title for k in ["method", "model", "approach", "training", "方法", "模型", "算法"]):
            return "METHOD"
        if any(
            k in title
            for k in ["experiment", "result", "results", "evaluation", "assessment", "实验", "结果", "评测"]
        ):
            return "EVIDENCE"
        if any(k in title for k in ["discussion", "conclusion", "讨论", "结论", "总结"]):
            return "INTERPRETATION"
        return "UNKNOWN"

    def _expected_stage_from_role(self, role: str) -> Optional[str]:
        if role == "METHOD_REFERENCE":
            return "METHOD"
        if role in ["RESULT_CLAIM", "COMPARISON_CLAIM"]:
            return "EVIDENCE"
        if role in ["ILLUSTRATIVE", "NON_ARGUMENTATIVE"]:
            return None
        return None

    def _mismatch_severity(self, role: str, actual_stage: str, image_type: Optional[str]) -> str:
        if role == "RESULT_CLAIM" and actual_stage == "METHOD":
            if is_method_diagram(image_type):
                return "High"
            if is_metric_chart(image_type):
                return "Low"
            return "Medium"
        if role == "METHOD_REFERENCE" and actual_stage == "EVIDENCE":
            if is_metric_chart(image_type):
                return "Low"
            return "Medium"
        return "Low"

    def _select_reference_sentence(
        self, figure_node: FigureNode, role_assignment: RoleAssignment
    ) -> str:
        if role_assignment.evidence_sentence:
            return role_assignment.evidence_sentence.strip()
        if figure_node.image_scope_text:
            return figure_node.image_scope_text.strip()
        if figure_node.references:
            return figure_node.references[0].sentence.strip()
        return ""

    def _keyword_hits(self, text: str, keywords: List[str]) -> int:
        if not text:
            return 0
        lowered = text.lower()
        hits = 0
        for k in keywords:
            key = k.lower()
            if key.isascii() and key.isalpha() and len(key) <= 3:
                if re.search(rf"\b{re.escape(key)}\b", lowered):
                    hits += 1
            else:
                if key in lowered:
                    hits += 1
        return hits

    def _infer_stage_by_sentence(self, sentence: str) -> Tuple[str, float, Dict[str, int]]:
        keywords = {
            "EVIDENCE": [
                "metric",
                "metrics",
                "loss",
                "accuracy",
                "map",
                "result",
                "results",
                "evaluation",
                "assessment",
                "curve",
                "curves",
                "statistic",
                "statistics",
                "准确率",
                "召回率",
                "损失",
                "结果",
                "曲线",
                "统计",
            ],
            "METHOD": [
                "architecture",
                "pipeline",
                "training",
                "train",
                "model",
                "framework",
                "network",
                "algorithm",
                "结构",
                "流程",
                "训练",
                "模型",
                "网络",
            ],
            "INTERPRETATION": [
                "analysis",
                "explanation",
                "interpretation",
                "insight",
                "discussion",
                "分析",
                "解释",
                "解读",
                "讨论",
            ],
        }
        hits = {stage: self._keyword_hits(sentence, keys) for stage, keys in keywords.items()}
        max_stage = max(hits, key=hits.get)
        max_count = hits[max_stage]
        if max_count == 0:
            return "UNKNOWN", 0.0, hits
        confidence = min(1.0, 0.5 + 0.1 * max_count)
        return max_stage, confidence, hits

    def _extract_local_context_paragraphs(
        self, section_elem: Optional[ET.Element], reference_sentence: str, max_paragraphs: int = 3
    ) -> List[str]:
        if section_elem is None:
            return []

        paragraphs: List[str] = []
        for node in section_elem.iter():
            if node.tag in ["Heading"]:
                continue
            if node.tag in ["Paragraph", "Para", "P", "Text", "Sentence", "Content"]:
                text = "".join(node.itertext()).strip()
                if text:
                    paragraphs.append(text)

        if not paragraphs:
            combined = " ".join(section_elem.itertext()).strip()
            if not combined:
                return []
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", combined) if p.strip()]
            if not paragraphs:
                paragraphs = [combined]

        if not reference_sentence:
            return paragraphs[:max_paragraphs]

        lowered_ref = reference_sentence.lower()
        match_index = next(
            (i for i, p in enumerate(paragraphs) if lowered_ref in p.lower()), None
        )
        if match_index is None:
            combined = " ".join(section_elem.itertext())
            sentences = self._split_sentences(combined)
            if not sentences:
                return paragraphs[:max_paragraphs]
            match_idx = next(
                (i for i, s in enumerate(sentences) if lowered_ref in s.lower()), None
            )
            if match_idx is None:
                return paragraphs[:max_paragraphs]
            start = max(0, match_idx - 2)
            end = min(len(sentences), match_idx + 3)
            return [" ".join(sentences[start:end]).strip()]

        start = max(0, match_index - 1)
        end = min(len(paragraphs), match_index + 2)
        return paragraphs[start:end][:max_paragraphs]

    def _infer_stage_by_local_context(
        self, paragraphs: List[str], reference_sentence: str
    ) -> Tuple[str, float, Dict[str, Any]]:
        text = "\n".join([p for p in paragraphs if p])
        stage, confidence, hits = self._infer_stage_by_sentence(text)
        info: Dict[str, Any] = {
            "source": "rule",
            "hits": hits,
            "llm_raw": None,
        }
        if confidence >= 0.6:
            return stage, confidence, info

        llm_stage, llm_conf, llm_raw = self._llm_stage_disambiguation(
            reference_sentence, paragraphs
        )
        info["llm_raw"] = llm_raw
        if llm_stage and llm_conf >= 0.6:
            info["source"] = "llm"
            return llm_stage, llm_conf, info
        return stage, confidence, info

    def _llm_stage_disambiguation(
        self, reference_sentence: str, paragraphs: List[str]
    ) -> Tuple[Optional[str], float, Dict[str, Any]]:
        if not reference_sentence and not paragraphs:
            return None, 0.0, {"error": "no_context"}
        context = "\n".join([p for p in paragraphs if p])[:2000]
        messages = [
            {"role": "system", "content": local_stage_alignment_prompt},
            {
                "role": "user",
                "content": (
                    f"引用句子：{reference_sentence}\n"
                    f"局部上下文：\n{context}"
                ),
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=128,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            parsed = self._parse_json_from_response(raw_content)
            stage = str(parsed.get("stage", "")).strip().upper()
            confidence = float(parsed.get("confidence", 0.0))
            if stage not in ["METHOD", "EVIDENCE", "INTERPRETATION"]:
                return None, 0.0, parsed
            return stage, confidence, parsed
        except Exception as e:
            return None, 0.0, {"error": str(e)}

    def _vote_final_stage(
        self,
        section_stage: str,
        reference_stage: str,
        reference_conf: float,
        reference_hits: Dict[str, int],
        local_stage: str,
        local_conf: float,
        local_info: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        stages = ["PROBLEM", "METHOD", "EVIDENCE", "INTERPRETATION"]
        scores = {s: 0.0 for s in stages}

        def add_vote(stage: str, weight: float, confidence: float):
            if stage in scores and confidence > 0:
                scores[stage] += weight * confidence

        add_vote(section_stage, 0.3, 1.0)
        add_vote(reference_stage, 0.4, reference_conf)
        add_vote(local_stage, 0.3, local_conf)

        max_score = max(scores.values())
        if max_score == 0:
            final_stage = "UNKNOWN"
        else:
            final_stage = next(s for s in stages if scores[s] == max_score)

        vote_breakdown = {
            "section_prior": {
                "stage": section_stage,
                "weight": 0.3,
                "confidence": 1.0,
                "contribution": 0.3 if section_stage in stages else 0.0,
            },
            "reference_sentence": {
                "stage": reference_stage,
                "weight": 0.4,
                "confidence": reference_conf,
                "hits": reference_hits,
                "contribution": 0.4 * reference_conf if reference_stage in stages else 0.0,
            },
            "local_context": {
                "stage": local_stage,
                "weight": 0.3,
                "confidence": local_conf,
                "source": local_info.get("source"),
                "hits": local_info.get("hits"),
                "llm_raw": local_info.get("llm_raw"),
                "contribution": 0.3 * local_conf if local_stage in stages else 0.0,
            },
            "scores": scores,
        }
        return final_stage, vote_breakdown

    def _align_stage(
        self,
        figure_node: FigureNode,
        role_assignment: RoleAssignment,
        image_type: Optional[str],
    ) -> StageAlignment:
        section_prior_stage = self._map_section_stage(figure_node.section_title)
        reference_sentence = self._select_reference_sentence(figure_node, role_assignment)
        reference_stage, reference_conf, reference_hits = self._infer_stage_by_sentence(
            reference_sentence
        )
        local_paragraphs = self._extract_local_context_paragraphs(
            figure_node.section_elem, reference_sentence
        )
        local_stage, local_conf, local_info = self._infer_stage_by_local_context(
            local_paragraphs, reference_sentence
        )
        context_stage, context_breakdown = self._vote_final_stage(
            section_prior_stage,
            reference_stage,
            reference_conf,
            reference_hits,
            local_stage,
            local_conf,
            local_info,
        )
        stage_resolution = self.stage_resolver.resolve(
            section_title=figure_node.section_title,
            image_type=image_type,
            text_role=role_assignment.role,
        )
        if stage_resolution.final_stage != "UNKNOWN":
            actual_stage = stage_resolution.final_stage
        else:
            if reference_conf == 0.0 and local_conf == 0.0:
                actual_stage = "UNKNOWN"
            else:
                actual_stage = context_stage

        expected_stage = self._expected_stage_from_role(role_assignment.role)
        status = "match"
        severity = None
        stage_mismatch = False
        mismatch_severity = None
        message = "阶段匹配"

        if actual_stage == "UNKNOWN":
            status = "unknown"
            message = "局部阶段无法判定"
        elif expected_stage is None:
            status = "match"
            message = "阶段无需严格匹配"
        elif (
            role_assignment.role in ["RESULT_CLAIM", "COMPARISON_CLAIM"]
            and expected_stage != actual_stage
        ):
            stage_mismatch = True
            status = "mismatch"
            mismatch_severity = self._mismatch_severity(
                role_assignment.role, actual_stage, image_type
            )
            severity = mismatch_severity
            message = (
                f"{role_assignment.role} 期望 {expected_stage}，但实际为 {actual_stage}"
            )

        vote_breakdown = {
            "stage_resolver": stage_resolution.votes,
            "context_vote": context_breakdown,
            "final": {
                "actual_stage": actual_stage,
                "expected_stage": expected_stage,
                "image_type": image_type,
            },
        }
        return StageAlignment(
            expected_stage=expected_stage,
            actual_stage=actual_stage,
            status=status,
            severity=severity,
            message=message,
            stage_mismatch=stage_mismatch,
            mismatch_severity=mismatch_severity,
            vote_breakdown=vote_breakdown,
            image_type=image_type,
        )

    def _analyze_image_capacity(
        self,
        client,
        vision_model_id: str,
        figure_node: FigureNode,
        role_assignment: RoleAssignment,
        media_kind: str = "image",
        image_scope_text: str = "",
    ) -> ImageCapacity:
        media_type, base64_img, error = self._load_media_image(
            media_kind, figure_node.figure_id
        )
        if error:
            return ImageCapacity(
                role=role_assignment.role,
                sufficient=False,
                reason=f"图片读取失败: {error}",
                raw={"error": error},
            )

        messages = [
            {"role": "system", "content": image_capacity_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请评估图片是否具备完成其论证角色的信息容量。\n"
                            f"Caption: {figure_node.caption}\n"
                            f"Scope: {image_scope_text}\n"
                            f"Role: {role_assignment.role}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{base64_img}"
                        },
                    },
                ],
            },
        ]

        try:
            response = client.chat.completions.create(
                model=vision_model_id,
                messages=messages,
                max_tokens=512,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            parsed = self._parse_json_from_response(raw_content)
            return ImageCapacity(
                role=str(parsed.get("role", role_assignment.role)),
                sufficient=bool(parsed.get("sufficient", False)),
                reason=str(parsed.get("reason", "")),
                raw=parsed,
            )
        except Exception as e:
            return ImageCapacity(
                role=role_assignment.role,
                sufficient=False,
                reason=f"图片容量分析失败: {str(e)}",
                raw={"error": str(e)},
            )

    def _judge_aggregate(
        self,
        figure_node: FigureNode,
        role_assignment: RoleAssignment,
        stage_alignment: StageAlignment,
        image_capacity: ImageCapacity,
        media_kind: str,
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []

        if role_assignment.confidence < 0.6:
            issues.append(
                {
                    "issue_type": "图文一致性",
                    "severity": "Medium",
                    "section": figure_node.section_title,
                    "page": figure_node.page_num,
                    "image_id": figure_node.figure_id,
                    "media_kind": media_kind,
                    "quote": role_assignment.evidence_sentence or "未能可靠识别图像论证角色",
                    "suggestion": "请明确图号引用句，使图像在论证中的角色更清晰。示例：将“结果如图所示”改为“如图3-11所示，Precision-Confidence曲线用于说明高置信度区间的精确率变化趋势”。",
                }
            )

        generalization_issue = self._build_generalization_issue(
            figure_node, role_assignment, stage_alignment.image_type
        )
        if generalization_issue:
            generalization_issue["media_kind"] = media_kind
            issues.append(generalization_issue)

        if stage_alignment.status == "mismatch":
            issues.append(
                {
                    "issue_type": "图文一致性",
                    "severity": stage_alignment.severity or "Medium",
                    "section": figure_node.section_title,
                    "page": figure_node.page_num,
                    "image_id": figure_node.figure_id,
                    "media_kind": media_kind,
                    "quote": stage_alignment.message,
                    "suggestion": "请调整图片位置或修改引用语句，使论证阶段一致。示例：若图为结果曲线，请移至“实验结果/评测”小节并补充“如图3-11所示，本实验在不同置信度下的Precision变化”。",
                }
            )

        if not image_capacity.sufficient:
            issues.append(
                {
                    "issue_type": "图文一致性",
                    "severity": "Medium",
                    "section": figure_node.section_title,
                    "page": figure_node.page_num,
                    "image_id": figure_node.figure_id,
                    "media_kind": media_kind,
                    "quote": image_capacity.reason,
                    "suggestion": "请补充更能支撑该论证角色的图像信息或调整角色描述。示例：在图中增加图例/数值标注/对比曲线，或将正文表述从“验证模型性能显著优于基线”改为“展示Precision随置信度的变化趋势”。",
                }
            )

        if issues:
            for issue in issues:
                issue["modification_advice"] = self._build_modification_advice(
                    figure_node=figure_node,
                    role_assignment=role_assignment,
                    stage_alignment=stage_alignment,
                    image_capacity=image_capacity,
                    issue=issue,
                )

        return issues

    def _decide_modification_by_scope(
        self,
        image_scope_text: str,
        image_type: Optional[str],
        stage_alignment: StageAlignment,
    ) -> Tuple[str, str, List[str]]:
        trace: List[str] = []
        if not image_scope_text:
            return "TEXT", "建议补充图像引用上下文或图注信息。", ["scope_missing"]

        scope_text = image_scope_text
        scope_is_result = bool(
            re.search(r"(结果|对比|实验|性能|准确率|召回率|精确率|F1|评测|验证)", scope_text)
        )
        scope_is_method = bool(
            re.search(r"(方法|流程|架构|系统|模块|设计|步骤|算法|模型|框架|实现)", scope_text)
        )
        image_is_method = is_method_diagram(image_type) if image_type else False
        image_is_metric = is_metric_chart(image_type) if image_type else False

        if scope_is_result and image_is_method:
            trace.append("scope_result_vs_image_method")
            return (
                "IMAGE",
                "图像更像方法/流程示意，而文本描述强调结果或对比，建议替换为结果图或将该图移至方法部分并补充说明。",
                trace,
            )
        if scope_is_method and image_is_metric:
            trace.append("scope_method_vs_image_metric")
            return (
                "TEXT",
                "图像偏结果类图表，但文本偏方法/设计描述，建议调整引用文字或图像位置以匹配论证阶段。",
                trace,
            )

        if stage_alignment.status == "mismatch":
            trace.append("stage_mismatch")
            return (
                "TEXT",
                "图像与语义作用域一致，但所在章节/引用位置不匹配论证阶段，建议调整引用文字或移动图片位置。",
                trace,
            )

        trace.append("scope_image_consistent")
        return (
            "TEXT",
            "图像与语义作用域整体一致，可在引用句中补充图像用途说明以避免歧义。",
            trace,
        )

    def _compute_claim_strengths(
        self, role_assignment: RoleAssignment, image_capacity: ImageCapacity
    ) -> Tuple[float, float]:
        text_claim_strength = 0.5
        image_evidence_strength = 0.5

        if role_assignment and role_assignment.confidence is not None:
            text_claim_strength = max(0.0, min(1.0, float(role_assignment.confidence)))

        if image_capacity and image_capacity.sufficient:
            image_evidence_strength = 0.85
        elif image_capacity and not image_capacity.sufficient:
            image_evidence_strength = 0.35

        return text_claim_strength, image_evidence_strength

    def _generate_modification_suggestion(self, prompt_text: str, fallback: str) -> str:
        if not prompt_text:
            return fallback
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=[{"role": "user", "content": prompt_text}],
                max_tokens=200,
                temperature=0.2,
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()
        except Exception:
            pass
        return fallback

    def _build_modification_advice(
        self,
        figure_node: FigureNode,
        role_assignment: RoleAssignment,
        stage_alignment: StageAlignment,
        image_capacity: ImageCapacity,
        issue: Dict[str, Any],
    ) -> Dict[str, Any]:
        text_strength, image_strength = self._compute_claim_strengths(
            role_assignment, image_capacity
        )
        text_is_explanatory = self._is_explanatory_reference(figure_node, role_assignment)
        decision = decide_modification_target(
            figure_role=role_assignment.role,
            expected_stage=stage_alignment.expected_stage,
            actual_stage=stage_alignment.actual_stage,
            text_claim_strength=text_strength,
            image_evidence_strength=image_strength,
            image_type=stage_alignment.image_type,
            text_is_explanatory=text_is_explanatory,
        )
        llm_instruction = build_llm_instruction(decision.get("modification_target"))
        context = {
            "figure_role": role_assignment.role,
            "expected_stage": stage_alignment.expected_stage,
            "actual_stage": stage_alignment.actual_stage,
            "text_claim_strength": text_strength,
            "image_evidence_strength": image_strength,
            "quote": issue.get("quote"),
        }
        prompt_text = build_suggestion_prompt(context, decision)

        fallback_map = {
            "MODIFY_FIGURE": "建议补充或调整图像内容，使其能直接支撑文本主张，增加关键数据、标注或对比信息。",
            "MODIFY_TEXT": "建议修订文字表述，使其与图像展示事实一致，避免过度结论并补充准确描述。",
            "BOTH_LIGHT": "建议对图像与文字做轻微一致性调整，确保引用句与图像信息对齐。",
        }
        fallback = fallback_map.get(
            decision.get("modification_target", "BOTH_LIGHT"), fallback_map["BOTH_LIGHT"]
        )
        suggestion = self._generate_modification_suggestion(prompt_text, fallback)

        return {
            "modification_target": decision.get("modification_target"),
            "reason": decision.get("reason"),
            "llm_instruction": llm_instruction,
            "suggestion": suggestion,
        }

    def _parse_json_from_response(self, raw_content: str) -> Dict:
        result = self.doc_agent._parse_json(raw_content)
        if result == {"issues": []} and raw_content:
            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw_content[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return result if result else {}
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
        results: List[Dict[str, Any]] = []

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

        image_info_map: Dict[str, Dict[str, Any]] = {}
        table_info_map: Dict[str, Dict[str, Any]] = {}
        parent_map = {c: p for p in self.doc_reader.root.iter() for c in p}

        for elem in self.doc_reader.root.iter("Image"):
            img_id = elem.get("image_id")
            page_num = elem.get("page_num")
            caption_text = ""
            adjacent_image = False
            adjacent_image_ids: List[str] = []
            caption_pattern = re.compile(
                r"^(figure|fig\.?|图)\s*[\d\-\.]+", re.IGNORECASE
            )

            for child in elem:
                if child.tag == "Caption" and child.text:
                    caption_text = child.text
                elif child.tag == "Alt_Text" and child.text and not caption_text:
                    caption_text = child.text

            # 向上查找 2 层父节点以处理更复杂的结构（如 Image 嵌套在 Figure 中）
            curr_elem = elem
            for _ in range(2):
                parent = parent_map.get(curr_elem)
                if not parent:
                    break

                try:
                    children = list(parent)
                    idx = children.index(curr_elem)
                    if not adjacent_image:
                        for neighbor_idx in (idx - 1, idx + 1):
                            if 0 <= neighbor_idx < len(children):
                                neighbor = children[neighbor_idx]
                                if neighbor.tag == "Image":
                                    adjacent_image = True
                                    neighbor_id = neighbor.get("image_id")
                                    if neighbor_id:
                                        adjacent_image_ids.append(neighbor_id)
                    # 扩大搜索范围（前后 5 个节点）并支持更多可能的标签
                    search_range = 5
                    start_idx = max(0, idx - search_range)
                    end_idx = min(len(children), idx + search_range + 1)

                    for i in range(start_idx, end_idx):
                        if i == idx and curr_elem == elem:
                            continue
                        node = children[i]
                        # 使用 itertext 确保能获取到带格式（如加粗）的标题文字
                        text = "".join(node.itertext()).strip()
                        if text:
                            if self.doc_agent._is_header_footer(
                                text, node.get("page_num") or page_num
                            ):
                                continue
                            if caption_pattern.match(text):
                                caption_text = text
                                break
                    if caption_text:
                        break
                except ValueError:
                    pass
                curr_elem = parent

            if img_id:
                image_filename = ""
                if img_id in self.doc_reader.image_path_dict:
                    image_filename = os.path.basename(
                        self.doc_reader.image_path_dict.get(img_id, "")
                    )
                section_info = self._find_section_by_element(elem, parent_map)
                if not section_info:
                    section_info = self.doc_reader.find_section_by_page(page_num)
                image_info_map[img_id] = {
                    "page_num": page_num,
                    "caption": caption_text,
                    "image_name": image_filename,
                    "section_info": section_info,
                    "adjacent_image": adjacent_image,
                    "adjacent_image_ids": adjacent_image_ids,
                }

        for elem in self.doc_reader.root.iter("CSV_Table"):
            table_id = elem.get("table_id")
            page_num = elem.get("page_num")
            caption_text = ""
            caption_pattern = re.compile(
                r"^(table|tab\.?|表)\s*[\d\-\.]+", re.IGNORECASE
            )

            for child in elem:
                if child.tag == "Alt_Text" and child.text:
                    caption_text = child.text
                    break

            curr_elem = elem
            for _ in range(2):
                parent = parent_map.get(curr_elem)
                if not parent:
                    break
                try:
                    children = list(parent)
                    idx = children.index(curr_elem)
                    search_range = 5
                    start_idx = max(0, idx - search_range)
                    end_idx = min(len(children), idx + search_range + 1)
                    for i in range(start_idx, end_idx):
                        if i == idx and curr_elem == elem:
                            continue
                        node = children[i]
                        text = "".join(node.itertext()).strip()
                        if text:
                            if self.doc_agent._is_header_footer(
                                text, node.get("page_num") or page_num
                            ):
                                continue
                            if caption_pattern.match(text):
                                caption_text = text
                                break
                    if caption_text:
                        break
                except ValueError:
                    pass
                curr_elem = parent

            if table_id:
                section_info = self._find_section_by_element(elem, parent_map)
                if not section_info:
                    section_info = self.doc_reader.find_section_by_page(page_num)
                table_info_map[table_id] = {
                    "page_num": page_num,
                    "caption": caption_text,
                    "section_info": section_info,
                }

        section_order: Dict[int, int] = {}
        intro_index = None
        section_idx = 0
        for child in self.doc_reader.root:
            if child.tag != "Section":
                continue
            section_order[id(child)] = section_idx
            title_text = ""
            for node in child:
                if node.tag == "Heading" and node.text:
                    title_text = node.text
                    break
            if intro_index is None and title_text and re.search(
                r"(摘要|abstract|摘\s*要)", title_text, re.IGNORECASE
            ):
                intro_index = section_idx
            section_idx += 1

        count = 0
        total_images = len(self.doc_reader.image_path_dict)
        total_tables = len(self.doc_reader.table_image_path_dict)
        total_media = total_images + total_tables
        process_limit = min(max_images, total_media)
        print(
            f"[Agent] 发现 {total_images} 张图片 + {total_tables} 张表格，将审查前 {process_limit} 项（ARG图文一致性）"
        )
        print(
            "  → 处理流程: Step 1 (FigureNode构建) → Step 2 (Role分类) → "
            "Step 3 (Image容量) → Step 4 (Stage对齐) → Step 5 (规则聚合)"
        )

        media_items = []
        for img_id in self.doc_reader.image_path_dict.keys():
            meta = image_info_map.get(img_id, {})
            sec_info = meta.get("section_info") or {}
            sec_elem = sec_info.get("section_elem")
            if intro_index is not None:
                if not sec_elem:
                    continue
                if section_order.get(id(sec_elem), -1) < intro_index:
                    continue
            media_items.append(("image", img_id))
        for table_id in self.doc_reader.table_image_path_dict.keys():
            meta = table_info_map.get(table_id, {})
            sec_info = meta.get("section_info") or {}
            sec_elem = sec_info.get("section_elem")
            if intro_index is not None:
                if not sec_elem:
                    continue
                if section_order.get(id(sec_elem), -1) < intro_index:
                    continue
            media_items.append(("table", table_id))

        for media_kind, media_id in media_items:
            if count >= max_images:
                break

            if media_kind == "table":
                meta = table_info_map.get(
                    media_id,
                    {
                        "page_num": "?",
                        "caption": "Unknown",
                        "section_info": None,
                    },
                )
                name_label = f"table_{media_id}"
            else:
                filename = self.doc_reader.image_path_dict.get(media_id)
                meta = image_info_map.get(
                    media_id,
                    {
                        "page_num": "?",
                        "caption": "Unknown",
                        "image_name": os.path.basename(filename) if filename else "",
                        "section_info": None,
                    },
                )
                name_label = meta.get("image_name", "")

            print(
                f"[Agent] 分析{ '表格' if media_kind == 'table' else '图片' } {media_id} ({name_label}) "
                f"(第 {meta['page_num']} 页): {meta['caption'][:30]}..."
            )

            try:
                if media_kind == "table":
                    figure_node = self._build_table_node(table_id=media_id, table_info=meta)
                else:
                    figure_node = self._build_figure_node(img_id=media_id, image_info=meta)
                if not figure_node:
                    results.append(
                        {
                            "figure_id": media_id,
                            "page": meta.get("page_num"),
                            "caption": meta.get("caption", ""),
                            "media_kind": media_kind,
                            "issues": [],
                            "raw_debug": {"error": "Failed to build figure node"},
                        }
                    )
                    count += 1
                    continue

                scope_text, scope_confidence, scope_trace = self._build_image_semantic_scope(
                    figure_node=figure_node,
                    caption_text=meta.get("caption", ""),
                    image_capacity=ImageCapacity(
                        role="UNKNOWN", sufficient=False, reason="", raw={}
                    ),
                )
                figure_node.image_scope_text = scope_text
                figure_node.scope_confidence = scope_confidence
                figure_node.decision_trace = scope_trace

                role_assignment = self._classify_role(
                    figure_node.references, scope_text=figure_node.image_scope_text
                )
                image_capacity = self._analyze_image_capacity(
                    client,
                    vision_model_id,
                    figure_node,
                    role_assignment,
                    media_kind,
                    image_scope_text=figure_node.image_scope_text,
                )
                # refresh scope keywords using image_capacity raw
                scope_text, scope_confidence, scope_trace = self._build_image_semantic_scope(
                    figure_node=figure_node,
                    caption_text=meta.get("caption", ""),
                    image_capacity=image_capacity,
                )
                figure_node.image_scope_text = scope_text
                figure_node.scope_confidence = scope_confidence
                figure_node.decision_trace = scope_trace

                image_type = self._infer_image_type(
                    figure_node, role_assignment, image_capacity
                )
                stage_alignment = self._align_stage(figure_node, role_assignment, image_type)
                issues = self._judge_aggregate(
                    figure_node,
                    role_assignment,
                    stage_alignment,
                    image_capacity,
                    media_kind,
                )
                modification_target, modification_suggestion, decision_trace = (
                    self._decide_modification_by_scope(
                        figure_node.image_scope_text, image_type, stage_alignment
                    )
                )
                consistency_result = (
                    "INCONSISTENT"
                    if stage_alignment.status == "mismatch"
                    or any(i.get("severity") in ["High", "Medium"] for i in issues)
                    else "CONSISTENT"
                )
                if media_kind == "image":
                    caption_missing = not (meta.get("caption") or "").strip()
                    if caption_missing and meta.get("adjacent_image"):
                        issues.append(
                            {
                                "issue_type": "图文一致性",
                                "severity": "Low",
                                "section": figure_node.section_title,
                                "page": figure_node.page_num,
                                "image_id": figure_node.figure_id,
                                "media_kind": media_kind,
                                "quote": "图片缺少图注/Alt_Text，且与相邻图片紧挨。",
                                "suggestion": (
                                    "请核对该图片是否应有图注并补充。"
                                    "tips:（若两张图片紧挨，可能会导致小标题扫描重叠导致检测错误，请人工核实是否正确并将图片间距拉大）"
                                ),
                                "adjacent_image_ids": meta.get(
                                    "adjacent_image_ids", []
                                ),
                            }
                        )

                result = FigureReviewResult(
                    figure_id=figure_node.figure_id,
                    page=figure_node.page_num,
                    caption=figure_node.caption,
                    section=figure_node.section_title,
                    media_kind=media_kind,
                    role_assignment=role_assignment,
                    stage_alignment=stage_alignment,
                    image_capacity=image_capacity,
                    issues=issues,
                    image_scope_text=figure_node.image_scope_text,
                    scope_confidence=figure_node.scope_confidence,
                    consistency_result=consistency_result,
                    modification_target=modification_target,
                    modification_suggestion=modification_suggestion,
                    decision_trace=list(dict.fromkeys(figure_node.decision_trace + decision_trace)),
                    raw_debug={
                        "references": [asdict(r) for r in figure_node.references],
                        "role_raw": role_assignment.raw,
                        "capacity_raw": image_capacity.raw,
                        "media_kind": media_kind,
                    },
                )
                results.append(asdict(result))
                count += 1
            except Exception as e:
                results.append(
                    {
                        "figure_id": media_id,
                        "page": meta.get("page_num"),
                        "caption": meta.get("caption", ""),
                        "media_kind": media_kind,
                        "issues": [],
                        "raw_debug": {"error": str(e)},
                    }
                )
                count += 1

        print(f"\n[Agent] ✅ 完成 {count} 张图片的ARG一致性分析")
        print(
            "  → 发现问题: 共 "
            f"{sum(len(r.get('issues', [])) for r in results if isinstance(r, dict))} 个图文一致性问题"
        )
        return results

    def run_vision_review_parallel(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_workers=3,
    ):
        raise NotImplementedError(
            "并行模式已废弃。请使用串行模式 run_vision_review()，"
            "它使用ARG流程：FigureNode构建 → Role分类 → Stage对齐 → "
            "Image容量评估 → 规则聚合"
        )
