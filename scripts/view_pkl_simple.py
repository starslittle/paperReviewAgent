"""
查看 data.pkl 内容的工具 (Windows 兼容版)
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
    parser = argparse.ArgumentParser(description="查看 data.pkl 内容")
    parser.add_argument("--data-path", type=str,
                       default="preprocess/processed_output/MinerU/bylw-pgy")
    parser.add_argument("--rows", type=int, default=20)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--export-csv", type=str)
    args = parser.parse_args()

    pkl_path = os.path.join(args.data_path, "data.pkl")
    if not os.path.exists(pkl_path):
        print(f"错误: 找不到文件 {pkl_path}")
        sys.exit(1)

    import pandas as pd

    print(f"正在读取: {pkl_path}")
    df = pd.read_pickle(pkl_path)

    print(f"\n成功读取数据!")
    print(f"总行数: {len(df)}")
    print(f"列名: {list(df.columns)}")

    print("\n" + "=" * 80)
    print("数据统计")
    print("=" * 80)

    if "style" in df.columns:
        style_counts = df["style"].value_counts()
        print("\n样式分布:")
        for style, count in style_counts.items():
            print(f"  {style}: {count}")

    if "para_text" in df.columns:
        df["text_length"] = df["para_text"].str.len()
        print(f"\n文本长度统计:")
        print(f"  平均: {df['text_length'].mean():.1f} 字符")
        print(f"  最短: {df['text_length'].min()} 字符")
        print(f"  最长: {df['text_length'].max()} 字符")

    if args.stats:
        return

    print("\n" + "=" * 80)
    print("数据内容 (前{}行)".format(args.rows))
    print("=" * 80)

    display_cols = ["style", "para_text"]
    display_cols = [col for col in display_cols if col in df.columns]

    # 设置显示选项
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', 100)

    print(df[display_cols].head(args.rows).to_string())

    if len(df) > args.rows:
        print(f"\n... 还有 {len(df) - args.rows} 行未显示")

    if args.export_csv:
        df.to_csv(args.export_csv, index=False, encoding='utf-8-sig')
        print(f"\n数据已导出到: {args.export_csv}")


if __name__ == "__main__":
    main()
