"""
DocAgent 预处理流程统一入口

一键运行完整的文档预处理流程：
    Step 1: Extract (MinerU API) -> 提取文档内容
    Step 2: Process -> 清洗与标准化数据
    Step 3: Build DocIR -> 构建文档结构与 XML 树

可选步骤（默认开启）：
    Tool: Make Page Images -> 生成页面图（用于视觉展示）

用法示例：
    # 运行完整流程
    python run_pipeline.py --doc-id bylw-zx

    # 跳过提取步骤（已有 extract_output）
    python run_pipeline.py --doc-id bylw-zx --skip-extract

    # 只生成页面图
    python run_pipeline.py --doc-id bylw-zx --only-page-images

    # 包含页面图生成（默认开启）
    python run_pipeline.py --doc-id bylw-zx --with-page-images

    # 关闭页面图生成（不推荐，逻辑审查会缺少封面图）
    python run_pipeline.py --doc-id bylw-zx --no-page-images
"""

import argparse
import os
import sys
import subprocess
from pathlib import Path


def run_command(cmd: list, description: str, cwd: str = None) -> bool:
    """运行命令并处理错误"""
    print(f"\n{'='*60}")
    print(f"{description}")
    print(f"{'='*60}")
    print(f"[CMD] {' '.join(cmd)}\n")

    try:
        result = subprocess.run(
            cmd, cwd=cwd, check=True, capture_output=False, text=True
        )
        print(f"\n[OK] {description} - 完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n[Error] {description} - 失败 (退出码: {e.returncode})")
        return False
    except FileNotFoundError:
        print(f"\n[Error] {description} - 失败: 找不到 Python 或脚本文件")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="DocAgent 预处理流程统一入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
    # 运行完整流程
    python run_pipeline.py --doc-id bylw-zx
    
    # 跳过提取步骤（已有 extract_output）
    python run_pipeline.py --doc-id bylw-zx --skip-extract
    
    # 跳过清洗步骤（已有 processed_output）
    python run_pipeline.py --doc-id bylw-zx --skip-process
    
    # 只运行 DocIR 构建
    python run_pipeline.py --doc-id bylw-zx --skip-extract --skip-process
    
    # 包含页面图生成（默认开启）
    python run_pipeline.py --doc-id bylw-zx --with-page-images
    
    # 只生成页面图（独立工具）
    python run_pipeline.py --doc-id bylw-zx --only-page-images
        """,
    )

    # 必需参数
    parser.add_argument(
        "--doc-id", type=str, required=True, help="文档 ID（data/ 目录下的子目录名）"
    )

    # 数据目录
    parser.add_argument(
        "--raw-data-dir", type=str, default="data/", help="原始数据目录（默认: data/）"
    )
    parser.add_argument(
        "--extract-dir",
        type=str,
        default="preprocess/extract_output",
        help="提取结果输出目录（默认: preprocess/extract_output）",
    )
    parser.add_argument(
        "--processed-dir",
        type=str,
        default="preprocess/processed_output/MinerU",
        help="处理后数据目录（默认: preprocess/processed_output/MinerU）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="sample_results",
        help="XML 输出目录（默认: sample_results）",
    )

    # 步骤控制
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="跳过 Step 1: Extract（假设已有 extract_output）",
    )
    parser.add_argument(
        "--skip-process",
        action="store_true",
        help="跳过 Step 2: Process（假设已有 processed_output）",
    )
    parser.add_argument(
        "--skip-build", action="store_true", help="跳过 Step 3: Build DocIR"
    )

    # 可选工具（默认开启页面图生成）
    parser.add_argument(
        "--with-page-images",
        action="store_true",
        default=True,
        help="在主流程后生成页面图（默认开启）",
    )
    parser.add_argument(
        "--no-page-images",
        action="store_false",
        dest="with_page_images",
        help="关闭页面图生成（不推荐）",
    )
    parser.add_argument(
        "--only-page-images", action="store_true", help="只生成页面图（跳过主流程）"
    )
    parser.add_argument(
        "--page-image-resolution",
        type=int,
        default=144,
        help="页面图分辨率 DPI（默认: 144）",
    )

    # 其他参数
    parser.add_argument(
        "--max-section-depth", type=int, default=10, help="最大章节嵌套深度（默认: 10）"
    )

    args = parser.parse_args()

    # 检查文档目录是否存在
    doc_dir = os.path.join(args.raw_data_dir, args.doc_id)
    if not os.path.exists(doc_dir):
        print(f"[Error] 错误: 文档目录不存在: {doc_dir}")
        sys.exit(1)

    # 工作目录
    # run_pipeline.py 位于 preprocess/ 目录下
    # workspace_root 应该是项目根目录（preprocess 的父目录）
    preprocess_dir = Path(__file__).parent.resolve()  # preprocess/ 目录
    workspace_root = preprocess_dir.parent  # 项目根目录

    print("\n" + "=" * 60)
    print("DocAgent 预处理流程")
    print("=" * 60)
    print(f"文档 ID: {args.doc_id}")
    print(f"工作目录: {workspace_root}")
    print(f"原始数据: {doc_dir}")
    print("=" * 60)

    # 如果只生成页面图
    if args.only_page_images:
        cmd = [
            sys.executable,
            str(preprocess_dir / "tool_make_page_images.py"),
            "--raw-data-dir",
            args.raw_data_dir,
            "--save-dir",
            args.processed_dir,
            "--resolution",
            str(args.page_image_resolution),
        ]
        success = run_command(cmd, "Tool: 生成页面图", cwd=str(workspace_root))
        sys.exit(0 if success else 1)

    # 主流程执行标志
    all_success = True

    # Step 1: Extract
    if not args.skip_extract:
        cmd = [
            sys.executable,
            str(preprocess_dir / "step1_extract.py"),
            "--raw-data-dir",
            args.raw_data_dir,
            "--result-dir",
            args.extract_dir,
            "--doc-id",
            args.doc_id,
        ]
        success = run_command(
            cmd, "Step 1: 提取文档内容 (MinerU)", cwd=str(workspace_root)
        )
        if not success:
            print("\n[Error] Step 1 失败，终止流程")
            sys.exit(1)
    else:
        print("\n[跳过] Step 1: Extract")

    # Step 2: Process
    if not args.skip_process:
        cmd = [
            sys.executable,
            str(preprocess_dir / "step2_process.py"),
            "--extract-data-dir",
            os.path.join(args.extract_dir, "MinerU"),
            "--save-dir",
            args.processed_dir,
        ]
        success = run_command(cmd, "Step 2: 清洗与标准化数据", cwd=str(workspace_root))
        if not success:
            print("\n[Error] Step 2 失败，终止流程")
            sys.exit(1)
    else:
        print("\n[跳过] Step 2: Process")

    # Step 3: Build DocIR
    if not args.skip_build:
        cmd = [
            sys.executable,
            str(preprocess_dir / "step3_build_docir.py"),
            "--doc-id",
            args.doc_id,
            "--processed-dir",
            args.processed_dir,
            "--output-dir",
            args.output_dir,
            "--max-section-depth",
            str(args.max_section_depth),
        ]

        success = run_command(
            cmd, "Step 3: 构建 DocIR 与 XML 树", cwd=str(workspace_root)
        )
        if not success:
            print("\n[Error] Step 3 失败")
            all_success = False
    else:
        print("\n[跳过] Step 3: Build DocIR")

    # 可选: 生成页面图
    if args.with_page_images:
        cmd = [
            sys.executable,
            str(preprocess_dir / "tool_make_page_images.py"),
            "--raw-data-dir",
            args.raw_data_dir,
            "--save-dir",
            args.processed_dir,
            "--resolution",
            str(args.page_image_resolution),
        ]
        success = run_command(cmd, "Tool: 生成页面图（可选）", cwd=str(workspace_root))
        if not success:
            print("\n[Warning] 页面图生成失败或跳过（不影响主流程）")

    # 总结
    print("\n" + "=" * 60)
    if all_success:
        print("[OK] 预处理流程全部完成")
        print(f"\n输出位置:")
        print(f"  - 提取结果: {args.extract_dir}/MinerU/{args.doc_id}/")
        print(f"  - 处理结果: {args.processed_dir}/{args.doc_id}/")
        print(f"  - 大纲视图: {args.output_dir}/outline_{args.doc_id}.xml")
    else:
        print("[Error] 预处理流程部分失败，请检查日志")
    print("=" * 60)

    sys.exit(0 if all_success else 1)


if __name__ == "__main__":
    main()
