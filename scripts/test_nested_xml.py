"""
独立脚本：直接从 data.pkl 生成嵌套结构的 XML
"""

import sys
import os
import xml.etree.ElementTree as ET

_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
sys.path.insert(0, _root)
if os.getcwd() != _root:
    os.chdir(_root)

from preprocess.doc_ir_builder import DocIRBuilder


def main():
    data_path = os.path.join(
        _root, "preprocess", "processed_output", "MinerU", "bylw-pgy"
    )
    output_dir = os.path.join(_root, "sample_results")
    output_path = os.path.join(output_dir, "outline_nested_bylw-pgy.xml")

    print("=" * 60)
    print("开始构建嵌套 XML 结构...")
    print("=" * 60)

    # 初始化 Builder
    builder = DocIRBuilder()

    # 构建
    result = builder.build_from_pkl(data_path)

    # 保存
    os.makedirs(output_dir, exist_ok=True)
    tree = ET.ElementTree(result.root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    print(f"\n[✓] 嵌套 XML 已保存到: {output_path}")
    print(f"\n统计信息:")
    print(f"  - 总页数: {result.num_page}")
    print(f"  - 章节数: {len(result.section_dict)}")
    print(f"  - 段落数: {result.para_count}")
    print(f"  - 图片数: {result.image_count}")
    print(f"  - 表格数: {result.table_count}")

    # 打印嵌套结构预览
    print("\n" + "=" * 60)
    print("嵌套结构预览（前3层）:")
    print("=" * 60)

    def print_section(element, indent=0, max_depth=3, current_depth=0):
        if element.tag == "Section" and current_depth < max_depth:
            section_id = element.get("section_id")
            heading = element.find("Heading")
            heading_text = heading.text[:40] if heading is not None else "No Heading"

            subsections = element.findall("Section")
            paragraphs = element.findall("Paragraph")

            print(f"{'  ' * indent}Section {section_id}: {heading_text}...")
            print(
                f"{'  ' * indent}  ├─ 段落: {len(paragraphs)}, 子章节: {len(subsections)}"
            )

            for child in element:
                if child.tag == "Section":
                    print_section(child, indent + 1, max_depth, current_depth + 1)

    print_section(result.root)

    print("\n[✓] 完成!")


if __name__ == "__main__":
    main()
