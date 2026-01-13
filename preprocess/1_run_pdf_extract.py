"""
PDF 提取脚本 - pdfplumber 版
功能：本地解析 PDF，提取文本、字体名称、字体大小及坐标，用于格式一致性检查。
"""

import argparse
import glob
import logging
import os
import json

# 导入 pdfplumber
try:
    import pdfplumber
except ImportError:
    print("错误: 缺少必要库，请执行: pip install pdfplumber")
    exit(1)

# 初始化日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class PDFPlumberExtractor:
    def __init__(self):
        pass

    def extract_pdf(self, pdf_path, sid, result_dir):
        # 输出目录：./extract_output/{sid}/MinerU/ (保留原目录名以维持一致性)
        output_dir = os.path.join(result_dir, sid, "MinerU")
        os.makedirs(output_dir, exist_ok=True)

        output_json = os.path.join(output_dir, "middle.json")

        try:
            logger.info(f"[→] 正在使用 pdfplumber 解析: {os.path.basename(pdf_path)}")

            data_list = []

            with pdfplumber.open(pdf_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    # 提取该页的所有文本块（带有字体信息）
                    words = page.extract_words(
                        extra_attrs=["fontname", "size"], horizontal_ltr=True
                    )

                    for word in words:
                        item = {
                            "type": "text",
                            "text": word["text"],
                            "font_name": word["fontname"],
                            "font_size": round(word["size"], 2),
                            "bbox": [
                                round(float(word["x0"]), 2),
                                round(float(word["top"]), 2),
                                round(float(word["x1"]), 2),
                                round(float(word["bottom"]), 2),
                            ],
                            "page_idx": page_idx,
                        }
                        data_list.append(item)

            # 保存结果
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(data_list, f, ensure_ascii=False, indent=4)

            # 同时生成一个简单的 markdown 用于预览
            self._generate_preview_md(data_list, os.path.join(output_dir, "full.md"))

            logger.info(f"[✓] 文档 {sid} 处理完成，结果保存至 {output_json}")
            return True

        except Exception as e:
            logger.error(f"[✗] 解析异常: {e}")
            return False

    def _generate_preview_md(self, data_list, md_path):
        """生成一个简单的预览 Markdown"""
        with open(md_path, "w", encoding="utf-8") as f:
            current_page = -1
            for item in data_list:
                if item["page_idx"] != current_page:
                    current_page = item["page_idx"]
                    f.write(f"\n\n<!-- Page {current_page + 1} -->\n\n")

                # 根据字体大小简单判定是否可能是标题（例如 > 14pt）
                if item["font_size"] > 14:
                    f.write(f"### {item['text']} ")
                else:
                    f.write(f"{item['text']} ")


def main():
    parser = argparse.ArgumentParser(description="PDF 提取脚本 (pdfplumber)")
    parser.add_argument(
        "--raw-data-dir", default="../sample_data/", help="原始数据目录"
    )
    parser.add_argument(
        "--result-dir", default="./extract_output/", help="输出结果目录"
    )
    parser.add_argument("--doc-id", type=str, default=None, help="指定处理的文档 ID")
    args = parser.parse_args()

    os.makedirs(args.result_dir, exist_ok=True)
    extractor = PDFPlumberExtractor()

    search_path = os.path.join(args.raw_data_dir, "*")
    pdf_count = 0

    all_items = glob.glob(search_path)
    for item in all_items:
        if not os.path.isdir(item):
            continue
        sid = os.path.basename(item)
        if args.doc_id and sid != args.doc_id:
            continue

        # 查找PDF
        pdf_list = glob.glob(os.path.join(item, "*.pdf"))
        if not pdf_list:
            if os.path.exists(os.path.join(item, "document.pdf")):
                pdf_list = [os.path.join(item, "document.pdf")]
            else:
                continue

        pdf_count += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"[{pdf_count}] 处理文档: {sid}")
        logger.info(f"{'='*60}")
        extractor.extract_pdf(pdf_list[0], sid, args.result_dir)

    logger.info(f"\n[✓] 全部处理完成，共处理 {pdf_count} 个文档")


if __name__ == "__main__":
    main()
