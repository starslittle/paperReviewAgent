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


def main(args):
    for file_name in glob.glob(os.path.join(args.raw_data_dir, "*")):

        basename = os.path.basename(file_name)
        if not os.path.isdir(file_name):
            continue

        print("Processing", basename)
        os.makedirs(os.path.join(args.save_dir, basename), exist_ok=True)
        os.makedirs(os.path.join(args.save_dir, basename, "page_images"), exist_ok=True)
        pdf_path = os.path.join(file_name, "document.pdf")
        if not os.path.exists(pdf_path):
            continue

        with fitz.open(pdf_path) as pdf:
            for index, page in enumerate(pdf):
                image = page.get_pixmap(dpi=args.resolution)
                index_string = "%04d" % index
                image.save(
                    os.path.join(
                        args.save_dir,
                        basename,
                        "page_images",
                        f"page_{index_string}.png",
                    )
                )


if __name__ == "__main__":
    main(args)
