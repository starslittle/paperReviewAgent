"""
OCR 标题检测测试脚本
快速测试 PaddleOCR 在单个 PDF 上的效果
"""

import argparse
import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

from ocr_heading_detector import OCRHeadingDetector


def main():
    parser = argparse.ArgumentParser(description="测试 OCR 标题检测")
    parser.add_argument("--pdf", type=str, required=True, help="PDF 文件路径")
    parser.add_argument(
        "--max-pages", type=int, default=10, help="最多处理页数（默认: 10）"
    )
    parser.add_argument("--use-gpu", action="store_true", help="使用 GPU 加速")
    parser.add_argument(
        "--output", type=str, default=None, help="输出文件路径（默认: 控制台输出）"
    )

    args = parser.parse_args()

    # 检查文件是否存在
    if not os.path.exists(args.pdf):
        print(f"[Error] 文件不存在: {args.pdf}")
        return

    print("=" * 70)
    print("PaddleOCR 标题检测测试")
    print("=" * 70)
    print(f"PDF 文件: {args.pdf}")
    print(f"处理页数: {args.max_pages}")
    print(f"使用 GPU: {'是' if args.use_gpu else '否'}")
    print("=" * 70)
    print()

    # 初始化检测器
    print("[1/3] 初始化 PaddleOCR...")
    try:
        # 新版本 PaddleOCR 自动检测 CPU/GPU，不需要传递 use_gpu 参数
        detector = OCRHeadingDetector()
        print("✓ 初始化成功")
    except Exception as e:
        print(f"✗ 初始化失败: {e}")
        print("\n提示: 请确保已安装 PaddleOCR")
        print("安装命令: pip install paddlepaddle paddleocr")
        return

    print()

    # 检测标题
    print("[2/3] 开始 OCR 识别...")
    try:
        headings = detector.detect_headings_from_pdf(args.pdf, max_pages=args.max_pages)
        print(f"✓ 检测完成，共识别 {len(headings)} 个标题")
    except Exception as e:
        print(f"✗ 检测失败: {e}")
        import traceback

        traceback.print_exc()
        return

    print()

    # 生成大纲
    print("[3/3] 生成文档大纲...")
    outline = detector.export_to_outline_format(headings)

    # 输出结果
    print()
    print(outline)
    print()

    # 保存到文件（如果指定）
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(outline)
        print(f"✓ 大纲已保存到: {args.output}")

    # 详细信息
    print()
    print("=" * 70)
    print("检测详情")
    print("=" * 70)

    # 统计各级标题数量
    level_counts = {}
    for h in headings:
        level = h["level"]
        level_counts[level] = level_counts.get(level, 0) + 1

    print(f"一级标题: {level_counts.get(1, 0)} 个")
    print(f"二级标题: {level_counts.get(2, 0)} 个")
    print(f"三级标题: {level_counts.get(3, 0)} 个")

    # 显示前几个标题的详细信息
    print()
    print("前 5 个标题详情:")
    print("-" * 70)
    for i, h in enumerate(headings[:5], 1):
        print(f"{i}. [{h['level']}级] 第{h['page']}页: {h['text']}")
        print(f"   字体大小: {h['font_size']:.1f}px, 置信度: {h['confidence']:.2%}")

    print()
    print("=" * 70)
    print("测试完成！")
    print()
    print("下一步:")
    print("  1. 如果识别效果好，运行完整处理流程:")
    print(
        f"     python 2_process_extracted_data.py --use-ocr --ocr-max-pages {args.max_pages}"
    )
    print()
    print("  2. 如果需要调整识别规则，请编辑 ocr_heading_detector.py")
    print()


if __name__ == "__main__":
    main()
