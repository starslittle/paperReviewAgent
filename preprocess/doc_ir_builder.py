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

    def _bbox_to_str(self, bbox: Optional[List[float]]) -> Optional[str]:
        if not bbox or len(bbox) < 4:
            return None
        return ",".join(f"{v:.2f}" for v in bbox[:4])

    def _get_heading_level(self, text: str, has_triple_numbering: bool) -> int:
        """根据标题文本识别层级"""
        text = text.strip()

        # 主章节: "1. 绪论", "1 绪论", "2 相关技术介绍"
        if re.match(r"^\d+(\.)?\s+[\u4e00-\u9fff\w]+", text):
            return 1

        # 子章节: "1.1 研究背景和意义", "1.1. 研究背景和意义", "2.1 目标检测"
        if re.match(r"^\d+\.\d+(\.)?\s+[\u4e00-\u9fff\w]+", text):
            return 2

        # 三级: "1.1.1 XXX", "3.2.1 XXX"
        if re.match(r"^\d+\.\d+\.\d+(\.)?\s+", text):
            return 3

        # 三级: 列表项 "（1）精确率" 或 "(1) 精确率"
        if re.match(r"^[（(]\d+[)）]", text):
            return 4 if has_triple_numbering else 3

        # 四级: "1) 召回率" (只有右括号)
        if re.match(r"^\d+\)\s", text):
            return 5 if has_triple_numbering else 4

        return 1  # 默认一级

    def _infer_heading_level_from_text(self, text: str, has_triple_numbering: bool):
        """在未标注 Heading 时，根据文本模式推断标题层级"""
        if not text:
            return None
        cleaned = " ".join(str(text).strip().split())
        if not cleaned:
            return None
        # 避免误判超长正文
        if len(cleaned) > 35:
            return None

        # 句末标点通常表示正文句子
        if cleaned[-1] in "。；，.!?;,":
            return None

        # 过多逗号一般是正文
        comma_count = cleaned.count("，") + cleaned.count(",")
        if comma_count >= 2:
            return None

        if re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*章", cleaned):
            return 1
        if re.match(r"^\d+(\.)?\s+\S", cleaned):
            return 1
        if re.match(r"^\d+\.\d+(\.)?\s+\S", cleaned):
            return 2
        if re.match(r"^\d+\.\d+\.\d+(\.)?\s+\S", cleaned):
            return 3
        if re.match(r"^[（(]\d+[)）]\s*\S", cleaned):
            return 4 if has_triple_numbering else 3
        if re.match(r"^\d+\)\s+\S", cleaned):
            return 5 if has_triple_numbering else 4

        return None

    def _has_triple_numbering(self, data: pd.DataFrame) -> bool:
        for _, row in data.iterrows():
            para = row.get("para_text", "")
            if not isinstance(para, str):
                continue
            cleaned = para.strip()
            if not cleaned:
                continue
            if re.match(r"^\d+\.\d+\.\d+(\.)?\s+\S", cleaned):
                return True
        return False

    def build_from_pkl(self, data_path: str) -> DocIRBuildResult:
        data_file = os.path.join(data_path, "data.pkl")
        data = pd.read_pickle(data_file)
        has_triple_numbering = self._has_triple_numbering(data)

        image_count, table_count, para_count = 0, 0, 0
        root = ET.Element("Document")
        toc_title_tokens = {"目录", "contents", "tableofcontents"}

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

        def _normalize_caption_text(text: str) -> str:
            return re.sub(r"\s+", " ", str(text)).strip()

        def _build_table_caption_index(
            df: pd.DataFrame,
        ) -> Dict[int, List[Tuple[int, str, Optional[List[float]]]]]:
            table_captions: Dict[int, List[Tuple[int, str, Optional[List[float]]]]] = {}
            for idx, row in df.iterrows():
                para = row.get("para_text", "")
                if not isinstance(para, str):
                    continue
                text = para.strip()
                if len(text) > 160:
                    continue
                if not _is_table_caption(text):
                    continue
                text = _normalize_caption_text(text)
                bbox = self._parse_bbox(row.get("bbox"))
                page_idx = row.get("page_idx", 0)
                page_num = page_idx + 1
                table_captions.setdefault(page_num, []).append((idx, text, bbox))
            return table_captions

        def _build_figure_caption_index(
            df: pd.DataFrame,
        ) -> Dict[int, List[Tuple[int, str, Optional[List[float]]]]]:
            figure_captions: Dict[int, List[Tuple[int, str, Optional[List[float]]]]] = (
                {}
            )
            for idx, row in df.iterrows():
                para = row.get("para_text", "")
                if not isinstance(para, str):
                    continue
                text = para.strip()
                if len(text) > 160:
                    continue
                if not _is_figure_caption(text):
                    continue
                text = _normalize_caption_text(text)
                bbox = self._parse_bbox(row.get("bbox"))
                page_idx = row.get("page_idx", 0)
                page_num = page_idx + 1
                figure_captions.setdefault(page_num, []).append((idx, text, bbox))
            return figure_captions

        def _find_nearest_table_caption(
            page_num: int,
            row_idx: int,
            table_captions: Dict[int, List[Tuple[int, str, Optional[List[float]]]]],
        ) -> Optional[Tuple[str, Optional[List[float]]]]:
            same_page = table_captions.get(page_num, [])
            if same_page:
                _, best_text, best_bbox = min(
                    ((abs(idx - row_idx), text, bbox) for idx, text, bbox in same_page),
                    key=lambda x: x[0],
                )
                return best_text, best_bbox
            candidates: List[Tuple[int, str, Optional[List[float]]]] = []
            for p in [page_num - 1, page_num + 1]:
                if p < 1:
                    continue
                for idx, text, bbox in table_captions.get(p, []):
                    score = abs(idx - row_idx) + 1000
                    candidates.append((score, text, bbox))
            if not candidates:
                return None
            best_score, best_text, best_bbox = min(candidates, key=lambda x: x[0])
            if best_score < 1200:
                return best_text, best_bbox
            return None

        def _find_nearest_figure_caption(
            page_num: int,
            row_idx: int,
            figure_captions: Dict[int, List[Tuple[int, str, Optional[List[float]]]]],
        ) -> Optional[Tuple[str, Optional[List[float]]]]:
            same_page = figure_captions.get(page_num, [])
            if same_page:
                _, best_text, best_bbox = min(
                    ((abs(idx - row_idx), text, bbox) for idx, text, bbox in same_page),
                    key=lambda x: x[0],
                )
                return best_text, best_bbox
            candidates: List[Tuple[int, str, Optional[List[float]]]] = []
            for p in [page_num - 1, page_num + 1]:
                if p < 1:
                    continue
                for idx, text, bbox in figure_captions.get(p, []):
                    score = abs(idx - row_idx) + 1000
                    candidates.append((score, text, bbox))
            if not candidates:
                return None
            best_score, best_text, best_bbox = min(candidates, key=lambda x: x[0])
            if best_score < 1200:
                return best_text, best_bbox
            return None

        def _normalize_toc_title(text: str) -> str:
            return re.sub(r"\s+", "", str(text)).strip().lower()

        def _is_toc_title(text: str) -> bool:
            normalized = _normalize_toc_title(text)
            return normalized in toc_title_tokens

        def _is_toc_line(text: str) -> bool:
            cleaned = re.sub(r"\s+", " ", str(text)).strip()
            if not cleaned:
                return False
            if re.search(r"(\.{2,}|·{2,}|…{2,}|-{2,}|_{2,})", cleaned):
                return True
            if re.search(r"[\.·…]\s*\d{1,4}$", cleaned):
                return True
            if re.search(r"\s\d{1,4}$", cleaned) and re.match(
                r"^\d+(\.\d+)*\s+\S", cleaned
            ):
                return True
            return False

        def _should_end_toc(text: str, current_page: int, toc_start_page: int) -> bool:
            cleaned = re.sub(r"\s+", " ", str(text)).strip()
            if current_page <= toc_start_page:
                return False
            if _is_toc_line(cleaned):
                return False
            if re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*章", cleaned):
                return True
            if re.match(r"^\d+(\.)?\s+\S", cleaned):
                return True
            return False

        # Section 栈: 存储当前打开的 Section 及其层级
        # 格式: [(section_node, section_id, level), ...]
        section_stack: List[Tuple[ET.Element, str, int]] = []

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

        toc_mode = False
        toc_section_node: Optional[ET.Element] = None
        toc_section_id: Optional[str] = None
        toc_start_page = 0

        print(f"[DocIRBuilder] Processing {len(data)} rows...")

        for index, row in data.iterrows():
            style = row["style"]
            page_idx = row.get("page_idx", 0)
            current_page = page_idx + 1

            # 跳过 Page_Start
            if style == "Page_Start":
                continue

            # 处理页眉和页脚
            if style in ["Header", "Footer", "Discarded"]:
                # 确保有当前 Section
                if not section_stack:
                    # 没有 Section，创建一个默认的
                    section_counter += 1
                    current_section_id = str(section_counter)
                    current_section_node = ET.SubElement(
                        root,
                        "Section",
                        section_id=current_section_id,
                        level="0",
                        start_page_num=str(current_page),
                    )
                    section_stack.append((current_section_node, current_section_id, 0))
                    section_dict[current_section_id] = current_section_node

                current_section_node = section_stack[-1][0]

                # 创建 Header/Footer 节点
                if style == "Header":
                    header_node = ET.SubElement(
                        current_section_node, "Header", page_num=str(current_page)
                    )
                    header_node.text = str(row["para_text"] or "").strip()
                elif style == "Footer":
                    footer_node = ET.SubElement(
                        current_section_node, "Footer", page_num=str(current_page)
                    )
                    footer_node.text = str(row["para_text"] or "").strip()
                # Discarded 类型跳过（既不是明确的页眉，也不是页脚）

                index += 1
                continue

            # 处理 Heading
            inferred_heading = None
            if not style.startswith("Heading") and isinstance(row["para_text"], str):
                inferred_heading = self._infer_heading_level_from_text(
                    row["para_text"], has_triple_numbering
                )
                if inferred_heading is not None:
                    style = f"Heading {inferred_heading}"

            if style.startswith("Heading"):
                heading_text = row["para_text"].strip()
                if toc_mode and toc_section_node and toc_section_id:
                    if _should_end_toc(heading_text, current_page, toc_start_page):
                        toc_mode = False
                        toc_section_node = None
                        toc_section_id = None
                    else:
                        para = ET.SubElement(
                            toc_section_node, "Paragraph", page_num=str(current_page)
                        )
                        para.text = heading_text
                        para_count += 1
                        blocks.append(
                            TextBlock(
                                block_id=f"toc_{para_count}",
                                block_type="Normal",
                                text=heading_text,
                                page_num=current_page,
                                section_id=toc_section_id,
                            )
                        )
                        continue

                if _is_toc_title(heading_text):
                    while section_stack:
                        section_stack.pop()
                    section_counter += 1
                    current_section_id = str(section_counter)
                    toc_section_node = ET.SubElement(
                        root,
                        "Section",
                        section_id=current_section_id,
                        level="1",
                        start_page_num=str(current_page),
                    )
                    heading = ET.SubElement(toc_section_node, "Heading")
                    heading.text = heading_text
                    section_dict[current_section_id] = toc_section_node
                    section_stack.append((toc_section_node, current_section_id, 1))
                    sections.append(
                        SectionNode(
                            section_id=current_section_id,
                            title=heading_text,
                            level=1,
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
                    toc_mode = True
                    toc_section_id = current_section_id
                    toc_start_page = current_page
                    continue
                heading_level = (
                    inferred_heading
                    if inferred_heading is not None
                    else self._get_heading_level(heading_text, has_triple_numbering)
                )
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

                # 3. 创建新的 Section（添加 level 属性）
                current_section_node = ET.SubElement(
                    parent_node,
                    "Section",
                    section_id=current_section_id,
                    level=str(heading_level),
                    start_page_num=str(current_page),
                )

                # 添加 Heading
                heading = ET.SubElement(
                    current_section_node, "Heading", level=str(heading_level)
                )
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
                if toc_mode and toc_section_node and toc_section_id:
                    content = row["para_text"]
                    para = ET.SubElement(
                        toc_section_node, "Paragraph", page_num=str(current_page)
                    )
                    para.text = content
                    para_count += 1
                    blocks.append(
                        TextBlock(
                            block_id=f"toc_{para_count}",
                            block_type="Normal",
                            text=content,
                            page_num=current_page,
                            section_id=toc_section_id,
                        )
                    )
                    continue
                if not section_stack:
                    # 如果栈为空，创建一个默认 Section
                    section_counter += 1
                    current_section_id = str(section_counter)
                    current_section_node = ET.SubElement(
                        root,
                        "Section",
                        section_id=current_section_id,
                        level="1",
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
                image_bbox = self._parse_bbox(row.get("bbox"))
                image = ET.SubElement(
                    current_section_node,
                    "Image",
                    image_id=str(image_count),
                    page_num=str(current_page),
                )
                if image_bbox:
                    bbox_str = self._bbox_to_str(image_bbox)
                    if bbox_str:
                        image.set("bbox", bbox_str)
                image_path = None
                alt_text = None
                alt_bbox = None

                if isinstance(item, dict):
                    image_path = item.get("path")
                    alt_text = item.get("alt_text")
                elif isinstance(item, str) and item:
                    image_path = item
                else:
                    image_path, _ = self._resolve_image_path(
                        data_path, doc_id, image_count
                    )

                # 如果没有 alt_text 或需要 bbox，尝试从最近的 figure caption 获取
                nearest_figure = _find_nearest_figure_caption(
                    current_page, index, figure_caption_index
                )
                if nearest_figure:
                    nearest_text, nearest_bbox = nearest_figure
                    if alt_text:
                        if _normalize_caption_text(
                            nearest_text
                        ) == _normalize_caption_text(alt_text):
                            alt_bbox = nearest_bbox
                    else:
                        alt_text = nearest_text
                        alt_bbox = nearest_bbox

                if image_path:
                    filename = os.path.basename(image_path)
                    image_path_dict[str(image_count)] = filename
                    image.set("image_path", filename)
                else:
                    image_path_dict[str(image_count)] = f"image_{image_count}.png"

                if alt_text:
                    # 手动添加换行和缩进，使 Alt_Text 换行显示
                    image.text = "\n          "  # 10 个空格缩进
                    alt_node = ET.SubElement(image, "Alt_Text")
                    alt_node.text = str(alt_text)
                    if alt_bbox:
                        bbox_str = self._bbox_to_str(alt_bbox)
                        if bbox_str:
                            alt_node.set("bbox", bbox_str)
                    alt_node.tail = "\n        "  # 8 个空格，回到 Image 标签的缩进级别

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
                table_bbox = self._parse_bbox(row.get("bbox"))

                # Table 的 para_text 可能是字典
                table_content = row["para_text"]
                table_alt_text = None
                table_alt_bbox = None
                if isinstance(table_content, dict):
                    table_alt_text = table_content.get("alt_text")
                    raw_image_path = table_content.get("image_path")
                    if raw_image_path:
                        table_image_path = raw_image_path

                # Fallback: 如果 MinerU 未提供 caption，尝试查找最近的表题
                if not table_alt_text:
                    nearest_table = _find_nearest_table_caption(
                        current_page, index, table_caption_index
                    )
                    if nearest_table:
                        table_alt_text, table_alt_bbox = nearest_table
                else:
                    nearest_table = _find_nearest_table_caption(
                        current_page, index, table_caption_index
                    )
                    if nearest_table:
                        nearest_text, nearest_bbox = nearest_table
                        if _normalize_caption_text(
                            nearest_text
                        ) == _normalize_caption_text(table_alt_text):
                            table_alt_bbox = nearest_bbox

                # type=table 就是表格，不再判断"是否有表题"
                if table_image_path:
                    table_image_path_dict[str(table_count)] = table_image_path

                table_attrs = {
                    "table_id": str(table_count),
                    "page_num": str(current_page),
                }
                if table_bbox:
                    bbox_str = self._bbox_to_str(table_bbox)
                    if bbox_str:
                        table_attrs["bbox"] = bbox_str
                if table_image_path:
                    table_attrs["image_path"] = table_image_path
                table = ET.SubElement(current_section_node, "Table", table_attrs)

                # 表格内容作为 text
                if isinstance(table_content, dict):
                    table.text = table_content.get("content", "")
                else:
                    table.text = str(table_content)

                # Alt_Text 作为子标签（与 Image 对齐）
                # 手动添加换行和缩进，使 Alt_Text 换行显示
                if table_alt_text:
                    # 在 table.text 后添加换行
                    if table.text:
                        table.text = table.text + "\n          "  # 10 个空格缩进
                    else:
                        table.text = "\n          "
                    alt_node = ET.SubElement(table, "Alt_Text")
                    alt_node.text = str(table_alt_text)
                    if table_alt_bbox:
                        bbox_str = self._bbox_to_str(table_alt_bbox)
                        if bbox_str:
                            alt_node.set("bbox", bbox_str)
                    alt_node.tail = "\n        "  # 8 个空格，回到 Table 标签的缩进级别

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
