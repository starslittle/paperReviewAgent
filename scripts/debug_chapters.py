"""
检查章节内容是否为空
"""

import sys
import os
from dotenv import load_dotenv

# UTF-8
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
    doc_id = "bylw-pgy"
    data_path = os.path.join("preprocess/processed_output/MinerU", doc_id)
    reader = DocReader(data_path=data_path)

    chapters = reader.get_chapters()
    print(f"Total chapters: {len(chapters)}")
    print()

    # 检查每个章节
    empty_chapters = []
    for i, chap in enumerate(chapters):
        content_len = len(chap["content"])
        title = chap["title"]

        if content_len < 100:
            print(f"[WARNING] Chapter {i+1} has very short content:")
            print(f"  Title: {title}")
            print(f"  Content length: {content_len}")
            print(f"  Section ID: {chap['section_id']}")
            print(f"  Content preview: {chap['content'][:200]}")
            print()
            empty_chapters.append((i + 1, title, chap["section_id"], content_len))
        else:
            print(f"[OK] Chapter {i+1}: {title[:40]}... ({content_len} chars)")

    print()
    print("=" * 60)
    if empty_chapters:
        print(f"Found {len(empty_chapters)} chapters with short content:")
        for idx, title, sid, length in empty_chapters:
            print(f"  Chapter {idx} (ID={sid}): {title} - {length} chars")
    else:
        print("All chapters have sufficient content!")


if __name__ == "__main__":
    main()
