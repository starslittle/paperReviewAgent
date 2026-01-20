"""
嵌套结构版 DocIRBuilder - 基于内容模式识别标题层级并构建父子关系
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
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

    def _get_heading_level(self, text: str) -> int:
        """根据标题文本识别层级"""
        text = text.strip()

        # 主章节: "1. 绪论", "2. 相关技术介绍"
        if re.match(r'^\d+\.\s+[\u4e00-\u9fff\w]+', text):
            return 1

        # 子章节: "1.1 研究背景和意义", "2.1 目标检测"
        if re.match(r'^\d+\.\d+\.\s+[\u4e00-\u9fff\w]+', text):
            return 2

        # 三级: "1.1.1 XXX", "3.2.1 XXX"
        if re.match(r'^\d+\.\d+\.\d+\.\s+', text):
            return 3

        # 三级: 列表项 "（1）精确率" 或 "(1) 精确率"
        if re.match(r'^[（(]\d+[)）]', text):
            return 3

        # 四级: "1) 召回率" (只有右括号)
        if re.match(r'^\d+\)\s', text):
            return 4

        return 1  # 默认一级

    def build_from_pkl(self, data_path: str) -> DocIRBuildResult:
        data_file = os.path.join(data_path, "data.pkl")
        data = pd.read_pickle(data_file)

        image_count, table_count, para_count = 0, 0, 0
        root = ET.Element("Document")

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

        print(f"[DocIRBuilder] Processing {len(data)} rows...")

        for index, row in data.iterrows():
            style = row["style"]
            page_idx = row.get("page_idx", 0)
            current_page = page_idx + 1

            # 跳过 Page_Start
            if style == "Page_Start":
                continue

            # 处理 Heading
            if style.startswith("Heading"):
                heading_text = row["para_text"].strip()
                heading_level = self._get_heading_level(heading_text)

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
                section_stack.append((current_section_node, current_section_id, heading_level))

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
                para = ET.SubElement(
                    current_section_node,
                    "Paragraph",
                    page_num=str(current_page)
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
                image = ET.SubElement(
                    current_section_node,
                    "Image",
                    image_id=str(image_count),
                    page_num=str(current_page),
                )
                image_path_dict[str(image_count)] = os.path.basename(item["path"])

                alt_text = item.get("alt_text")
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
                    caption = ET.SubElement(current_section_node, "Caption")
                    caption.text = str(row["para_text"])

                    blocks.append(
                        TextBlock(
                            block_id=f"caption_{index}",
                            block_type="Caption",
                            text=str(row["para_text"]),
                            page_num=current_page,
                            section_id=current_section_id,
                        )
                    )

            # 处理 Table
            elif style == "Table":
                if not section_stack:
                    continue

                current_section_node, current_section_id, _ = section_stack[-1]

                table = ET.SubElement(
                    current_section_node,
                    "CSV_Table",
                    table_id=str(table_count),
                    page_num=str(current_page),
                )

                # Table 的 para_text 可能是字典
                table_content = row["para_text"]
                if isinstance(table_content, dict):
                    table.text = table_content.get("content", "")
                else:
                    table.text = str(table_content)

                tables.append(
                    TableNode(
                        table_id=str(table_count),
                        page_num=current_page,
                        content=table.text,
                        image_path=None,
                    )
                )
                table_count += 1

        # 为所有 Section 设置结束页码
        for section_node in root.iter('Section'):
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

        print(f"[DocIRBuilder] Built {len(section_dict)} sections (nested) with {para_count} paragraphs")

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
