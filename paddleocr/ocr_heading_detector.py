"""
PaddleOCR 增强的章节标题检测器
用于修正 Adobe PDF 提取中标题识别不准确的问题
"""

import os
import re
from typing import List, Dict, Tuple
import fitz  # PyMuPDF
from paddleocr import PaddleOCR
import numpy as np


class OCRHeadingDetector:
    """基于 PaddleOCR 的标题检测器"""

    def __init__(self, use_gpu=False, lang="ch"):
        """
        初始化 OCR 检测器

        Args:
            use_gpu: 是否使用 GPU
            lang: 语言，'ch' 中文，'en' 英文
        """
        # 初始化 PaddleOCR（新版本自动检测 CPU/GPU）
        self.ocr = PaddleOCR(use_angle_cls=True, lang=lang)

    def detect_headings_from_pdf(
        self, pdf_path: str, max_pages: int = None
    ) -> List[Dict]:
        """
        从 PDF 中检测所有标题

        Returns:
            List of detected headings with metadata:
            [
                {
                    'page': 1,
                    'text': '第1章 绪论',
                    'level': 1,  # 1=一级标题, 2=二级, 3=三级
                    'bbox': [x1, y1, x2, y2],  # 边界框
                    'font_size': 16.5,  # 估计的字体大小
                    'confidence': 0.95
                }
            ]
        """
        doc = fitz.open(pdf_path)
        all_headings = []

        total_pages = min(len(doc), max_pages) if max_pages else len(doc)

        print(f"[OCR] 开始处理 {total_pages} 页...")

        for page_num in range(total_pages):
            page = doc[page_num]

            # 转换页面为图像
            pix = page.get_pixmap(dpi=150)  # 150 DPI 足够识别标题
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )

            # OCR 识别（新版本自动使用方向分类，不需要 cls 参数）
            result = self.ocr.ocr(img_array)

            if result is None or len(result) == 0:
                continue

            # 调试：打印第一页的结果格式（仅第一次）
            if page_num == 0 and len(result) > 0:
                print(f"[OCR Debug] 结果格式: {type(result[0])}")
                # 打印 OCRResult 对象的属性
                if hasattr(result[0], "__dict__"):
                    print(
                        f"[OCR Debug] 对象属性: {list(result[0].__dict__.keys())[:5]}"
                    )
                # 尝试获取第一个元素
                try:
                    first_item = (
                        result[0][0] if hasattr(result[0], "__getitem__") else None
                    )
                    if first_item:
                        print(f"[OCR Debug] 第一个元素: {type(first_item)}")
                        if hasattr(first_item, "__dict__"):
                            print(
                                f"[OCR Debug] 元素属性: {list(first_item.__dict__.keys())}"
                            )
                except:
                    pass

            # 处理 OCRResult 对象（新版本）或列表（旧版本）
            ocr_data = result[0]

            # 如果是 OCRResult 对象，转换为列表格式
            # PaddleX 3.0 OCRResult 对象通常包含 'rec_text', 'rec_score', 'dt_polys' 等键值
            # 或者它可能是一个包含这些键的字典列表
            try:
                ocr_list = []

                # 情况 1: OCRResult 本身是类似列表的可迭代对象（传统 PaddleOCR 格式）
                if (
                    isinstance(ocr_data, list)
                    and len(ocr_data) > 0
                    and isinstance(ocr_data[0], list)
                ):
                    ocr_list = ocr_data

                # 情况 2: PaddleX 3.0 OCRResult 对象（类字典格式）
                else:
                    boxes = None
                    texts = None
                    scores = None

                    # 尝试字典式访问
                    if hasattr(ocr_data, "get") or isinstance(ocr_data, dict):
                        boxes = ocr_data.get("dt_polys")
                        texts = ocr_data.get("rec_text") or ocr_data.get("rec_texts")
                        scores = ocr_data.get("rec_score") or ocr_data.get("rec_scores")

                    # 尝试属性访问
                    if boxes is None and hasattr(ocr_data, "dt_polys"):
                        boxes = ocr_data.dt_polys
                    if texts is None:
                        if hasattr(ocr_data, "rec_text"):
                            texts = ocr_data.rec_text
                        elif hasattr(ocr_data, "rec_texts"):
                            texts = ocr_data.rec_texts
                    if scores is None and hasattr(ocr_data, "rec_score"):
                        scores = ocr_data.rec_score

                    # 组装结果
                    if boxes is not None and texts is not None:
                        if scores is None:
                            scores = [1.0] * len(texts)
                        for box, txt, sc in zip(boxes, texts, scores):
                            # 将坐标转换为标准 PaddleOCR 格式 [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
                            # 如果 box 已经是 4 个点的列表则直接使用，否则如果是 [x1,y1,x2,y2] 则转换
                            if len(box) == 4 and not isinstance(box[0], (list, tuple)):
                                x1, y1, x2, y2 = box
                                box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
                            ocr_list.append([box, (txt, sc)])

                if ocr_list:
                    ocr_data = ocr_list
                    if page_num == 0:
                        print(f"[OCR Debug] 成功提取 {len(ocr_list)} 条文本数据")
                else:
                    # 最后的兜底：尝试遍历
                    try:
                        for item in ocr_data:
                            # ... 原有的遍历逻辑 ...
                            pass
                    except:
                        pass

                    if not ocr_list and page_num == 0:
                        print(f"[OCR Warning] 无法从 OCRResult 提取数据。")
                        print(
                            f"[OCR Debug] 可用键名: {ocr_data.keys() if hasattr(ocr_data, 'keys') else 'None'}"
                        )

            except Exception as e:
                print(f"[OCR Warning] 转换 OCRResult 失败: {e}")
                continue

            # 分析每一行文本
            page_headings = self._analyze_page_text(
                ocr_data, page_num + 1, page_height=pix.height
            )

            all_headings.extend(page_headings)

            if (page_num + 1) % 10 == 0:
                print(f"[OCR] 已处理 {page_num + 1}/{total_pages} 页")

        doc.close()
        print(f"[OCR] 检测到 {len(all_headings)} 个标题")

        return all_headings

    def _analyze_page_text(
        self, ocr_result: List, page_num: int, page_height: int
    ) -> List[Dict]:
        """
        分析单页的 OCR 结果，识别标题

        Args:
            ocr_result: PaddleOCR 返回的结果
            page_num: 页码
            page_height: 页面高度（用于计算相对位置）

        Returns:
            该页检测到的标题列表
        """
        headings = []

        for line in ocr_result:
            # 兼容不同版本的 PaddleOCR 返回格式
            try:
                # 新版本格式：line 可能是 [bbox, (text, confidence)] 或其他格式
                if isinstance(line, (list, tuple)) and len(line) >= 2:
                    bbox = line[0]
                    # line[1] 可能是 (text, confidence) 或 [text, confidence]
                    if isinstance(line[1], (list, tuple)) and len(line[1]) >= 2:
                        text, confidence = line[1][0], line[1][1]
                    else:
                        # 如果格式不对，跳过这一行
                        continue
                else:
                    continue
            except (ValueError, IndexError, TypeError) as e:
                # 解析失败，跳过这一行
                continue

            # 跳过置信度过低的文本
            if confidence < 0.8:
                continue

            # 计算文本框的高度（估算字体大小）
            # 确保 bbox 是 NumPy 数组并提取标量值，防止出现 "ambiguous" 错误
            try:
                # 兼容不同形状的 bbox
                b = np.array(bbox)
                if b.ndim == 2 and b.shape == (4, 2):
                    # 标准 PaddleOCR 格式: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
                    text_height = float(b[2][1] - b[0][1])
                    text_width = float(b[1][0] - b[0][0])
                    center_x = float((b[0][0] + b[1][0]) / 2)
                elif b.ndim == 1 and len(b) == 4:
                    # 矩形格式: [x1, y1, x2, y2]
                    text_height = float(b[3] - b[1])
                    text_width = float(b[2] - b[0])
                    center_x = float((b[0] + b[2]) / 2)
                elif b.ndim == 1 and len(b) == 8:
                    # 八点多边形格式: [x1,y1, x2,y2, x3,y3, x4,y4]
                    text_height = float(b[5] - b[1])
                    text_width = float(b[2] - b[0])
                    center_x = float((b[0] + b[2]) / 2)
                else:
                    # 自动推断
                    b_reshaped = b.reshape(-1, 2)
                    text_height = float(
                        np.max(b_reshaped[:, 1]) - np.min(b_reshaped[:, 1])
                    )
                    text_width = float(
                        np.max(b_reshaped[:, 0]) - np.min(b_reshaped[:, 0])
                    )
                    center_x = float(np.mean(b_reshaped[:, 0]))
            except Exception:
                # 兜底：如果解析失败，使用固定的估计值
                text_height = 15.0
                text_width = 100.0
                center_x = 300.0

            # 判断是否为标题
            is_heading, level = self._is_heading(
                text, text_height, center_x, page_height
            )

            if is_heading:
                headings.append(
                    {
                        "page": page_num,
                        "text": text.strip(),
                        "level": level,
                        "bbox": [bbox[0][0], bbox[0][1], bbox[2][0], bbox[2][1]],
                        "font_size": text_height,
                        "confidence": confidence,
                    }
                )

        return headings

    def _is_heading(
        self, text: str, font_size: float, center_x: float, page_height: int
    ) -> Tuple[bool, int]:
        """
        判断文本是否为标题，以及标题级别

        规则：
        1. 文本模式匹配（章节编号）
        2. 字体大小（标题通常较大）
        3. 位置（标题通常居中或靠左）
        4. 长度（标题通常较短）

        Returns:
            (is_heading, level)
        """
        text = text.strip()

        # 规则1：章节编号模式识别（最高优先级）
        patterns = [
            # 一级标题
            (r"^(第\s*[一二三四五六七八九十百\d]+\s*章)", 1),
            (r"^(Chapter\s+\d+|CHAPTER\s+\d+)", 1),
            (r"^(附录\s*[A-Z一二三四五])", 1),
            (r"^([一二三四五六七八九十])\s*[、\.]", 1),  # 中文数字编号
            # 特殊一级标题（摘要、目录等）
            (
                r"^(摘\s*要|ABSTRACT|Abstract|目\s*录|参考文献|致\s*谢|REFERENCES|Acknowledgment|结\s*论|引\s*言)$",
                1,
            ),
            # 二级标题
            (r"^\d+\.\d+\.?\s+", 2),
            (r"^\d+\.\d+\s", 2),
            # 三级标题
            (r"^\d+\.\d+\.\d+\.?\s+", 3),
        ]

        for pattern, level in patterns:
            if re.match(pattern, text, re.IGNORECASE):
                # 额外验证：标题通常不会太长
                if len(text) < 100:
                    return True, level

        # 规则2：字体大小判断（辅助规则）
        # 假设正文字体 12pt ≈ 16-20 像素，标题会更大
        if font_size >= 25:  # 一级标题
            if len(text) < 50 and not self._is_likely_noise(text):
                return True, 1
        elif font_size >= 20:  # 二级标题
            if len(text) < 60 and re.match(r"^\d+\.", text):
                return True, 2

        return False, 0

    def _is_likely_noise(self, text: str) -> bool:
        """
        判断是否为噪声（页眉页脚、图标说明等）
        """
        noise_patterns = [
            r"^\d+$",  # 纯数字（页码）
            r"^第\s*\d+\s*页",  # 页码标识
            r"^图\s*\d+",  # 图标说明开头
            r"^表\s*\d+",  # 表格说明开头
        ]

        for pattern in noise_patterns:
            if re.match(pattern, text):
                return True

        return False

    def export_to_outline_format(self, headings: List[Dict]) -> str:
        """
        将检测到的标题导出为大纲格式（用于对比）
        """
        outline = []
        outline.append("=" * 60)
        outline.append("OCR 检测到的文档大纲")
        outline.append("=" * 60)

        for h in headings:
            indent = "  " * (h["level"] - 1)
            outline.append(f"{indent}[{h['level']}] 第{h['page']}页: {h['text']}")

        return "\n".join(outline)


def compare_with_adobe_extraction(ocr_headings: List[Dict], adobe_df) -> Dict:
    """
    对比 OCR 检测结果与 Adobe 提取结果

    Returns:
        {
            'ocr_only': [...],  # OCR 检测到但 Adobe 漏掉的
            'adobe_only': [...],  # Adobe 识别的但 OCR 没有的
            'matched': [...],  # 两者都识别的
            'suggestions': [...]  # 修正建议
        }
    """
    # 提取 Adobe 识别的标题
    adobe_headings = []
    for idx, row in adobe_df.iterrows():
        if row["style"].startswith("Heading"):
            level = int(row["style"].split()[1])
            adobe_headings.append(
                {"index": idx, "text": row["para_text"].strip(), "level": level}
            )

    # 对比分析
    ocr_texts = set(h["text"] for h in ocr_headings)
    adobe_texts = set(h["text"] for h in adobe_headings)

    ocr_only = [h for h in ocr_headings if h["text"] not in adobe_texts]
    adobe_only = [h for h in adobe_headings if h["text"] not in ocr_texts]
    matched = [h for h in ocr_headings if h["text"] in adobe_texts]

    # 生成修正建议
    suggestions = []
    for h in ocr_only:
        suggestions.append(
            {
                "action": "add",
                "page": h["page"],
                "text": h["text"],
                "level": h["level"],
                "reason": "OCR 检测到但 Adobe 遗漏",
            }
        )

    return {
        "ocr_only": ocr_only,
        "adobe_only": adobe_only,
        "matched": matched,
        "suggestions": suggestions,
        "stats": {
            "ocr_total": len(ocr_headings),
            "adobe_total": len(adobe_headings),
            "matched_count": len(matched),
            "ocr_only_count": len(ocr_only),
            "adobe_only_count": len(adobe_only),
        },
    }


if __name__ == "__main__":
    # 测试代码
    import argparse
    import pandas as pd

    parser = argparse.ArgumentParser(description="OCR 标题检测器测试")
    parser.add_argument("--pdf", type=str, required=True, help="PDF 文件路径")
    parser.add_argument("--max-pages", type=int, default=None, help="最多处理页数")
    parser.add_argument("--use-gpu", action="store_true", help="使用 GPU 加速")
    args = parser.parse_args()

    # 初始化检测器
    detector = OCRHeadingDetector(use_gpu=args.use_gpu)

    # 检测标题
    headings = detector.detect_headings_from_pdf(args.pdf, max_pages=args.max_pages)

    # 导出大纲
    outline = detector.export_to_outline_format(headings)
    print("\n" + outline)

    # 保存结果
    output_file = args.pdf.replace(".pdf", "_ocr_outline.txt")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(outline)

    print(f"\n大纲已保存到: {output_file}")
