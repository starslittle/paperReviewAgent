"""
MinerU middle.json 处理脚本
充分利用字体大小、坐标、层级等元数据进行格式一致性分析
"""

import argparse
import json
import os
import shutil
from typing import Dict, List
import pandas as pd
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(override=True, encoding="utf-8")


class MiddleJsonProcessor:
    """
    MinerU middle.json 处理器
    暴露字体大小、坐标、层级等元数据用于格式一致性分析
    """

    def __init__(self):
        self.font_size_threshold = {}  # 字体大小阈值（用于层级判定）

    def process(self, root_path):
        """
        处理 MinerU 产物，生成带元数据的 DataFrame

        Args:
            root_path: MinerU 输出目录 (包含 middle.json)

        Returns:
            DataFrame 包含以下列：
            - para_text: 文本内容
            - style: 样式 (Heading 1/2/3, Normal, Caption, Table, Image)
            - table_id: 表格/图片编号或页码
            - font_size: 字体大小 (用于一致性检查)
            - font_family: 字体族
            - bbox: 边界框坐标 [x0, y0, x1, y1]
            - page_idx: 页码
        """
        middle_json_path = os.path.join(root_path, "middle.json")
        if not os.path.exists(middle_json_path):
            print(f"[Error] 在 {root_path} 中未找到 middle.json")
            return None

        try:
            with open(middle_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Error] 读取 middle.json 失败: {e}")
            return None

        # 解析元素列表
        elements = data.get("pdf_info", [])
        if not elements:
            print(f"[Warning] middle.json 中未找到 pdf_info 数据")
            return None

        # 第一轮遍历：统计字体大小分布（用于自适应层级判定）
        self._analyze_font_sizes(elements)

        # 第二轮遍历：构建 DataFrame
        records = []
        curr_page = -1
        image_count, table_count = 1, 1

        for element in elements:
            # 页面标记
            page_idx = element.get("page_idx", 0)
            if page_idx > curr_page:
                curr_page = page_idx
                records.append(
                    {
                        "para_text": None,
                        "style": "Page_Start",
                        "table_id": str(page_idx + 1),  # 页码从1开始
                        "font_size": None,
                        "font_family": None,
                        "bbox": None,
                        "page_idx": page_idx,
                    }
                )

            etype = element.get("type", "").lower()
            content = element.get("content", "").strip()
            font_size = element.get("font_size")
            font_family = element.get("font_family", "")
            bbox = element.get("bbox", [])

            # 过滤无效内容
            if not content and etype not in ["image", "figure", "table"]:
                continue

            # 根据类型和元数据判定样式
            style, item_id = self._determine_style(
                etype, content, font_size, element, image_count, table_count
            )

            # 更新计数器
            if style == "Image":
                image_count += 1
            elif style == "Table":
                table_count += 1

            records.append(
                {
                    "para_text": content,
                    "style": style,
                    "table_id": item_id,
                    "font_size": font_size,
                    "font_family": font_family,
                    "bbox": bbox,
                    "page_idx": page_idx,
                }
            )

        if not records:
            print(f"[Warning] 未提取到有效内容")
            return None

        df = pd.DataFrame(records)

        # 打印统计信息
        self._print_statistics(df)

        return df

    def _analyze_font_sizes(self, elements):
        """
        分析字体大小分布，用于自适应层级判定
        """
        font_sizes = []
        for elem in elements:
            if elem.get("type", "").lower() in ["title", "heading", "text"]:
                fs = elem.get("font_size")
                if fs:
                    font_sizes.append(fs)

        if not font_sizes:
            return

        # 计算字体大小的四分位数
        sorted_sizes = sorted(set(font_sizes), reverse=True)
        if len(sorted_sizes) >= 3:
            self.font_size_threshold = {
                "heading1": sorted_sizes[0],
                "heading2": (
                    sorted_sizes[1] if len(sorted_sizes) > 1 else sorted_sizes[0]
                ),
                "heading3": (
                    sorted_sizes[2] if len(sorted_sizes) > 2 else sorted_sizes[1]
                ),
                "normal": sorted_sizes[-1],
            }
            print(f"[Info] 自适应字体阈值: {self.font_size_threshold}")

    def _determine_style(
        self, etype, content, font_size, element, image_count, table_count
    ):
        """
        根据类型、内容和字体大小综合判定样式

        Returns:
            (style, item_id)
        """
        # 1. 标题类型
        if etype in ["title", "heading"]:
            level = element.get("level", 1)

            # 如果 MinerU 没有提供层级，通过字体大小推断
            if not level and font_size and self.font_size_threshold:
                if font_size >= self.font_size_threshold.get("heading1", 20):
                    level = 1
                elif font_size >= self.font_size_threshold.get("heading2", 16):
                    level = 2
                else:
                    level = 3

            return f"Heading {level}", None

        # 2. 图表说明
        if etype in ["caption", "figure_caption", "table_caption"]:
            return "Caption", None

        # 3. 图片
        if etype in ["image", "figure"]:
            img_path = element.get("img_path", f"image_{image_count}.png")
            return "Image", image_count

        # 4. 表格
        if etype == "table":
            # MinerU 的表格内容通常是 Markdown 格式
            table_data = {"content": content}
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
        print(f"\n[📊] 处理统计:")
        print(f"    总行数: {len(df)}")
        print(f"    样式分布:")
        for style, count in df["style"].value_counts().items():
            print(f"        - {style}: {count}")

        # 字体大小统计（仅对有字体的行）
        font_df = df[df["font_size"].notna()]
        if not font_df.empty:
            print(
                f"    字体大小范围: {font_df['font_size'].min():.1f} - {font_df['font_size'].max():.1f}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="处理 MinerU middle.json 并生成标准 DataFrame"
    )
    parser.add_argument(
        "--extract-data-dir", default="./extract_output/", help="MinerU 输出目录"
    )
    parser.add_argument(
        "--save-dir", default="./processed_output/", help="最终处理结果目录"
    )
    parser.add_argument("--doc-id", type=str, default=None, help="指定要处理的文档ID")
    args = parser.parse_args()

    os.makedirs(args.save_dir, exist_ok=True)
    processor = MiddleJsonProcessor()

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

        # 保存 DataFrame (标准格式供 Agent 调用)
        df.to_pickle(os.path.join(save_path, "data.pkl"))

        # 同时保存 CSV 方便调试
        df.to_csv(
            os.path.join(save_path, "data.csv"), index=False, encoding="utf-8-sig"
        )

        # 拷贝原始 middle.json (供高级分析使用)
        src_middle = os.path.join(root_path, "middle.json")
        if os.path.exists(src_middle):
            shutil.copy(src_middle, os.path.join(save_path, "middle.json"))

        # 拷贝图片和表格文件夹
        for folder in ["images", "tables", "figures"]:
            src = os.path.join(root_path, folder)
            if os.path.exists(src):
                shutil.copytree(
                    src, os.path.join(save_path, folder), dirs_exist_ok=True
                )

        print(f"[✓] {sid} 处理完成 -> {save_path}/")
        print(f"    - data.pkl (标准格式)")
        print(f"    - data.csv (调试查看)")
        print(f"    - middle.json (完整元数据)")

    print(f"\n[✓] 全部处理完成")


if __name__ == "__main__":
    main()
