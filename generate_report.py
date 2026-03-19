import json
import argparse
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


def generate_html(json_path, output_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues = data.get("issues", [])
    pending_review_issues = data.get("pending_review_issues", [])
    all_issues = data.get("all_issues", []) or (issues + pending_review_issues)
    doc_id = data.get("doc_id", "Unknown Document")
    preprocess_eval = {}
    preprocess_eval_path = os.path.join(
        os.path.dirname(os.path.abspath(json_path)), f"preprocess_eval_{doc_id}.json"
    )
    if os.path.exists(preprocess_eval_path):
        try:
            with open(preprocess_eval_path, "r", encoding="utf-8") as ef:
                preprocess_eval = json.load(ef)
        except Exception:
            preprocess_eval = {}

    preprocess_risk_score = (
        preprocess_eval.get("preprocess_quality", {}).get("risk_score")
        if preprocess_eval
        else None
    )
    evidence_unmatched_rate = (
        preprocess_eval.get("evidence_match_proxy", {}).get("unmatched_rate")
        if preprocess_eval
        else None
    )
    verifiable_non_layout_count = (
        preprocess_eval.get("evidence_match_proxy", {}).get("verifiable_non_layout_count")
        if preprocess_eval
        else None
    )
    preprocess_risk_text = (
        f"{float(preprocess_risk_score):.4f}"
        if isinstance(preprocess_risk_score, (int, float))
        else "N/A"
    )
    evidence_unmatched_text = (
        f"{float(evidence_unmatched_rate):.2%}"
        if isinstance(evidence_unmatched_rate, (int, float))
        else "N/A"
    )
    evidence_sample_text = (
        str(int(verifiable_non_layout_count))
        if isinstance(verifiable_non_layout_count, int)
        else "-"
    )
    # 开发阶段默认全量展示，便于逐条核对 AI 建议正确性
    full_display_mode = True

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

    def enrich_missing_subsection_text(text: str) -> str:
        """将“缺少必备小节 X.Y”增强为“缺少必备小节 X.Y 标题”以便快速定位。"""
        if not text:
            return ""
        subsection_title_map = {
            "3.3": "非功能性需求分析",
            "3.4": "业务流程分析",
            "5.1": "开发环境",
            "6.4": "测试结果",
            "7.1": "总结",
            "7.2": "展望",
        }
        s = str(text)
        for num, title in subsection_title_map.items():
            pattern = rf"(缺少必备小节\s*{re.escape(num)})(?!\s*{re.escape(title)})"
            s = re.sub(pattern, rf"\1 {title}", s)
        return s

    def _norm_cmp_text(text: str) -> str:
        s = str(text or "").strip().lower()
        s = re.sub(r"\s+", "", s)
        s = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`【】\[\]<>]", "", s)
        return s

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

    def _resolve_rule_doc_link() -> tuple[str, str]:
        """定位 rules 中的“开发类论文目录结构”文档并返回 report 可用的相对链接。"""
        project_root = os.path.dirname(os.path.abspath(__file__))
        rules_dir = os.path.join(project_root, "rules")
        if not os.path.isdir(rules_dir):
            return "", ""
        docx_files = [
            os.path.join(rules_dir, x)
            for x in os.listdir(rules_dir)
            if x.lower().endswith(".docx")
        ]
        if not docx_files:
            return "", ""
        preferred = [
            p for p in docx_files if "开发类论文目录结构" in os.path.basename(p)
        ]
        rule_path = preferred[0] if preferred else docx_files[0]

        rel_path = os.path.relpath(
            rule_path, os.path.dirname(os.path.abspath(output_path))
        ).replace("\\", "/")
        href = urllib.parse.quote(rel_path, safe="/-_.")
        return href, os.path.basename(rule_path)

    rule_doc_href, rule_doc_name = _resolve_rule_doc_link()

    toc_suggestion = data.get("toc_suggestion") or {}
    toc_summary = (toc_suggestion.get("summary") or "").strip()
    toc_outline = toc_suggestion.get("suggested_outline") or []
    # 目录结构问题默认“汇总展示”：降低逐条噪声，强调可执行整改路径
    toc_summary_only_mode = bool(toc_summary or toc_outline)
    tutor_feedback = data.get("tutor_feedback") or {}
    tutor_overall = (tutor_feedback.get("overall_comment") or "").strip()
    tutor_tasks = tutor_feedback.get("tasks") or []

    # 去重基准：导师任务单中的（章节 + 问题描述）不再出现在待复核区
    tutor_task_keys = set()
    for t in tutor_tasks:
        t_sec = _norm_cmp_text(t.get("section", ""))
        t_prob = _norm_cmp_text(t.get("problem", ""))
        if t_prob:
            tutor_task_keys.add((t_sec, t_prob))

    def _issue_in_tutor_tasks(issue: dict) -> bool:
        i_sec = _norm_cmp_text(issue.get("section", ""))
        i_desc = _norm_cmp_text(issue.get("diagnosis") or issue.get("quote") or "")
        if not i_desc:
            return False
        if (i_sec, i_desc) in tutor_task_keys:
            return True
        # 容错：同章节下，描述可能被裁剪，做包含匹配
        for t_sec, t_prob in tutor_task_keys:
            if t_sec and i_sec and t_sec != i_sec:
                continue
            if i_desc in t_prob or t_prob in i_desc:
                return True
        return False

    def _is_toc_issue(issue: dict) -> bool:
        raw_type = str(issue.get("issue_type", "") or "")
        return (
            raw_type in {"目录结构", "章节结构"}
            or "目录结构" in raw_type
            or "章节结构" in raw_type
        )

    def toc_line_level(line):
        """根据目录行首编号推断层级：1 -> 0, 1.1 -> 1, 1.2.1 -> 2，以此类推。"""
        s = (line or "").strip()
        if not s:
            return 0
        match = re.match(r"^(\d+(?:\.\d+)*)\s*", s)
        if not match:
            return 0
        num_part = match.group(1)
        return max(0, num_part.count("."))

    def _chapter_lines(outline_lines):
        out = []
        for line in outline_lines or []:
            s = (line or "").strip()
            if re.match(r"^\d+\s+\S+", s):
                out.append(s)
        return out

    def _issue_text(issue: dict) -> str:
        return str(issue.get("diagnosis") or issue.get("quote") or "").strip()

    def _issue_suggestion(issue: dict) -> str:
        return str(issue.get("suggestion") or "").strip()

    def _is_uncertain_toc_phrase(text: str) -> bool:
        t = (text or "").lower()
        return any(
            k in t
            for k in [
                "规则复核",
                "ocr",
                "人工复核",
                "建议人工复核",
                "排版噪声",
            ]
        )

    def _toc_signals(issue_pool):
        signals = {
            "order": False,
            "title_mismatch": False,
            "need_ch1_structure": False,
            "need_ch3_feasibility": False,
            "need_ch3_flow": False,
            "need_ch4_core_flow": False,
            "need_ch5_env": False,
            "need_ch7_summary_outlook": False,
            "numbering": False,
        }

        for issue in issue_pool or []:
            t = f"{_issue_text(issue)} {_issue_suggestion(issue)}"
            if not t.strip():
                continue

            if "章节顺序" in t or ("需求分析" in t and "系统设计" in t):
                signals["order"] = True
            if "标题" in t and ("改为" in t or "不符" in t or "匹配" in t):
                signals["title_mismatch"] = True
            if "论文组织结构" in t:
                signals["need_ch1_structure"] = True
            if "可行性分析" in t:
                signals["need_ch3_feasibility"] = True
            if "业务流程分析" in t:
                signals["need_ch3_flow"] = True
            if "系统核心业务流程设计" in t:
                signals["need_ch4_core_flow"] = True
            if "开发环境" in t:
                signals["need_ch5_env"] = True
            if "总结与展望" in t or ("7.1" in t and "7.2" in t):
                signals["need_ch7_summary_outlook"] = True
            if "编号" in t or "页码" in t or "跳号" in t:
                signals["numbering"] = True

        return signals

    def _build_toc_diff_points(issue_pool, outline_lines):
        sig = _toc_signals(issue_pool)
        points = []

        if sig["order"]:
            points.append("章节主线存在先后关系偏差，当前目录未完全体现“需求分析→系统设计→系统实现→系统测试”的流程。")
        if sig["title_mismatch"]:
            points.append("部分章节标题与正文内容匹配度不足，存在“标题语义与小节内容不一致”的情况。")
        if sig["need_ch1_structure"] or sig["need_ch3_feasibility"] or sig["need_ch3_flow"] or sig["need_ch4_core_flow"] or sig["need_ch5_env"] or sig["need_ch7_summary_outlook"]:
            points.append("目录在“必备小节完整性”上仍有缺口，主要集中在绪论结构说明、需求分析细化、核心流程设计和总结展望。")
        if sig["numbering"]:
            points.append("目录与正文联动项存在编号风险，需同步校正章节号、目录页码和图表编号。")

        chapter_lines = _chapter_lines(outline_lines)
        if chapter_lines:
            points.append("当前目录主章节框架已形成，建议在保持主框架的前提下做增量重排，而非整体重写。")

        if not points:
            points.append("当前目录与规范总体接近，建议按推荐目录做小范围对齐与编号复核。")
        return points[:5]

    def _build_toc_action_points(issue_pool):
        sig = _toc_signals(issue_pool)
        actions = []

        if sig["order"]:
            actions.append("章节顺序先调整：将“需求分析”放在“系统设计”之前，再保持“实现→测试→总结与展望”的后续链路。")
        if sig["title_mismatch"]:
            actions.append("第2章优先做标题对齐：若正文为技术栈介绍，章名统一为“相关技术/关键技术与工具”，避免与需求分析重名。")

        ch3_parts = []
        if sig["need_ch3_feasibility"]:
            ch3_parts.append("3.1 可行性分析")
        if sig["need_ch3_flow"]:
            ch3_parts.append("3.4 业务流程分析")
        if ch3_parts:
            actions.append(f"第3章补齐必备小节：新增或校正 { '、'.join(ch3_parts) }，并与现有需求条目合并成完整分析链。")

        if sig["need_ch4_core_flow"]:
            actions.append("第4章在“总体设计”和“详细设计”之间补入“系统核心业务流程设计”，用于承接需求到实现的过渡。")
        if sig["need_ch5_env"]:
            actions.append("第5章开头增加“开发环境”小节，并将后续实现类小节编号整体顺延。")
        if sig["need_ch7_summary_outlook"]:
            actions.append("末章统一为“总结与展望”，拆分为“总结”和“展望”两个小节，分别对应成果回顾与后续计划。")
        if sig["numbering"]:
            actions.append("完成章节调整后执行一次全局重编号：目录页码、章节号、图表号三者同批次校正。")

        if not actions:
            actions.append("按推荐目录逐章对齐标题、顺序与小节设置，完成后统一检查编号与页码。")
        return actions[:7]

    # 统计
    high_count = len([i for i in issues if i.get("severity") == "High"])
    medium_count = len([i for i in issues if i.get("severity") == "Medium"])
    low_count = len([i for i in issues if i.get("severity") == "Low"])
    pending_count = len(
        [
            x
            for x in pending_review_issues
            if (not _issue_in_tutor_tasks(x))
            and (not (toc_summary_only_mode and _is_toc_issue(x)))
        ]
    )
    total_score = max(0, 100 - (high_count * 5 + medium_count * 2 + low_count * 1))

    # 分类问题
    toc_issues = []
    normative_issues = []
    logic_issues = []
    vision_issues = []
    other_issues = []
    pending_toc_issues = []
    pending_normative_issues = []
    pending_logic_issues = []
    pending_vision_issues = []
    pending_other_issues = []

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

    def get_section_num(issue):
        section = issue.get("section", "")
        if not isinstance(section, str):
            return 9999
        match = re.search(r"(\d+)", section)
        if match:
            return int(match.group(1))
        return 9999

    def get_issue_priority(issue):
        raw_priority = (issue.get("priority", "") or "").strip().upper()
        if raw_priority in {"P0", "P1", "P2"}:
            return raw_priority
        severity = (issue.get("severity", "") or "").strip()
        severity_to_priority = {"High": "P0", "Medium": "P1", "Low": "P2"}
        return severity_to_priority.get(severity, "P1")

    def get_priority_rank(issue):
        return {"P0": 0, "P1": 1, "P2": 2}.get(get_issue_priority(issue), 3)

    def has_page_value(item):
        page = item.get("page")
        if isinstance(page, int):
            return True
        if isinstance(page, str) and page.strip():
            return True
        return False

    def get_location_text(item):
        page = item.get("page")
        section = str(item.get("section", "") or "").strip()
        if has_page_value(item):
            if section:
                return f"第 {page} 页 · {section}"
            return f"第 {page} 页"
        if section:
            return f"章节定位 · {section}"
        return "全局定位"

    def get_location_meta_text(item):
        page = item.get("page")
        section = str(item.get("section", "") or "").strip()
        if has_page_value(item):
            if section:
                return f"📍 位置: 第 {page} 页 | 章节: {section}"
            return f"📍 位置: 第 {page} 页"
        if section:
            return f"📍 位置: 章节定位 | 章节: {section}"
        return "📍 位置: 全局定位"

    def _build_issue_page_lookup(issue_pool):
        lookup = {}
        for issue in issue_pool or []:
            page_num = get_page_num(issue)
            if page_num == 9999:
                continue
            sec = _norm_cmp_text(issue.get("section", ""))
            desc = _norm_cmp_text(issue.get("diagnosis") or issue.get("quote") or "")
            if sec and desc and (sec, desc) not in lookup:
                lookup[(sec, desc)] = issue.get("page")
        return lookup

    def _fill_task_page(task, page_lookup):
        page = task.get("page")
        if isinstance(page, int):
            return task
        if isinstance(page, str) and page.strip():
            return task
        sec = _norm_cmp_text(task.get("section", ""))
        desc = _norm_cmp_text(task.get("problem", ""))
        if sec and desc and (sec, desc) in page_lookup:
            task["page"] = page_lookup[(sec, desc)]
        return task

    issue_page_lookup = _build_issue_page_lookup(all_issues)
    tutor_tasks = [_fill_task_page(dict(t), issue_page_lookup) for t in tutor_tasks]
    tutor_tasks = sorted(
        tutor_tasks,
        key=lambda x: (
            get_page_num(x),
            get_priority_rank(x),
            get_section_num(x),
        ),
    )
    tutor_task_types = sorted(
        {
            str(t.get("source_issue_type", "未分类") or "未分类")
            for t in tutor_tasks
            if str(t.get("source_issue_type", "") or "").strip()
        }
    )

    # 定义逻辑类别的集合
    logic_types = {
        "Logic",
        "Language",
        "Coherence",
        "Cohesion",
        "逻辑性",
        "语言",
        "连贯性",
        "语义重复",
    }
    # TOC category types (display separately)
    toc_types = {"目录结构", "章节结构"}
    # Normative types (excluding TOC structure)
    normative_types = {"Format", "规范性", "编号问题", "格式问题"}
    vision_types = {"Vision", "图文一致性"}

    def get_issue_bucket(issue):
        raw_type = issue.get("issue_type", "Unknown")
        img_id = issue.get("image_id", "")
        if raw_type in toc_types or "目录结构" in raw_type or "章节结构" in raw_type:
            return "toc"
        if raw_type in normative_types or any(
            key in raw_type for key in ["规范", "格式", "编号"]
        ):
            return "normative"
        if raw_type in logic_types or any(
            key in raw_type for key in ["逻辑", "语言", "连贯", "语义重复", "重复"]
        ):
            return "logic"
        if raw_type in vision_types or "图文一致性" in raw_type or img_id:
            return "vision"
        return "other"

    def classify_issue(issue, toc_list, norm_list, logic_list, vision_list, other_list):
        raw_type = issue.get("issue_type", "Unknown")
        img_id = issue.get("image_id", "")

        # Classify by issue_type first to avoid img_id overriding category
        if raw_type in toc_types or "目录结构" in raw_type or "章节结构" in raw_type:
            toc_list.append(issue)
        elif raw_type in normative_types or any(
            key in raw_type for key in ["规范", "格式", "编号"]
        ):
            norm_list.append(issue)
        elif raw_type in logic_types or any(
            key in raw_type for key in ["逻辑", "语言", "连贯", "语义重复", "重复"]
        ):
            logic_list.append(issue)
        elif raw_type in vision_types or "图文一致性" in raw_type:
            vision_list.append(issue)
        elif img_id:
            # If no explicit type but has image id, treat as vision issue
            vision_list.append(issue)
        else:
            other_list.append(issue)

    for issue in issues:
        classify_issue(
            issue,
            toc_issues,
            normative_issues,
            logic_issues,
            vision_issues,
            other_issues,
        )

    for issue in pending_review_issues:
        classify_issue(
            issue,
            pending_toc_issues,
            pending_normative_issues,
            pending_logic_issues,
            pending_vision_issues,
            pending_other_issues,
        )

    # 在各分类内按页码排序
    toc_issues.sort(key=get_page_num)
    normative_issues.sort(key=get_page_num)
    logic_issues.sort(key=get_page_num)
    vision_issues.sort(key=get_page_num)
    other_issues.sort(key=get_page_num)
    pending_toc_issues.sort(key=get_page_num)
    pending_normative_issues.sort(key=get_page_num)
    pending_logic_issues.sort(key=get_page_num)
    pending_vision_issues.sort(key=get_page_num)
    pending_other_issues.sort(key=get_page_num)
    toc_all_issues = toc_issues + pending_toc_issues
    pending_default_sort_key = lambda issue: (
        get_page_num(issue),
        get_priority_rank(issue),
        get_section_num(issue),
    )

    # HTML 模板
    html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>审查报告 - {doc_id}</title>
        <style>
            :root {{
                --bg: #f2f4f8;
                --ink-1: #152238;
                --ink-2: #334e68;
                --ink-soft: #627d98;
                --line: #d9e2ec;
                --panel: #ffffff;
                --panel-soft: #f8fbff;
                --brand-a: #0ea5e9;
                --brand-b: #22c55e;
                --danger: #dc2626;
                --warn: #d97706;
                --ok: #2563eb;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                font-family: "Plus Jakarta Sans", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
                background:
                    radial-gradient(circle at 92% 8%, rgba(14, 165, 233, 0.08), transparent 32%),
                    radial-gradient(circle at 10% 22%, rgba(34, 197, 94, 0.07), transparent 28%),
                    var(--bg);
                color: var(--ink-1);
                margin: 0;
                padding: 18px;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
                background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
                border: 1px solid #e6edf5;
                padding: 28px;
                box-shadow: 0 14px 38px rgba(15, 23, 42, 0.08);
                border-radius: 16px;
            }}
            .hero {{
                background: linear-gradient(120deg, #0f172a 0%, #1f3b64 48%, #0f766e 100%);
                color: #f8fbff;
                border-radius: 14px;
                padding: 20px 22px;
                margin-bottom: 16px;
                box-shadow: 0 10px 24px rgba(15, 23, 42, 0.24);
            }}
            .hero h1 {{
                margin: 0 0 8px;
                font-size: clamp(1.35rem, 2.1vw, 1.8rem);
                letter-spacing: 0.02em;
                border: 0;
                padding: 0;
            }}
            .hero-meta {{
                color: #d7e7ff;
                font-size: 0.92rem;
            }}
            .quick-nav {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 14px 0 20px;
            }}
            .quick-nav .nav-tab {{
                appearance: none;
                border: 1px solid #cddcec;
                text-decoration: none;
                color: #17324f;
                background: #ffffff;
                border-radius: 999px;
                padding: 8px 13px;
                font-size: 0.9rem;
                transition: all 0.2s ease;
                cursor: pointer;
            }}
            .quick-nav .nav-tab:hover {{
                transform: translateY(-1px);
                background: #eff7ff;
                border-color: #b9d4f1;
            }}
            .quick-nav .nav-tab.active {{
                background: linear-gradient(120deg, #dbeeff 0%, #edf8ff 100%);
                border-color: #86b7e8;
                color: #0f3b6d;
                font-weight: 700;
            }}
            .rule-link-box {{
                margin: 0 0 14px;
                padding: 10px 12px;
                border-radius: 10px;
                border: 1px solid #d9e7f7;
                background: linear-gradient(120deg, #f4f8ff 0%, #eef5ff 100%);
                color: #2a4663;
                font-size: 0.92rem;
            }}
            .rule-link-box a {{
                color: #1d4f85;
                font-weight: 700;
                text-decoration: none;
                border-bottom: 1px dashed #8bb2db;
            }}
            .rule-link-box a:hover {{
                color: #133a61;
                border-bottom-color: #133a61;
            }}
            h2 {{
                color: #1d3557;
                border-left: 5px solid #3b82f6;
                padding-left: 12px;
                margin-top: 30px;
                margin-bottom: 16px;
            }}
            .category-header {{
                background: linear-gradient(120deg, #f8fbff 0%, #edf4fb 100%);
                padding: 12px 18px;
                border-radius: 10px;
                margin-top: 20px;
                margin-bottom: 12px;
                font-size: 1.05em;
                font-weight: 700;
                color: var(--ink-2);
                border: 1px solid var(--line);
                transition: all 0.2s ease;
            }}
            .category-header:hover {{ background: linear-gradient(120deg, #f2f8ff 0%, #e9f2fc 100%); }}
            .category-container {{ transition: all 0.3s ease; }}
            .dashboard {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
                margin-bottom: 18px;
            }}
            .card {{
                padding: 14px 16px;
                border-radius: 12px;
                color: #fff;
                border: 1px solid rgba(255, 255, 255, 0.25);
                box-shadow: 0 6px 18px rgba(15, 23, 42, 0.12);
            }}
            .bg-blue {{ background: linear-gradient(120deg, #2563eb 0%, #0ea5e9 100%); }}
            .bg-red {{ background: linear-gradient(120deg, #b91c1c 0%, #ef4444 100%); }}
            .bg-orange {{ background: linear-gradient(120deg, #b45309 0%, #f59e0b 100%); }}
            .bg-green {{ background: linear-gradient(120deg, #047857 0%, #22c55e 100%); }}
            .bg-slate {{ background: linear-gradient(120deg, #334155 0%, #64748b 100%); }}
            .bg-cyan {{ background: linear-gradient(120deg, #0369a1 0%, #06b6d4 100%); }}
            .score {{ font-size: 2rem; font-weight: 800; letter-spacing: 0.02em; }}
            .card-note {{ margin-top: 4px; font-size: 0.82rem; opacity: 0.9; }}
            .issue-card {{ border: 1px solid #e2e8f0; margin-bottom: 14px; border-radius: 12px; overflow: hidden; background: var(--panel); box-shadow: 0 2px 8px rgba(18, 38, 63, 0.04); transition: all 0.2s ease; }}
            .issue-card:hover {{ box-shadow: 0 10px 24px rgba(18, 38, 63, 0.08); border-color: #cfdeee; }}
            .issue-header {{ padding: 12px 16px; background: #f8fbfe; cursor: pointer; }}
            .issue-header-first-row {{ display: flex; align-items: center; justify-content: space-between; }}
            .issue-header-suggestion {{ margin-top: 8px; padding-left: 1.2em; line-height: 1.55; color: #3d4f63; }}
            .issue-body {{ padding: 18px; display: {"block" if full_display_mode else "none"}; border-top: 1px solid #e6edf3; line-height: 1.6; background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%); }}
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; color: white; }}
            .badge.High {{ background: #e74c3c; }}
            .badge.Medium {{ background: #f39c12; }}
            .badge.Low {{ background: #3498db; }}
            .meta {{
                font-size: 0.92em;
                color: #5b7086;
                margin-bottom: 8px;
                padding: 8px 10px;
                background: #f8fbff;
                border: 1px solid #e6edf5;
                border-radius: 8px;
            }}
            .detail-stack {{
                margin-top: 12px;
                display: grid;
                gap: 10px;
                padding: 10px;
                background:
                    linear-gradient(180deg, #fffefb 0%, #fbfaf5 100%);
                border: 1px solid #e4dfd2;
                border-radius: 12px;
            }}
            .detail-card {{
                position: relative;
                border: 1px solid #ddd6c7;
                border-left-width: 6px;
                border-radius: 8px;
                background: #fffdf8;
                padding: 12px 14px 12px 16px;
                line-height: 1.72;
                box-shadow: 0 1px 2px rgba(28, 35, 52, 0.06);
            }}
            .detail-head {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 6px;
            }}
            .detail-label {{
                font-weight: 700;
                font-size: 0.88rem;
                letter-spacing: 0.02em;
                color: #304055;
            }}
            .detail-index {{
                min-width: 1.6rem;
                height: 1.6rem;
                border-radius: 999px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 0.75rem;
                font-weight: 700;
                border: 1px solid currentColor;
                background: #fff;
            }}
            .detail-content {{
                color: #2f3b4e;
                word-break: break-word;
                font-family: "Plus Jakarta Sans", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
                font-size: 1.03rem;
            }}
            .detail-card.problem {{
                border-left-color: #b7791f;
                background: #fff9ef;
            }}
            .detail-card.problem .detail-label,
            .detail-card.problem .detail-index {{ color: #9a6412; }}
            .detail-card.evidence {{
                border-left-color: #2b6cb0;
                background: #f7fbff;
            }}
            .detail-card.evidence .detail-label,
            .detail-card.evidence .detail-index {{ color: #24598e; }}
            .detail-card.advice {{
                border-left-color: #2f855a;
                background: #f4fbf7;
            }}
            .detail-card.advice .detail-label,
            .detail-card.advice .detail-index {{ color: #2a6d4b; }}
            @media (max-width: 720px) {{
                .detail-stack {{ padding: 8px; }}
                .detail-card {{ padding: 10px 11px 10px 13px; }}
                .detail-content {{ font-size: 0.96rem; }}
            }}
            .thinking-box {{ background: #f7f9fd; border: 1px solid #dce7f4; border-radius: 12px; margin-top: 14px; margin-bottom: 8px; overflow: hidden; box-shadow: 0 6px 20px rgba(22, 38, 63, 0.06); }}
            .thinking-header {{ padding: 12px 16px; background: linear-gradient(120deg, #eaf3ff 0%, #edf5ff 100%); cursor: pointer; font-weight: 700; display: flex; justify-content: space-between; align-items: center; font-size: 1.02em; color: #213a57; }}
            .thinking-content {{ padding: 20px 22px; display: block; background: #fff; color: #2f3d4f; font-family: "Plus Jakarta Sans", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.72; min-height: 220px; max-height: 55vh; overflow-y: auto; white-space: normal; word-break: break-word; }}
            .thinking-content h1, .thinking-content h2 {{ margin: 1.1em 0 0.6em; color: #17324f; border-bottom: 1px solid #e5edf6; padding-bottom: 0.28em; line-height: 1.35; }}
            .thinking-content h3, .thinking-content h4 {{ margin: 0.9em 0 0.5em; color: #214b74; line-height: 1.38; }}
            .thinking-content h3 {{ border-left: 3px solid #3b82f6; padding-left: 8px; }}
            .thinking-content p {{ margin: 0.45em 0 0.75em; line-height: 1.78; }}
            .thinking-content ul, .thinking-content ol {{ margin: 0.45em 0 0.85em 1.2em; padding-left: 1.05em; line-height: 1.72; }}
            .thinking-content li {{ margin: 0.32em 0; }}
            .thinking-content blockquote {{ margin: 0.8em 0; padding: 8px 12px; background: #f7fbff; border-left: 3px solid #93c5fd; color: #36526f; border-radius: 6px; }}
            .thinking-content code {{ background: #f2f6fb; padding: 2px 6px; border-radius: 4px; font-family: "JetBrains Mono", Consolas, monospace; color: #1e3a8a; font-size: 0.92em; }}
            .thinking-content pre {{ background: #f6f9fd; padding: 10px 12px; border-radius: 8px; overflow-x: auto; border: 1px solid #dde7f3; margin: 0.8em 0; }}
            .thinking-content pre code {{ background: none; color: #2b3d52; padding: 0; }}
            .thinking-content hr {{ border: 0; border-top: 1px dashed #d5e1ee; margin: 1em 0; }}
            .empty-msg {{ color: #95a5a6; font-style: italic; padding: 10px; }}
            /* 推荐目录样式（仅树状缩进，无左侧蓝线） */
            .toc-merge-card {{ margin-top: 18px; border: 1px solid #d8e7ff; border-radius: 12px; overflow: hidden; background: var(--panel-soft); box-shadow: 0 8px 22px rgba(47, 128, 237, 0.08); }}
            .toc-merge-head {{ padding: 12px 16px; background: linear-gradient(120deg, rgba(47, 128, 237, 0.12) 0%, rgba(86, 204, 242, 0.12) 100%); border-bottom: 1px solid #d8e7ff; font-weight: 700; color: var(--ink-1); }}
            .toc-merge-body {{ padding: 16px; background: #fbfdff; }}
            .toc-outline-wrap {{ margin-top: 14px; }}
            .toc-outline-box {{ background: #ffffff; border: 1px solid #e5eefb; border-radius: 10px; padding: 16px 18px; }}
            .toc-outline-title {{ font-size: 0.96em; font-weight: 700; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #edf2f7; color: #243447; }}
            .toc-outline-list {{ list-style: none; padding: 0; margin: 0; }}
            .toc-item {{ padding: 7px 8px; line-height: 1.62; margin-bottom: 2px; border-radius: 6px; }}
            .toc-item:hover {{ background: #f3f8ff; }}
            .toc-item-l0 {{ margin-left: 0; font-weight: 700; color: #1e2d3d; font-size: 1em; }}
            .toc-item-l1 {{ margin-left: 1.1em; font-weight: 600; color: #27384a; font-size: 0.98em; }}
            .toc-item-l2 {{ margin-left: 2.2em; font-weight: 500; color: #334a62; font-size: 0.95em; }}
            .toc-item-l3 {{ margin-left: 3.3em; font-weight: 500; color: #47617a; font-size: 0.93em; }}
            .toc-item-l4 {{ margin-left: 4.4em; font-weight: 500; color: #5a7289; font-size: 0.91em; }}
            .toc-summary-block {{ background: #ffffff; border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; border: 1px solid #e5eefb; color: #2f4358; line-height: 1.75; }}
            .teacher-box {{ background: #f7fbff; border: 1px solid #d8e7ff; border-radius: 12px; padding: 16px; margin-bottom: 24px; box-shadow: 0 6px 18px rgba(37, 99, 235, 0.08); }}
            .teacher-title {{ font-size: 1.05em; font-weight: 700; color: #1f2a37; margin-bottom: 10px; }}
            .teacher-overall {{ background: #fff; border: 1px solid #e5eefb; border-radius: 8px; padding: 12px 14px; color: #2f4358; line-height: 1.7; margin-bottom: 12px; }}
            .task-list {{ margin: 0; padding: 0; list-style: none; }}
            .task-item {{ background: #fff; border: 1px solid #e9eef5; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }}
            .task-head {{ display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #2f4358; }}
            .task-pri {{ font-size: 0.85em; border-radius: 4px; padding: 2px 6px; color: #fff; }}
            .task-pri.P0 {{ background: #e74c3c; }}
            .task-pri.P1 {{ background: #f39c12; }}
            .task-pri.P2 {{ background: #3498db; }}
            .task-body {{ margin-top: 6px; color: #3f5267; line-height: 1.6; }}
            .section-divider {{ clear: both; margin-top: 26px; border-top: 1px dashed #ccd9e8; padding-top: 12px; }}
            .view-panel {{ display: none; }}
            .view-panel.active {{ display: block; }}
            .pending-toolbar {{
                margin: 10px 0 14px;
                padding: 10px 12px;
                border: 1px solid #dde7f3;
                border-radius: 10px;
                background: #f7fbff;
                display: flex;
                flex-wrap: wrap;
                gap: 10px 12px;
                align-items: center;
            }}
            .pending-toolbar label {{
                display: flex;
                align-items: center;
                gap: 6px;
                font-size: 0.93rem;
                color: #27455f;
                font-weight: 600;
            }}
            .pending-toolbar select {{
                border: 1px solid #c9d9ee;
                border-radius: 8px;
                background: #fff;
                color: #1f3349;
                padding: 5px 8px;
                font-size: 0.92rem;
            }}
            .pending-toolbar input[type="checkbox"] {{
                width: 15px;
                height: 15px;
            }}
            @media (max-width: 980px) {{
                .container {{ padding: 16px; }}
                .dashboard {{ grid-template-columns: repeat(2, minmax(140px, 1fr)); }}
            }}
            @media (max-width: 640px) {{
                body {{ padding: 10px; }}
                .dashboard {{ grid-template-columns: 1fr; }}
                .issue-header-first-row {{ gap: 8px; align-items: flex-start; }}
                .task-head {{ flex-direction: column; align-items: flex-start; gap: 6px; }}
            }}
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
            function activateView(viewId) {{
                var panels = document.querySelectorAll(".view-panel");
                panels.forEach(function(p) {{ p.classList.remove("active"); }});
                var target = document.getElementById(viewId);
                if (target) {{
                    target.classList.add("active");
                }}
                var tabs = document.querySelectorAll(".quick-nav .nav-tab");
                tabs.forEach(function(tab) {{
                    tab.classList.toggle("active", tab.getAttribute("data-view") === viewId);
                }});
            }}
            function extractFirstNumber(text, fallbackValue) {{
                if (typeof text !== "string") return fallbackValue;
                var m = text.match(/(\d+)/);
                return m ? parseInt(m[1], 10) : fallbackValue;
            }}
            function getPendingComparator(mode) {{
                return function(a, b) {{
                    var pa = parseInt(a.dataset.priorityRank || "3", 10);
                    var pb = parseInt(b.dataset.priorityRank || "3", 10);
                    var sa = parseInt(a.dataset.sectionNum || "9999", 10);
                    var sb = parseInt(b.dataset.sectionNum || "9999", 10);
                    var ga = parseInt(a.dataset.pageNum || "9999", 10);
                    var gb = parseInt(b.dataset.pageNum || "9999", 10);
                    if (mode === "page") {{
                        if (ga !== gb) return ga - gb;
                        if (sa !== sb) return sa - sb;
                        if (pa !== pb) return pa - pb;
                        return 0;
                    }}
                    if (mode === "section") {{
                        if (sa !== sb) return sa - sb;
                        if (ga !== gb) return ga - gb;
                        if (pa !== pb) return pa - pb;
                        return 0;
                    }}
                    if (pa !== pb) return pa - pb;
                    if (sa !== sb) return sa - sb;
                    if (ga !== gb) return ga - gb;
                    return 0;
                }};
            }}
            function applyPendingControls() {{
                var list = document.getElementById("pending_issue_list");
                if (!list) return;
                var sortEl = document.getElementById("pending_sort");
                var typeEl = document.getElementById("pending_type");
                var highOnlyEl = document.getElementById("pending_high_only");
                var cards = Array.prototype.slice.call(list.querySelectorAll(".issue-card"));
                var typeValue = typeEl ? typeEl.value : "all";
                var highOnly = !!(highOnlyEl && highOnlyEl.checked);
                cards.forEach(function(card) {{
                    var showByType = typeValue === "all" || card.dataset.bucket === typeValue;
                    var showByPriority = !highOnly || card.dataset.priority === "P0";
                    card.style.display = (showByType && showByPriority) ? "" : "none";
                }});
                cards.sort(getPendingComparator(sortEl ? sortEl.value : "page"));
                cards.forEach(function(card) {{ list.appendChild(card); }});
            }}
            function getTeacherComparator(mode) {{
                return function(a, b) {{
                    var pa = parseInt(a.dataset.priorityRank || "3", 10);
                    var pb = parseInt(b.dataset.priorityRank || "3", 10);
                    var sa = parseInt(a.dataset.sectionNum || "9999", 10);
                    var sb = parseInt(b.dataset.sectionNum || "9999", 10);
                    var ga = parseInt(a.dataset.pageNum || "9999", 10);
                    var gb = parseInt(b.dataset.pageNum || "9999", 10);
                    if (mode === "priority") {{
                        if (pa !== pb) return pa - pb;
                        if (ga !== gb) return ga - gb;
                        if (sa !== sb) return sa - sb;
                        return 0;
                    }}
                    if (mode === "section") {{
                        if (sa !== sb) return sa - sb;
                        if (ga !== gb) return ga - gb;
                        if (pa !== pb) return pa - pb;
                        return 0;
                    }}
                    if (ga !== gb) return ga - gb;
                    if (pa !== pb) return pa - pb;
                    if (sa !== sb) return sa - sb;
                    return 0;
                }};
            }}
            function applyTeacherControls() {{
                var list = document.getElementById("teacher_task_list");
                if (!list) return;
                var sortEl = document.getElementById("teacher_sort");
                var typeEl = document.getElementById("teacher_type");
                var cards = Array.prototype.slice.call(list.querySelectorAll(".task-item"));
                var typeValue = typeEl ? typeEl.value : "all";
                cards.forEach(function(card) {{
                    var showByType = typeValue === "all" || card.dataset.issueType === typeValue;
                    card.style.display = showByType ? "" : "none";
                }});
                cards.sort(getTeacherComparator(sortEl ? sortEl.value : "page"));
                cards.forEach(function(card) {{ list.appendChild(card); }});
            }}
            document.addEventListener("DOMContentLoaded", function() {{
                var tabs = document.querySelectorAll(".quick-nav .nav-tab");
                tabs.forEach(function(tab) {{
                    tab.addEventListener("click", function() {{
                        activateView(tab.getAttribute("data-view"));
                    }});
                }});
                activateView("view_teacher");
                ["pending_sort", "pending_type", "pending_high_only"].forEach(function(id) {{
                    var el = document.getElementById(id);
                    if (el) {{
                        el.addEventListener("change", applyPendingControls);
                    }}
                }});
                ["teacher_sort", "teacher_type"].forEach(function(id) {{
                    var el = document.getElementById(id);
                    if (el) {{
                        el.addEventListener("change", applyTeacherControls);
                    }}
                }});
                applyPendingControls();
                applyTeacherControls();

                var markdownDivs = document.querySelectorAll(".markdown-content");
                markdownDivs.forEach(function(div) {{
                    var rawContent = div.textContent.trim();
                    if (!rawContent || rawContent === "无思考过程") return;
                    
                    try {{
                        if (typeof marked !== 'undefined') {{
                            marked.setOptions({{
                                gfm: true,
                                breaks: true,
                                headerIds: false,
                                mangle: false
                            }});
                        }}
                        var processedContent = rawContent.replace(/^\[Image\s+(.*?)\s*\|\s*Page\s+(.*?)\]\s*(.*)/gm, '### 🖼️ 图片分析: $1 (第 $2 页)\\n\\n$3');
                        processedContent = processedContent.replace(/\[Image\s+(.*?)\s*\|\s*Page\s+(.*?)\]\s*(.*)/g, '\\n\\n### 🖼️ 图片分析: $1 (第 $2 页)\\n\\n$3');
                        processedContent = processedContent.replace(/^==\s*(.+?)\s*==$/gm, '### $1');
                        processedContent = processedContent.replace(/^\s*检查思路[：:]\s*$/gm, '#### 检查思路');
                        
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
            <div class="hero">
                <h1>论文审查报告 · {doc_id}</h1>
                <div class="hero-meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")} | 模式: 开发核查（全量展示）</div>
            </div>

            <div class="quick-nav">
                <button type="button" class="nav-tab" data-view="view_teacher">导师建议</button>
                <button type="button" class="nav-tab" data-view="block_cat_pending">待复核</button>
                <button type="button" class="nav-tab" data-view="block_cat_toc">目录结构</button>
                <button type="button" class="nav-tab" data-view="block_cat_norm">规范性</button>
                <button type="button" class="nav-tab" data-view="block_cat_logic">逻辑性</button>
                <button type="button" class="nav-tab" data-view="block_cat_vision">图文一致性</button>
                <button type="button" class="nav-tab" data-view="thinking_panel">AI 思考过程</button>
            </div>
            {(
                f'<div class="rule-link-box">📚 对照规范：<a href="{rule_doc_href}" target="_blank">{escape_html(rule_doc_name)}</a>（建议学生先打开规范文档，再对照本报告逐项修改）</div>'
                if rule_doc_href
                else '<div class="rule-link-box">📚 对照规范：未找到 rules 目录中的“开发类论文目录结构”文档</div>'
            )}
            
            <div class="dashboard">
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
                    <div>确认问题数</div>
                </div>
                <div class="card bg-blue">
                    <div class="score">{pending_count}</div>
                    <div>待复核问题</div>
                </div>
                <div class="card bg-slate">
                    <div class="score">{preprocess_risk_text}</div>
                    <div>预处理风险分</div>
                    <div class="card-note">0~1，越低越好</div>
                </div>
                <div class="card bg-cyan">
                    <div class="score">{evidence_unmatched_text}</div>
                    <div>证据不可检索率</div>
                    <div class="card-note">样本数: {evidence_sample_text}</div>
                </div>
            </div>
            <div id="view_teacher" class="view-panel active">
            {f'''
            <div class="teacher-box">
                <div class="teacher-title">👩‍🏫 导师总评与全量任务</div>
                {f'<div class="teacher-overall">{escape_html(tutor_overall)}</div>' if tutor_overall else ""}
                {(
                    '<div class="pending-toolbar">'
                    '<label>排序'
                    '<select id="teacher_sort">'
                    '<option value="page">页码（默认）</option>'
                    '<option value="priority">优先级</option>'
                    '<option value="section">章节</option>'
                    '</select>'
                    '</label>'
                    '<label>类型'
                    '<select id="teacher_type">'
                    '<option value="all">全部</option>'
                    + "".join(
                        f'<option value="{escape_html(tp)}">{escape_html(tp)}</option>'
                        for tp in tutor_task_types
                    )
                    + '</select>'
                    '</label>'
                    '</div>'
                    + '<ul class="task-list" id="teacher_task_list">'
                    + "".join(
                        f'<li class="task-item" data-priority="{escape_html(t.get("priority", "P2"))}" data-priority-rank="{get_priority_rank(t)}" data-page-num="{get_page_num(t)}" data-section-num="{get_section_num(t)}" data-issue-type="{escape_html(t.get("source_issue_type", "未分类"))}">'
                        f'<div class="task-head"><span>{escape_html(t.get("task_id", ""))} · {escape_html(get_location_text(t))}</span>'
                        f'<span class="task-pri {escape_html(t.get("priority", "P2"))}">{escape_html(t.get("priority", "P2"))}</span></div>'
                        f'<div class="task-body"><strong>类型：</strong>{escape_html(t.get("source_issue_type", "未分类"))}<br>'
                        f'<strong>问题：</strong>{escape_html(enrich_missing_subsection_text(t.get("problem", "")))}<br>'
                        f'<strong>建议：</strong>{escape_html(enrich_missing_subsection_text(t.get("action", "")))}</div></li>'
                        for t in tutor_tasks
                    )
                    + '</ul>'
                ) if tutor_tasks else '<div class="empty-msg">暂无任务单</div>'}
            </div>
            ''' if (tutor_overall or tutor_tasks) else ""}
            </div>

    """

    # 定义英文到中文的类型映射（兜底用）
    type_mapping = {
        "Format": "规范性",
        "Logic": "逻辑性",
        "Language": "语言",
        "Coherence": "连贯性",
        "Cohesion": "连贯性",
        "语义重复": "语义重复",
        "Vision": "图文一致性",
        "EVIDENCE_GENERALIZATION": "证据外推",
        "Unknown": "未分类",
    }

    def render_issues(issue_list, title, start_idx, cat_id):
        nonlocal html
        display_count = len(issue_list)
        is_pending_block = cat_id == "cat_pending"
        if cat_id == "cat_toc" and toc_summary_only_mode:
            display_count = 0
        if cat_id == "cat_toc" and toc_summary_only_mode:
            # 目录汇总模式：取消折叠条，直接展示汇总建议与推荐目录
            html += f"""
                <section class="category-block view-panel" id="block_{cat_id}">
                <div id="{cat_id}" class="category-container">
            """
        else:
            html += f"""
                <section class="category-block view-panel" id="block_{cat_id}">
                <div class="category-header" onclick="toggle('{cat_id}')" style="cursor: pointer; display: flex; justify-content: space-between; align-items: center;">
                    <span>{title} ({display_count})</span>
                    <span>▼</span>
                </div>
                <div id="{cat_id}" class="category-container">
            """

        if is_pending_block:
            html += """
                <div class="pending-toolbar">
                    <label>排序
                        <select id="pending_sort">
                            <option value="page">页码（默认）</option>
                            <option value="priority">优先级</option>
                            <option value="section">章节</option>
                        </select>
                    </label>
                    <label>类型
                        <select id="pending_type">
                            <option value="all">全部</option>
                            <option value="toc">目录结构</option>
                            <option value="normative">规范性</option>
                            <option value="logic">逻辑性</option>
                            <option value="vision">图文一致性</option>
                            <option value="other">其他</option>
                        </select>
                    </label>
                    <label><input type="checkbox" id="pending_high_only">仅看 P0</label>
                </div>
                <div id="pending_issue_list">
            """

        # 将目录问题与目录检测建议合并到同一分类：先推荐目录，再问题明细
        if cat_id == "cat_toc" and (toc_summary or toc_outline):
            chapter_lines = _chapter_lines(toc_outline)
            toc_problem_points = _build_toc_diff_points(toc_all_issues, toc_outline)
            renumber_points = [
                "图表编号建议按“章号-章内序号”统一（例如第4章对应图4-1、表4-1），避免沿用旧章号。",
                "章节顺序调整后，记得同步更新受影响的小节编号和目录页码。",
                "如果新增“开发环境/业务流程分析”等小节，后续同级编号建议整体顺延。",
                "建议最后交叉核对一次：正文标题、目录条目、图表题注中的章号是否一致。",
            ]
            acceptance_points = [
                "目录主章节顺序满足：绪论→相关技术→需求分析→系统设计→系统实现→系统测试→总结与展望。",
                "第一章包含“论文组织结构”，末章包含“总结”和“展望”两个小节。",
                "需求分析章节包含可行性分析、功能/非功能需求及业务流程分析。",
                "目录页码与正文起始页一致，且不存在0页码或缺页码项。",
                "随机抽查3处章节标题与3处图表编号，均与目录和章号一致。",
            ]
            html += f"""
                <div class="toc-merge-card">
                    <div class="toc-merge-head">📑 目录修改建议（汇总）</div>
                    <div class="toc-merge-body">
                        {f'<div class="toc-summary-block"><strong>总建议（原始摘要）：</strong><br>{escape_html(toc_summary)}</div>' if toc_summary else ""}
                        <div class="toc-summary-block">
                            <strong>一、现状差异（当前目录 vs 规范）</strong>
                            <ol>{"".join(f"<li>{escape_html(x)}</li>" for x in (toc_problem_points or ['未检出明显差异，建议仍按推荐目录复核。']))}</ol>
                        </div>
                        
                        <div class="toc-summary-block">
                            <strong>二、修改注意事项（编号与页码）</strong>
                            <ol>{"".join(f"<li>{escape_html(x)}</li>" for x in renumber_points)}</ol>
                        </div>
                        <div class="toc-summary-block">
                            <strong>三、验收清单（改完必须满足）</strong>
                            <ol>{"".join(f"<li>{escape_html(x)}</li>" for x in acceptance_points)}</ol>
                        </div>
                        <div class="toc-outline-wrap">
                            <div class="toc-outline-title">修改后的推荐目录（目标结构）</div>
                            <div class="toc-outline-box">
                                <ul class="toc-outline-list">{"".join(f'<li class="toc-item toc-item-l{min(toc_line_level(line), 4)}">{escape_html(line)}</li>' for line in toc_outline)}</ul>
                            </div>
                        </div>
                        {(
                            '<div class="toc-summary-block"><strong>主章节快照：</strong><br>'
                            + " → ".join(escape_html(x) for x in chapter_lines[:10])
                            + "</div>"
                        ) if chapter_lines else ""}
                    </div>
                </div>
            """
            if toc_summary_only_mode:
                html += "</div></section>"
                return start_idx
        if not issue_list:
            html += '<div class="empty-msg">未发现该类别问题</div>'
            html += "</div></section>"
            return start_idx

        for i, issue in enumerate(issue_list):
            idx = start_idx + i
            severity = issue.get("severity", "Medium")
            priority = get_issue_priority(issue)
            priority_rank = get_priority_rank(issue)
            issue_bucket = get_issue_bucket(issue)
            page_num = get_page_num(issue)
            section_num = get_section_num(issue)
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
            diagnosis = (issue.get("diagnosis", "") or "").strip()
            evidence_quote = (issue.get("evidence_quote", "") or "").strip()
            evidence_status = (issue.get("evidence_status", "") or "").strip()
            legacy_quote = (issue.get("quote", "") or "").strip()
            diagnosis = enrich_missing_subsection_text(diagnosis)
            evidence_quote = enrich_missing_subsection_text(evidence_quote)
            legacy_quote = enrich_missing_subsection_text(legacy_quote)
            evidence_status_map = {
                "verifiable": "可核验",
                "unverifiable": "不可核验",
                "synthetic": "规则判定",
            }
            evidence_status_cn = evidence_status_map.get(
                evidence_status.lower(), evidence_status
            ) if isinstance(evidence_status, str) and evidence_status else evidence_status
            evidence_status_value = (
                evidence_status.lower() if isinstance(evidence_status, str) and evidence_status else "unknown"
            )

            # 标题描述优先显示诊断结论，再回退证据/旧 quote
            title_quote = diagnosis or evidence_quote or legacy_quote or "无引用"
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
                <div class="issue-card" data-priority="{priority}" data-priority-rank="{priority_rank}" data-page-num="{page_num}" data-section-num="{section_num}" data-bucket="{issue_bucket}" data-evidence-status="{escape_html(evidence_status_value)}">
                    <div class="issue-header" onclick="toggle('issue_{idx}')">
                        <div class="issue-header-first-row">
                            <span>
                                <span class="task-pri {priority}" style="margin-right: 6px;">{priority}</span>
                                <span class="badge {severity}">{severity}</span>
                                <strong>{title_prefix}</strong>
                            </span>
                            <span>▼</span>
                        </div>
                        <div class="issue-header-suggestion">{escape_html(quote_short)}</div>
                    </div>
                    <div id="issue_{idx}" class="issue-body">
                        <div class="meta">{escape_html(get_location_meta_text(issue))}</div>
                        {f'<div class="meta" style="margin-top: 8px; color: #666;">🖼️ 图片名称: {escape_html(image_name)}</div>' if image_name else ''}
                        {f'<div class="meta" style="margin-top: 8px; color: #666;">📊 图表名称: {escape_html(caption)}</div>' if caption else ''}
                        {f'<div class="meta" style="margin-top: 8px; color: #666;">🔎 证据状态: {escape_html(evidence_status_cn)}</div>' if evidence_status_cn else ''}
                        
                        <div class="detail-stack">
                            <div class="detail-card problem">
                                <div class="detail-head">
                                    <span class="detail-label">问题描述</span>
                                    <span class="detail-index">01</span>
                                </div>
                                <div class="detail-content">{escape_html(diagnosis or legacy_quote or "无描述")}</div>
                            </div>

                            <div class="detail-card evidence">
                                <div class="detail-head">
                                    <span class="detail-label">原文证据</span>
                                    <span class="detail-index">02</span>
                                </div>
                                <div class="detail-content">{escape_html(evidence_quote or legacy_quote or "无可检索证据")}</div>
                            </div>

                            <div class="detail-card advice">
                                <div class="detail-head">
                                    <span class="detail-label">修改建议</span>
                                    <span class="detail-index">03</span>
                                </div>
                                <div class="detail-content">{escape_html(enrich_missing_subsection_text(modification_suggestion)) or escape_html(enrich_missing_subsection_text(issue.get('suggestion', '无建议')))}</div>
                            </div>
                        </div>
                        {(
                            f'<div class="detail-card advice" style="margin-top:10px;"><div class="detail-head"><span class="detail-label">修改方向</span><span class="detail-index">↗</span></div>'
                            f'<div class="detail-content">{escape_html(modification_target_cn) or "未给出"}</div></div>'
                            if modification_advice
                            else ""
                        )}
                        {(
                            f'<div class="detail-card advice" style="margin-top:10px;"><div class="detail-head"><span class="detail-label">修改理由</span><span class="detail-index">i</span></div>'
                            f'<div class="detail-content">{escape_html(modification_reason) or "未给出"}</div></div>'
                            if modification_advice
                            else ""
                        )}
                    </div>
                </div>
            """

        if is_pending_block:
            html += "</div>"
        html += "</div></section>"
        return start_idx + len(issue_list)

    # Render categories
    current_idx = 0
    current_idx = render_issues(
        toc_issues, "目录结构问题", current_idx, "cat_toc"
    )
    current_idx = render_issues(
        normative_issues, "规范性问题", current_idx, "cat_norm"
    )
    current_idx = render_issues(
        logic_issues, "逻辑性问题", current_idx, "cat_logic"
    )
    current_idx = render_issues(
        vision_issues, "视觉问题", current_idx, "cat_vision"
    )

    if other_issues:
        current_idx = render_issues(
            other_issues, "❓ 其他分类问题", current_idx, "cat_other"
        )

    pending_flat_issues = (
        ([] if toc_summary_only_mode else pending_toc_issues)
        + pending_normative_issues
        + pending_logic_issues
        + pending_vision_issues
        + pending_other_issues
    )
    pending_flat_issues = [x for x in pending_flat_issues if not _issue_in_tutor_tasks(x)]
    pending_flat_issues.sort(key=pending_default_sort_key)
    current_idx = render_issues(
        pending_flat_issues, "待复核问题区（低置信度）", current_idx, "cat_pending"
    )

    html += f"""
            <div id="thinking_panel" class="view-panel">
            <div class="section-divider">
                <h2>🧠 AI 思考过程</h2>
            </div>

            <div class="thinking-box">
                <div class="thinking-header" onclick="toggle('thinking_norm')">
                    <span>规范性审查思考过程</span>
                    <span>▶</span>
                </div>
                <div id="thinking_norm" class="thinking-content markdown-content" style="display: none;">
                    {norm_thinking}
                </div>
            </div>

            <div class="thinking-box">
                <div class="thinking-header" onclick="toggle('thinking_logic')">
                    <span>逻辑审查思考过程</span>
                    <span>▶</span>
                </div>
                <div id="thinking_logic" class="thinking-content markdown-content" style="display: none;">
                    {logic_thinking}
                </div>
            </div>

            <div class="thinking-box" style="margin-bottom: 20px;">
                <div class="thinking-header" onclick="toggle('thinking_vision')">
                    <span>视觉审查思考过程</span>
                    <span>▶</span>
                </div>
                <div id="thinking_vision" class="thinking-content markdown-content" style="display: none;">
                    {vision_thinking}
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


def _resolve_review_json(result_dir: str, doc_id: str) -> str:
    json_file = os.path.join(result_dir, "review", f"review_{doc_id}.json")
    if os.path.exists(json_file):
        return json_file
    return os.path.join(result_dir, f"review_{doc_id}.json")


def _resolve_report_html(result_dir: str, doc_id: str) -> str:
    report_subdir = os.path.join(result_dir, "report")
    if not os.path.isdir(report_subdir):
        os.makedirs(report_subdir, exist_ok=True)
    return os.path.join(report_subdir, f"report_{doc_id}.html")


def _discover_doc_ids(result_dir: str) -> list[str]:
    review_dir = os.path.join(result_dir, "review")
    search_dir = review_dir if os.path.isdir(review_dir) else result_dir
    doc_ids = []
    for name in os.listdir(search_dir):
        if not (name.startswith("review_") and name.endswith(".json")):
            continue
        doc_ids.append(name[len("review_") : -len(".json")])
    return sorted(set(doc_ids))


def _generate_one_report(result_dir: str, doc_id: str) -> tuple[str, bool, str]:
    json_file = _resolve_review_json(result_dir, doc_id)
    html_file = _resolve_report_html(result_dir, doc_id)
    if not os.path.exists(json_file):
        return doc_id, False, f"Error: {json_file} not found."
    generate_html(json_file, html_file)
    return doc_id, True, html_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", default=None, help="单个 doc_id，保持向后兼容")
    parser.add_argument(
        "--doc-ids",
        default=None,
        help="批量 doc_id，逗号分隔，例如 bylw-a,bylw-b",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="为 result-dir 下所有 review_*.json 批量生成报告",
    )
    parser.add_argument("--result-dir", default="./sample_results/")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=max(1, min(8, (os.cpu_count() or 4))),
        help="批量生成时的并发数，默认按 CPU 数自动设置",
    )
    args = parser.parse_args()

    doc_ids: list[str] = []
    if args.doc_id:
        doc_ids.append(args.doc_id.strip())
    if args.doc_ids:
        doc_ids.extend([x.strip() for x in args.doc_ids.split(",") if x.strip()])
    if args.all:
        doc_ids.extend(_discover_doc_ids(args.result_dir))

    doc_ids = sorted(set([x for x in doc_ids if x]))
    if not doc_ids:
        parser.error("请提供 --doc-id / --doc-ids / --all 之一。")

    if len(doc_ids) == 1:
        doc_id, ok, message = _generate_one_report(args.result_dir, doc_ids[0])
        if not ok:
            print(message)
        raise SystemExit(0 if ok else 1)

    print(f"[Batch] Generating {len(doc_ids)} reports with {args.max_workers} workers...")
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(_generate_one_report, args.result_dir, doc_id): doc_id
            for doc_id in doc_ids
        }
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                _, ok, message = future.result()
                if ok:
                    print(f"[Batch] Done: {doc_id}")
                else:
                    failures.append((doc_id, message))
                    print(f"[Batch] Failed: {doc_id} -> {message}")
            except Exception as exc:
                failures.append((doc_id, str(exc)))
                print(f"[Batch] Failed: {doc_id} -> {exc}")

    if failures:
        raise SystemExit(1)
