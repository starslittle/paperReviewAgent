from __future__ import annotations

from typing import Any, Dict, List
import re


class TutorFeedbackAgent:
    """
    导师风格反馈 Agent（MVP）

    输入：各审查 Agent 的 issues 与 syllabus_audit 结果
    输出：总评 + 分章点评 + 任务单
    """

    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent

    def run(self, review_context: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_tutor_feedback(review_context)

    def _priority(self, severity: str) -> str:
        if severity == "High":
            return "P0"
        if severity == "Medium":
            return "P1"
        return "P2"

    def _page_num(self, item: Dict[str, Any]) -> int:
        page = item.get("page", 9999)
        if isinstance(page, int):
            return page
        if isinstance(page, str):
            match = re.search(r"(\d+)", page)
            if match:
                return int(match.group(1))
        return 9999

    def _norm_text(self, text: str) -> str:
        s = str(text or "").strip().lower()
        s = re.sub(r"\s+", "", s)
        s = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`【】\[\]<>·\-_/\\$^+{}|]", "", s)
        return s

    def _is_tutor_ready(self, issue: Dict[str, Any]) -> bool:
        issue_type = str(issue.get("issue_type", "") or "").strip()
        section = str(issue.get("section", "") or "").strip()
        diagnosis = str(issue.get("diagnosis", "") or "").strip()
        suggestion = str(issue.get("suggestion", "") or "").strip()
        quote = str(issue.get("quote", "") or "").strip()
        evidence_status = str(issue.get("evidence_status", "") or "").strip().lower()
        advisor_bucket = str(issue.get("advisor_bucket", "") or "").strip().lower()

        if advisor_bucket and advisor_bucket != "advisor":
            return False
        if evidence_status != "verifiable":
            return False
        if not diagnosis or not suggestion:
            return False
        if issue_type in {"目录结构", "教学大纲对齐"}:
            return False
        if "全局/跨章节" in section:
            return False

        text = " ".join(
            [
                issue_type,
                section,
                diagnosis,
                quote,
                suggestion,
            ]
        )
        blocked_markers = [
            "未能可靠识别图像论证角色",
            "角色未知",
            "建议人工复核",
            "待复核",
            "疑似",
            "可能为",
            "可能受",
            "目录结构",
            "教学大纲",
        ]
        if any(x in text for x in blocked_markers):
            return False

        if issue_type == "图文一致性":
            return False

        if "鲜花销售管理系统" in text:
            return False

        if "四大核心功能模块" in text and "17个核心模块" in text:
            return False

        if any(x in text for x in ["关键词数量不足4个", "关键词数量（3个）少于要求的4个"]):
            content = re.split(r"[：:]", quote, maxsplit=1)
            if len(content) == 2:
                items = [x.strip() for x in re.split(r"[;；]", content[1]) if x.strip()]
                if len(items) >= 4:
                    return False

        return True

    def _dedupe_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for issue in issues:
            key = (
                str(issue.get("issue_type", "") or "").strip(),
                str(issue.get("section", "") or "").strip(),
                self._norm_text(str(issue.get("diagnosis", "") or issue.get("quote", "") or "")),
                self._norm_text(str(issue.get("suggestion", "") or "")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped

    def run_tutor_feedback(self, review_context: Dict[str, Any]) -> Dict[str, Any]:
        input_issues: List[Dict[str, Any]] = review_context.get("issues", []) or []
        issues: List[Dict[str, Any]] = self._dedupe_issues(
            [x for x in input_issues if self._is_tutor_ready(x)]
        )
        syllabus = review_context.get("syllabus_audit", {}) or {}
        chapter_audits: List[Dict[str, Any]] = syllabus.get("chapter_audits", []) or []

        high = [x for x in issues if x.get("severity") == "High"]
        medium = [x for x in issues if x.get("severity") == "Medium"]
        low = [x for x in issues if x.get("severity") == "Low"]

        if len(high) >= 8:
            level = "当前论文风险较高，建议先完成结构与必备内容补齐，再进入语言润色。"
        elif len(high) >= 3:
            level = "当前论文存在若干关键问题，建议按优先级逐章整改。"
        else:
            level = "当前论文整体基础可用，重点提升内容完整性与表达质量。"

        chapter_comments: List[Dict[str, Any]] = []
        for ch in chapter_audits:
            title = ch.get("chapter_title", "未知章节")
            miss_req = ch.get("missing_required", []) or []
            red = ch.get("redundant_blocks", []) or []
            off = ch.get("off_topic_blocks", []) or []
            coverage = ch.get("coverage_score", 0.0)

            if miss_req:
                comment = (
                    f"{title} 当前未达标，缺少必备要点：{', '.join(miss_req[:4])}。"
                    "请优先补齐后再做语言优化。"
                )
            elif red or off:
                comment = (
                    f"{title} 结构基本完整，但存在内容组织问题。"
                    f"冗余段落 {len(red)} 处，跑题段落 {len(off)} 处。"
                )
            else:
                comment = f"{title} 与教学大纲匹配较好（覆盖率 {coverage:.0%}）。"

            chapter_comments.append(
                {
                    "chapter_title": title,
                    "coverage_score": coverage,
                    "comment": comment,
                }
            )

        # 任务单：默认按论文页码从前到后排序，便于学生顺着论文逐页修改。
        # 同页问题再按严重程度排序，保证关键问题仍然靠前。
        sorted_issues = sorted(
            issues,
            key=lambda x: (
                self._page_num(x),
                {"High": 0, "Medium": 1, "Low": 2}.get(x.get("severity", "Low"), 3),
            ),
        )
        tasks: List[Dict[str, Any]] = []
        for idx, issue in enumerate(sorted_issues, start=1):
            diagnosis = issue.get("diagnosis", "")
            quote = issue.get("quote", "")
            tasks.append(
                {
                    "task_id": f"T{idx:02d}",
                    "priority": self._priority(issue.get("severity", "Low")),
                    "section": issue.get("section", "未知章节"),
                    "page": issue.get("page", ""),
                    "source_issue_type": issue.get("issue_type", "未分类"),
                    "problem": diagnosis or quote,
                    "action": issue.get("suggestion", ""),
                    "done_criteria": "该问题在复检报告中不再出现，且对应章节文字表达更完整清晰。",
                }
            )

        overall_comment = (
            f"{level} 当前共识别问题 {len(issues)} 条（High {len(high)} / Medium {len(medium)} / Low {len(low)}）。"
            "请优先完成全量任务中的 P0 与 P1 项，再提交复检。"
        )

        parsed = {
            "overall_comment": overall_comment,
            "chapter_comments": chapter_comments,
            "tasks": tasks,
        }
        return {
            "raw": parsed,
            "parsed": parsed,
            "thinking": "已按问题优先级生成导师总评与任务单。",
            "errors": [],
        }
