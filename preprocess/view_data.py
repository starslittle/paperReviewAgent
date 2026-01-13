"""
快速查看处理后的 data.pkl 文件内容
"""

import pandas as pd
import sys
import os

def main():
    if len(sys.argv) < 2:
        print("用法: python view_data.py <data.pkl路径>")
        print("\n示例:")
        print("  python view_data.py ./processed_output/bylw/data.pkl")
        return
    
    pkl_path = sys.argv[1]
    
    if not os.path.exists(pkl_path):
        print(f"[错误] 文件不存在: {pkl_path}")
        return
    
    print("=" * 70)
    print(f"📄 查看文档数据: {pkl_path}")
    print("=" * 70)
    print()
    
    # 读取数据
    df = pd.read_pickle(pkl_path)
    
    print(f"📊 基本信息:")
    print(f"   总行数: {len(df)}")
    print(f"   列名: {df.columns.tolist()}")
    print()
    
    # 统计样式类型
    print("📋 样式类型统计:")
    style_counts = df['style'].value_counts()
    for style, count in style_counts.items():
        print(f"   {style:20} {count:5} 条")
    print()
    
    # 提取标题
    headings = df[df['style'].str.startswith('Heading', na=False)]
    
    print("=" * 70)
    print(f"📑 检测到的标题 (共 {len(headings)} 个):")
    print("=" * 70)
    print()
    
    if len(headings) == 0:
        print("⚠️  未检测到任何标题！")
        print("    建议使用 --use-ocr 参数重新处理")
    else:
        for idx, row in headings.iterrows():
            level = row['style'].split()[1]
            indent = "  " * (int(level) - 1)
            text = row['para_text'][:60]
            if len(row['para_text']) > 60:
                text += "..."
            print(f"{indent}[{row['style']}] {text}")
    
    print()
    print("=" * 70)
    
    # 显示前几条正文
    print()
    print("📝 前 5 条正文内容:")
    print("-" * 70)
    normals = df[df['style'] == 'Normal'].head(5)
    for idx, row in normals.iterrows():
        text = row['para_text'][:80]
        if len(row['para_text']) > 80:
            text += "..."
        print(f"[{idx}] {text}")
    
    print()
    print("✅ 数据查看完成")
    print()
    print("💡 提示:")
    print("   - 如果标题数量少，建议使用 OCR 增强")
    print("   - 如果章节编号不连续，检查原始 PDF")
    print()

if __name__ == "__main__":
    main()

