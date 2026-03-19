"""
改进版 MinerU middle.json 处理脚本
修复标题识别问题:正确使用 text_level 字段
"""

import argparse
import json
import os
import shutil
from typing import Dict, List, Optional, Tuple
import pandas as pd
from dotenv import load_dotenv
import re
from collections import defaultdict

# 加载环境变量
load_dotenv(override=True, encoding="utf-8")


class ImprovedJsonProcessor:
    """
    改进的 MinerU 处理器,正确识别标题层级
    """

    def __init__(self):
        self.heading_pattern = re.compile(r"^(\d+\.|\d+\.\d+\.|第.+章)")
        self.last_source_json_path = ""
        self.last_source_json_type = ""
        self.last_quality_report: Dict = {}

    def _normalize_header_text(self, text: str) -> str:
        return " ".join(str(text).strip().split())

    def _pick_best_caption(self, caption_obj) -> Optional[str]:
        if caption_obj is None:
            return None
        if isinstance(caption_obj, str):
            cleaned = caption_obj.strip()
            return cleaned or None
        if isinstance(caption_obj, list):
            cands = [str(x).strip() for x in caption_obj if str(x).strip()]
            if not cands:
                return None
            # 优先非“续表”标题，降低弱标题噪声
            non_cont = [x for x in cands if "续表" not in x]
            if non_cont:
                return max(non_cont, key=len)
            return max(cands, key=len)
        return None

    def _extract_table_data(
        self, element: dict
    ) -> Tuple[str, Optional[str], Optional[str], Optional[List[str]]]:
        """
        简化版：优先读顶层字段，兼容嵌套 blocks
        - content_list.json：直接有 table_body / img_path / table_caption
        - layout.json：从嵌套 blocks 提取（兼容）
        """
        # 1. 优先读顶层字段（MinerU 标准格式）
        content = (element.get("table_body") or "").strip()
        image_path = element.get("img_path") or element.get("image_path")

        # 2. Caption：直接用 table_caption（MinerU 真源）
        caption_raw = element.get("table_caption")
        caption = self._pick_best_caption(caption_raw)

        # 3. 如果顶层有数据，直接返回
        if content or image_path:
            return (
                content or "",
                caption,
                image_path,
                caption_raw if isinstance(caption_raw, list) else None,
            )

        # 4. 兼容 layout.json：从嵌套 blocks 提取
        for block in element.get("blocks") or []:
            btype = (block.get("type") or "").lower()
            if btype == "table_body":
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        if not content:
                            content = (
                                span.get("html")
                                or span.get("content")
                                or span.get("text")
                                or ""
                            ).strip()
                        if not image_path:
                            image_path = span.get("image_path")
                        if content and image_path:
                            break
                    if content and image_path:
                        break
                if content or image_path:
                    break

        return (
            content or "",
            caption,
            image_path,
            caption_raw if isinstance(caption_raw, list) else None,
        )

    def _select_source_json(self, root_path: str) -> Tuple[Optional[str], str]:
        """
        稳定选择数据源，避免多个 *_content_list.json 带来的非确定性：
        优先级：content_list.json > 最新 *_content_list.json > middle.json > layout.json
        """
        canonical_content = os.path.join(root_path, "content_list.json")
        if os.path.exists(canonical_content):
            return canonical_content, "content_list_canonical"

        content_list_candidates = [
            os.path.join(root_path, f)
            for f in os.listdir(root_path)
            if f.endswith("_content_list.json")
        ]
        if content_list_candidates:
            content_list_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            selected = content_list_candidates[0]
            if len(content_list_candidates) > 1:
                print(
                    f"[Warning] 检测到多个 *_content_list.json，使用最新文件: {os.path.basename(selected)}"
                )
            return selected, "content_list"

        middle_json_path = os.path.join(root_path, "middle.json")
        if os.path.exists(middle_json_path):
            return middle_json_path, "middle"

        layout_json_path = os.path.join(root_path, "layout.json")
        if os.path.exists(layout_json_path):
            return layout_json_path, "layout"

        return None, "none"

    def _safe_bbox(self, raw_bbox) -> Optional[List[float]]:
        if not raw_bbox or not isinstance(raw_bbox, list) or len(raw_bbox) < 4:
            return None
        try:
            return [float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3])]
        except Exception:
            return None

    def _estimate_page_heights(self, elements: List[dict]) -> Dict[int, float]:
        page_heights: Dict[int, float] = {}
        for elem in elements:
            if not isinstance(elem, dict):
                continue
            page_idx = int(elem.get("page_idx", 0) or 0)
            bbox = self._safe_bbox(elem.get("bbox"))
            if not bbox:
                continue
            page_heights[page_idx] = max(page_heights.get(page_idx, 0.0), bbox[3])
        # 给没检测到 bbox 的页一个合理兜底
        for elem in elements:
            page_idx = int(elem.get("page_idx", 0) or 0)
            if page_idx not in page_heights:
                page_heights[page_idx] = 1000.0
        return page_heights

    def process(self, root_path):
        """
        处理 MinerU 产物,生成带正确标题层级的 DataFrame
        """
        json_path, source_type = self._select_source_json(root_path)
        if not json_path:
            print(f"[Error] 未找到任何可用的解析 JSON")
            return None
        self.last_source_json_path = json_path
        self.last_source_json_type = source_type
        print(f"[Info] Using {source_type}: {os.path.basename(json_path)}")

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

        page_heights = self._estimate_page_heights(elements)
        discarded_text_pages = defaultdict(set)
        for elem in elements:
            if not isinstance(elem, dict):
                continue
            if (elem.get("type", "") or "").lower() != "discarded":
                continue
            text = self._normalize_header_text(elem.get("content") or elem.get("text") or "")
            if not text:
                continue
            page_idx = int(elem.get("page_idx", 0) or 0)
            discarded_text_pages[text].add(page_idx)

        # 构建 DataFrame
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
                        "table_id": str(page_idx + 1),
                        "font_size": None,
                        "font_family": None,
                        "bbox": None,
                        "page_idx": page_idx,
                    }
                )

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

            if etype == "discarded":
                bbox = self._safe_bbox(element.get("bbox"))
                text_norm = self._normalize_header_text(
                    element.get("content") or element.get("text") or ""
                )
                repeat_pages = len(discarded_text_pages.get(text_norm, set()))
                page_height = page_heights.get(page_idx, 1000.0)
                top_ratio = None
                bottom_ratio = None
                if bbox:
                    top_ratio = bbox[1] / max(page_height, 1.0)
                    bottom_ratio = bbox[3] / max(page_height, 1.0)

                is_page_no = bool(
                    re.fullmatch(r"[-\s]*\d+[-\s]*", text_norm)
                    or re.fullmatch(r"第\s*\d+\s*页", text_norm)
                )

                # 仅在“跨页重复”或“页码样式明显”时判定为页眉页脚，降低误判
                if repeat_pages >= 3 and top_ratio is not None and top_ratio <= 0.20:
                    style, item_id = "Header", None
                elif (
                    (repeat_pages >= 3 and bottom_ratio is not None and bottom_ratio >= 0.80)
                    or (is_page_no and bottom_ratio is not None and bottom_ratio >= 0.75)
                ):
                    style, item_id = "Footer", None
                else:
                    style, item_id = "Discarded", None
            else:
                # 判定样式
                style, item_id = self._determine_style_improved(
                    etype,
                    content,
                    font_size,
                    text_level,
                    element,
                    image_count,
                    table_count,
                )

            # 更新计数器
            if style == "Image":
                image_count += 1
                content = item_id
            elif style == "Table":
                # 提取表格数据（优先用顶层字段）
                (
                    table_content,
                    table_caption,
                    table_img_path,
                    table_caption_raw,
                ) = self._extract_table_data(element)
                if not table_content and isinstance(content, str):
                    table_content = content

                # 构建统一结构（与 Image 对齐）
                content = {"content": table_content or "", "alt_text": table_caption}
                if table_caption_raw:
                    content["alt_text_raw"] = table_caption_raw
                # 表格图统一用 figures/ 前缀
                if table_img_path:
                    content["image_path"] = "figures/" + os.path.basename(
                        table_img_path
                    )
                table_count += 1

            records.append(
                {
                    "para_text": content,
                    "style": style,
                    "table_id": item_id if style != "Image" else None,
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

        # 2.5 页眉页脚：仅依赖 MinerU discarded
        if etype == "discarded":
            return "Discarded", None

        # 3. 图片
        if etype in ["image", "figure"]:
            img_path = element.get("img_path", f"image_{image_count}.png")
            image_data = {
                "path": img_path,
                "alt_text": (
                    element.get("image_caption", [""])[0]
                    if element.get("image_caption")
                    else None
                ),
            }
            return "Image", image_data

        # 4. 表格：layout 中 "type": "table" 的顶层块即判为 Table
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
            print(
                f"    Font size range: {font_df['font_size'].min():.1f} - {font_df['font_size'].max():.1f}"
            )

    def build_quality_report(self, df: pd.DataFrame) -> Dict:
        """
        预处理质量报告（用于观察预处理误诊风险）
        """
        report: Dict = {
            "source_json_path": self.last_source_json_path,
            "source_json_type": self.last_source_json_type,
            "total_rows": int(len(df)),
            "style_distribution": {
                str(k): int(v) for k, v in df["style"].value_counts().to_dict().items()
            },
        }

        headers = df[df["style"] == "Header"].copy()
        footers = df[df["style"] == "Footer"].copy()
        tables = df[df["style"] == "Table"].copy()
        headings = df[df["style"].astype(str).str.startswith("Heading", na=False)].copy()

        for frame in (headers, footers):
            if not frame.empty:
                frame["text_norm"] = (
                    frame["para_text"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
                )

        header_unique = (
            headers["text_norm"].nunique() if not headers.empty and "text_norm" in headers else 0
        )
        footer_unique = (
            footers["text_norm"].nunique() if not footers.empty and "text_norm" in footers else 0
        )
        header_singletons = 0
        footer_singletons = 0
        if not headers.empty and "text_norm" in headers:
            header_singletons = int((headers["text_norm"].value_counts() == 1).sum())
        if not footers.empty and "text_norm" in footers:
            footer_singletons = int((footers["text_norm"].value_counts() == 1).sum())

        report["header_footer_quality"] = {
            "header_count": int(len(headers)),
            "footer_count": int(len(footers)),
            "header_unique_count": int(header_unique),
            "footer_unique_count": int(footer_unique),
            "header_singleton_count": int(header_singletons),
            "footer_singleton_count": int(footer_singletons),
            "header_singleton_rate": round(
                header_singletons / max(len(headers), 1), 4
            ),
            "footer_singleton_rate": round(
                footer_singletons / max(len(footers), 1), 4
            ),
        }

        table_with_caption = 0
        if not tables.empty:
            for v in tables["para_text"].tolist():
                if isinstance(v, dict) and str(v.get("alt_text", "")).strip():
                    table_with_caption += 1
        report["table_caption_quality"] = {
            "table_count": int(len(tables)),
            "table_with_caption_count": int(table_with_caption),
            "table_caption_coverage": round(
                table_with_caption / max(len(tables), 1), 4
            ),
        }

        toc_like_heading = 0
        if not headings.empty:
            for t in headings["para_text"].astype(str).tolist():
                if re.search(r"(\.{2,}|…{2,}|·{2,})", t):
                    toc_like_heading += 1
        report["heading_quality"] = {
            "heading_count": int(len(headings)),
            "toc_like_heading_count": int(toc_like_heading),
            "toc_like_heading_rate": round(toc_like_heading / max(len(headings), 1), 4),
        }

        suspicious_pattern = r"</?Section|</?Paragraph|</?Heading|\$\s*\\|end-toend|\.3\s+研究内容"
        suspicious_rows = df[
            df["para_text"].astype(str).str.contains(suspicious_pattern, regex=True, na=False)
        ]
        report["noise_quality"] = {
            "suspicious_row_count": int(len(suspicious_rows)),
            "suspicious_row_rate": round(len(suspicious_rows) / max(len(df), 1), 4),
        }

        # 预处理误诊风险代理分：加权汇总（用于横向比较同一文档多次处理）
        proxy_score = (
            report["header_footer_quality"]["header_singleton_rate"] * 0.30
            + report["heading_quality"]["toc_like_heading_rate"] * 0.35
            + (1.0 - report["table_caption_quality"]["table_caption_coverage"]) * 0.20
            + report["noise_quality"]["suspicious_row_rate"] * 0.15
        )
        report["preprocess_misdiagnosis_proxy"] = {
            "risk_score": round(float(proxy_score), 4),
            "score_range": "0~1（越低越好）",
        }

        self.last_quality_report = report
        return report


def main():
    parser = argparse.ArgumentParser(
        description="改进版:处理 MinerU JSON 并生成带正确标题的 DataFrame"
    )
    parser.add_argument(
        "--extract-data-dir",
        default="preprocess/extract_output/MinerU",
        help="MinerU 输出目录",
    )
    parser.add_argument(
        "--save-dir",
        default="preprocess/processed_output/MinerU",
        help="最终处理结果目录",
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

        # 生成预处理质量报告（用于观察误诊风险）
        quality_report = processor.build_quality_report(df)
        quality_report_path = os.path.join(save_path, "quality_report.json")
        with open(quality_report_path, "w", encoding="utf-8") as f:
            json.dump(quality_report, f, ensure_ascii=False, indent=2)

        # 复制图片到 figures 目录，供后续 DocReader 读取
        src_images_dir = os.path.join(root_path, "images")
        dst_figures_dir = os.path.join(save_path, "figures")
        if os.path.isdir(src_images_dir):
            os.makedirs(dst_figures_dir, exist_ok=True)
            copied = 0
            skipped = 0
            for filename in os.listdir(src_images_dir):
                lower = filename.lower()
                if not lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                src_path = os.path.join(src_images_dir, filename)
                dst_path = os.path.join(dst_figures_dir, filename)
                if os.path.exists(dst_path):
                    skipped += 1
                    continue
                shutil.copy2(src_path, dst_path)
                copied += 1
            print(
                f"    - figures/ synced from images (copied={copied}, skipped={skipped})"
            )

        print(f"[OK] {sid} processed -> {save_path}/")
        print(f"    - data.pkl (updated with correct headings)")
        print(f"    - data.csv (for debugging)")
        print(
            f"    - quality_report.json (misdiagnosis proxy={quality_report.get('preprocess_misdiagnosis_proxy', {}).get('risk_score', 'N/A')})"
        )

    print(f"\n[OK] All processing completed")


if __name__ == "__main__":
    main()
