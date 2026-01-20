"""
Word文档提取脚本
功能：解析Word文档内容，提取文本、样式、字体信息，为后续格式检查做准备。
"""

import argparse
import glob
import logging
import os
import json

# 导入必要库
try:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.document import Document as _Document
    from docx.table import _Cell
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
except ImportError:
    print("错误: 缺少必要库，请执行: pip install python-docx")
    exit(1)

# 初始化日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DocxExtractor:
    """Word 文档提取器"""

    def __init__(self):
        pass

    def _iter_block_items(self, parent):
        if isinstance(parent, _Document):
            parent_elm = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elm = parent._tc
        else:
            raise ValueError("Unsupported parent type")

        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    def extract(self, docx_path, sid, result_dir):
        # 输出到docx子目录
        output_dir = os.path.join(result_dir, sid, "docx")
        os.makedirs(output_dir, exist_ok=True)
        output_json = os.path.join(output_dir, "middle.json")

        try:
            logger.info(f"[→] 正在使用 python-docx 解析: {os.path.basename(docx_path)}")
            doc = Document(docx_path)
            doc_info = []

            for item in self._iter_block_items(doc):
                if isinstance(item, Paragraph):
                    para = item
                    if not para.text.strip():
                        continue

                    style_name = para.style.name
                    font_name = None
                    font_size = None
                    if para.runs:
                        run = para.runs[0]
                        font_name = run.font.name
                        if run.font.size:
                            font_size = run.font.size.pt

                    if not font_size and para.style.font.size:
                        font_size = para.style.font.size.pt
                    if not font_name and para.style.font.name:
                        font_name = para.style.font.name

                    etype = "text"
                    level = 0
                    if "Heading" in style_name:
                        etype = "heading"
                        try:
                            # 尝试提取 Heading 1 中的数字
                            level_part = style_name.split()[-1]
                            if level_part.isdigit():
                                level = int(level_part)
                            else:
                                level = 1
                        except (ValueError, IndexError):
                            level = 1
                    elif "Title" in style_name:
                        etype = "title"
                        level = 1
                    elif "Caption" in style_name:
                        etype = "caption"

                    doc_info.append(
                        {
                            "type": etype,
                            "content": para.text.strip(),
                            "style_name": style_name,
                            "font_family": font_name,
                            "font_size": font_size,
                            "level": level,
                            "bbox": None,
                            "page_idx": 0,
                        }
                    )
                elif isinstance(item, Table):
                    # 简单处理表格：将所有单元格文本合并
                    table_text = []
                    for row in item.rows:
                        row_text = [cell.text.strip() for cell in row.cells]
                        table_text.append(" | ".join(row_text))

                    doc_info.append(
                        {
                            "type": "table",
                            "content": "\n".join(table_text),
                            "style_name": "Table",
                            "font_family": None,
                            "font_size": None,
                            "bbox": None,
                            "page_idx": 0,
                        }
                    )

            with open(output_json, "w", encoding="utf-8") as f:
                json.dump({"pdf_info": doc_info}, f, ensure_ascii=False, indent=4)

            logger.info(f"[✓] 文档 {sid} 处理完成")
            return True

        except Exception as e:
            logger.error(f"[✗] Docx 解析异常: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False


def main():
    parser = argparse.ArgumentParser(description="Word文档预处理提取工具")
    parser.add_argument(
        "--raw-data-dir", default="../sample_data/", help="原始数据目录"
    )
    parser.add_argument(
        "--result-dir", default="./extract_output/", help="输出结果目录"
    )
    parser.add_argument("--doc-id", type=str, default=None, help="指定处理的文档 ID")
    args = parser.parse_args()

    os.makedirs(args.result_dir, exist_ok=True)

    docx_extractor = DocxExtractor()

    search_path = os.path.join(args.raw_data_dir, "*")
    doc_count = 0

    all_items = glob.glob(search_path)
    for item in all_items:
        if not os.path.isdir(item):
            continue
        sid = os.path.basename(item)
        if args.doc_id and sid != args.doc_id:
            continue

        # 只查找 DOCX 文件
        docx_list = glob.glob(os.path.join(item, "*.docx"))

        if docx_list:
            doc_count += 1
            logger.info(f"\n[{doc_count}] 处理 Word 文档: {sid}")
            docx_extractor.extract(docx_list[0], sid, args.result_dir)

    logger.info(f"\n[✓] 全部处理完成，共处理 {doc_count} 个文档")


if __name__ == "__main__":
    main()
