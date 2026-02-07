"""
演示如何从MinerU的layout.json提取完整信息
包括：页码、bbox、标题层级、图片、表格等
"""
import json
import pandas as pd
from typing import Dict, List, Tuple


def extract_text_from_block(block: Dict) -> str:
    """从block中提取文本内容"""
    lines = block.get('lines', [])
    text_parts = []
    for line in lines:
        spans = line.get('spans', [])
        for span in spans:
            content = span.get('content', '')
            if content:
                text_parts.append(content)
    return ' '.join(text_parts)


def get_heading_level_by_height(bbox: List[int], page_size: List[int]) -> int:
    """
    根据bbox高度判断标题层级
    高度越大 -> 层级越高（一级标题）
    """
    height = bbox[3] - bbox[1]  # y1 - y0
    page_height = page_size[1]
    height_ratio = height / page_height

    if height_ratio > 0.08:    # 占页面8%以上 -> 一级标题
        return 1
    elif height_ratio > 0.05:  # 占页面5-8% -> 二级标题
        return 2
    else:                      # 占页面5%以下 -> 三级标题
        return 3


def process_layout_json(layout_json_path: str) -> pd.DataFrame:
    """
    处理MinerU的layout.json，生成标准DataFrame

    Args:
        layout_json_path: layout.json文件路径

    Returns:
        DataFrame包含以下列:
        - para_text: 文本内容或图片/表格路径字典
        - style: 样式类型 (Heading 1/2/3, Normal, Image, Table, etc.)
        - table_id: 编号或页码
        - page_idx: 页码（0-based）
        - bbox: 边界框坐标 [x0, y0, x1, y1]
        - font_size: 字体大小（使用bbox高度作为代理）
    """
    with open(layout_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    records = []
    image_count = 0
    table_count = 0

    print(f"处理文档，共 {len(data['pdf_info'])} 页")

    # 遍历每一页
    for page in data['pdf_info']:
        page_idx = page['page_idx']
        page_size = page.get('page_size', [595, 841])  # 默认A4
        page_num = page_idx + 1  # 人类可读页码（1-based）

        # 处理段落块（para_blocks）：包含title和text
        for block in page.get('para_blocks', []):
            bbox = block['bbox']
            font_size = bbox[3] - bbox[1]  # 使用高度作为字体大小代理

            if block['type'] == 'title':
                # 判断标题层级
                level = get_heading_level_by_height(bbox, page_size)
                text = extract_text_from_block(block)

                records.append({
                    'para_text': text,
                    'style': f'Heading {level}',
                    'table_id': str(page_num),
                    'font_size': font_size,
                    'font_family': None,
                    'bbox': bbox,
                    'page_idx': page_idx
                })

            elif block['type'] == 'text':
                text = extract_text_from_block(block)

                # 检查是否是列表项
                is_list = any(
                    line.get('is_list_start_line', False)
                    for line in block.get('lines', [])
                )

                style = 'List Paragraph' if is_list else 'Normal'

                records.append({
                    'para_text': text,
                    'style': style,
                    'table_id': str(page_num),
                    'font_size': font_size,
                    'font_family': None,
                    'bbox': bbox,
                    'page_idx': page_idx
                })

        # 处理预处理块（preproc_blocks）：包含image和table
        for block in page.get('preproc_blocks', []):
            bbox = block['bbox']

            if block['type'] == 'image':
                # 提取图片信息
                image_path = None
                caption = None

                # 遍历子块
                for sub_block in block.get('blocks', []):
                    if sub_block['type'] == 'image_body':
                        # 提取图片路径
                        lines = sub_block.get('lines', [])
                        if lines and lines[0].get('spans'):
                            span = lines[0]['spans'][0]
                            if span.get('type') == 'image':
                                image_path = span.get('image_path')

                    elif sub_block['type'] == 'image_caption':
                        # 提取图片标题
                        caption = extract_text_from_block(sub_block)

                records.append({
                    'para_text': {
                        'path': f"images/{image_path}" if image_path else None,
                        'alt_text': caption
                    },
                    'style': 'Image',
                    'table_id': str(image_count),
                    'font_size': None,
                    'font_family': None,
                    'bbox': bbox,
                    'page_idx': page_idx
                })
                image_count += 1

            elif block['type'] == 'table':
                # 提取表格信息
                table_html = None
                table_image_path = None
                caption = None

                # 遍历子块
                for sub_block in block.get('blocks', []):
                    if sub_block['type'] == 'table_caption':
                        # 提取表格标题
                        caption = extract_text_from_block(sub_block)

                    elif sub_block['type'] == 'table_body':
                        # 提取表格HTML和图片
                        lines = sub_block.get('lines', [])
                        if lines and lines[0].get('spans'):
                            span = lines[0]['spans'][0]
                            table_html = span.get('html')
                            if span.get('type') == 'table':
                                table_image_path = span.get('image_path')

                # 将HTML转换为Markdown（简化版）
                table_content = html_to_markdown(table_html) if table_html else ""

                records.append({
                    'para_text': {
                        'content': table_content,
                        'html': table_html,
                        'image_path': table_image_path
                    },
                    'style': 'Table',
                    'table_id': str(table_count),
                    'font_size': None,
                    'font_family': None,
                    'bbox': bbox,
                    'page_idx': page_idx
                })
                table_count += 1

    df = pd.DataFrame(records)

    # 打印统计信息
    print(f"\n处理完成:")
    print(f"  总记录数: {len(df)}")
    print(f"  样式分布:")
    for style, count in df['style'].value_counts().items():
        print(f"    - {style}: {count}")
    print(f"  图片数: {image_count}")
    print(f"  表格数: {table_count}")

    return df


def html_to_markdown(html: str) -> str:
    """
    简单的HTML表格转Markdown
    注意：这是一个简化版本，实际项目可能需要更复杂的转换
    """
    if not html:
        return ""

    try:
        from html.parser import HTMLParser

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows = []
                self.current_row = []
                self.in_td = False

            def handle_starttag(self, tag, attrs):
                if tag == 'tr':
                    self.current_row = []
                elif tag == 'td':
                    self.in_td = True
                    self.current_cell = []

            def handle_endtag(self, tag):
                if tag == 'tr':
                    self.rows.append(self.current_row)
                elif tag == 'td':
                    self.in_td = False

            def handle_data(self, data):
                if self.in_td:
                    self.current_cell.append(data.strip())

        parser = TableParser()
        parser.feed(html)

        # 转换为Markdown
        if not parser.rows:
            return ""

        markdown_lines = []
        for i, row in enumerate(parser.rows):
            # 清理单元格内容
            cells = [' '.join(cell) for cell in row]
            markdown_lines.append('| ' + ' | '.join(cells) + ' |')

            # 添加分隔线（在第一行后）
            if i == 0:
                separator = '|' + '|'.join(['---' for _ in cells]) + '|'
                markdown_lines.append(separator)

        return '\n'.join(markdown_lines)

    except Exception as e:
        print(f"HTML转Markdown失败: {e}")
        return ""


def analyze_bbox_statistics(df: pd.DataFrame):
    """分析bbox统计信息"""
    print("\n=== BBox统计分析 ===")

    # 过滤有bbox的记录
    with_bbox = df[df['bbox'].notna()]

    if len(with_bbox) == 0:
        print("没有bbox数据")
        return

    # 计算宽度和高度
    with_bbox = with_bbox.copy()
    with_bbox['width'] = with_bbox['bbox'].apply(lambda b: b[2] - b[0])
    with_bbox['height'] = with_bbox['bbox'].apply(lambda b: b[3] - b[1])

    print(f"\n总体统计:")
    print(f"  宽度范围: {with_bbox['width'].min():.0f} - {with_bbox['width'].max():.0f} px")
    print(f"  高度范围: {with_bbox['height'].min():.0f} - {with_bbox['height'].max():.0f} px")

    # 按样式分组统计
    print(f"\n按样式分组:")
    for style in ['Heading 1', 'Heading 2', 'Heading 3', 'Normal']:
        style_df = with_bbox[with_bbox['style'] == style]
        if len(style_df) > 0:
            print(f"  {style}:")
            print(f"    数量: {len(style_df)}")
            print(f"    平均高度: {style_df['height'].mean():.1f} px")
            print(f"    高度范围: {style_df['height'].min():.0f} - {style_df['height'].max():.0f} px")


if __name__ == "__main__":
    # 示例用法
    layout_path = r"C:\Users\10245\Desktop\paperwiseAI\DocAgent-main\extract_output\bylw\MinerU\layout.json"

    print("开始处理layout.json...")
    df = process_layout_json(layout_path)

    # 保存结果
    output_path = layout_path.replace('layout.json', 'processed_data.pkl')
    df.to_pickle(output_path)
    print(f"\n结果已保存到: {output_path}")

    # 同时保存CSV方便查看
    csv_path = layout_path.replace('layout.json', 'processed_data.csv')
    # 将复杂对象转换为字符串
    df_for_csv = df.copy()
    df_for_csv['para_text'] = df_for_csv['para_text'].apply(
        lambda x: str(x) if isinstance(x, dict) else x
    )
    df_for_csv['bbox'] = df_for_csv['bbox'].apply(
        lambda x: str(x) if isinstance(x, list) else x
    )
    df_for_csv.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"CSV已保存到: {csv_path}")

    # 分析bbox统计
    analyze_bbox_statistics(df)

    # 显示前几条记录
    print("\n=== 前10条记录预览 ===")
    for i, row in df.head(10).iterrows():
        print(f"\n记录 {i+1}:")
        print(f"  样式: {row['style']}")
        print(f"  页码: {row['page_idx'] + 1}")
        if isinstance(row['para_text'], dict):
            print(f"  内容: {str(row['para_text'])[:100]}...")
        else:
            print(f"  内容: {row['para_text'][:100]}...")
        if row['bbox']:
            print(f"  BBox: {row['bbox']}")
            print(f"  位置: 距左{row['bbox'][0]}px, 距顶{row['bbox'][1]}px")
