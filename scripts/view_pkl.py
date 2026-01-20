"""
查看 data.pkl 内容的工具

使用方法:
    # 查看前10行数据
    python view_pkl.py --data-path preprocess/processed_output/MinerU/bylw-pgy --rows 10

    # 查看完整数据结构
    python view_pkl.py --data-path preprocess/processed_output/MinerU/bylw-pgy --full

    # 查看统计信息
    python view_pkl.py --data-path preprocess/processed_output/MinerU/bylw-pgy --stats
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="查看 data.pkl 内容")
    parser.add_argument(
        "--data-path",
        type=str,
        default="preprocess/processed_output/MinerU/bylw-pgy",
        help="文档数据目录",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=20,
        help="显示的行数",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="显示完整数据",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="只显示统计信息",
    )
    parser.add_argument(
        "--export-csv",
        type=str,
        help="导出为 CSV 文件",
    )
    args = parser.parse_args()

    # 检查文件是否存在
    pkl_path = os.path.join(args.data_path, "data.pkl")
    if not os.path.exists(pkl_path):
        print(f"[ERROR] File not found: {pkl_path}")
        sys.exit(1)

    # 读取 pickle 文件
    import pandas as pd

    print(f"[INFO] Reading: {pkl_path}")
    df = pd.read_pickle(pkl_path)

    print(f"\n[SUCCESS] Data loaded successfully!")
    print(f"[INFO] Total rows: {len(df)}")
    print(f"[INFO] Columns: {list(df.columns)}")

    # 显示统计信息
    print("\n" + "=" * 80)
    print("STATISTICS")
    print("=" * 80)

    # 样式统计
    if "style" in df.columns:
        style_counts = df["style"].value_counts()
        print("\n[Style Distribution]")
        for style, count in style_counts.items():
            print(f"  {style}: {count}")

    # 页码统计
    if "page_num" in df.columns:
        print(f"\n[Page Range] {df['page_num'].min()} - {df['page_num'].max()}")

    # 段落文本长度统计
    if "para_text" in df.columns:
        df["text_length"] = df["para_text"].str.len()
        print(f"\n[Text Length Statistics]")
        print(f"  Average: {df['text_length'].mean():.1f} chars")
        print(f"  Min: {df['text_length'].min()} chars")
        print(f"  Max: {df['text_length'].max()} chars")

    if args.stats:
        return

    # 显示数据
    print("\n" + "=" * 80)
    print("DATA CONTENT")
    print("=" * 80)

    if args.full:
        # 显示全部数据
        pd.set_option("display.max_rows", None)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", None)
        pd.set_option("display.max_colwidth", None)
        print(df)
    else:
        # 显示前 N 行
        display_cols = ["style", "para_text"]
        if "page_num" in df.columns:
            display_cols.insert(1, "page_num")

        # 只显示存在的列
        display_cols = [col for col in display_cols if col in df.columns]

        print(df[display_cols].head(args.rows).to_string())

        if len(df) > args.rows:
            print(f"\n... {len(df) - args.rows} more rows (use --full to view all)")

    # 导出 CSV
    if args.export_csv:
        export_path = args.export_csv
        df.to_csv(export_path, index=False, encoding="utf-8-sig")
        print(f"\n[SUCCESS] Data exported to: {export_path}")

    # 交互式查看
    print("\n" + "=" * 80)
    print("TIPS: Use pandas for more analysis")
    print("=" * 80)
    print("""
# 在 Python 中交互式查看:
import pandas as pd
df = pd.read_pickle('preprocess/processed_output/MinerU/bylw-pgy/data.pkl')

# 查看特定样式的行
headings = df[df['style'].str.startswith('Heading', na=False)]
print(headings)

# 查看特定页的内容
page_5 = df[df['page_num'] == 5]
print(page_5)

# 搜索关键词
results = df[df['para_text'].str.contains('YOLO', na=False)]
print(results)
    """)


if __name__ == "__main__":
    main()
