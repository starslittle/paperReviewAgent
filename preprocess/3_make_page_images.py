import argparse
import glob
import os

import fitz

parser = argparse.ArgumentParser(description="Process extracted data")

parser.add_argument(
    "--raw-data-dir",
    type=str,
    default="../sample_data/",
    help="Directory to save results",
)
parser.add_argument(
    "--save-dir",
    type=str,
    default="./processed_output/",
    help="Directory to save results",
)
parser.add_argument(
    "--resolution",
    type=int,
    default=144,
    help="Resolution for page images",
)

args = parser.parse_args()


def process_one(pdf_path: str, save_root: str):
    """Render all pages of a PDF to images."""
    os.makedirs(save_root, exist_ok=True)
    os.makedirs(os.path.join(save_root, "page_images"), exist_ok=True)

        with fitz.open(pdf_path) as pdf:
            for index, page in enumerate(pdf):
                image = page.get_pixmap(dpi=args.resolution)
                index_string = "%04d" % index
                image.save(
                    os.path.join(
                    save_root,
                        "page_images",
                        f"page_{index_string}.png",
                    )
                )


def main(args):
    # 支持两种输入：
    # 1) raw_data_dir 下有多个子目录，每个目录里有 document.pdf 或其它 pdf
    # 2) raw_data_dir 直接包含若干 pdf 文件（无子目录）

    entries = glob.glob(os.path.join(args.raw_data_dir, "*"))
    if not entries:
        print(f"No entries found under {args.raw_data_dir}")
        return

    for file_name in entries:
        basename = os.path.basename(file_name)

        # 情况 A：子目录模式
        if os.path.isdir(file_name):
            pdf_path = os.path.join(file_name, "document.pdf")
            if not os.path.exists(pdf_path):
                pdf_candidates = glob.glob(os.path.join(file_name, "*.pdf"))
                if not pdf_candidates:
                    continue
                pdf_path = pdf_candidates[0]

            print("Processing", basename)
            save_root = os.path.join(args.save_dir, basename)
            process_one(pdf_path, save_root)

        # 情况 B：raw_data_dir 内直接是 PDF 文件
        elif file_name.lower().endswith(".pdf"):
            pdf_path = file_name
            name_no_ext = os.path.splitext(basename)[0]
            print("Processing", name_no_ext)
            save_root = os.path.join(args.save_dir, name_no_ext)
            process_one(pdf_path, save_root)


if __name__ == "__main__":
    main(args)
