"""
测试 Agent 实际获取的章节内容
"""

import os
import sys
from dotenv import load_dotenv

# 设置 UTF-8 编码
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

load_dotenv(override=True, encoding="utf-8")
_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
sys.path.insert(0, _root)
if os.getcwd() != _root:
    os.chdir(_root)

from agent.doc_reader import DocReader
import xml.etree.ElementTree as ET


def main():
    print("=" * 80)
    print("Testing Section Content Retrieval")
    print("=" * 80)

    doc_id = "bylw-pgy"
    data_path = os.path.join("preprocess/processed_output/MinerU", doc_id)

    # 初始化 DocReader
    reader = DocReader(data_path=data_path)

    # 测试获取 Section 8
    print("\n[TEST] Getting Section 8 content...")
    section_8 = reader.get_section_content("8")

    if section_8 is not None:
        # 转换为字符串查看
        xml_str = ET.tostring(section_8, encoding="unicode", method="xml")

        print(f"\nSection 8 XML length: {len(xml_str)} characters")
        print(f"\nSection 8 XML structure:")

        # 统计子元素
        from collections import Counter

        tags = [child.tag for child in section_8]
        tag_counts = Counter(tags)

        for tag, count in tag_counts.items():
            print(f"  - {tag}: {count}")

        # 显示前几个元素
        print(f"\nFirst 5 elements:")
        for i, child in enumerate(section_8[:5]):
            text_len = len(child.text) if child.text else 0
            text_preview = child.text[:80] if child.text else "[empty]"
            print(f"  {i+1}. <{child.tag}> ({text_len} chars)")
            print(f"     {text_preview}...")

        # 检查是否有段落
        paragraphs = section_8.findall("Paragraph")
        print(f"\nTotal Paragraphs: {len(paragraphs)}")

        if len(paragraphs) == 0:
            print("\n[ERROR] No paragraphs found in Section 8!")
            print(
                "This is the root cause - Agent cannot review content that doesn't exist."
            )
        else:
            print(f"\n[SUCCESS] Found {len(paragraphs)} paragraphs")
    else:
        print("[ERROR] Section 8 not found")


if __name__ == "__main__":
    main()
