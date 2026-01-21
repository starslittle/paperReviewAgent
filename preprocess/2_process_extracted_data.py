"""
改进版 MinerU middle.json 处理脚本
修复标题识别问题:正确使用 text_level 字段
"""

import argparse
import json
import os
import shutil
from typing import Dict, List
import pandas as pd
from dotenv import load_dotenv
import re

# 加载环境变量
load_dotenv(override=True, encoding="utf-8")


class ImprovedJsonProcessor:
    """
    改进的 MinerU 处理器,正确识别标题层级
    """

    def __init__(self):
        self.heading_pattern = re.compile(r'^(\d+\.|\d+\.\d+\.|第.+章)')

    def _normalize_header_text(self, text: str) -> str:
        return " ".join(str(text).strip().split())

    def _infer_page_bounds(self, elements: list) -> Dict[int, float]:
        page_max_y = {}
        for element in elements:
            page_idx = element.get("page_idx", 0)
            bbox = element.get("bbox", [])
            if isinstance(bbox, list) and len(bbox) >= 4:
                try:
                    max_y = float(bbox[3])
                except Exception:
                    continue
                if page_idx not in page_max_y or max_y > page_max_y[page_idx]:
                    page_max_y[page_idx] = max_y
        return page_max_y

    def _collect_header_footer_candidates(self, elements: list) -> Dict[str, set]:
        page_max_y = self._infer_page_bounds(elements)
        header_pages: Dict[str, set] = {}
        footer_pages: Dict[str, set] = {}
        page_indices = {element.get("page_idx", 0) for element in elements}
        page_count = max(page_indices) + 1 if page_indices else 0
        min_pages = max(2, int(page_count * 0.6))

        for element in elements:
            etype = (element.get("type") or "").lower()
            if etype not in ["text", "paragraph"]:
                continue
            content = (element.get("content") or element.get("text") or "").strip()
            if not content:
                continue
            cleaned = self._normalize_header_text(content)
            if len(cleaned) > 80:
                continue

            page_idx = element.get("page_idx", 0)
            bbox = element.get("bbox", [])
            if not (isinstance(bbox, list) and len(bbox) >= 4):
                continue
            page_height = page_max_y.get(page_idx)
            if not page_height:
                continue

            try:
                y1 = float(bbox[1])
                y2 = float(bbox[3])
            except Exception:
                continue

            top_thresh = page_height * 0.12
            bottom_thresh = page_height * 0.88
            if y2 <= top_thresh:
                header_pages.setdefault(cleaned, set()).add(page_idx)
            elif y1 >= bottom_thresh:
                footer_pages.setdefault(cleaned, set()).add(page_idx)

        header_candidates = {
            text for text, pages in header_pages.items() if len(pages) >= min_pages
        }
        footer_candidates = {
            text for text, pages in footer_pages.items() if len(pages) >= min_pages
        }
        return {
            "header": header_candidates,
            "footer": footer_candidates,
            "page_max_y": page_max_y,
        }

    def process(self, root_path):
        """
        处理 MinerU 产物,生成带正确标题层级的 DataFrame
        """
        # 优先级：content_list.json > middle.json > layout.json
        content_list_path = None
        middle_json_path = None

        # 查找 content_list.json
        for f in os.listdir(root_path):
            if f.endswith("_content_list.json"):
                content_list_path = os.path.join(root_path, f)
                break

        # 查找 middle.json
        middle_json_path = os.path.join(root_path, "middle.json")
        layout_json_path = os.path.join(root_path, "layout.json")

        # 确定使用的文件
        if content_list_path and os.path.exists(content_list_path):
            json_path = content_list_path
            print(f"[Info] Using content_list.json: {os.path.basename(json_path)}")
        elif middle_json_path and os.path.exists(middle_json_path):
            json_path = middle_json_path
            print(f"[Info] Using middle.json")
        elif layout_json_path and os.path.exists(layout_json_path):
            json_path = layout_json_path
            print(f"[Info] Using layout.json")
        else:
            print(f"[Error] 未找到任何可用的解析 JSON")
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Error] 读取 JSON 失败: {e}")
            return None

        # 解析元素列表
        if isinstance(data, list):
            raw_elements = data
        elif isinstance(data, dict):
            raw_elements = data.get("pdf_info", [])
        else:
            raw_elements = []

        # 处理 layout.json 按页组织的情况
        elements = []
        for item in raw_elements:
            if isinstance(item, dict) and "preproc_blocks" in item:
                page_idx = item.get("page_idx", 0)
                for block in item["preproc_blocks"]:
                    block["page_idx"] = page_idx
                    elements.append(block)
            else:
                elements.append(item)

        if not elements:
            print(f"[Warning] JSON 中未找到有效数据")
            return None

        header_footer_info = self._collect_header_footer_candidates(elements)
        header_texts = header_footer_info["header"]
        footer_texts = header_footer_info["footer"]
        page_max_y = header_footer_info["page_max_y"]

        # 构建 DataFrame
        records = []
        curr_page = -1
        image_count, table_count = 1, 1

        for element in elements:
            # 页面标记
            page_idx = element.get("page_idx", 0)
            if page_idx > curr_page:
                curr_page = page_idx
                records.append({
                    "para_text": None,
                    "style": "Page_Start",
                    "table_id": str(page_idx + 1),
                    "font_size": None,
                    "font_family": None,
                    "bbox": None,
                    "page_idx": page_idx,
                })

            etype = element.get("type", "").lower()
            content = (element.get("content") or element.get("text") or "").strip()
            font_size = element.get("font_size")
            font_family = element.get("font_family", "")
            bbox = element.get("bbox", [])

            # 🔥 关键改进:使用 text_level 字段
            text_level = element.get("text_level", 0)

            # 过滤无效内容
            if not content and etype not in ["image", "figure", "table"]:
                continue

            # 判定页眉/页脚
            header_footer_style = None
            if etype in ["text", "paragraph"] and content:
                cleaned = self._normalize_header_text(content)
                if cleaned in header_texts or cleaned in footer_texts:
                    if isinstance(bbox, list) and len(bbox) >= 4:
                        page_height = page_max_y.get(page_idx)
                        if page_height:
                            try:
                                y1 = float(bbox[1])
                                y2 = float(bbox[3])
                            except Exception:
                                y1 = y2 = None
                            if y1 is not None and y2 is not None:
                                top_thresh = page_height * 0.12
                                bottom_thresh = page_height * 0.88
                                if cleaned in header_texts and y2 <= top_thresh:
                                    header_footer_style = "Header"
                                elif cleaned in footer_texts and y1 >= bottom_thresh:
                                    header_footer_style = "Footer"

            if header_footer_style:
                style, item_id = header_footer_style, None
            else:
                # 判定样式
                style, item_id = self._determine_style_improved(
                    etype, content, font_size, text_level, element, image_count, table_count
                )

            # 更新计数器
            if style == "Image":
                image_count += 1
                content = item_id
            elif style == "Table":
                table_caption = (
                    element.get("table_caption")
                    or element.get("caption")
                    or element.get("title")
                    or element.get("name")
                )
                if isinstance(table_caption, list):
                    table_caption = table_caption[0] if table_caption else None
                if isinstance(table_caption, str):
                    table_caption = table_caption.strip() or None
                if content or table_caption:
                    content = {"content": content, "alt_text": table_caption}
                table_count += 1

            records.append({
                "para_text": content,
                "style": style,
                "table_id": item_id if style != "Image" else None,
                "font_size": font_size,
                "font_family": font_family,
                "bbox": bbox,
                "page_idx": page_idx,
            })

        if not records:
            print(f"[Warning] 未提取到有效内容")
            return None

        df = pd.DataFrame(records)

        # 打印统计信息
        self._print_statistics(df)

        return df

    def _determine_style_improved(
        self, etype, content, font_size, text_level, element, image_count, table_count
    ):
        """
        改进的样式判定逻辑,正确使用 text_level

        Args:
            text_level: MinerU 提供的标题层级 (0=普通, 1=一级标题, 2=二级标题...)
        """
        # 🔥 优先使用 text_level 字段判断标题
        if text_level and text_level > 0:
            # text_level > 0 就是标题,不再过滤长文本
            # MinerU 已经正确识别了标题
            return f"Heading {text_level}", None

        # 兼容其他类型的标题标记
        level = element.get("level") or element.get("text_level")
        if level and level > 0:
            return f"Heading {level}", None

        # 2. 图表说明
        if etype in ["caption", "figure_caption", "table_caption"]:
            return "Caption", None

        # 3. 图片
        if etype in ["image", "figure"]:
            img_path = element.get("img_path", f"image_{image_count}.png")
            image_data = {
                "path": img_path,
                "alt_text": element.get("image_caption", [""])[0] if element.get("image_caption") else None
            }
            return "Image", image_data

        # 4. 表格
        if etype == "table":
            return "Table", table_count

        # 5. 脚注
        if etype == "footnote":
            return "Footnote", None

        # 6. 列表项
        if etype in ["list_item", "list"]:
            return "List Paragraph", None

        # 7. 普通文本
        if etype in ["text", "paragraph"]:
            # 根据内容特征二次判定
            if content.startswith(("图", "Fig", "表", "Table")) and len(content) < 50:
                return "Caption", None
            return "Normal", None

        # 默认归类为普通文本
        return "Normal", None

    def _print_statistics(self, df):
        """打印 DataFrame 统计信息"""
        print(f"\n[Statistics] Processing results:")
        print(f"    Total rows: {len(df)}")
        print(f"    Style distribution:")
        for style, count in df["style"].value_counts().items():
            print(f"        - {style}: {count}")

        # 检查是否识别到标题
        has_heading = df["style"].str.startswith("Heading", na=False).any()
        if has_heading:
            heading_count = df[df["style"].str.startswith("Heading", na=False)].shape[0]
            print(f"\n    [SUCCESS] Identified {heading_count} headings!")
        else:
            print(f"\n    [WARNING] No headings found")

        # 字体大小统计
        font_df = df[df["font_size"].notna()]
        if not font_df.empty:
            print(f"    Font size range: {font_df['font_size'].min():.1f} - {font_df['font_size'].max():.1f}")


def main():
    parser = argparse.ArgumentParser(
        description="改进版:处理 MinerU JSON 并生成带正确标题的 DataFrame"
    )
    parser.add_argument(
        "--extract-data-dir", default="preprocess/extract_output/MinerU",
        help="MinerU 输出目录"
    )
    parser.add_argument(
        "--save-dir", default="preprocess/processed_output/MinerU",
        help="最终处理结果目录"
    )
    parser.add_argument("--doc-id", type=str, default=None, help="指定要处理的文档ID")
    args = parser.parse_args()

    os.makedirs(args.save_dir, exist_ok=True)
    processor = ImprovedJsonProcessor()

    # 遍历 MinerU 提取后的目录
    for sid in os.listdir(args.extract_data_dir):
        if args.doc_id and sid != args.doc_id:
            continue

        root_path = os.path.join(args.extract_data_dir, sid)
        if not os.path.isdir(root_path):
            continue

        print(f"\n{'='*60}")
        print(f"正在处理: {sid}")
        print(f"{'='*60}")

        df = processor.process(root_path)
        if df is None:
            continue

        # 保存处理结果
        save_path = os.path.join(args.save_dir, sid)
        os.makedirs(save_path, exist_ok=True)

        # 备份旧文件
        old_pkl = os.path.join(save_path, "data.pkl")
        if os.path.exists(old_pkl):
            import shutil
            shutil.copy(old_pkl, old_pkl + ".backup")
            print(f"[Backup] Old file backed up to data.pkl.backup")

        # 保存新的 DataFrame
        df.to_pickle(old_pkl)

        # 保存 CSV 方便调试
        df.to_csv(
            os.path.join(save_path, "data.csv"), index=False, encoding="utf-8-sig"
        )

        print(f"[OK] {sid} processed -> {save_path}/")
        print(f"    - data.pkl (updated with correct headings)")
        print(f"    - data.csv (for debugging)")

    print(f"\n[OK] All processing completed")


if __name__ == "__main__":
    main()
