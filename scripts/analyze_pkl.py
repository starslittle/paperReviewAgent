"""
深度分析 data.pkl 的工具
"""
import argparse
import os
import sys

# 设置 UTF-8 编码输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description="深度分析 data.pkl")
    parser.add_argument("--data-path", type=str,
                       default="preprocess/processed_output/MinerU/bylw-pgy")
    args = parser.parse_args()

    pkl_path = os.path.join(args.data_path, "data.pkl")
    if not os.path.exists(pkl_path):
        print(f"错误: 找不到文件 {pkl_path}")
        sys.exit(1)

    import pandas as pd

    print("=" * 80)
    print(f"正在读取: {pkl_path}")
    print("=" * 80)

    df = pd.read_pickle(pkl_path)

    print(f"\n[基本信息]")
    print(f"总行数: {len(df)}")
    print(f"总页数: {df['page_idx'].max() + 1}")
    print(f"列名: {list(df.columns)}")

    print(f"\n[样式分布]")
    style_counts = df["style"].value_counts()
    for style, count in style_counts.items():
        percentage = (count / len(df)) * 100
        print(f"  {style}: {count} ({percentage:.1f}%)")

    print(f"\n[数据样例]")

    # 显示每种样式的样例
    for style in df['style'].unique():
        style_df = df[df['style'] == style]
        print(f"\n--- {style} 样式 (共{len(style_df)}条) ---")
        sample = style_df.head(3)

        for idx, row in sample.iterrows():
            print(f"\n[第 {idx} 行 | page_idx={row['page_idx']}]")

            # 处理不同类型的内容
            if style == 'Image':
                # Image 类型，para_text 是字典
                import json
                try:
                    if isinstance(row['para_text'], str):
                        print(f"  内容: {row['para_text'][:100]}")
                    elif isinstance(row['para_text'], dict):
                        print(f"  图片信息: {json.dumps(row['para_text'], ensure_ascii=False, indent=2)[:200]}")
                    else:
                        print(f"  类型: {type(row['para_text'])}")
                except:
                    print(f"  内容: [无法解析]")
            elif style == 'Table':
                print(f"  表格ID: {row['table_id']}")
            elif style == 'Page_Start':
                print(f"  页面开始标记")
            else:  # Normal
                text = row['para_text']
                if text and len(str(text)) > 0:
                    preview = str(text)[:100]
                    if len(str(text)) > 100:
                        preview += "..."
                    print(f"  文本: {preview}")
                else:
                    print(f"  [空文本]")

    print(f"\n[问题诊断]")

    # 检查是否有 Heading 样式
    has_heading = df['style'].str.startswith('Heading', na=False).any()
    if not has_heading:
        print("⚠️  警告: 数据中没有 'Heading' 样式!")
        print("   这意味着 DocIRBuilder 构建的 XML 树可能缺少章节结构。")
        print("\n   可能的原因:")
        print("   1. PDF 解析器未能正确识别标题样式")
        print("   2. 预处理步骤 (preprocess/2_process_extracted_data.py) 未正确标记标题")
        print("   3. 原始 PDF 使用了非标准的标题格式")
    else:
        heading_count = df[df['style'].str.startswith('Heading', na=False)].shape[0]
        print(f"✓ 发现 {heading_count} 个标题样式")

    # 检查页码
    print(f"\n[页码分布]")
    print(f"页码范围: {df['page_idx'].min()} - {df['page_idx'].max()}")
    print(f"总页数: {df['page_idx'].max() + 1}")

    # 统计每页的元素数量
    page_stats = df.groupby('page_idx').size().describe()
    print(f"每页元素数量: 平均 {page_stats['mean']:.1f}, 最少 {int(page_stats['min'])}, 最多 {int(page_stats['max'])}")

    # 搜索可能的标题（以数字开头的文本）
    print(f"\n[可能的标题] (搜索以数字或'第'开头的文本)")
    potential_headings = df[
        (df['style'] == 'Normal') &
        (df['para_text'].notna()) &
        (
            (df['para_text'].str.match(r'^\d+\.', na=False)) |
            (df['para_text'].str.match(r'^第.+章', na=False)) |
            (df['para_text'].str.match(r'^\d+\s+', na=False))
        )
    ].head(10)

    if len(potential_headings) > 0:
        for idx, row in potential_headings.iterrows():
            print(f"  page {row['page_idx']}: {row['para_text'][:60]}")
    else:
        print("  (未找到明显的标题模式)")


if __name__ == "__main__":
    main()
