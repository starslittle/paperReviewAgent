from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
import os
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
    def __init__(self, max_section_depth: int = 10):
        self.max_section_depth = max_section_depth

    def build_from_pkl(self, data_path: str) -> DocIRBuildResult:
        data_file = os.path.join(data_path, "data.pkl")
        data = pd.read_pickle(data_file)

        image_count, table_count, para_count = 0, 0, 0
        root = ET.Element("Document")
        prev_node = root
        prev_heading_num = 0
        stack = [(prev_node, prev_heading_num)]

        section_dict: Dict[str, ET.Element] = {}
        image_path_dict: Dict[str, str] = {}
        table_image_path_dict: Dict[str, str] = {}

        prev_section_id = ""
        num_page = len(list(os.scandir(os.path.join(data_path, "page_images"))))

        sections = []
        blocks = []
        figures = []
        tables = []

        index = 0
        curr_page_num = 1

        if not data.iloc[0]["style"].startswith("Heading"):
            curr_section_id = "1"
            curr_node = ET.SubElement(
                prev_node,
                "Section",
                section_id=curr_section_id,
                start_page_num=str(curr_page_num),
            )
            section_dict[curr_section_id] = curr_node
            stack.append([curr_node, 1])
            prev_section_id = curr_section_id
            prev_node = curr_node
            sections.append(
                SectionNode(
                    section_id=curr_section_id,
                    title=f"Section {curr_section_id}",
                    level=1,
                    start_page_num=curr_page_num,
                )
            )

        while index < len(data):
            row = data.iloc[index]

            if row["style"].startswith("Heading"):
                curr_heading_num = int(row["style"].split()[1])
                while curr_heading_num < stack[-1][1]:
                    stack[-1][0].set("end_page_num", str(curr_page_num))
                    stack.pop()
                    prev_section_id_list = prev_section_id.split(".")
                    prev_section_id = ".".join(prev_section_id_list[:-1])

                if curr_heading_num == stack[-1][1]:
                    stack[-1][0].set("end_page_num", str(curr_page_num))
                    curr_section_id_list = prev_section_id.split(".")
                    curr_section_id_list[-1] = str(int(curr_section_id_list[-1]) + 1)
                    curr_section_id = ".".join(curr_section_id_list)
                    prev_node = stack[-2][0]

                    curr_node = ET.SubElement(
                        prev_node,
                        "Section",
                        section_id=curr_section_id,
                        start_page_num=str(curr_page_num),
                    )
                    section_dict[curr_section_id] = curr_node
                    heading = ET.SubElement(curr_node, "Heading")
                    heading.text = row["para_text"].strip()

                    stack[-1][0] = curr_node

                else:
                    if len(stack) <= self.max_section_depth:
                        prev_node = stack[-1][0]
                        curr_section_id = prev_section_id + ".1"
                        curr_node = ET.SubElement(
                            prev_node,
                            "Section",
                            section_id=curr_section_id,
                            start_page_num=str(curr_page_num),
                        )
                        section_dict[curr_section_id] = curr_node
                        heading = ET.SubElement(curr_node, "Heading")
                        heading.text = row["para_text"].strip()

                        stack.append([curr_node, curr_heading_num])
                    else:
                        content = row["para_text"]
                        while index + 1 < len(data) and data.iloc[index + 1]["style"] in [
                            "Normal",
                            "Body Text",
                            "List Paragraph",
                            "Footnote",
                        ]:
                            index += 1
                            content = content + " " + data.iloc[index]["para_text"]

                        para = ET.SubElement(
                            prev_node, "Paragraph", page_num=str(curr_page_num)
                        )
                        para.text = content

                        para_count += 1
                        blocks.append(
                            TextBlock(
                                block_id=f"para_{para_count}",
                                block_type="Paragraph",
                                text=content,
                                page_num=curr_page_num,
                                section_id=prev_section_id or None,
                            )
                        )
                        index += 1
                        continue

                prev_section_id = curr_section_id
                prev_node = curr_node

                sections.append(
                    SectionNode(
                        section_id=curr_section_id,
                        title=row["para_text"].strip(),
                        level=curr_heading_num,
                        start_page_num=curr_page_num,
                    )
                )

                blocks.append(
                    TextBlock(
                        block_id=f"heading_{curr_section_id}",
                        block_type=row["style"],
                        text=row["para_text"].strip(),
                        page_num=curr_page_num,
                        section_id=curr_section_id,
                    )
                )

            elif row["style"] in ["Normal", "Body Text", "List Paragraph", "Footnote"]:
                curr_style = row["style"]
                content = row["para_text"]
                while (
                    index + 1 < len(data)
                    and data.iloc[index + 1]["style"] == curr_style
                ):
                    index += 1
                    content = content + " " + data.iloc[index]["para_text"]

                para = ET.SubElement(
                    prev_node, "Paragraph", page_num=str(curr_page_num)
                )
                para.text = content
                para_count += 1

                blocks.append(
                    TextBlock(
                        block_id=f"para_{para_count}",
                        block_type=curr_style,
                        text=content,
                        page_num=curr_page_num,
                        section_id=prev_section_id or None,
                    )
                )

            elif row["style"] == "Image":
                item = row["para_text"]
                image = ET.SubElement(
                    prev_node,
                    "Image",
                    image_id=str(image_count),
                    page_num=str(curr_page_num),
                )
                image_path_dict[str(image_count)] = os.path.basename(item["path"])

                alt_text = None
                if item["alt_text"] is not None:
                    alt_text = str(item["alt_text"])
                    alt_node = ET.SubElement(image, "Alt_Text")
                    alt_node.text = alt_text

                figures.append(
                    FigureNode(
                        figure_id=str(image_count),
                        page_num=curr_page_num,
                        image_path=image_path_dict[str(image_count)],
                        alt_text=alt_text,
                    )
                )
                image_count += 1

            elif row["style"] == "Caption":
                prev_row = data.iloc[index - 1]
                if prev_row["style"] == "Image":
                    caption = ET.SubElement(image, "Caption")
                    if figures:
                        figures[-1].caption = str(row["para_text"])
                else:
                    caption = ET.SubElement(prev_node, "Caption")
                caption.text = str(row["para_text"])

                blocks.append(
                    TextBlock(
                        block_id=f"caption_{index}",
                        block_type="Caption",
                        text=str(row["para_text"]),
                        page_num=curr_page_num,
                        section_id=prev_section_id or None,
                    )
                )

            elif row["style"] == "Table":
                if len(row["para_text"]) == 0 or "content" not in row["para_text"]:
                    index += 1
                    continue
                table = ET.SubElement(
                    prev_node,
                    "CSV_Table",
                    table_id=str(table_count),
                    page_num=str(curr_page_num),
                )
                table.text = row["para_text"]["content"]
                if "image_path" in row["para_text"]:
                    table_image_path_dict[str(table_count)] = row["para_text"]["image_path"]

                tables.append(
                    TableNode(
                        table_id=str(table_count),
                        page_num=curr_page_num,
                        content=row["para_text"]["content"],
                        image_path=table_image_path_dict.get(str(table_count)),
                    )
                )
                table_count += 1

            elif row["style"] == "Page_Start":
                curr_page_num = row["table_id"]

            elif row["style"] == "Title":
                content = row["para_text"]
                para = ET.SubElement(prev_node, "Title", page_num=str(curr_page_num))
                para.text = content
                blocks.append(
                    TextBlock(
                        block_id=f"title_{index}",
                        block_type="Title",
                        text=content,
                        page_num=curr_page_num,
                        section_id=prev_section_id or None,
                    )
                )
            else:
                raise Exception(f"Uncovered style: {row['style']}")

            index += 1

        for item in stack:
            if item[0].tag == "Section":
                item[0].set("end_page_num", str(curr_page_num))

        for section in sections:
            if section.section_id in section_dict:
                end_page = section_dict[section.section_id].get("end_page_num")
                if end_page is not None:
                    section.end_page_num = int(float(end_page))

        doc_id = os.path.basename(os.path.abspath(data_path))
        meta = DocIRMeta(
            doc_id=doc_id,
            source_type="pkl",
            page_count=num_page,
            input_files=[data_file],
        )
        doc_ir = DocIR(
            meta=meta,
            sections=sections,
            blocks=blocks,
            figures=figures,
            tables=tables,
        )

        return DocIRBuildResult(
            doc_ir=doc_ir,
            root=root,
            section_dict=section_dict,
            image_path_dict=image_path_dict,
            table_image_path_dict=table_image_path_dict,
            num_page=num_page,
            image_count=image_count,
            table_count=table_count,
            para_count=para_count,
        )
