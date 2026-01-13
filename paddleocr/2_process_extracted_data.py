import argparse
import csv
import glob
import io
import json
import os
import re
import shutil
import zipfile

import openpyxl
import pandas as pd

# 尝试导入 OCR 模块（可选依赖）
try:
    from ocr_heading_detector import OCRHeadingDetector, compare_with_adobe_extraction

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[Warning] PaddleOCR 未安装，OCR 增强功能不可用")

parser = argparse.ArgumentParser(description="Process extracted data")
parser.add_argument(
    "--extract-data-dir",
    type=str,
    default="./extract_output/",
    help="Extracted data directory",
)
parser.add_argument(
    "--save-dir",
    type=str,
    default="./processed_output/",
    help="Directory to save results",
)
parser.add_argument(
    "--raw-data-dir",
    type=str,
    default="../sample_data/",
    help="Raw PDF data directory (for OCR enhancement)",
)
parser.add_argument(
    "--use-ocr",
    action="store_true",
    help="Use PaddleOCR to enhance heading detection",
)
parser.add_argument(
    "--ocr-max-pages",
    type=int,
    default=50,
    help="Maximum pages to process with OCR (default: 50)",
)
parser.add_argument(
    "--use-gpu",
    action="store_true",
    help="Use GPU for OCR (if available)",
)
parser.add_argument(
    "--doc-id",
    type=str,
    default=None,
    help="Specific document ID to process (directory name)",
)

args = parser.parse_args()


def get_xlsx_content(file_path):
    workbook = openpyxl.load_workbook(file_path, read_only=True)
    worksheet = workbook.active
    output = io.StringIO()

    csv_writer = csv.writer(output)
    for row in worksheet.iter_rows(values_only=True):
        csv_writer.writerow(row)

    output_str = output.getvalue()

    output_str = (
        output_str.replace(" _x000D_", "").replace("_x000D_", "").replace("\r\n", "\n")
    )
    return output_str


def json2df(root_path):

    def add_data(style, item_id, data):
        style_list.append(style)
        id_list.append(item_id)
        data_list.append(data)

    with open(os.path.join(root_path, "structuredData.json")) as f:
        data = json.load(f)

    style_list, id_list, data_list = [], [], []

    curr_page = 1
    image_count, table_count = 1, 1

    add_data("Page_Start", 1, None)

    for item in data["elements"]:
        # clean the item
        if "Text" in item:
            item["Text"] = item["Text"].replace("�", "")
            item["Text"] = item["Text"].replace("", "")

        if (
            "Page" in item and (item["Page"] + 1) > curr_page
        ):  # Page attribute is 0-indexed
            curr_page = item["Page"] + 1
            add_data("Page_Start", str(curr_page), None)

        if "/Table" in item["Path"]:
            if "filePaths" in item:
                table_data = {}
                for file_path in item["filePaths"]:
                    if file_path[-4:] == "xlsx":
                        table_content = get_xlsx_content(
                            os.path.join(root_path, file_path)
                        )
                        table_data["content"] = table_content
                    else:  # image
                        table_data["image_path"] = file_path

                add_data("Table", table_count, table_data)
                table_count += 1

        elif "/Figure" in item["Path"]:
            if "filePaths" in item:
                for file_path in item["filePaths"]:
                    image_data = {"path": file_path}
                    if "alternate_text" in item:
                        image_data["alt_text"] = item["alternate_text"]
                    else:
                        image_data["alt_text"] = None
                    add_data("Image", image_count, image_data)
                    image_count += 1

            elif "Text" in item:
                add_data("Caption", None, item["Text"])

        elif re.search(r"/H(\d+)", item["Path"]) and "Text" in item:

            heading_num = re.findall(r"/H(\d+)", rf"{item['Path']}")[0]
            heading_name = f"Heading {heading_num}"
            if style_list[-1] == heading_name:
                data_list[-1] += " " + item["Text"]
            else:
                add_data(heading_name, None, item["Text"])

        elif "/P" in item["Path"] and "Text" in item:
            add_data("Normal", None, item["Text"])

        elif "/Footnote" in item["Path"] and "Text" in item:
            add_data("Footnote", None, item["Text"])

        elif "/LBody" in item["Path"] and "Text" in item:
            add_data("List Paragraph", None, item["Text"])

        elif "/Title" in item["Path"]:
            add_data("Title", None, item["Text"])

    df = pd.DataFrame(
        {"para_text": data_list, "table_id": id_list, "style": style_list}
    )
    return df


def enhance_headings_with_ocr(df, pdf_path, ocr_detector, max_pages=50):
    """
    使用 PaddleOCR 增强标题检测

    Args:
        df: Adobe 提取的数据框
        pdf_path: 原始 PDF 路径
        ocr_detector: OCR 检测器实例
        max_pages: 最多处理的页数

    Returns:
        修正后的数据框
    """
    if not os.path.exists(pdf_path):
        print(f"[OCR] PDF 文件不存在: {pdf_path}")
        return df

    print(f"[OCR] 开始 OCR 增强处理: {os.path.basename(pdf_path)}")

    # 1. 使用 OCR 检测标题
    ocr_headings = ocr_detector.detect_headings_from_pdf(pdf_path, max_pages=max_pages)

    # 2. 对比 OCR 和 Adobe 结果
    comparison = compare_with_adobe_extraction(ocr_headings, df)

    # 3. 打印统计信息
    stats = comparison["stats"]
    print(
        f"[OCR] 统计: OCR检测={stats['ocr_total']}, Adobe识别={stats['adobe_total']}, "
        f"匹配={stats['matched_count']}, OCR独有={stats['ocr_only_count']}, "
        f"Adobe独有={stats['adobe_only_count']}"
    )

    # 4. 应用修正建议
    corrections_applied = 0

    # 4.1 添加 OCR 检测到但 Adobe 遗漏的标题
    for suggestion in comparison["suggestions"]:
        if suggestion["action"] == "add":
            text = suggestion["text"]
            level = suggestion["level"]
            page = suggestion["page"]

            # 在数据框中查找对应的文本行
            # 策略：找到该页的文本，匹配最相似的
            page_mask = df["style"] == f"Page_Start"
            page_indices = df[page_mask].index.tolist()

            # 找到该页范围
            if page in [int(df.at[i, "table_id"]) for i in page_indices]:
                page_idx = page_indices[
                    [int(df.at[i, "table_id"]) for i in page_indices].index(page)
                ]

                # 在该页后面查找匹配的文本
                next_page_idx = (
                    page_indices[page_indices.index(page_idx) + 1]
                    if page_indices.index(page_idx) + 1 < len(page_indices)
                    else len(df)
                )

                for idx in range(page_idx + 1, next_page_idx):
                    row_text = str(df.at[idx, "para_text"]).strip()

                    # 模糊匹配（允许一些差异）
                    if text in row_text or row_text in text:
                        if df.at[idx, "style"] in ["Normal", "Body Text"]:
                            old_style = df.at[idx, "style"]
                            df.at[idx, "style"] = f"Heading {level}"
                            corrections_applied += 1
                            print(
                                f"[OCR修正] 第{idx}行: {old_style} → Heading {level}: {row_text[:40]}..."
                            )
                            break

    # 4.2 降级明显错误的标题（Adobe 识别但 OCR 未检测到的，且不像标题）
    for h in comparison["adobe_only"]:
        text = h["text"]

        # 如果文本很长（>80字符），且没有章节编号，可能是误识别
        if (
            len(text) > 80
            and not re.match(r"^\d+\.", text)
            and not re.match(r"^第.*章", text)
        ):
            # 在数据框中查找并降级
            for idx in df.index:
                if df.at[idx, "para_text"] == text and df.at[idx, "style"].startswith(
                    "Heading"
                ):
                    old_style = df.at[idx, "style"]
                    df.at[idx, "style"] = "Normal"
                    corrections_applied += 1
                    print(
                        f"[OCR修正] 第{idx}行降级: {old_style} → Normal: {text[:40]}..."
                    )
                    break

    print(f"[OCR] 共应用 {corrections_applied} 处修正")

    # 5. 保存对比报告
    return df


def main(args):

    os.makedirs(args.save_dir, exist_ok=True)

    # 初始化 OCR 检测器（如果启用）
    ocr_detector = None
    if args.use_ocr:
        if not OCR_AVAILABLE:
            print("[Error] 启用了 --use-ocr 但 PaddleOCR 未安装")
            print("[Info] 请运行: pip install paddlepaddle paddleocr")
            return

        print("[OCR] 初始化 PaddleOCR...")
        # 新版本 PaddleOCR 自动检测 CPU/GPU，不需要传递 use_gpu 参数
        ocr_detector = OCRHeadingDetector()
        print("[OCR] 初始化完成")

    for zip_path in glob.glob(os.path.join(args.extract_data_dir, "*.zip")):
        sid = os.path.splitext(os.path.basename(zip_path))[0]

        # 如果指定了 doc_id，则只处理匹配的文档
        if args.doc_id and sid != args.doc_id:
            continue

        print(f"\n{'='*60}")
        print(f"处理文档: {sid}")
        print("=" * 60)

        root_path = os.path.join(args.extract_data_dir, sid)
        # Unzip a file
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(root_path)

        df = json2df(root_path)

        # OCR 增强（如果启用）
        if args.use_ocr and ocr_detector is not None:
            # 查找原始 PDF
            pdf_path = os.path.join(args.raw_data_dir, sid, "document.pdf")
            if not os.path.exists(pdf_path):
                # 尝试其他可能的命名
                pdf_path = os.path.join(args.raw_data_dir, sid, f"{sid}.pdf")

            if os.path.exists(pdf_path):
                df = enhance_headings_with_ocr(
                    df, pdf_path, ocr_detector, max_pages=args.ocr_max_pages
                )
            else:
                print(f"[Warning] 未找到 PDF 文件: {pdf_path}，跳过 OCR 增强")

        save_path = os.path.join(args.save_dir, sid)
        os.makedirs(save_path, exist_ok=True)
        df.to_pickle(os.path.join(save_path, "data.pkl"))

        print(f"[OK] 已保存处理结果到: {save_path}/data.pkl")

        # if PDF contains images or tables, copy the images and tables
        figures_path = os.path.join(root_path, "figures")
        if os.path.exists(figures_path):
            shutil.copytree(
                figures_path, os.path.join(save_path, "figures"), dirs_exist_ok=True
            )
        tables_path = os.path.join(root_path, "tables")
        if os.path.exists(tables_path):
            shutil.copytree(
                tables_path, os.path.join(save_path, "tables"), dirs_exist_ok=True
            )


if __name__ == "__main__":
    main(args)
