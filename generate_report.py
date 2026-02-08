import json
import argparse
import os
import re
from datetime import datetime


def generate_html(json_path, output_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues = data.get("issues", [])
    doc_id = data.get("doc_id", "Unknown Document")

    def escape_html(text):
        if not text:
            return ""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    # 预处理思考过程文本，确保不破坏 HTML 结构；空时显示占位说明
    def _thinking_with_fallback(key: str, fallback: str) -> str:
        raw = data.get(key, "")
        if not (raw and str(raw).strip()):
            return escape_html(fallback)
        return escape_html(str(raw))

    norm_thinking = _thinking_with_fallback(
        "normative_thinking",
        "（未记录：规范性审查的思考过程为空，可能因模型未返回 <thinking> 或该次运行未保存。请用 review_runner 重新跑一遍并生成报告。）",
    )
    logic_thinking = _thinking_with_fallback(
        "logic_thinking",
        "（未记录：逻辑审查的思考过程为空，可能因模型未返回 <thinking> 或该次运行未保存。请用 review_runner 重新跑一遍并生成报告。）",
    )
    vision_thinking = _thinking_with_fallback(
        "vision_thinking",
        "（未记录：视觉审查的思考过程为空。若使用最新代码，VisionAgent 会汇总决策轨迹；请用 review_runner 重新跑一遍并生成报告。）",
    )

    toc_suggestion = data.get("toc_suggestion") or {}
    toc_summary = (toc_suggestion.get("summary") or "").strip()
    toc_outline = toc_suggestion.get("suggested_outline") or []

    # 统计
    high_count = len([i for i in issues if i.get("severity") == "High"])
    medium_count = len([i for i in issues if i.get("severity") == "Medium"])
    low_count = len([i for i in issues if i.get("severity") == "Low"])
    total_score = max(0, 100 - (high_count * 5 + medium_count * 2 + low_count * 1))

    # 分类问题
    normative_issues = []
    logic_issues = []
    vision_issues = []
    other_issues = []

    # 页码排序辅助函数
    def get_page_num(issue):
        page = issue.get("page", "9999")
        if isinstance(page, int):
            return page
        if isinstance(page, str):
            # 处理 "12-14" 这种情况，取第一位数字
            match = re.search(r"(\d+)", page)
            if match:
                return int(match.group(1))
        return 9999

    # 定义逻辑类别的集合
    logic_types = {
        "Logic",
        "Language",
        "Coherence",
        "Cohesion",
        "逻辑性",
        "语言",
        "连贯性",
    }
    normative_types = {"Format", "规范性"}
    vision_types = {"Vision", "图文一致性"}

    for issue in issues:
        raw_type = issue.get("issue_type", "Unknown")
        img_id = issue.get("image_id", "")

        if img_id or raw_type in vision_types or "一致性" in raw_type:
            vision_issues.append(issue)
        elif raw_type in logic_types:
            logic_issues.append(issue)
        elif raw_type in normative_types:
            normative_issues.append(issue)
        else:
            if "一致性" in raw_type or "图" in raw_type or "视觉" in raw_type:
                vision_issues.append(issue)
            elif "逻辑" in raw_type or "语言" in raw_type or "连贯" in raw_type:
                logic_issues.append(issue)
            elif "格式" in raw_type or "规范" in raw_type:
                normative_issues.append(issue)
            else:
                other_issues.append(issue)

    # 在各分类内按页码排序
    normative_issues.sort(key=get_page_num)
    logic_issues.sort(key=get_page_num)
    vision_issues.sort(key=get_page_num)
    other_issues.sort(key=get_page_num)

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
            h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 15px; margin-top: 40px; margin-bottom: 20px; }}
            .category-header {{ background: #ecf0f1; padding: 10px 20px; border-radius: 6px; margin-top: 30px; margin-bottom: 15px; font-size: 1.2em; font-weight: bold; color: #34495e; border-bottom: 2px solid #bdc3c7; transition: background 0.3s; }}
            .category-header:hover {{ background: #dfe6e9; }}
            .category-container {{ transition: all 0.3s ease; }}
            .dashboard {{ display: flex; gap: 20px; margin-bottom: 30px; }}
            .card {{ flex: 1; padding: 20px; border-radius: 8px; text-align: center; color: white; }}
            .bg-blue {{ background: #3498db; }}
            .bg-red {{ background: #e74c3c; }}
            .bg-orange {{ background: #f39c12; }}
            .bg-green {{ background: #2ecc71; }}
            .score {{ font-size: 2.5em; font-weight: bold; }}
            .issue-card {{ border: 1px solid #ddd; margin-bottom: 15px; border-radius: 6px; overflow: hidden; }}
            .issue-header {{ padding: 10px 15px; background: #f8f9fa; cursor: pointer; }}
            .issue-header-first-row {{ display: flex; align-items: center; justify-content: space-between; }}
            .issue-header-suggestion {{ margin-top: 6px; padding-left: 1.2em; line-height: 1.5; }}
            .issue-body {{ padding: 18px; display: none; border-top: 1px solid #ddd; line-height: 1.6; }}
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; color: white; }}
            .badge.High {{ background: #e74c3c; }}
            .badge.Medium {{ background: #f39c12; }}
            .badge.Low {{ background: #3498db; }}
            .quote {{ background: #fff3cd; padding: 10px; border-left: 4px solid #ffc107; margin: 10px 0; font-style: italic; }}
            .suggestion {{ background: #d4edda; padding: 10px; border-left: 4px solid #28a745; margin: 10px 0; }}
            .thinking-box {{ background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 4px; margin-top: 24px; margin-bottom: 8px; }}
            .thinking-header {{ padding: 12px 18px; background: #e9ecef; cursor: pointer; font-weight: bold; display: flex; justify-content: space-between; align-items: center; font-size: 1.05em; }}
            .thinking-content {{ padding: 20px; display: block; background: #fff; color: #333; font-family: 'Segoe UI', sans-serif; line-height: 1.1; min-height: 220px; max-height: 55vh; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}
            .thinking-content h1, .thinking-content h2, .thinking-content h3 {{ margin-top: 0.2em; margin-bottom: 0.1em; color: #2c3e50; border-bottom: 1px solid #eee; padding-bottom: 2px; }}
            .thinking-content h3 {{ color: #2980b9; border-left: 4px solid #2980b9; padding-left: 8px; background: #f0f7fd; padding: 4px 8px; border-radius: 4px; }}
            .thinking-content p {{ margin-bottom: 0.05em; line-height: 1.1; }}
            .thinking-content ul, .thinking-content ol {{ margin-left: 20px; margin-bottom: 0.1em; line-height: 1.1; }}
            .thinking-content code {{ background: #f1f3f5; padding: 2px 4px; border-radius: 3px; font-family: Consolas, monospace; color: #c7254e; }}
            .thinking-content pre {{ background: #f8f9fa; padding: 8px; border-radius: 4px; overflow-x: auto; border: 1px solid #ddd; margin-bottom: 0.2em; }}
            .thinking-content pre code {{ background: none; color: inherit; padding: 0; }}
            .meta {{ font-size: 0.9em; color: #7f8c8d; margin-bottom: 5px; }}
            .empty-msg {{ color: #95a5a6; font-style: italic; padding: 10px; }}
        </style>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <script>
            function toggle(id) {{
                var x = document.getElementById(id);
                var currentDisplay = window.getComputedStyle(x).display;
                if (currentDisplay === "none") {{
                    x.style.display = "block";
                }} else {{
                    x.style.display = "none";
                }}
            }}
            document.addEventListener("DOMContentLoaded", function() {{
                var markdownDivs = document.querySelectorAll(".markdown-content");
                markdownDivs.forEach(function(div) {{
                    var rawContent = div.textContent.trim();
                    if (!rawContent || rawContent === "无思考过程") return;
                    
                    try {{
                        var processedContent = rawContent.replace(/^\[Image\s+(.*?)\s*\|\s*Page\s+(.*?)\]\s*(.*)/gm, '### 🖼️ 图片分析: $1 (第 $2 页)\\n\\n$3');
                        processedContent = processedContent.replace(/\[Image\s+(.*?)\s*\|\s*Page\s+(.*?)\]\s*(.*)/g, '\\n\\n### 🖼️ 图片分析: $1 (第 $2 页)\\n\\n$3');
                        
                        // 修复视觉验证环节的图标：移除误报用勾，保留Issue用叉
                        processedContent = processedContent.replace(/❌\\s+\*\*移除误报/g, '✅ **移除误报');
                        processedContent = processedContent.replace(/✅\\s+\*\*保留\\s+Issue/g, '❌ **保留 Issue');
                        
                        if (typeof marked !== 'undefined') {{
                            div.innerHTML = marked.parse(processedContent);
                        }}
                    }} catch (e) {{
                        console.error("Markdown parse error:", e);
                    }}
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
                    <span>▶</span>
                </div>
                <div id="thinking_norm" class="thinking-content markdown-content" style="display: none;">
                    {norm_thinking}
                </div>
            </div>

            <div class="thinking-box">
                <div class="thinking-header" onclick="toggle('thinking_logic')">
                    <span>🧠 AI 思考过程：逻辑审查</span>
                    <span>▶</span>
                </div>
                <div id="thinking_logic" class="thinking-content markdown-content" style="display: none;">
                    {logic_thinking}
                </div>
            </div>

            <div class="thinking-box" style="margin-bottom: 30px;">
                <div class="thinking-header" onclick="toggle('thinking_vision')">
                    <span>🧠 AI 思考过程：视觉审查</span>
                    <span>▶</span>
                </div>
                <div id="thinking_vision" class="thinking-content markdown-content" style="display: none;">
                    {vision_thinking}
                </div>
            </div>

            {f'''
            <div class="thinking-box" style="margin-bottom: 30px; border-left: 4px solid #3498db;">
                <div class="thinking-header" onclick="toggle('toc_suggestion')" style="background: #e8f4fc;">
                    <span>📑 目录检测：AI 总建议与修改后的推荐目录</span>
                    <span>▶</span>
                </div>
                <div id="toc_suggestion" class="thinking-content" style="display: none; padding: 20px; background: #fff;">
                    <p><strong>总建议：</strong></p>
                    <p style="margin-left: 1em; line-height: 1.6;">{escape_html(toc_summary) or "（无）"}</p>
                    <p style="margin-top: 16px;"><strong>修改后的推荐目录：</strong></p>
                    <ul style="margin-left: 1.5em; line-height: 1.8; list-style: none; padding-left: 0;">{"".join(f"<li>{escape_html(line)}</li>" for line in toc_outline)}</ul>
                </div>
            </div>
            ''' if (toc_summary or toc_outline) else ""}

            <div style="clear: both; margin-top: 40px; border-top: 2px solid #eee; padding-top: 20px;">
                <h2>📝 详细修改建议</h2>
            </div>
    """

    # 定义英文到中文的类型映射（兜底用）
    type_mapping = {
        "Format": "规范性",
        "Logic": "逻辑性",
        "Language": "语言",
        "Coherence": "连贯性",
        "Cohesion": "连贯性",
        "Vision": "图文一致性",
        "EVIDENCE_GENERALIZATION": "证据外推",
        "Unknown": "未分类",
    }

    def render_issues(issue_list, title, start_idx, cat_id):
        nonlocal html
        html += f"""
            <div class="category-header" onclick="toggle('{cat_id}')" style="cursor: pointer; display: flex; justify-content: space-between; align-items: center;">
                <span>{title} ({len(issue_list)})</span>
                <span>▼</span>
            </div>
            <div id="{cat_id}" class="category-container">
        """
        if not issue_list:
            html += '<div class="empty-msg">未发现该类别问题</div>'
            html += "</div>"
            return start_idx

        for i, issue in enumerate(issue_list):
            idx = start_idx + i
            severity = issue.get("severity", "Medium")
            raw_issue_type = issue.get("issue_type", "Unknown")
            issue_type_base = type_mapping.get(raw_issue_type, raw_issue_type)
            issue_type = (
                f"{issue_type_base}问题"
                if issue_type_base != "未分类"
                else issue_type_base
            )
            page = issue.get("page", "N/A")
            img_id = issue.get("image_id", "")
            caption = issue.get("caption", "")
            image_name = issue.get("image_name", "")

            modification_advice = issue.get("modification_advice", {}) or {}
            modification_target = modification_advice.get("modification_target", "")
            modification_reason = modification_advice.get("reason", "")
            modification_suggestion = modification_advice.get("suggestion", "")

            # 标题描述显示原文片段，便于快速定位问题
            title_quote = issue.get("quote", "无引用")
            quote_short = (
                title_quote[:160] + "..." if len(title_quote) > 160 else title_quote
            )

            modification_target_map = {
                "MODIFY_FIGURE": "优先改图",
                "MODIFY_TEXT": "优先改文",
                "BOTH_LIGHT": "轻微调整（图文均可）",
            }
            modification_target_cn = modification_target_map.get(
                modification_target, modification_target
            )
            if isinstance(modification_reason, str) and modification_reason:
                modification_reason = (
                    modification_reason.replace("MODIFY_FIGURE", "优先改图")
                    .replace("MODIFY_TEXT", "优先改文")
                    .replace("BOTH_LIGHT", "轻微调整")
                    .replace("EVIDENCE", "证据阶段")
                    .replace("METHOD", "方法阶段")
                    .replace("RESULT/COMPARISON", "结果/对比主张")
                )

            # 构建标题前缀（到「第X页:」为止）与建议内容分开，便于第二行起缩进显示
            if img_id:
                image_name_short = (
                    image_name[:50] + "..." if len(image_name) > 50 else image_name
                )
                if caption:
                    caption_short = (
                        caption[:50] + "..." if len(caption) > 50 else caption
                    )
                    if image_name_short:
                        title_prefix = (
                            f"[图表 {img_id} | {image_name_short}: {caption_short}] "
                            f"[{issue_type}] 第 {page} 页:"
                        )
                    else:
                        title_prefix = (
                            f"[图表 {img_id}: {caption_short}] "
                            f"[{issue_type}] 第 {page} 页:"
                        )
                else:
                    if image_name_short:
                        title_prefix = (
                            f"[图表 {img_id} | {image_name_short}] "
                            f"[{issue_type}] 第 {page} 页:"
                        )
                    else:
                        title_prefix = f"[图表 {img_id}] [{issue_type}] 第 {page} 页:"
            else:
                title_prefix = f"[{issue_type}] 第 {page} 页:"

            html += f"""
                <div class="issue-card">
                    <div class="issue-header" onclick="toggle('issue_{idx}')">
                        <div class="issue-header-first-row">
                            <span>
                                <span class="badge {severity}">{severity}</span>
                                <strong>{title_prefix}</strong>
                            </span>
                            <span>▼</span>
                        </div>
                        <div class="issue-header-suggestion">{escape_html(quote_short)}</div>
                    </div>
                    <div id="issue_{idx}" class="issue-body">
                        <div class="meta">📍 位置: 第 {page} 页 | 章节: {issue.get('section', '未知')}</div>
                        {f'<div class="meta" style="margin-top: 8px; color: #666;">🖼️ 图片名称: {escape_html(image_name)}</div>' if image_name else ''}
                        {f'<div class="meta" style="margin-top: 8px; color: #666;">📊 图表名称: {escape_html(caption)}</div>' if caption else ''}
                        
                        <div class="quote">
                            <strong>原文片段/描述:</strong><br>
                            {issue.get('quote', '无引用')}
                        </div>
                        
                        <div class="suggestion">
                            <strong>🤖 AI 修改建议:</strong><br>
                            {escape_html(modification_suggestion) or escape_html(issue.get('suggestion', '无建议'))}
                        </div>
                        {(
                            f'<div class="suggestion"><strong>🧭 修改方向:</strong><br>'
                            f'{escape_html(modification_target_cn) or "未给出"}</div>'
                            if modification_advice
                            else ""
                        )}
                        {(
                            f'<div class="suggestion"><strong>📌 修改理由:</strong><br>'
                            f'{escape_html(modification_reason) or "未给出"}</div>'
                            if modification_advice
                            else ""
                        )}
                    </div>
                </div>
            """
        html += "</div>"
        return start_idx + len(issue_list)

    # 按类别渲染
    current_idx = 0
    current_idx = render_issues(
        normative_issues, "📏 规范性一致性问题", current_idx, "cat_norm"
    )
    current_idx = render_issues(
        logic_issues, "🧠 逻辑性一致性问题", current_idx, "cat_logic"
    )
    current_idx = render_issues(
        vision_issues, "🖼️ 图文一致性问题", current_idx, "cat_vision"
    )

    if other_issues:
        current_idx = render_issues(
            other_issues, "❓ 其他分类问题", current_idx, "cat_other"
        )

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
