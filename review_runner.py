"""
Run normative + logic review on preprocessed documents.
Outputs JSON issues per document.
"""

import argparse
import json
import os
from pathlib import Path
import concurrent.futures
from typing import Any, Dict, List
import re

from dotenv import load_dotenv

from agent import doc_agent
from agent import doc_reader
from agent.logic_agent import LogicAgent
from agent.normative_agent import NormativeAgent
from agent.syllabus_audit_agent import SyllabusAuditAgent
from agent.tutor_feedback_agent import TutorFeedbackAgent
from agent.vision_agent import VisionAgent
from scripts.evaluate_preprocess_misdiagnosis import evaluate as evaluate_preprocess_misdiagnosis


def parse_args():
    # Force UTF-8 to avoid BOM/UTF-16 issues when reading .env
    load_dotenv(override=True, encoding="utf-8")
    parser = argparse.ArgumentParser(description="Logic-only review runner")
    parser.add_argument(
        "--preprocessed-data-dir",
        type=str,
        default="./preprocess/processed_output/MinerU/",
        help="Directory containing processed documents (each doc in a subfolder)",
    )
    parser.add_argument(
        "--save-dir",
        type=str,
        default="./sample_results/",
        help="Directory to save review JSON files",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        default=None,
        help="Specific document id (subfolder name). If not set, review all docs.",
    )
    parser.add_argument(
        "--outline-path",
        type=str,
        default=None,
        help="Use outline XML directly (skip data.pkl). When set, vision review is disabled.",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("DASHSCOPE_API_KEY"),
        help="API key for text model (env DASHSCOPE_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        help="Base URL for text model API",
    )
    parser.add_argument(
        "--vision-model",
        type=str,
        default="qwen3-vl-flash",
        help="Model ID for vision review",
    )
    parser.add_argument(
        "--vision-api-key",
        type=str,
        default=os.getenv("DASHSCOPE_API_KEY"),
        help="API key for vision model (default: DASHSCOPE_API_KEY for Qwen)",
    )
    parser.add_argument(
        "--vision-base-url",
        type=str,
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        help="Base URL for vision model",
    )
    parser.add_argument(
        "--thesis-type",
        type=str,
        choices=["auto", "system", "algorithm"],
        default="auto",
        help="论文类型: auto(自动检测) / system(程序开发类) / algorithm(算法理论类)",
    )
    # 已废弃：不再支持并行模式，只使用串行结构化流程
    # parser.add_argument(
    #     "--no-parallel",
    #     dest="parallel",
    #     action="store_false",
    #     default=True,
    #     help="Disable parallel processing (use serial mode). Default: parallel mode enabled.",
    # )
    # parser.add_argument(
    #     "--max-workers",
    #     type=int,
    #     default=3,
    #     help="Number of parallel workers for vision review (default: 3)",
    # )
    return parser.parse_args()


def get_doc_list(pre_dir: str, doc_id: str | None):
    if doc_id:
        return [doc_id]
    items = []
    for name in os.listdir(pre_dir):
        if os.path.isdir(os.path.join(pre_dir, name)):
            items.append(name)
    return sorted(items)


def main():
    args = parse_args()
    os.makedirs(args.save_dir, exist_ok=True)

    def _normalize_section_title(text: str) -> str:
        s = str(text or "").strip().lower()
        s = re.sub(r"\s+", "", s)
        s = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`【】\[\]<>]", "", s)
        return s

    def _is_empty_content_claim(issue: Dict[str, Any]) -> bool:
        section = str(issue.get("section", "") or "").strip()
        if not section:
            return False
        text = " ".join(
            [
                str(issue.get("quote", "") or ""),
                str(issue.get("diagnosis", "") or ""),
                str(issue.get("suggestion", "") or ""),
            ]
        )
        patterns = [
            r"标题下无内容",
            r"小节为空",
            r"该小节为空",
            r"仅有一个标题",
            r"内容缺失",
        ]
        return any(re.search(p, text) for p in patterns)

    def _section_has_nonempty_paragraph(reader: Any, section_title: str) -> bool:
        if not reader or not getattr(reader, "root", None) or not section_title:
            return False
        target = _normalize_section_title(section_title)
        if not target:
            return False

        for sec in reader.root.iter("Section"):
            heading_text = ""
            for child in list(sec):
                if child.tag in {"Heading", "Title"} and (child.text or "").strip():
                    heading_text = (child.text or "").strip()
                    break
            if _normalize_section_title(heading_text) != target:
                continue

            for p in sec.iter("Paragraph"):
                txt = str(p.text or "").strip()
                if not txt:
                    continue
                if txt in {"杭州电子科技大学继续教育学院本科毕业论文", "目 录"}:
                    continue
                if re.fullmatch(r"-?\s*\d+\s*-?", txt):
                    continue
                return True
        return False

    def _normalize_issue_schema(issue: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(issue, dict):
            return {}

        normalized = dict(issue)
        quote = str(normalized.get("quote", "") or "").strip()
        diagnosis = str(normalized.get("diagnosis", "") or "").strip()
        evidence_quote = str(normalized.get("evidence_quote", "") or "").strip()
        issue_type = str(normalized.get("issue_type", "") or "").strip()
        section = str(normalized.get("section", "") or "").strip()

        def _is_structure_or_rule_issue() -> bool:
            structure_types = {"目录结构", "教学大纲对齐"}
            if issue_type in structure_types:
                return True
            if section in {"目录", "目录结构"}:
                return True
            text = f"{quote} {diagnosis}"
            return bool(
                re.search(
                    r"(缺少|缺失|未见|没有)\s*第?\s*([0-9一二三四五六七八九十]{1,2})\s*章",
                    text,
                )
            )

        def _is_non_literal_quote(text: str) -> bool:
            if not text:
                return False
            # 非原文摘录的常见模式：规则复核、摘要省略、XML残留、结构化结论句
            patterns = [
                r"\[规则复核\]",
                r"\.\.\.|…",
                r"</?[A-Za-z]+",
                r"<Heading|<Section|<Paragraph",
                r"^摘要结构[:：]",
                r"标题下无内容",
                r"小节为空",
                r"仅有一个标题",
            ]
            return any(re.search(p, text) for p in patterns)

        # 向后兼容：保留 quote；若为空则从 evidence/diagnosis 回填
        if not quote:
            quote = evidence_quote or diagnosis
            normalized["quote"] = quote

        if not diagnosis:
            normalized["diagnosis"] = quote or "未提供诊断描述"
        if evidence_quote:
            normalized["evidence_quote"] = evidence_quote
        else:
            # 低风险策略：不再将 quote 无条件回填为证据，避免“结论句伪装成原文证据”
            # 仅当 quote 看起来是字面摘录时才回填，结构/规则类问题保持空证据。
            if quote and (not _is_structure_or_rule_issue()) and (not _is_non_literal_quote(quote)):
                normalized["evidence_quote"] = quote
            else:
                normalized["evidence_quote"] = ""

        if not normalized.get("evidence_status"):
            if normalized.get("evidence_quote"):
                normalized["evidence_status"] = "verifiable"
            elif _is_structure_or_rule_issue():
                normalized["evidence_status"] = "synthetic"
            else:
                normalized["evidence_status"] = "unverifiable"

        return normalized

    def _normalize_issues(
        issues: List[Dict[str, Any]], reader: Any | None = None
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for issue in issues or []:
            normalized = _normalize_issue_schema(issue)
            if normalized:
                # 判空类问题做硬校验：若小节确有正文，则降级为不可核验，避免误报进入 confirmed。
                if _is_empty_content_claim(normalized) and _section_has_nonempty_paragraph(
                    reader, str(normalized.get("section", "") or "")
                ):
                    raw_diag = str(
                        normalized.get("diagnosis", "") or normalized.get("quote", "")
                    ).strip()
                    normalized["diagnosis"] = (
                        "[结构复核] 该小节在 outline 中检出正文段落，"
                        "“标题下无内容/小节为空”结论疑似误报。"
                        + (f" 原始结论：{raw_diag}" if raw_diag else "")
                    )
                    normalized["evidence_quote"] = ""
                    normalized["evidence_status"] = "unverifiable"
                    normalized["evidence_mode"] = "structural_guardrail"
                out.append(normalized)
        return out

    def _build_source_text(reader: Any | None) -> str:
        if not reader or not getattr(reader, "root", None):
            return ""
        parts: List[str] = []
        for node in reader.root.iter():
            text = str(getattr(node, "text", "") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    def _norm_search_text(text: str) -> str:
        s = str(text or "").strip().lower()
        s = re.sub(r"<[^>]+>", "", s)
        s = re.sub(r"\s+", "", s)
        s = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`【】\[\]<>·\-_/\\$^+{}|]", "", s)
        return s

    def _looks_like_literal_evidence(text: str) -> bool:
        if not text:
            return False
        raw = str(text).strip()
        if len(raw) < 6:
            return False
        blocked_patterns = [
            r"缺少必备小节",
            r"未能可靠识别",
            r"角色未知",
            r"无法支撑",
            r"条目格式异常",
            r"正文中混入的单独数字行",
            r"全文",
            r"全局/跨章节",
        ]
        return not any(re.search(p, raw) for p in blocked_patterns)

    def _issue_literal_match(issue: Dict[str, Any], source_text_norm: str) -> bool:
        if not source_text_norm:
            return False
        for field in ("evidence_quote", "quote"):
            candidate = str(issue.get(field, "") or "").strip()
            if not _looks_like_literal_evidence(candidate):
                continue
            norm_candidate = _norm_search_text(candidate)
            if len(norm_candidate) >= 6 and norm_candidate in source_text_norm:
                return True
        return False

    def _has_actionable_suggestion(issue: Dict[str, Any]) -> bool:
        suggestion = str(issue.get("suggestion", "") or "").strip()
        if len(suggestion) < 8:
            return False
        blocked = ["建议人工复核", "待复核", "请人工", "可进一步检查"]
        return not any(x in suggestion for x in blocked)

    def _has_page_anchor(issue: Dict[str, Any]) -> bool:
        page = issue.get("page")
        if isinstance(page, int):
            return True
        if isinstance(page, str) and re.search(r"\d+", page):
            return True
        return False

    def _is_rule_template_issue(issue: Dict[str, Any]) -> bool:
        issue_type = str(issue.get("issue_type", "") or "").strip()
        text = " ".join(
            [
                str(issue.get("diagnosis", "") or ""),
                str(issue.get("quote", "") or ""),
                str(issue.get("suggestion", "") or ""),
            ]
        )
        if issue_type in {"目录结构", "教学大纲对齐"}:
            return True
        return any(x in text for x in ["缺少必备小节", "必须包含", "教学大纲", "目录结构"])

    def _is_cross_chapter_issue(issue: Dict[str, Any]) -> bool:
        section = str(issue.get("section", "") or "")
        text = " ".join(
            [
                section,
                str(issue.get("diagnosis", "") or ""),
                str(issue.get("quote", "") or ""),
                str(issue.get("suggestion", "") or ""),
            ]
        )
        return any(x in text for x in ["全局/跨章节", "跨章节", "全文其他所有章节", "全文其他章节"])

    def _is_weak_visual_issue(issue: Dict[str, Any]) -> bool:
        issue_type = str(issue.get("issue_type", "") or "").strip()
        text = " ".join(
            [
                str(issue.get("diagnosis", "") or ""),
                str(issue.get("quote", "") or ""),
                str(issue.get("suggestion", "") or ""),
            ]
        )
        if issue_type != "图文一致性":
            return False
        markers = [
            "未能可靠识别图像论证角色",
            "角色未知",
            "无法支撑",
            "仅展示ui布局",
            "仅展示列表和操作按钮",
            "未包含任何技术实现细节",
        ]
        lower_text = text.lower()
        return any(x.lower() in lower_text for x in markers)

    def _is_layout_issue(issue: Dict[str, Any]) -> bool:
        issue_type = str(issue.get("issue_type", "") or "").strip()
        text = " ".join(
            [
                issue_type,
                str(issue.get("diagnosis", "") or ""),
                str(issue.get("quote", "") or ""),
                str(issue.get("suggestion", "") or ""),
            ]
        ).lower()
        layout_keywords = [
            "上标",
            "右上角",
            "字体",
            "字号",
            "行距",
            "段前",
            "段后",
            "缩进",
            "首行缩进",
            "两端对齐",
            "居中",
            "加粗",
            "斜体",
            "下划线",
            "版式",
            "排版",
            "superscript",
            "font",
            "fontsize",
            "line spacing",
            "indent",
            "alignment",
            "bold",
            "italic",
        ]
        type_hint = any(k in issue_type for k in ["规范", "格式", "编号"])
        return bool(type_hint and any(k in text for k in layout_keywords))

    def _keyword_count(text: str) -> int:
        if not text or "关键词" not in text and "key words" not in text.lower() and "keywords" not in text.lower():
            return 0
        content = re.split(r"[：:]", str(text), maxsplit=1)
        if len(content) < 2:
            return 0
        items = [x.strip() for x in re.split(r"[;；]", content[1]) if x.strip()]
        return len(items)

    def _has_internal_contradiction(issue: Dict[str, Any], source_text_norm: str) -> bool:
        text = " ".join(
            [
                str(issue.get("diagnosis", "") or ""),
                str(issue.get("quote", "") or ""),
                str(issue.get("suggestion", "") or ""),
            ]
        )

        if "鲜花销售管理系统" in text and _norm_search_text("鲜花销售管理系统") not in source_text_norm:
            return True

        if any(x in text for x in ["关键词数量不足4个", "关键词数量（3个）少于要求的4个"]):
            for field in ("evidence_quote", "quote"):
                if _keyword_count(str(issue.get(field, "") or "")) >= 4:
                    return True

        if "正文中混入的单独数字行" in text and not _issue_literal_match(issue, source_text_norm):
            return True

        return False

    def _advisor_score(issue: Dict[str, Any], source_text_norm: str) -> int:
        score = 0
        evidence_status = str(issue.get("evidence_status", "") or "").strip().lower()
        issue_type = str(issue.get("issue_type", "") or "").strip()

        if evidence_status == "verifiable":
            score += 2
        elif evidence_status == "synthetic":
            score -= 2
        else:
            score -= 2

        if _has_page_anchor(issue):
            score += 2
        elif str(issue.get("section", "") or "").strip():
            score += 1

        if _issue_literal_match(issue, source_text_norm):
            score += 2
        elif evidence_status == "verifiable":
            score -= 2

        if issue_type in {"规范性", "语言"}:
            score += 2
        elif issue_type in {"逻辑性", "连贯性"}:
            score += 1
        elif issue_type == "图文一致性":
            score -= 1
        elif issue_type in {"目录结构", "教学大纲对齐"}:
            score -= 2

        if _has_actionable_suggestion(issue):
            score += 2
        else:
            score -= 1

        if _is_layout_issue(issue):
            score -= 2
        if _is_rule_template_issue(issue):
            score -= 2
        if _is_weak_visual_issue(issue):
            score -= 2
        if _is_cross_chapter_issue(issue):
            score -= 1
        if _has_internal_contradiction(issue, source_text_norm):
            score -= 3

        return score

    def _is_low_confidence_issue(issue: Dict[str, Any]) -> bool:
        evidence_status = str(issue.get("evidence_status", "") or "").strip().lower()
        confidence = issue.get("confidence")
        confidence_score = issue.get("confidence_score")
        issue_type = str(issue.get("issue_type", "") or "").strip()
        diagnosis = str(issue.get("diagnosis", "") or "").strip()
        quote = str(issue.get("quote", "") or "").strip()
        suggestion = str(issue.get("suggestion", "") or "").strip()
        evidence_mode = str(issue.get("evidence_mode", "") or "").strip().lower()
        has_image_evidence = bool(issue.get("image_id") or issue.get("image_name"))

        # 硬门槛：需要版式证据但当前缺少可验证版式证据 -> 待复核
        # 允许两种方式放行：视觉证据（image_id/image_name）或显式布局证据模式。
        if _is_layout_issue(issue):
            if (not has_image_evidence) and evidence_mode not in {
                "layout_verifiable",
                "vision_verifiable",
            }:
                return True

        # 若模型给出明确置信分，优先采用分值
        if isinstance(confidence_score, (int, float)):
            return float(confidence_score) < 0.6
        if isinstance(confidence, (int, float)):
            return float(confidence) < 0.6

        # 经验规则：不可核验/规则推断进入待复核
        if evidence_status in {"unverifiable", "synthetic"}:
            return True
        return False

    def _is_confirmed_issue(issue: Dict[str, Any], source_text_norm: str) -> bool:
        """
        导师建议区硬门槛：
        1) 可核实（evidence_status=verifiable）
        2) 可直接修改（诊断与建议完整）
        3) 排除 OCR/版式噪声/跨段映射不稳等高争议条目
        """
        evidence_status = str(issue.get("evidence_status", "") or "").strip().lower()
        diagnosis = str(issue.get("diagnosis", "") or "").strip()
        suggestion = str(issue.get("suggestion", "") or "").strip()

        # 先过低置信门槛：低置信条目不能进入导师建议
        if _is_low_confidence_issue(issue):
            return False

        # 只接收可核实证据
        if evidence_status != "verifiable":
            return False

        # 必须可执行：有明确问题和修改建议
        if not diagnosis or not suggestion:
            return False

        if _is_rule_template_issue(issue):
            return False

        if _is_weak_visual_issue(issue):
            return False

        if _is_layout_issue(issue):
            return False

        if _is_cross_chapter_issue(issue) and not _issue_literal_match(issue, source_text_norm):
            return False

        if _has_internal_contradiction(issue, source_text_norm):
            return False

        return _advisor_score(issue, source_text_norm) >= 5

    def _split_issue_confidence(
        issues: List[Dict[str, Any]],
        source_text_norm: str,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        confirmed: List[Dict[str, Any]] = []
        pending_review: List[Dict[str, Any]] = []
        for issue in issues or []:
            issue["advisor_score"] = _advisor_score(issue, source_text_norm)
            issue["advisor_bucket"] = (
                "advisor" if _is_confirmed_issue(issue, source_text_norm) else "pending"
            )
            if issue["advisor_bucket"] == "advisor":
                confirmed.append(issue)
            else:
                pending_review.append(issue)
        return confirmed, pending_review

    def _write_preprocess_eval(doc_id: str) -> None:
        try:
            result = evaluate_preprocess_misdiagnosis(
                doc_id=doc_id,
                processed_dir=args.preprocessed_data_dir,
                review_dir=str(Path(args.save_dir) / "review"),
                outline_dir=str(Path(args.save_dir) / "outline"),
            )
            review_dir = Path(args.save_dir) / "review"
            review_dir.mkdir(parents=True, exist_ok=True)
            out_path = review_dir / f"preprocess_eval_{doc_id}.json"
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[OK] preprocess eval saved -> {out_path}")
        except Exception as e:
            print(f"[Warn] preprocess eval generation failed for {doc_id}: {e}")

    def _collect_table_caption_issues(root):
        issues = []

        def _section_title(section):
            for child in list(section):
                if child.tag == "Heading" and (child.text or "").strip():
                    return (child.text or "").strip()
            return section.get("section_id", "未知章节")

        def _walk_section(section):
            title = _section_title(section)
            for child in list(section):
                if child.tag == "CSV_Table":
                    alt_text_node = child.find("Alt_Text")
                    alt_text = (
                        (alt_text_node.text or "").strip()
                        if alt_text_node is not None
                        else ""
                    )
                    if not alt_text:
                        page_num = (
                            child.get("page_num")
                            or section.get("start_page_num")
                            or "N/A"
                        )
                        table_id = child.get("table_id", "")
                        issues.append(
                            {
                                "issue_type": "规范性",
                                "severity": "Medium",
                                "page": page_num,
                                "section": title,
                                "quote": (
                                    f"表格 {table_id} 缺少标题/Alt_Text"
                                    if table_id
                                    else "表格缺少标题/Alt_Text"
                                ),
                                "suggestion": "为该表格补充标题（Alt_Text），例如“表 2-1 …”。",
                                "table_id": table_id,
                            }
                        )
                elif child.tag == "Section":
                    _walk_section(child)

        for sec in root.findall("Section"):
            _walk_section(sec)
        return issues

    if not args.outline_path and args.doc_id:
        auto_outline = Path(args.save_dir) / "outline" / f"outline_{args.doc_id}.xml"
        if not auto_outline.exists():
            auto_outline = Path(args.save_dir) / f"outline_{args.doc_id}.xml"
        if auto_outline.exists():
            args.outline_path = str(auto_outline)

    if args.outline_path:
        doc_id = Path(args.outline_path).stem.replace("outline_", "")
        review_context = {
            "doc_id": doc_id,
            "requested_thesis_type": args.thesis_type,
            "resolved_thesis_type": args.thesis_type,
            "rules_profile": "default",
        }
        print(f"[Review] {doc_id} (outline-only)")
        data_path = os.path.join(args.preprocessed_data_dir, doc_id)
        if not os.path.isdir(data_path):
            data_path = None
        reader = doc_reader.OutlineOnlyReader(
            outline_path=args.outline_path,
            data_path=data_path,
        )
        agent = doc_agent.DocAgent(
            reader,
            model_id="deepseek-v3.2",
            api_key=args.api_key,
            base_url=args.base_url,
        )

        # ========== 并行审查（Outline-only 模式） ==========
        print("\n[Agent] Starting parallel review (Normative + Logic + Vision)...")

        def run_normative_outline():
            try:
                print("[Agent] [Normative] Thread started...")
                result = NormativeAgent(agent).run()
                print("[Agent] [Normative] Thread completed [OK]")
                return result
            except Exception as e:
                print(f"[Agent] [Normative] Thread failed: {e}")
                return {"parsed": {"issues": []}, "thinking": f"Error: {e}"}

        def run_logic_outline():
            try:
                print("[Agent] [Logic] Thread started...")
                logic_agent_instance = LogicAgent(
                    agent,
                    thesis_type=args.thesis_type,
                    vision_model_id=args.vision_model,
                    vision_api_key=args.vision_api_key,
                    vision_base_url=args.vision_base_url,
                )
                # 顶层上下文：统一记录最终论文类型判定
                review_context["resolved_thesis_type"] = (
                    logic_agent_instance.thesis_type
                )
                result = logic_agent_instance.run()
                print("[Agent] [Logic] Thread completed [OK]")
                return result
            except Exception as e:
                print(f"[Agent] [Logic] Thread failed: {e}")
                return {"parsed": {"issues": []}, "thinking": f"Error: {e}"}

        def run_vision_outline():
            try:
                if not (reader.image_path_dict or reader.table_image_path_dict):
                    print("[Agent] [Vision] Skipped (no images)")
                    return {"parsed": {"issues": []}, "thinking": ""}

                print("[Agent] [Vision] Thread started...")
                vision_agent_instance = VisionAgent(agent)
                result = vision_agent_instance.run(
                    vision_model_id=args.vision_model,
                    vision_api_key=args.vision_api_key,
                    vision_base_url=args.vision_base_url,
                    include_page_image=True,
                    parallel=None,
                    max_workers=None,
                )
                print("[Agent] [Vision] Thread completed [OK]")
                return result
            except Exception as e:
                print(f"[Agent] [Vision] Thread failed: {e}")
                return {"parsed": {"issues": []}, "thinking": f"Error: {e}"}

        import time

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_normative = executor.submit(run_normative_outline)
            future_logic = executor.submit(run_logic_outline)
            future_vision = executor.submit(run_vision_outline)

            timeout_seconds = 1800
            try:
                normative_out = future_normative.result(timeout=timeout_seconds)
                logic_out = future_logic.result(timeout=timeout_seconds)
                vision_out = future_vision.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                print(f"[Error] Review timed out after {timeout_seconds} seconds")
                normative_out = {"parsed": {"issues": []}, "thinking": "Timeout"}
                logic_out = {"parsed": {"issues": []}, "thinking": "Timeout"}
                vision_out = {"parsed": {"issues": []}, "thinking": "Timeout"}

        elapsed_time = time.time() - start_time
        print(f"\n[Agent] [OK] All agents completed in {elapsed_time:.1f} seconds\n")

        normative_data = normative_out.get("parsed", {"issues": []})
        normative_thinking = normative_out.get("thinking", "")

        logic_data = logic_out.get("parsed", {"issues": []})
        logic_thinking = logic_out.get("thinking", "")

        vision_data = vision_out.get("parsed", {"issues": []})
        vision_thinking = vision_out.get("thinking", "")

        # Syllabus 审计与导师反馈（串行，依赖前述审查结果）
        try:
            syllabus_out = SyllabusAuditAgent(
                agent, thesis_type=review_context.get("resolved_thesis_type", "auto")
            ).run()
        except Exception as e:
            print(f"[Agent] [SyllabusAudit] failed: {e}")
            syllabus_out = {"parsed": {"chapter_audits": [], "issues": []}, "thinking": f"Error: {e}"}
        syllabus_data = syllabus_out.get("parsed", {"chapter_audits": [], "issues": []})
        syllabus_thinking = syllabus_out.get("thinking", "")

        normative_issues = _normalize_issues(normative_data.get("issues", []), reader)
        logic_issues = _normalize_issues(logic_data.get("issues", []), reader)
        vision_issues = _normalize_issues(vision_data.get("issues", []), reader)
        syllabus_issues = _normalize_issues(syllabus_data.get("issues", []), reader)
        all_issues = normative_issues + logic_issues + vision_issues + syllabus_issues
        source_text_norm = _norm_search_text(_build_source_text(reader))
        confirmed_issues, pending_review_issues = _split_issue_confidence(
            all_issues, source_text_norm
        )
        try:
            tutor_out = TutorFeedbackAgent(agent).run(
                {
                    "issues": confirmed_issues,
                    "syllabus_audit": syllabus_data,
                }
            )
        except Exception as e:
            print(f"[Agent] [TutorFeedback] failed: {e}")
            tutor_out = {"parsed": {"overall_comment": "", "chapter_comments": [], "tasks": []}, "thinking": f"Error: {e}"}
        tutor_data = tutor_out.get("parsed", {"overall_comment": "", "chapter_comments": [], "tasks": []})
        tutor_thinking = tutor_out.get("thinking", "")

        final_result = {
            "doc_id": doc_id,
            "review_context": review_context,
            "normative_thinking": normative_thinking,
            "logic_thinking": logic_thinking,
            "vision_thinking": vision_thinking,
            "syllabus_thinking": syllabus_thinking,
            "tutor_thinking": tutor_thinking,
            "normative_issues": normative_issues,
            "logic_issues": logic_issues,
            "vision_issues": vision_issues,
            "syllabus_issues": syllabus_issues,
            "syllabus_audit": syllabus_data,
            "tutor_feedback": tutor_data,
            "tasks": tutor_data.get("tasks", []),
            "tutor_issue_pool_count": len(confirmed_issues),
            "toc_suggestion": logic_data.get("toc_suggestion", {}),
            "issues": confirmed_issues,
            "pending_review_issues": pending_review_issues,
            "all_issues": all_issues,
        }

        review_dir = Path(args.save_dir) / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        result_file = review_dir / f"review_{doc_id}.json"
        result_file.write_text(
            json.dumps(final_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] Outline-only review saved -> {result_file}")
        _write_preprocess_eval(doc_id)
        return

    docs = get_doc_list(args.preprocessed_data_dir, args.doc_id)
    for doc_id in docs:
        review_context = {
            "doc_id": doc_id,
            "requested_thesis_type": args.thesis_type,
            "resolved_thesis_type": args.thesis_type,
            "rules_profile": "default",
        }
        data_path = os.path.join(args.preprocessed_data_dir, doc_id)

        # 统一使用预处理生成的 outline XML（避免重复构建）
        outline_path = Path(args.save_dir) / "outline" / f"outline_{doc_id}.xml"
        if not outline_path.exists():
            outline_path = Path(args.save_dir) / f"outline_{doc_id}.xml"
        if not outline_path.exists():
            print(f"[Skip] {doc_id}: outline XML not found at {outline_path}")
            print(
                f"[Hint] Please run preprocessing first: ./scripts/run_pipeline.ps1 -DocName {doc_id}"
            )
            continue

        print(f"[Review] {doc_id} (using preprocessed outline)")
        reader = doc_reader.OutlineOnlyReader(
            outline_path=str(outline_path),
            data_path=data_path,
        )
        agent = doc_agent.DocAgent(
            reader,
            model_id="deepseek-v3.2",
            api_key=args.api_key,
            base_url=args.base_url,
        )

        # 三个 Agent 审查起点已对齐：均从「摘要」开始（封面/诚信声明等之前内容不审查）
        # Normative: 正文片段 = 摘要及之后 (_extract_plain_text_from_abstract)
        # Logic: 顶层 Section = 摘要及之后 (_get_outermost_section_ids)
        # Vision: 图片 = 摘要章节及之后的图片 (intro_index)

        # ========== 并行审查（使用 ThreadPoolExecutor） ==========
        print("\n[Agent] Starting parallel review (Normative + Logic + Vision)...")
        print("[Agent] This will significantly reduce total review time!\n")

        normative_out = {}
        logic_out = {}
        vision_out = {}

        def run_normative():
            """并行任务：规范性审查"""
            try:
                print("[Agent] [Normative] Thread started...")
                result = NormativeAgent(agent).run()
                print("[Agent] [Normative] Thread completed [OK]")
                return result
            except Exception as e:
                print(f"[Agent] [Normative] Thread failed: {e}")
                return {"parsed": {"issues": []}, "thinking": f"Error: {e}"}

        def run_logic():
            """并行任务：逻辑性审查"""
            try:
                print("[Agent] [Logic] Thread started...")
                logic_agent_instance = LogicAgent(
                    agent,
                    thesis_type=args.thesis_type,
                    vision_model_id=args.vision_model,
                    vision_api_key=args.vision_api_key,
                    vision_base_url=args.vision_base_url,
                )
                # 顶层上下文：统一记录最终论文类型判定
                review_context["resolved_thesis_type"] = (
                    logic_agent_instance.thesis_type
                )
                result = logic_agent_instance.run()
                print("[Agent] [Logic] Thread completed [OK]")
                return result
            except Exception as e:
                print(f"[Agent] [Logic] Thread failed: {e}")
                return {"parsed": {"issues": []}, "thinking": f"Error: {e}"}

        def run_vision():
            """并行任务：视觉审查"""
            try:
                print("[Agent] [Vision] Thread started...")
                vision_agent_instance = VisionAgent(agent)
                result = vision_agent_instance.run(
                    vision_model_id=args.vision_model,
                    vision_api_key=args.vision_api_key,
                    vision_base_url=args.vision_base_url,
                    include_page_image=True,  # 启用三页窗口辅助定位
                    parallel=None,  # 已废弃，不再使用并行模式
                    max_workers=None,  # 已废弃，不再使用并行模式
                )
                print("[Agent] [Vision] Thread completed [OK]")
                return result
            except Exception as e:
                print(f"[Agent] [Vision] Thread failed: {e}")
                return {"parsed": {"issues": []}, "thinking": f"Error: {e}"}

        # 使用 ThreadPoolExecutor 并行运行三个 Agent
        import time

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            # 提交三个任务
            future_normative = executor.submit(run_normative)
            future_logic = executor.submit(run_logic)
            future_vision = executor.submit(run_vision)

            # 等待所有任务完成（带超时控制，默认 30 分钟）
            timeout_seconds = 1800  # 30 分钟
            try:
                normative_out = future_normative.result(timeout=timeout_seconds)
                logic_out = future_logic.result(timeout=timeout_seconds)
                vision_out = future_vision.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                print(f"[Error] Review timed out after {timeout_seconds} seconds")
                # 使用空结果继续
                if not normative_out:
                    normative_out = {"parsed": {"issues": []}, "thinking": "Timeout"}
                if not logic_out:
                    logic_out = {"parsed": {"issues": []}, "thinking": "Timeout"}
                if not vision_out:
                    vision_out = {"parsed": {"issues": []}, "thinking": "Timeout"}

        elapsed_time = time.time() - start_time
        print(f"\n[Agent] [OK] All agents completed in {elapsed_time:.1f} seconds")
        print(
            f"[Agent] Parallel speedup: ~{elapsed_time/60:.1f} min (vs ~{elapsed_time*3/60:.1f} min serial)\n"
        )

        # 提取结果
        normative_issues = _normalize_issues(
            normative_out.get("parsed", {}).get("issues", []), reader
        )
        normative_thinking_str = normative_out.get("thinking", "")

        logic_issues = _normalize_issues(
            logic_out.get("parsed", {}).get("issues", []), reader
        )
        logic_thinking_str = logic_out.get("thinking", "")

        vision_issues = _normalize_issues(
            vision_out.get("parsed", {}).get("issues", []), reader
        )
        vision_thinking_str = vision_out.get("thinking", "")

        # Syllabus 审计与导师反馈（串行）
        try:
            syllabus_out = SyllabusAuditAgent(
                agent, thesis_type=review_context.get("resolved_thesis_type", "auto")
            ).run()
        except Exception as e:
            print(f"[Agent] [SyllabusAudit] failed: {e}")
            syllabus_out = {"parsed": {"chapter_audits": [], "issues": []}, "thinking": f"Error: {e}"}
        syllabus_data = syllabus_out.get("parsed", {"chapter_audits": [], "issues": []})
        syllabus_issues = _normalize_issues(syllabus_data.get("issues", []), reader)
        syllabus_thinking_str = syllabus_out.get("thinking", "")

        all_issues = normative_issues + logic_issues + vision_issues + syllabus_issues
        source_text_norm = _norm_search_text(_build_source_text(reader))
        confirmed_issues, pending_review_issues = _split_issue_confidence(
            all_issues, source_text_norm
        )
        try:
            tutor_out = TutorFeedbackAgent(agent).run(
                {
                    "issues": confirmed_issues,
                    "syllabus_audit": syllabus_data,
                }
            )
        except Exception as e:
            print(f"[Agent] [TutorFeedback] failed: {e}")
            tutor_out = {"parsed": {"overall_comment": "", "chapter_comments": [], "tasks": []}, "thinking": f"Error: {e}"}
        tutor_data = tutor_out.get("parsed", {"overall_comment": "", "chapter_comments": [], "tasks": []})
        tutor_thinking_str = tutor_out.get("thinking", "")

        # 调试信息
        print(f"[Debug] Normative issues count: {len(normative_issues)}")
        print(f"[Debug] Logic issues count: {len(logic_issues)}")
        print(f"[Debug] Vision issues count: {len(vision_issues)}")
        print(f"[Debug] Syllabus issues count: {len(syllabus_issues)}")
        print(f"[Debug] Confirmed issues count: {len(confirmed_issues)}")
        print(f"[Debug] Pending-review issues count: {len(pending_review_issues)}")
        print(f"[Debug] Tutor issue pool (confirmed) count: {len(confirmed_issues)}")

        merged = {
            "doc_id": doc_id,
            "review_context": review_context,
            "normative_thinking": normative_thinking_str,
            "logic_thinking": logic_thinking_str,
            "vision_thinking": vision_thinking_str,
            "syllabus_thinking": syllabus_thinking_str,
            "tutor_thinking": tutor_thinking_str,
            "normative_issues": normative_issues,
            "logic_issues": logic_issues,
            "vision_issues": vision_issues,
            "syllabus_issues": syllabus_issues,
            "syllabus_audit": syllabus_data,
            "tutor_feedback": tutor_data,
            "tasks": tutor_data.get("tasks", []),
            "tutor_issue_pool_count": len(confirmed_issues),
            "toc_suggestion": logic_out.get("parsed", {}).get("toc_suggestion", {}),
            "issues": confirmed_issues,
            "pending_review_issues": pending_review_issues,
            "all_issues": all_issues,
        }

        review_dir = Path(args.save_dir) / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        out_path = review_dir / f"review_{doc_id}.json"
        out_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] saved -> {out_path}")
        _write_preprocess_eval(doc_id)


if __name__ == "__main__":
    main()
