"""
构建 XML 树（DocIR）预处理第四步

从标准化数据 (data.pkl) 构建文档的层次化 XML 树结构。
这是预处理流程的最后一步，生成的 XML 树用于 Agent 审查。
"""

import argparse
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from doc_ir_builder import DocIRBuilder


def main():
    parser = argparse.ArgumentParser(
        description="构建 XML 树（DocIR）- 预处理第四步"
    )
    parser.add_argument(
        "--data-path",
        type=str,
        required=False,
        default=None,
        help="单个文档的数据路径（preprocess/processed_output/MinerU/{doc_id}）",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        default=None,
        help="指定要处理的文档 ID（如果不指定 --data-path，会在默认目录搜索）",
    )
    parser.add_argument(
        "--processed-dir",
        type=str,
        default="preprocess/processed_output/MinerU",
        help="处理后数据的根目录",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="sample_results",
        help="输出 XML 树的目录",
    )
    parser.add_argument(
        "--max-section-depth",
        type=int,
        default=10,
        help="最大章节嵌套深度",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    builder = DocIRBuilder(max_section_depth=args.max_section_depth)

    # 确定要处理的文档列表
    if args.data_path:
        # 直接指定了数据路径
        docs_to_process = [(os.path.basename(args.data_path), args.data_path)]
    elif args.doc_id:
        # 指定了 doc_id，组织路径
        data_path = os.path.join(args.processed_dir, args.doc_id)
        docs_to_process = [(args.doc_id, data_path)]
    else:
        # 处理目录下的所有文档
        docs_to_process = []
        if os.path.exists(args.processed_dir):
            for doc_id in sorted(os.listdir(args.processed_dir)):
                doc_path = os.path.join(args.processed_dir, doc_id)
                if os.path.isdir(doc_path) and os.path.exists(
                    os.path.join(doc_path, "data.pkl")
                ):
                    docs_to_process.append((doc_id, doc_path))

    if not docs_to_process:
        print(
            f"[Error] 未找到任何有效的数据。请检查路径或指定 --data-path / --doc-id"
        )
        return

    # 处理每个文档
    for doc_id, data_path in docs_to_process:
        print(f"\n{'='*60}")
        print(f"正在构建 XML 树: {doc_id}")
        print(f"{'='*60}")

        # 验证必要文件
        data_pkl = os.path.join(data_path, "data.pkl")
        if not os.path.exists(data_pkl):
            print(f"[Skip] {doc_id}: 缺少 data.pkl")
            continue

        page_images_dir = os.path.join(data_path, "page_images")
        if not os.path.exists(page_images_dir):
            print(
                f"[Warning] {doc_id}: 缺少 page_images 目录，XML 树可能不完整"
            )

        try:
            # 构建 XML 树
            result = builder.build_from_pkl(data_path)

            # 保存 XML 树为文件
            xml_output_path = os.path.join(args.output_dir, f"tree_{doc_id}.xml")
            outline_output_path = os.path.join(args.output_dir, f"outline_{doc_id}.xml")

            # 保存完整树
            tree = ET.ElementTree(result.root)
            tree.write(xml_output_path, encoding="utf-8", xml_declaration=True)
            print(f"[✓] 完整 XML 树已保存 -> {xml_output_path}")

            # 保存大纲视图（简化版）
            outline_root = result.root
            outline_tree = ET.ElementTree(outline_root)
            outline_tree.write(
                outline_output_path, encoding="utf-8", xml_declaration=True
            )
            print(f"[✓] 大纲视图已保存 -> {outline_output_path}")

            # 打印统计信息
            print(f"\n[📊] 统计信息:")
            print(f"    总页数: {result.num_page}")
            print(f"    图片数: {result.image_count}")
            print(f"    表格数: {result.table_count}")
            print(f"    段落数: {result.para_count}")
            print(f"    章节数: {len(result.section_dict)}")

        except Exception as e:
            print(f"[Error] 处理失败: {e}")
            import traceback

            traceback.print_exc()
            continue

    print(f"\n[✓] 全部处理完成")


if __name__ == "__main__":
    main()
