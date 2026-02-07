"""
构建 DocIR 与 XML 树（预处理第三步）

从标准化数据 (data.pkl) 构建文档的层次化 XML 树结构。
这是预处理流程的最后一步，生成的 XML 树用于 Agent 审查。
"""

import argparse
import os
import xml.etree.ElementTree as ET

from doc_ir_builder import DocIRBuilder


def _build_outline_from_tree(root: ET.Element) -> ET.Element:
    """从完整 XML 树构建简化的 Outline 视图"""
    outline_root = ET.Element("Outline")

    def _copy_section(src_section: ET.Element, dst_parent: ET.Element) -> None:
        dst_section = ET.SubElement(dst_parent, "Section", src_section.attrib)
        for child in list(src_section):
            if child.tag == "Section":
                _copy_section(child, dst_section)
                continue

            if child.tag == "Heading":
                heading = ET.SubElement(
                    dst_section, "Heading", child.attrib
                )  # ← 保留所有属性（包括level）
                heading.text = (child.text or "").strip()
                continue

            if child.tag == "Paragraph":
                para = ET.SubElement(dst_section, "Paragraph")
                para.set("page_num", child.get("page_num", ""))
                para.text = child.text
                continue

            if child.tag == "Image":
                image = ET.SubElement(dst_section, "Image", child.attrib)
                for sub in list(child):
                    if sub.tag == "Alt_Text":
                        alt = ET.SubElement(image, "Alt_Text", sub.attrib)
                        alt.text = sub.text
                continue

            if child.tag == "Table":
                table = ET.SubElement(dst_section, "Table", child.attrib)
                table.text = child.text
                for sub in list(child):
                    if sub.tag == "Alt_Text":
                        alt = ET.SubElement(table, "Alt_Text", sub.attrib)
                        alt.text = sub.text
                continue

            if child.tag == "Caption":
                caption = ET.SubElement(dst_section, "Caption", child.attrib)
                caption.text = (child.text or "").strip()
                continue

            if child.tag == "Header":
                header = ET.SubElement(dst_section, "Header", child.attrib)
                header.text = (child.text or "").strip()
                continue

            if child.tag == "Footer":
                footer = ET.SubElement(dst_section, "Footer", child.attrib)
                footer.text = (child.text or "").strip()
                continue

    for section in root.findall("Section"):
        _copy_section(section, outline_root)

    return outline_root


def main():
    parser = argparse.ArgumentParser(description="构建 DocIR 与 XML 树 - 预处理第三步")
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
        print("[Error] 未找到任何有效的数据。请检查路径或指定 --data-path / --doc-id")
        return

    # 处理每个文档
    for doc_id, data_path in docs_to_process:
        print(f"\n{'='*60}")
        print(f"正在构建 DocIR 与 XML 树: {doc_id}")
        print(f"{'='*60}")

        # 验证必要文件
        data_pkl = os.path.join(data_path, "data.pkl")
        if not os.path.exists(data_pkl):
            print(f"[Skip] {doc_id}: 缺少 data.pkl")
            continue

        page_images_dir = os.path.join(data_path, "page_images")
        if not os.path.exists(page_images_dir):
            print(f"[Warning] {doc_id}: 缺少 page_images 目录，XML 树可能不完整")

        try:
            # 构建 DocIR 与 XML 树
            result = builder.build_from_pkl(data_path)

            # 保存 Outline 视图（用于审查）
            outline_output_path = os.path.join(args.output_dir, f"outline_{doc_id}.xml")
            outline_root = _build_outline_from_tree(result.root)
            ET.indent(outline_root, space="  ")
            outline_tree = ET.ElementTree(outline_root)
            outline_tree.write(outline_output_path, encoding="utf-8")
            print(f"[OK] 大纲视图已保存 -> {outline_output_path}")

            # 打印统计信息
            print("\n[Stats] 统计信息:")
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

    print("\n[OK] 全部处理完成")


if __name__ == "__main__":
    main()
