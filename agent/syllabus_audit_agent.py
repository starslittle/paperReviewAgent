from __future__ import annotations

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Set


class SyllabusAuditAgent:
    """
    教学大纲对齐审计 Agent（MVP）

    职责：
    - 检查章节/小节是否覆盖规则文档要求（缺失项）
    - 进行轻量冗余检测（段落重复）
    - 进行轻量跑题检测（章节关键词冲突）
    """

    def __init__(self, doc_agent: Any, thesis_type: str = "auto"):
        self.doc_agent = doc_agent
        self.thesis_type = thesis_type
        self._fallback_chapter_titles: Dict[int, str] = {
            1: "绪论",
            2: "关键技术与工具",
            3: "需求分析",
            4: "系统设计",
            5: "系统实现",
            6: "系统测试",
            7: "总结与展望",
        }
        # 兜底标题映射：当规则文档未解析到小节名称时，仍尽量输出“节号+名称”
        self._fallback_subsection_titles: Dict[str, str] = {
            # 第1章
            "1.1": "研究背景和意义",
            "1.2": "国内外研究现状",
            "1.3": "论文主要研究内容",
            "1.4": "论文组织结构",
            # 第2章
            "2.1": "B/S架构",
            "2.2": "SpringBoot框架",
            "2.3": "Java语言",
            "2.4": "Vue.js框架",
            "2.5": "UniApp框架",
            "2.6": "MySQL数据库",
            # 第3章
            "3.1": "可行性分析",
            "3.2": "系统需求分析",
            "3.3": "非功能性需求分析",
            "3.4": "业务流程分析",
            # 第4章
            "4.1": "系统架构设计",
            "4.2": "系统总体设计",
            "4.3": "功能详细设计",
            "4.4": "数据库设计",
            # 第5章
            "5.1": "开发环境",
            "5.2": "登录功能实现",
            "5.3": "用户信息管理功能实现",
            "5.4": "角色信息管理功能实现",
            "5.5": "部门信息管理功能实现",
            "5.6": "工程信息管理功能实现",
            "5.7": "设备信息管理功能实现",
            "5.8": "巡检项管理功能实现",
            "5.9": "对象管理功能实现",
            "5.10": "范围管理功能实现",
            "5.11": "计划管理功能实现",
            "5.12": "任务管理功能实现",
            "5.13": "巡检监控管理功能实现",
            "5.14": "移动端巡检管理功能实现",
            # 第6章
            "6.1": "测试目的",
            "6.2": "测试环境",
            "6.3": "测试用例及其执行",
            "6.4": "测试结果",
            # 第7章
            "7.1": "总结",
            "7.2": "展望",
        }

    def run(self) -> Dict[str, Any]:
        return self.run_syllabus_audit()

    def _resolve_subsection_title(self, rules: Dict[str, Any], subsection_num: str) -> str:
        from_rules = (rules.get("subsection_titles", {}) or {}).get(subsection_num, "")
        if isinstance(from_rules, str) and from_rules.strip():
            return from_rules.strip()
        return self._fallback_subsection_titles.get(subsection_num, "")

    def _resolve_chapter_title(self, rules: Dict[str, Any], chapter_num: int) -> str:
        from_rules = (rules.get("chapter_titles", {}) or {}).get(chapter_num, "")
        if isinstance(from_rules, str) and from_rules.strip():
            return from_rules.strip()
        return self._fallback_chapter_titles.get(chapter_num, "")

    def _load_system_rules(self) -> Dict[str, Any]:
        rules: Dict[str, Any] = {
            "source": "",
            "top_required": [1, 2, 3, 4, 5, 6, 7],
            "required_subsections": set(),
            "optional_subsections": set(),
            "chapter_titles": {},
            "subsection_titles": {},
        }

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rules_dir = os.path.join(base_dir, "rules")
        if not os.path.isdir(rules_dir):
            return rules

        docx_files = [
            os.path.join(rules_dir, x)
            for x in os.listdir(rules_dir)
            if x.lower().endswith(".docx")
        ]
        if not docx_files:
            return rules

        preferred = [
            p for p in docx_files if "开发类论文目录结构" in os.path.basename(p)
        ]
        docx_path = preferred[0] if preferred else docx_files[0]
        rules["source"] = docx_path

        try:
            with zipfile.ZipFile(docx_path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
            text = re.sub(r"<[^>]+>", "\n", xml)
            text = re.sub(r"\n+", "\n", text)
            lines = [x.strip() for x in text.splitlines() if x.strip()]

            title_map: Dict[int, str] = {}
            required_subs: Set[str] = set()
            optional_subs: Set[str] = set()
            subsection_titles: Dict[str, str] = {}
            cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
            for line in lines:
                top = re.match(r"^第\s*([一二三四五六七])\s*章\s*(.+)$", line)
                if top:
                    idx = cn_map.get(top.group(1))
                    if idx:
                        title_map[idx] = top.group(2).strip()
                    continue
                sub = re.match(r"^(\d+\.\d+)\s*(.+)$", line)
                if sub:
                    num = sub.group(1)
                    subtitle = sub.group(2).strip()
                    if subtitle:
                        subsection_titles[num] = subtitle
                    if "可选" in line:
                        optional_subs.add(num)
                    else:
                        required_subs.add(num)

            if title_map:
                rules["chapter_titles"] = title_map
            if required_subs:
                rules["required_subsections"] = required_subs
            if optional_subs:
                rules["optional_subsections"] = optional_subs
            if subsection_titles:
                rules["subsection_titles"] = subsection_titles
        except Exception:
            pass

        return rules

    def _iter_top_sections(self) -> List[ET.Element]:
        root = self.doc_agent.doc_reader.root
        return [x for x in list(root) if x.tag == "Section"]

    def _section_title(self, section: ET.Element) -> str:
        for child in list(section):
            if child.tag in ["Heading", "Title"] and (child.text or "").strip():
                return (child.text or "").strip()
        return f"Section {section.get('section_id', '')}"

    def _chapter_num_from_title(self, title: str) -> int | None:
        if not title:
            return None
        m = re.match(r"^\s*(\d+)\b", title)
        if m:
            return int(m.group(1))
        return None

    def _extract_subsection_nums(self, section: ET.Element) -> Set[str]:
        nums: Set[str] = set()
        for node in section.iter():
            if node.tag not in ["Heading", "Title"]:
                continue
            text = (node.text or "").strip()
            m = re.match(r"^\s*(\d+\.\d+)\b", text)
            if m:
                nums.add(m.group(1))
        return nums

    def _extract_global_subsections_by_chapter(self) -> Dict[int, Set[str]]:
        """
        全局扫描 Heading/Title，按章节号聚合小节编号。
        解决目录/图表 section 切分导致的小节“跨 section 漂移”问题（如 6.4 误落到图表 section）。
        """
        chapter_map: Dict[int, Set[str]] = {}
        root = self.doc_agent.doc_reader.root
        for node in root.iter():
            if node.tag not in ["Heading", "Title"]:
                continue
            text = (node.text or "").strip()
            m = re.match(r"^\s*(\d+)\.(\d+)\b", text)
            if not m:
                continue
            chapter_num = int(m.group(1))
            subsection_num = f"{m.group(1)}.{m.group(2)}"
            chapter_map.setdefault(chapter_num, set()).add(subsection_num)
        return chapter_map

    def _extract_paragraphs(self, section: ET.Element) -> List[str]:
        paras: List[str] = []
        for node in section.iter():
            if node.tag != "Paragraph":
                continue
            text = (node.text or "").strip()
            if text:
                paras.append(re.sub(r"\s+", " ", text))
        return paras

    def _norm(self, text: str) -> str:
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[，。、“”‘’：:；;,.!?！？（）()\-\[\]{}]", "", text)
        return text

    def _find_redundant_paragraphs(self, paragraphs: List[str]) -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []
        seen = {}
        for idx, p in enumerate(paragraphs):
            key = self._norm(p)
            if len(key) < 40:
                continue
            if key in seen:
                output.append(
                    {
                        "current_index": idx,
                        "previous_index": seen[key],
                        "snippet": p[:120],
                    }
                )
            else:
                seen[key] = idx
        return output[:3]

    def _find_off_topic_paragraphs(
        self, chapter_num: int, paragraphs: List[str]
    ) -> List[Dict[str, Any]]:
        chapter_keywords = {
            1: ["背景", "现状", "研究", "组织结构", "章节"],
            2: ["技术", "框架", "工具", "语言", "数据库", "选型"],
            3: ["需求", "可行性", "用例", "角色", "业务流程", "数据流程"],
            4: ["设计", "架构", "模块", "流程", "数据库", "接口"],
            5: ["实现", "开发环境", "功能", "代码", "页面", "模块实现"],
            6: ["测试", "用例", "性能", "结果", "环境"],
            7: ["总结", "展望", "不足", "改进"],
        }
        cross_keywords = {
            5: ["可行性分析", "研究现状"],
            6: ["系统实现", "代码实现"],
            3: ["测试结果", "性能测试"],
        }

        self_keys = chapter_keywords.get(chapter_num, [])
        risk_keys = cross_keywords.get(chapter_num, [])
        output: List[Dict[str, Any]] = []
        for idx, p in enumerate(paragraphs):
            if len(p) < 40:
                continue
            has_self = any(k in p for k in self_keys)
            has_risk = any(k in p for k in risk_keys)
            if has_risk and not has_self:
                output.append({"index": idx, "snippet": p[:120]})
        return output[:3]

    def run_syllabus_audit(self) -> Dict[str, Any]:
        rules = self._load_system_rules()
        chapter_audits: List[Dict[str, Any]] = []
        issues: List[Dict[str, Any]] = []
        if self.thesis_type != "system":
            return {
                "raw": {
                    "chapter_audits": [],
                    "issues": [],
                    "note": "非程序开发类论文，跳过教学大纲对齐审计",
                },
                "parsed": {"chapter_audits": [], "issues": []},
                "thinking": "非程序开发类论文，未执行 SyllabusAudit。",
                "errors": [],
            }

        top_sections = self._iter_top_sections()
        global_subsections_by_chapter = self._extract_global_subsections_by_chapter()
        present_top = set()
        for section in top_sections:
            title = self._section_title(section)
            chapter_num = self._chapter_num_from_title(title)
            if not chapter_num:
                continue
            present_top.add(chapter_num)
            local_sub_nums = self._extract_subsection_nums(section)
            # 以“本章局部 + 全局聚合”并集为准，避免因 section 切分导致误报
            sub_nums = set(local_sub_nums) | set(
                global_subsections_by_chapter.get(chapter_num, set())
            )
            required_subs = sorted(
                [x for x in rules.get("required_subsections", set()) if x.startswith(f"{chapter_num}.")]
            )
            optional_subs = sorted(
                [x for x in rules.get("optional_subsections", set()) if x.startswith(f"{chapter_num}.")]
            )
            missing_required = [x for x in required_subs if x not in sub_nums]
            missing_optional = [x for x in optional_subs if x not in sub_nums]

            paragraphs = self._extract_paragraphs(section)
            redundant = self._find_redundant_paragraphs(paragraphs)
            off_topic = self._find_off_topic_paragraphs(chapter_num, paragraphs)

            coverage_denom = max(1, len(required_subs))
            coverage = (len(required_subs) - len(missing_required)) / coverage_denom

            chapter_audits.append(
                {
                    "chapter_num": chapter_num,
                    "chapter_title": title,
                    "required_subsections": required_subs,
                    "required_subsection_labels": [
                        f"{num} {self._resolve_subsection_title(rules, num)}".strip()
                        for num in required_subs
                    ],
                    "missing_required": missing_required,
                    "missing_required_labels": [
                        f"{num} {self._resolve_subsection_title(rules, num)}".strip()
                        for num in missing_required
                    ],
                    "missing_optional": missing_optional,
                    "redundant_blocks": redundant,
                    "off_topic_blocks": off_topic,
                    "coverage_score": round(coverage, 3),
                }
            )

            page_num = section.get("start_page_num", "")
            for m in missing_required:
                subsection_title = self._resolve_subsection_title(rules, m)
                subsection_label = (
                    f"{m} {subsection_title}" if subsection_title else m
                )
                diagnosis = f"缺少必备小节 {subsection_label}"
                issues.append(
                    {
                        "issue_type": "教学大纲对齐",
                        "severity": "High",
                        "section": title,
                        "page": page_num,
                        "quote": diagnosis,
                        "diagnosis": diagnosis,
                        "evidence_quote": "",
                        "evidence_status": "synthetic",
                        "missing_subsection_num": m,
                        "missing_subsection_title": subsection_title,
                        "missing_subsection_label": subsection_label,
                        "suggestion": f"请在 {title} 中补充 {subsection_label} 对应内容，满足教学大纲必备要求。",
                    }
                )
            for r in redundant:
                evidence_quote = r.get("snippet", "")
                issues.append(
                    {
                        "issue_type": "教学大纲对齐",
                        "severity": "Medium",
                        "section": title,
                        "page": page_num,
                        "quote": evidence_quote,
                        "diagnosis": "该段与前文语义高度重复",
                        "evidence_quote": evidence_quote,
                        "evidence_status": (
                            "verifiable" if evidence_quote else "unverifiable"
                        ),
                        "suggestion": "该段与前文语义高度重复，建议压缩合并，避免冗余描述。",
                    }
                )
            for o in off_topic:
                evidence_quote = o.get("snippet", "")
                issues.append(
                    {
                        "issue_type": "教学大纲对齐",
                        "severity": "Medium",
                        "section": title,
                        "page": page_num,
                        "quote": evidence_quote,
                        "diagnosis": "该段与本章目标关联较弱",
                        "evidence_quote": evidence_quote,
                        "evidence_status": (
                            "verifiable" if evidence_quote else "unverifiable"
                        ),
                        "suggestion": "该段与本章目标关联较弱，建议移动到更匹配章节或重写为本章目标内容。",
                    }
                )

        for n in rules.get("top_required", [1, 2, 3, 4, 5, 6, 7]):
            if n not in present_top:
                chapter_title = self._resolve_chapter_title(rules, n)
                chapter_label = f"第{n}章 {chapter_title}".strip() if chapter_title else f"第{n}章"
                diagnosis = f"缺少{chapter_label}"
                issues.append(
                    {
                        "issue_type": "教学大纲对齐",
                        "severity": "High",
                        "section": "目录结构",
                        "page": None,
                        "quote": diagnosis,
                        "diagnosis": diagnosis,
                        "evidence_quote": "",
                        "evidence_status": "synthetic",
                        "missing_chapter_num": n,
                        "missing_chapter_title": chapter_title,
                        "missing_chapter_label": chapter_label,
                        "suggestion": f"请补充{chapter_label}内容，保证程序开发类论文章节结构完整。",
                    }
                )

        parsed = {"chapter_audits": chapter_audits, "issues": issues}
        return {
            "raw": parsed,
            "parsed": parsed,
            "thinking": "已按规则文档执行章节覆盖/冗余/跑题审计。",
            "errors": [],
        }
