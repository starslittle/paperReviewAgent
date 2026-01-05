import json
import argparse
import os
from datetime import datetime


def generate_html(json_path, output_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues = data.get("issues", [])
    doc_id = data.get("doc_id", "Unknown Document")

    # 统计
    high_count = len([i for i in issues if i.get("severity") == "High"])
    medium_count = len([i for i in issues if i.get("severity") == "Medium"])
    low_count = len([i for i in issues if i.get("severity") == "Low"])
    total_score = max(0, 100 - (high_count * 5 + medium_count * 2 + low_count * 1))

    # HTML 模板
    html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>审查报告 - {doc_id}</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #f5f7fa; color: #333; margin: 0; padding: 20px; }}
            .container {{ max_width: 1000px; margin: 0 auto; background: white; padding: 40px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border-radius: 8px; }}
            h1 {{ border-bottom: 2px solid #eaeaea; padding-bottom: 10px; }}
            .dashboard {{ display: flex; gap: 20px; margin-bottom: 30px; }}
            .card {{ flex: 1; padding: 20px; border-radius: 8px; text-align: center; color: white; }}
            .bg-blue {{ background: #3498db; }}
            .bg-red {{ background: #e74c3c; }}
            .bg-orange {{ background: #f39c12; }}
            .bg-green {{ background: #2ecc71; }}
            .score {{ font-size: 2.5em; font-weight: bold; }}
            .issue-card {{ border: 1px solid #ddd; margin-bottom: 15px; border-radius: 6px; overflow: hidden; }}
            .issue-header {{ padding: 10px 15px; background: #f8f9fa; display: flex; align-items: center; justify-content: space-between; cursor: pointer; }}
            .issue-body {{ padding: 15px; display: none; border-top: 1px solid #ddd; }}
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; color: white; }}
            .badge.High {{ background: #e74c3c; }}
            .badge.Medium {{ background: #f39c12; }}
            .badge.Low {{ background: #3498db; }}
            .quote {{ background: #fff3cd; padding: 10px; border-left: 4px solid #ffc107; margin: 10px 0; font-style: italic; }}
            .suggestion {{ background: #d4edda; padding: 10px; border-left: 4px solid #28a745; margin: 10px 0; }}
            .thinking-box {{ background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 4px; margin-top: 20px; }}
            .thinking-header {{ padding: 10px 15px; background: #e9ecef; cursor: pointer; font-weight: bold; display: flex; justify-content: space-between; align-items: center; }}
            .thinking-content {{ padding: 15px; display: none; background: #fff; color: #333; font-family: 'Segoe UI', sans-serif; line-height: 1.6; }}
            .thinking-content h1, .thinking-content h2, .thinking-content h3 {{ margin-top: 1em; margin-bottom: 0.5em; color: #2c3e50; }}
            .thinking-content p {{ margin-bottom: 1em; }}
            .thinking-content ul, .thinking-content ol {{ margin-left: 20px; margin-bottom: 1em; }}
            .thinking-content code {{ background: #f1f3f5; padding: 2px 4px; border-radius: 3px; font-family: Consolas, monospace; color: #c7254e; }}
            .thinking-content pre {{ background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto; border: 1px solid #ddd; }}
            .thinking-content pre code {{ background: none; color: inherit; padding: 0; }}
            .meta {{ font-size: 0.9em; color: #7f8c8d; margin-bottom: 5px; }}
        </style>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <script>
            function toggle(id) {{
                var x = document.getElementById(id);
                if (x.style.display === "none" || x.style.display === "") {{
                    x.style.display = "block";
                }} else {{
                    x.style.display = "none";
                }}
            }}
            document.addEventListener("DOMContentLoaded", function() {{
                var markdownDivs = document.querySelectorAll(".markdown-content");
                markdownDivs.forEach(function(div) {{
                    var rawContent = div.textContent.trim();
                    // Fix: Add newlines before each [Image X] thinking block to ensure they render as separate paragraphs/headers
                    // This regex looks for [Image and adds two newlines before it
                    // Also make "Image X" bold using markdown syntax
                    var processedContent = rawContent.replace(/(\\[Image\\s+.*?\\]:)/g, '\\n\\n**$1**\\n');
                    div.innerHTML = marked.parse(processedContent);
                }});
            }});
        </script>
    </head>
    <body>
        <div class="container">
            <h1>🎓 论文审查报告: {doc_id}</h1>
            <p>生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
            
            <div class="dashboard">
                <div class="card bg-blue">
                    <div class="score">{total_score}</div>
                    <div>综合评分</div>
                </div>
                <div class="card bg-red">
                    <div class="score">{high_count}</div>
                    <div>严重问题</div>
                </div>
                <div class="card bg-orange">
                    <div class="score">{medium_count}</div>
                    <div>建议修改</div>
                </div>
                <div class="card bg-green">
                    <div class="score">{len(issues)}</div>
                    <div>问题总数</div>
                </div>
            </div>

            <div class="thinking-box">
                <div class="thinking-header" onclick="toggle('thinking_norm')">
                    <span>🧠 AI 思考过程：规范性审查</span>
                    <span>▼</span>
                </div>
                <div id="thinking_norm" class="thinking-content markdown-content">
                    {data.get('normative_thinking', '无思考过程')}
                </div>
            </div>

            <div class="thinking-box">
                <div class="thinking-header" onclick="toggle('thinking_logic')">
                    <span>🧠 AI 思考过程：逻辑审查</span>
                    <span>▼</span>
                </div>
                <div id="thinking_logic" class="thinking-content markdown-content">
                    {data.get('logic_thinking', '无思考过程')}
                </div>
            </div>

            <div class="thinking-box" style="margin-bottom: 30px;">
                <div class="thinking-header" onclick="toggle('thinking_vision')">
                    <span>🧠 AI 思考过程：视觉审查</span>
                    <span>▼</span>
                </div>
                <div id="thinking_vision" class="thinking-content markdown-content">
                    {data.get('vision_thinking', '无思考过程')}
                </div>
            </div>

            <h2>📝 详细修改建议</h2>
    """

    # 定义英文到中文的类型映射（兜底用）
    type_mapping = {
        "Format": "规范性",
        "Logic": "逻辑性",
        "Language": "语言",
        "Coherence": "连贯性",
        "Cohesion": "连贯性",  # 兼容旧版拼写
        "Vision": "图文一致性",
        "Unknown": "未分类",
    }

    for idx, issue in enumerate(issues):
        severity = issue.get("severity", "Medium")
        raw_issue_type = issue.get("issue_type", "Unknown")
        # 如果是英文类型，自动转换为中文
        issue_type_base = type_mapping.get(raw_issue_type, raw_issue_type)
        # 在类型后面加上"问题"
        issue_type = (
            f"{issue_type_base}问题" if issue_type_base != "未分类" else issue_type_base
        )
        page = issue.get("page", "N/A")
        img_id = issue.get("image_id", "")

        suggestion_short = (
            issue.get("suggestion", "")[:40] + "..."
            if len(issue.get("suggestion", "")) > 40
            else issue.get("suggestion", "")
        )

        title_text = f"[{issue_type}] 第 {page} 页: {suggestion_short}"
        if img_id:
            title_text = f"[视觉/图{img_id}] " + title_text

        html += f"""
            <div class="issue-card">
                <div class="issue-header" onclick="toggle('issue_{idx}')">
                    <span>
                        <span class="badge {severity}">{severity}</span>
                        <strong>{title_text}</strong>
                    </span>
                    <span>▼</span>
                </div>
                <div id="issue_{idx}" class="issue-body">
                    <div class="meta">📍 位置: 第 {page} 页 | 章节: {issue.get('section', '未知')}</div>
                    
                    <div class="quote">
                        <strong>原文片段/描述:</strong><br>
                        {issue.get('quote', '无引用')}
                    </div>
                    
                    <div class="suggestion">
                        <strong>🤖 AI 修改建议:</strong><br>
                        {issue.get('suggestion', '无建议')}
                    </div>
                </div>
            </div>
        """

    html += """
        </div>
    </body>
    </html>
    """

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Report] Generated: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--result-dir", default="./sample_results/")
    args = parser.parse_args()

    json_file = os.path.join(args.result_dir, f"review_{args.doc_id}.json")
    html_file = os.path.join(args.result_dir, f"report_{args.doc_id}.html")

    if os.path.exists(json_file):
        generate_html(json_file, html_file)
    else:
        print(f"Error: {json_file} not found.")
