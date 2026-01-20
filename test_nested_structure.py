"""
测试嵌套Section结构
"""
import xml.etree.ElementTree as ET

def print_section_structure(element, indent=0, max_depth=5, current_depth=0):
    """递归打印Section结构 - 树状展示"""
    if element.tag == "Section" and current_depth < max_depth:
        section_id = element.get("section_id")
        heading = element.find("Heading")
        heading_text = heading.text if heading is not None else "No Heading"

        # 统计子元素
        paragraphs = element.findall("Paragraph")
        subsections = element.findall("Section")
        images = element.findall("Image")
        tables = element.findall("CSV_Table")

        # 树状符号
        if indent == 0:
            prefix = "📦 "
        else:
            prefix = "  ├── "

        print(f"{'  ' * indent}{prefix}Section {section_id}: {heading_text}")

        # 只在有内容时才显示详情
        if paragraphs or images or tables:
            if subsections:
                print(f"{'  ' * (indent+1)}│ 📄 内容: {len(paragraphs)}段, {len(images)}图, {len(tables)}表")
            else:
                print(f"{'  ' * (indent+1)}└─ 📄 内容: {len(paragraphs)}段, {len(images)}图, {len(tables)}表")

    # 递归处理子Section
    if element.tag == "Document" or (element.tag == "Section" and current_depth < max_depth):
        for i, child in enumerate(element):
            if child.tag == "Section":
                is_last = (i == len(list(element)) - 1)
                print_section_structure(child, indent + 1, max_depth, current_depth + 1)

if __name__ == "__main__":
    xml_path = r"c:\Users\Admin\Desktop\paperReviewAgent\sample_results\outline_bylw-pgy.xml"

    print(f"Parsing: {xml_path}\n")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    print("=" * 80)
    print("NESTED SECTION STRUCTURE:")
    print("=" * 80)

    # 统计
    total_sections = len([s for s in root.iter('Section')])
    top_level_sections = len(root.findall('Section'))

    print(f"\nTotal Sections: {total_sections}")
    print(f"Top-Level Sections: {top_level_sections}")
    print("\nSection Tree:\n")

    print_section_structure(root)

    print("\n" + "=" * 80)
