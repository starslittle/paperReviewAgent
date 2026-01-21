"""
嵌套结构版 DocIRBuilder - 基于内容模式识别标题层级并构建父子关系
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import ast
import os
import re
import xml.etree.ElementTree as ET

import pandas as pd
from PIL import Image

try:
    from .doc_ir import DocIR, DocIRMeta, FigureNode, SectionNode, TableNode, TextBlock
except ImportError:
    from doc_ir import DocIR, DocIRMeta, FigureNode, SectionNode, TableNode, TextBlock


@dataclass
class DocIRBuildResult:
    doc_ir: DocIR
    root: ET.Element
    section_dict: Dict[str, ET.Element]
    image_path_dict: Dict[str, str]
    table_image_path_dict: Dict[str, str]
    num_page: int
    image_count: int
    table_count: int
    para_count: int


class DocIRBuilder:
    """嵌套结构版 DocIRBuilder - 构建正确的父子Section关系"""

    def __init__(self, max_section_depth: int = 10):
        self.max_section_depth = max_section_depth
        self._image_cache: Dict[Tuple[str, str], Tuple[List[str], str]] = {}

    def _resolve_image_path(
        self, data_path: str, doc_id: str, image_index: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """从可用目录中兜底获取图片路径与来源目录"""
        cache_key = (data_path, doc_id)
        if cache_key not in self._image_cache:
            image_files: List[str] = []
            source_dir = None

            # 优先使用按页编号的图片目录
            candidate_dirs = [
                os.path.join(data_path, "page_images"),
            ]

            # 其次使用 MinerU 提取出的 images 目录
            repo_root = os.path.abspath(os.path.join(data_path, "..", "..", "..", ".."))
            candidate_dirs.extend(
                [
                    os.path.join(
                        repo_root,
                        "preprocess",
                        "extract_output",
                        "MinerU",
                        doc_id,
                        "images",
                    ),
                    os.path.join(
                        repo_root, "extract_output", "MinerU", doc_id, "images"
                    ),
                ]
            )

            for candidate in candidate_dirs:
                if not os.path.isdir(candidate):
                    continue
                files = [
                    f
                    for f in os.listdir(candidate)
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                ]
                if not files:
                    continue
                files.sort()
                image_files = [os.path.join(candidate, f) for f in files]
                source_dir = candidate
                break

            self._image_cache[cache_key] = (image_files, source_dir)

        image_files, source_dir = self._image_cache[cache_key]
        if not image_files or image_index >= len(image_files):
            return None, source_dir
        return image_files[image_index], source_dir

    def _parse_bbox(self, raw_bbox: object) -> Optional[List[float]]:
        if raw_bbox is None or raw_bbox == "":
            return None
        if isinstance(raw_bbox, list):
            if len(raw_bbox) < 4:
                return None
            return [float(v) for v in raw_bbox[:4]]
        if isinstance(raw_bbox, str):
            try:
                parsed = ast.literal_eval(raw_bbox)
            except Exception:
                return None
            if isinstance(parsed, list) and len(parsed) >= 4:
                return [float(v) for v in parsed[:4]]
        return None

    def _compute_page_bbox_max(
        self, data: pd.DataFrame
    ) -> Dict[int, Tuple[float, float]]:
        page_bbox_max: Dict[int, Tuple[float, float]] = {}
        for _, row in data.iterrows():
            bbox = self._parse_bbox(row.get("bbox"))
            if not bbox:
                continue
            page_idx = int(row.get("page_idx", 0))
            _, _, x2, y2 = bbox
            max_x, max_y = page_bbox_max.get(page_idx, (0.0, 0.0))
            page_bbox_max[page_idx] = (max(max_x, x2), max(max_y, y2))
        return page_bbox_max

    def _get_heading_level(self, text: str) -> int:
        """根据标题文本识别层级"""
        text = text.strip()

        # 主章节: "1. 绪论", "2. 相关技术介绍"
        if re.match(r"^\d+\.\s+[\u4e00-\u9fff\w]+", text):
            return 1

        # 子章节: "1.1 研究背景和意义", "2.1 目标检测"
        if re.match(r"^\d+\.\d+\.\s+[\u4e00-\u9fff\w]+", text):
            return 2

        # 三级: "1.1.1 XXX", "3.2.1 XXX"
        if re.match(r"^\d+\.\d+\.\d+\.\s+", text):
            return 3

        # 三级: 列表项 "（1）精确率" 或 "(1) 精确率"
        if re.match(r"^[（(]\d+[)）]", text):
            return 3

        # 四级: "1) 召回率" (只有右括号)
        if re.match(r"^\d+\)\s", text):
            return 4

        return 1  # 默认一级

    def _infer_heading_level_from_text(self, text: str):
        """在未标注 Heading 时，根据文本模式推断标题层级"""
        if not text:
            return None
        cleaned = " ".join(str(text).strip().split())
        if not cleaned:
            return None
        # 避免误判超长正文
        if len(cleaned) > 80:
            return None

        if re.match(r"^第[一二三四五六七八九十百]+章", cleaned):
            return 1
        if re.match(r"^\d+\.\s+\S", cleaned):
            return 1
        if re.match(r"^\d+\.\d+\.\s+\S", cleaned):
            return 2
        if re.match(r"^\d+\.\d+\.\d+\.\s+\S", cleaned):
            return 3
        if re.match(r"^[（(]\d+[)）]\s*\S", cleaned):
            return 3
        if re.match(r"^\d+\)\s+\S", cleaned):
            return 4

        return None

    def build_from_pkl(self, data_path: str) -> DocIRBuildResult:
        data_file = os.path.join(data_path, "data.pkl")
        data = pd.read_pickle(data_file)

        image_count, table_count, para_count = 0, 0, 0
        root = ET.Element("Document")
        page_bbox_max = self._compute_page_bbox_max(data)
        page_image_cache: Dict[int, Image.Image] = {}
        page_images_dir = os.path.join(data_path, "page_images")
        table_images_dir = os.path.join(data_path, "table_images")
        pad_ratio = float(os.getenv("TABLE_CROP_PAD_RATIO", "0.002"))
        pad_px = int(os.getenv("TABLE_CROP_PAD_PX", "1"))
        header_footer_repeat_threshold = int(
            os.getenv("HEADER_FOOTER_REPEAT_PAGES", "3")
        )
        header_footer_top_bottom_n = int(os.getenv("HEADER_FOOTER_TOP_BOTTOM_N", "2"))
        header_footer_max_len = int(os.getenv("HEADER_FOOTER_MAX_LEN", "120"))
        header_regex = re.compile(
            r"(第\s*\d+\s*页|页码|学院|专业|指导教师|学校|论文|本科|毕业|\d{4}\s*年)",
            re.IGNORECASE,
        )
        footer_regex = re.compile(
            r"^(第\s*\d+\s*页|\d+\s*页|\d+\s*/\s*\d+|\d+)$",
            re.IGNORECASE,
        )

        def _is_table_caption(text: str) -> bool:
            cleaned = re.sub(r"\s+", " ", str(text)).strip()
            if not cleaned:
                return False
            return bool(
                re.match(
                    r"^(表|Table)\s*[\d一二三四五六七八九十]+[\d\.\-—]*\s*[:：]?\s*.+",
                    cleaned,
                    re.IGNORECASE,
                )
            )

        def _is_figure_caption(text: str) -> bool:
            cleaned = re.sub(r"\s+", " ", str(text)).strip()
            if not cleaned:
                return False
            return bool(
                re.match(
                    r"^(图|Fig\.?|Figure)\s*[\d一二三四五六七八九十]+[\d\.\-—]*\s*[:：]?\s*.+",
                    cleaned,
                    re.IGNORECASE,
                )
            )

        def _build_table_caption_index(
            df: pd.DataFrame,
        ) -> Dict[int, List[Tuple[int, str]]]:
            table_captions: Dict[int, List[Tuple[int, str]]] = {}
            for idx, row in df.iterrows():
                para = row.get("para_text", "")
                if not isinstance(para, str):
                    continue
                text = para.strip()
                if len(text) > 160:
                    continue
                if not _is_table_caption(text):
                    continue
                text = re.sub(r"\s+", " ", text).strip()
                page_idx = row.get("page_idx", 0)
                page_num = page_idx + 1
                table_captions.setdefault(page_num, []).append((idx, text))
            return table_captions

        def _build_figure_caption_index(
            df: pd.DataFrame,
        ) -> Dict[int, List[Tuple[int, str]]]:
            figure_captions: Dict[int, List[Tuple[int, str]]] = {}
            for idx, row in df.iterrows():
                para = row.get("para_text", "")
                if not isinstance(para, str):
                    continue
                text = para.strip()
                if len(text) > 160:
                    continue
                if not _is_figure_caption(text):
                    continue
                text = re.sub(r"\s+", " ", text).strip()
                page_idx = row.get("page_idx", 0)
                page_num = page_idx + 1
                figure_captions.setdefault(page_num, []).append((idx, text))
            return figure_captions

        def _find_nearest_table_caption(
            page_num: int,
            row_idx: int,
            table_captions: Dict[int, List[Tuple[int, str]]],
        ) -> Optional[str]:
            same_page = table_captions.get(page_num, [])
            if same_page:
                _, best_text = min(
                    ((abs(idx - row_idx), text) for idx, text in same_page),
                    key=lambda x: x[0],
                )
                return best_text
            candidates: List[Tuple[int, str]] = []
            for p in [page_num - 1, page_num + 1]:
                if p < 1:
                    continue
                for idx, text in table_captions.get(p, []):
                    score = abs(idx - row_idx) + 1000
                    candidates.append((score, text))
            if not candidates:
                return None
            best_score, best_text = min(candidates, key=lambda x: x[0])
            if best_score < 1200:
                return best_text
            return None

        def _find_nearest_figure_caption(
            page_num: int,
            row_idx: int,
            figure_captions: Dict[int, List[Tuple[int, str]]],
        ) -> Optional[str]:
            same_page = figure_captions.get(page_num, [])
            if same_page:
                _, best_text = min(
                    ((abs(idx - row_idx), text) for idx, text in same_page),
                    key=lambda x: x[0],
                )
                return best_text
            candidates: List[Tuple[int, str]] = []
            for p in [page_num - 1, page_num + 1]:
                if p < 1:
                    continue
                for idx, text in figure_captions.get(p, []):
                    score = abs(idx - row_idx) + 1000
                    candidates.append((score, text))
            if not candidates:
                return None
            best_score, best_text = min(candidates, key=lambda x: x[0])
            if best_score < 1200:
                return best_text
            return None

        def _normalize_header_footer_text(text: str) -> str:
            return re.sub(r"\s+", " ", str(text)).strip()

        def _build_header_footer_index(
            df: pd.DataFrame,
        ) -> Tuple[set, set, Dict[int, List[str]], Dict[int, List[str]]]:
            per_page_items: Dict[int, List[Tuple[Optional[float], str]]] = {}
            for _, row in df.iterrows():
                para = row.get("para_text", "")
                if not isinstance(para, str):
                    continue
                text = para.strip()
                if not text:
                    continue
                if len(text) > header_footer_max_len:
                    continue
                page_idx = row.get("page_idx", 0)
                page_num = page_idx + 1
                bbox = self._parse_bbox(row.get("bbox"))
                y1 = float(bbox[1]) if bbox else None
                per_page_items.setdefault(page_num, []).append((y1, text))

            top_by_page: Dict[int, List[str]] = {}
            bottom_by_page: Dict[int, List[str]] = {}
            for page_num, items in per_page_items.items():
                if not items:
                    continue
                if all(y is not None for y, _ in items):
                    items.sort(key=lambda x: x[0])  # top to bottom
                texts = [t for _, t in items]
                top_by_page[page_num] = texts[:header_footer_top_bottom_n]
                bottom_by_page[page_num] = (
                    texts[-header_footer_top_bottom_n:]
                    if len(texts) > header_footer_top_bottom_n
                    else texts
                )

            header_counts: Dict[str, int] = {}
            footer_counts: Dict[str, int] = {}
            for page_num in top_by_page:
                for text in set(top_by_page.get(page_num, [])):
                    norm = _normalize_header_footer_text(text)
                    if not norm:
                        continue
                    header_counts[norm] = header_counts.get(norm, 0) + 1
                for text in set(bottom_by_page.get(page_num, [])):
                    norm = _normalize_header_footer_text(text)
                    if not norm:
                        continue
                    footer_counts[norm] = footer_counts.get(norm, 0) + 1

            repeated_headers = {
                t
                for t, c in header_counts.items()
                if c >= header_footer_repeat_threshold
            }
            repeated_footers = {
                t
                for t, c in footer_counts.items()
                if c >= header_footer_repeat_threshold
            }

            header_regex_texts = {t for t in header_counts if header_regex.search(t)}
            footer_regex_texts = {t for t in footer_counts if footer_regex.search(t)}

            header_index = set(repeated_headers)
            header_index.update(header_regex_texts)
            footer_index = set(repeated_footers)
            footer_index.update(footer_regex_texts)
            # 避免页眉文本被当作页脚：若匹配页眉正则且不匹配页脚正则，则从 footer_index 中移除
            footer_index = {
                t
                for t in footer_index
                if not (header_regex.search(t) and not footer_regex.search(t))
            }
            return header_index, footer_index, top_by_page, bottom_by_page

        def _classify_header_footer_text(
            text: str,
            page_num: int,
            header_index: set,
            footer_index: set,
            top_by_page: Dict[int, List[str]],
            bottom_by_page: Dict[int, List[str]],
        ) -> Optional[str]:
            norm = _normalize_header_footer_text(text)
            if not norm:
                return None
            top_set = {
                _normalize_header_footer_text(t) for t in top_by_page.get(page_num, [])
            }
            bottom_set = {
                _normalize_header_footer_text(t)
                for t in bottom_by_page.get(page_num, [])
            }

            # 优先判定页眉：匹配页眉正则且不匹配页脚正则的文本，直接归为 Header
            if header_regex.search(norm) and not footer_regex.search(norm):
                return "header"
            if norm in footer_index and (
                norm in bottom_set or footer_regex.search(norm)
            ):
                return "footer"
            if norm in header_index and (norm in top_set or header_regex.search(norm)):
                return "header"
            if norm in bottom_set and footer_regex.search(norm):
                return "footer"
            if norm in top_set and header_regex.search(norm):
                return "header"
            return None

        # Section 栈: 存储当前打开的 Section 及其层级
        # 格式: [(section_node, section_id, level), ...]
        section_stack: List[Tuple[ET.Element, str, int]] = []

        # 顶层的section计数器
        top_section_count = 0

        section_dict: Dict[str, ET.Element] = {}
        image_path_dict: Dict[str, str] = {}
        table_image_path_dict: Dict[str, str] = {}

        sections = []
        blocks = []
        figures = []
        tables = []

        # 用于生成section_id的计数器
        section_counter = 0

        table_caption_index = _build_table_caption_index(data)
        figure_caption_index = _build_figure_caption_index(data)
        header_index, footer_index, header_top_by_page, header_bottom_by_page = (
            _build_header_footer_index(data)
        )

        def _append_header_footer(text: str, page_num: int, hf_type: str) -> None:
            target = section_stack[-1][0] if section_stack else root
            tag = "Header" if hf_type == "header" else "Footer"
            node = ET.SubElement(target, tag, page_num=str(page_num))
            node.text = str(text)

        print(f"[DocIRBuilder] Processing {len(data)} rows...")

        for index, row in data.iterrows():
            style = row["style"]
            page_idx = row.get("page_idx", 0)
            current_page = page_idx + 1

            # 跳过 Page_Start
            if style == "Page_Start":
                continue

            # 处理 Heading
            inferred_heading = None
            if not style.startswith("Heading") and isinstance(row["para_text"], str):
                inferred_heading = self._infer_heading_level_from_text(row["para_text"])
                if inferred_heading is not None:
                    style = f"Heading {inferred_heading}"

            if style.startswith("Heading"):
                heading_text = row["para_text"].strip()
                heading_level = (
                    inferred_heading
                    if inferred_heading is not None
                    else self._get_heading_level(heading_text)
                )
                # 若已识别为标题层级，则不进行页眉/页脚归类
                if heading_level is None:
                    header_footer_type = _classify_header_footer_text(
                        heading_text,
                        current_page,
                        header_index,
                        footer_index,
                        header_top_by_page,
                        header_bottom_by_page,
                    )
                    if header_footer_type:
                        _append_header_footer(
                            heading_text, current_page, header_footer_type
                        )
                        continue

                print(f"[Heading] Level {heading_level}: {heading_text[:50]}...")

                # 生成新的 section_id
                section_counter += 1
                current_section_id = str(section_counter)

                # 根据层级，找到父 Section
                # 1. 先关闭所有比当前层级高或相等的 Section
                while section_stack and section_stack[-1][2] >= heading_level:
                    section_stack.pop()

                # 2. 确定父节点
                if section_stack:
                    # 有父节点，添加到父节点中
                    parent_node = section_stack[-1][0]
                else:
                    # 没有父节点，直接添加到 root
                    parent_node = root

                # 3. 创建新的 Section
                current_section_node = ET.SubElement(
                    parent_node,
                    "Section",
                    section_id=current_section_id,
                    start_page_num=str(current_page),
                )

                # 添加 Heading
                heading = ET.SubElement(current_section_node, "Heading")
                heading.text = heading_text

                section_dict[current_section_id] = current_section_node

                # 将新 Section 压入栈
                section_stack.append(
                    (current_section_node, current_section_id, heading_level)
                )

                # 添加到 sections 列表
                sections.append(
                    SectionNode(
                        section_id=current_section_id,
                        title=heading_text,
                        level=heading_level,
                        start_page_num=current_page,
                    )
                )

                blocks.append(
                    TextBlock(
                        block_id=f"heading_{current_section_id}",
                        block_type=style,
                        text=heading_text,
                        page_num=current_page,
                        section_id=current_section_id,
                    )
                )

            # 处理 Header/Footer
            elif style in ["Header", "Footer"]:
                tag = "Header" if style == "Header" else "Footer"
                node = ET.SubElement(root, tag, page_num=str(current_page))
                node.text = str(row["para_text"])

                blocks.append(
                    TextBlock(
                        block_id=f"{tag.lower()}_{index}",
                        block_type=style,
                        text=str(row["para_text"]),
                        page_num=current_page,
                        section_id=None,
                    )
                )

            # 处理 Normal - 添加到当前最底层的 Section
            elif style in ["Normal", "Body Text", "List Paragraph", "Footnote"]:
                if not section_stack:
                    # 如果栈为空，创建一个默认 Section
                    section_counter += 1
                    current_section_id = str(section_counter)
                    current_section_node = ET.SubElement(
                        root,
                        "Section",
                        section_id=current_section_id,
                        start_page_num=str(current_page),
                    )
                    section_dict[current_section_id] = current_section_node
                    section_stack.append((current_section_node, current_section_id, 1))

                    sections.append(
                        SectionNode(
                            section_id=current_section_id,
                            title=f"Section {current_section_id}",
                            level=1,
                            start_page_num=current_page,
                        )
                    )

                # 获取当前最底层的 Section
                current_section_node, current_section_id, _ = section_stack[-1]

                content = row["para_text"]
                header_footer_type = _classify_header_footer_text(
                    content,
                    current_page,
                    header_index,
                    footer_index,
                    header_top_by_page,
                    header_bottom_by_page,
                )
                if header_footer_type:
                    _append_header_footer(content, current_page, header_footer_type)
                    continue
                para = ET.SubElement(
                    current_section_node, "Paragraph", page_num=str(current_page)
                )
                para.text = content
                para_count += 1

                blocks.append(
                    TextBlock(
                        block_id=f"para_{para_count}",
                        block_type=style,
                        text=content,
                        page_num=current_page,
                        section_id=current_section_id,
                    )
                )

            # 处理 Image
            elif style == "Image":
                if not section_stack:
                    continue  # Image 不能在没有 Section 的情况下存在

                # 获取当前最底层的 Section
                current_section_node, current_section_id, _ = section_stack[-1]

                item = row["para_text"]
                doc_id = os.path.basename(os.path.normpath(data_path))
                image = ET.SubElement(
                    current_section_node,
                    "Image",
                    image_id=str(image_count),
                    page_num=str(current_page),
                )
                image_path = None
                alt_text = None

                if isinstance(item, dict):
                    image_path = item.get("path")
                    alt_text = item.get("alt_text")
                elif isinstance(item, str) and item:
                    image_path = item
                else:
                    image_path, _ = self._resolve_image_path(
                        data_path, doc_id, image_count
                    )

                if image_path:
                    image_path_dict[str(image_count)] = os.path.basename(image_path)
                else:
                    image_path_dict[str(image_count)] = f"image_{image_count}.png"

                if alt_text:
                    alt_node = ET.SubElement(image, "Alt_Text")
                    alt_node.text = str(alt_text)

                figures.append(
                    FigureNode(
                        figure_id=str(image_count),
                        page_num=current_page,
                        image_path=image_path_dict[str(image_count)],
                        alt_text=str(alt_text) if alt_text else None,
                    )
                )
                image_count += 1

            # 处理 Caption
            elif style == "Caption":
                if section_stack:
                    current_section_node, current_section_id, _ = section_stack[-1]
                    caption_text = str(row["para_text"])
                    header_footer_type = _classify_header_footer_text(
                        caption_text,
                        current_page,
                        header_index,
                        footer_index,
                        header_top_by_page,
                        header_bottom_by_page,
                    )
                    if header_footer_type:
                        _append_header_footer(
                            caption_text, current_page, header_footer_type
                        )
                        continue
                    caption = ET.SubElement(current_section_node, "Caption")
                    caption.text = caption_text

                    blocks.append(
                        TextBlock(
                            block_id=f"caption_{index}",
                            block_type="Caption",
                            text=caption_text,
                            page_num=current_page,
                            section_id=current_section_id,
                        )
                    )

            # 处理 Table
            elif style == "Table":
                if not section_stack:
                    continue

                current_section_node, current_section_id, _ = section_stack[-1]
                table_image_path = None
                figure_image_path = None
                crop_box = None
                page_image = None
                bbox = self._parse_bbox(row.get("bbox"))
                if bbox and os.path.isdir(page_images_dir):
                    if page_idx not in page_image_cache:
                        page_filename = f"page_{page_idx:04d}.png"
                        page_path = os.path.join(page_images_dir, page_filename)
                        if os.path.exists(page_path):
                            page_image_cache[page_idx] = Image.open(page_path)
                    page_image = page_image_cache.get(page_idx)
                    if page_image:
                        max_x, max_y = page_bbox_max.get(
                            page_idx, (page_image.width, page_image.height)
                        )
                        scale_x = page_image.width / max_x if max_x else 1.0
                        scale_y = page_image.height / max_y if max_y else 1.0
                        x1, y1, x2, y2 = bbox
                        pad = max(
                            pad_px,
                            int(max(page_image.width, page_image.height) * pad_ratio),
                        )
                        left = max(0, int(x1 * scale_x) - pad)
                        top = max(0, int(y1 * scale_y) - pad)
                        right = min(page_image.width, int(x2 * scale_x) + pad)
                        bottom = min(page_image.height, int(y2 * scale_y) + pad)
                        if right > left and bottom > top:
                            crop_box = (left, top, right, bottom)

                # Table 的 para_text 可能是字典
                table_content = row["para_text"]
                table_alt_text = None
                if isinstance(table_content, dict):
                    table_alt_text = table_content.get("alt_text")
                if not table_alt_text:
                    table_alt_text = _find_nearest_table_caption(
                        current_page, index, table_caption_index
                    )

                figure_alt_text = _find_nearest_figure_caption(
                    current_page, index, figure_caption_index
                )

                # 仅当附近确实有“表”标题时才按表格处理，否则视为图片
                if not table_alt_text:
                    if crop_box and page_image:
                        figures_dir = os.path.join(data_path, "figures")
                        os.makedirs(figures_dir, exist_ok=True)
                        figure_filename = (
                            f"figure_{image_count:04d}_page_{current_page:04d}.png"
                        )
                        figure_path = os.path.join(figures_dir, figure_filename)
                        page_image.crop(crop_box).save(figure_path)
                        figure_image_path = figure_filename
                    if figure_image_path:
                        image_path_dict[str(image_count)] = figure_image_path
                    image = ET.SubElement(
                        current_section_node,
                        "Image",
                        image_id=str(image_count),
                        page_num=str(current_page),
                    )
                    if figure_alt_text:
                        alt_node = ET.SubElement(image, "Alt_Text")
                        alt_node.text = str(figure_alt_text)
                    figures.append(
                        FigureNode(
                            figure_id=str(image_count),
                            page_num=current_page,
                            image_path=image_path_dict.get(str(image_count)),
                            alt_text=str(figure_alt_text) if figure_alt_text else None,
                        )
                    )
                    image_count += 1
                    continue

                if crop_box and page_image:
                    os.makedirs(table_images_dir, exist_ok=True)
                    table_filename = (
                        f"table_{table_count:04d}_page_{current_page:04d}.png"
                    )
                    table_path = os.path.join(table_images_dir, table_filename)
                    page_image.crop(crop_box).save(table_path)
                    table_image_path = table_filename
                    table_image_path_dict[str(table_count)] = table_image_path

                table_attrs = {
                    "table_id": str(table_count),
                    "page_num": str(current_page),
                }
                if table_image_path:
                    table_attrs["image_path"] = table_image_path
                table = ET.SubElement(current_section_node, "CSV_Table", table_attrs)

                if isinstance(table_content, dict):
                    table.text = table_content.get("content", "")
                else:
                    table.text = str(table_content)

                if table_alt_text:
                    alt_node = ET.SubElement(table, "Alt_Text")
                    alt_node.text = str(table_alt_text)

                tables.append(
                    TableNode(
                        table_id=str(table_count),
                        page_num=current_page,
                        content=table.text,
                        image_path=table_image_path,
                        alt_text=str(table_alt_text) if table_alt_text else None,
                    )
                )
                table_count += 1

        # 为所有 Section 设置结束页码
        for section_node in root.iter("Section"):
            if section_node.get("end_page_num") is None:
                # 查找该 Section 中最后一个元素的页码
                last_page = "1"
                for elem in section_node.iter():
                    if elem.get("page_num"):
                        last_page = elem.get("page_num", "1")
                section_node.set("end_page_num", last_page)

        # 更新 sections 的结束页码
        for section in sections:
            if section.section_id in section_dict:
                end_page = section_dict[section.section_id].get("end_page_num")
                if end_page is not None:
                    section.end_page_num = int(float(end_page))

        # 构建 DocIR
        doc_id = os.path.basename(os.path.abspath(data_path))
        meta = DocIRMeta(
            doc_id=doc_id,
            source_type="pkl",
            page_count=current_page,
            input_files=[data_file],
        )
        doc_ir = DocIR(
            meta=meta,
            sections=sections,
            blocks=blocks,
            figures=figures,
            tables=tables,
        )

        print(
            f"[DocIRBuilder] Built {len(section_dict)} sections (nested) with {para_count} paragraphs"
        )

        return DocIRBuildResult(
            doc_ir=doc_ir,
            root=root,
            section_dict=section_dict,
            image_path_dict=image_path_dict,
            table_image_path_dict=table_image_path_dict,
            num_page=current_page,
            image_count=image_count,
            table_count=table_count,
            para_count=para_count,
        )
