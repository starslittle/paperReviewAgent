"""
检查 Heading 与目录的对应关系
"""

import xml.etree.ElementTree as ET
import pandas as pd
import re

# 读取 XML
tree = ET.parse("sample_results/outline_bylw-pgy.xml")
root = tree.getroot()

# 读取 data.pkl
df = pd.read_pickle("preprocess/processed_output/MinerU/bylw-pgy/data.pkl")

# 提取所有 Heading
headings = []
for section in root.findall("Section"):
    heading_elem = section.find("Heading")
    if heading_elem is not None and heading_elem.text:
        section_id = section.get("section_id")
        start_page = section.get("start_page_num")
        heading_text = heading_elem.text.strip()
        headings.append(
            {"section_id": section_id, "page": int(start_page), "text": heading_text}
        )

# 按页码排序
headings_sorted = sorted(headings, key=lambda x: x["page"])

# 提取目录中的条目
print("=" * 80)
print("TOC vs Heading Comparison")
print("=" * 80)

# 从第5页提取目录内容
toc_rows = df[df["page_idx"] == 5]
toc_text = "\n".join([str(row) for _, row in toc_rows.iterrows() if row["para_text"]])

# 定义目录中的主要章节
toc_main_sections = [
    "1. XXX",  # 使用编号模式匹配
    "2. XXX",
    "3. XXX",
    "4. XXX",
    "5. XXX",
]

toc_sub_sections = {
    "1.1": "XXX",
    "1.2": "XXX",
    "1.3": "XXX",
    "1.4": "XXX",
    "2.1": "XXX",
    "2.2": "XXX",
    "2.3": "XXX",
    "2.4": "XXX",
    "3.1": "XXX",
    "3.2": "XXX",
    "3.3": "XXX",
    "3.4": "XXX",
    "4.1": "XXX",
    "4.2": "XXX",
    "4.3": "XXX",
    "4.4": "XXX",
    "4.5": "XXX",
    "5.1": "XXX",
    "5.2": "XXX",
}

print(f"\nTOC main sections: {len(toc_main_sections)}")
print(f"TOC sub sections: {len(toc_sub_sections)}")

print(f"\n=== Checking correspondence ===\n")

found_main = []
missing_main = []

for section in toc_main_sections:
    found = False
    for h in headings_sorted:
        if section in h["text"]:
            found_main.append((section, h["page"], h["text"]))
            found = True
            break
    if not found:
        missing_main.append(section)

print("[Main Sections Comparison]")
print(f"Found: {len(found_main)}/{len(toc_main_sections)}")
for section, page, text in found_main:
    print(f"  OK {section} -> Page {page}: {text[:50]}")

if missing_main:
    print(f"\nMissing: {len(missing_main)}")
    for section in missing_main:
        print(f"  X {section}")

# Sub-sections comparison
print(f"\n[Sub-sections Comparison]")
found_sub = []
missing_sub = []

for section_num, section_name in toc_sub_sections.items():
    found = False
    for h in headings_sorted:
        if section_num in h["text"] or section_name in h["text"]:
            found_sub.append(
                (f"{section_num} {section_name}", h["page"], h["text"][:60])
            )
            found = True
            break
    if not found:
        missing_sub.append(f"{section_num} {section_name}")

print(f"找到: {len(found_sub)}/{len(toc_sub_sections)}")
for section, page, text in found_sub[:10]:  # 只显示前10个
    print(f"  ✓ {section} -> 页{page}")

if len(found_sub) > 10:
    print(f"  ... 还有 {len(found_sub) - 10} 个")

if missing_sub:
    print(f"\n缺失: {len(missing_sub)}")
    for section in missing_sub[:5]:
        print(f"  ✗ {section}")
    if len(missing_sub) > 5:
        print(f"  ... 还有 {len(missing_sub) - 5} 个")

# 额外的 Heading (在目录中没有的)
print(f"\n【额外的 Heading】(在正文中识别为标题,但目录中没有)")
heading_texts = {h["text"] for h in headings_sorted}
extra_headings = []

for h in headings_sorted:
    is_extra = True
    for section in toc_main_sections + list(toc_sub_sections.values()):
        if section in h["text"]:
            is_extra = False
            break
    if is_extra and h["text"]:
        extra_headings.append((h["page"], h["text"][:60]))

if extra_headings:
    print(f"共 {len(extra_headings)} 个:")
    for page, text in extra_headings[:10]:
        print(f"  页{page}: {text}")
    if len(extra_headings) > 10:
        print(f"  ... 还有 {len(extra_headings) - 10} 个")
else:
    print("无")

print(f"\n=== 总结 ===")
total_toc = len(toc_main_sections) + len(toc_sub_sections)
total_found = len(found_main) + len(found_sub)
coverage = (total_found / total_toc * 100) if total_toc > 0 else 0

print(f"目录覆盖率: {total_found}/{total_toc} ({coverage:.1f}%)")
print(f"额外识别的标题: {len(extra_headings)} 个")

# 统计 heading 类型
print(f"\n=== Heading 类型统计 ===")
from collections import Counter

heading_types = []
for h in headings_sorted:
    text = h["text"]
    if re.match(r"^\d+\.\s", text):
        heading_types.append("主章节 (如 1. 绪论)")
    elif re.match(r"^\d+\.\d+\.\s", text):
        heading_types.append("子章节 (如 1.1 研究背景)")
    elif re.match(r"^[（(]\d+[)）]\s", text):
        heading_types.append("列表项 (如 (1) xxx)")
    else:
        heading_types.append("其他标题")

type_counts = Counter(heading_types)
for htype, count in type_counts.items():
    print(f"  {htype}: {count} 个")
