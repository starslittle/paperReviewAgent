from __future__ import annotations

import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Dict, List

from openai import OpenAI

from .prompts import (
    global_logic_review_prompt,
    local_chapter_review_prompt,
    local_chapter_review_retry_prompt,
    logic_prompt,
    system_development_structure_check_prompt,
    system_development_abstract_check_prompt,
    table_of_contents_check_prompt,
    toc_final_suggestion_prompt,
    cover_title_vision_prompt,
    system_development_chapter_hint,
)


class LogicAgent:
    """
    逻辑性审查 Agent (Logic Review)

    【职责范围】：
    - ✅ 论证逻辑：论证跳跃、前后矛盾、论据不足
    - ✅ 语言学术性：口语化表达（"我觉得"、"超级"、"很"等）、用词不规范
    - ✅ 内容一致性：摘要vs结论、方法vs实验、标题vs内容
    - ✅ 连贯性：段落衔接、章节过渡
    - ✅ 内容充分性：工作量、创新点、实验数据

    【不负责】：
    - ❌ 格式编号：章节编号、图表编号（由 NormativeAgent 负责）
    - ❌ 引用格式：参考文献格式（由 NormativeAgent 负责）
    - ❌ 页面格式：页码、页眉页脚（由 NormativeAgent 负责）

    【审查方式】：
    - Map-Reduce：分章节局部审查 + 全局一致性检查
    - 覆盖范围：全文（每章 12000 字）
    - 审查起点：从「摘要」开始（与 NormativeAgent、VisionAgent 对齐）

    【输出】：
    - issue_type: "逻辑性" | "语言" | "连贯性"
    - 细粒度事实抽取（实体/数值/时间/论断）→ fact_store
    - 跨章节冲突检测（数值差异 > 5%）
    """

    ROLE_WEIGHTS = {
        "RESULT": 1.0,
        "METHOD": 0.9,
        "DESIGN": 0.8,
        "BACKGROUND": 0.3,
        "CONCLUSION": 0.6,
    }

    def __init__(
        self,
        doc_agent: Any,
        thesis_type: str = "auto",
        vision_model_id: str = "qwen3-vl-flash",
        vision_api_key: str | None = None,
        vision_base_url: (
            str | None
        ) = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        self.doc_agent = doc_agent
        self.logic_memory: List[Dict[str, Any]] = []
        self.fact_store = {"entities": {}, "numbers": {}, "dates": {}, "claims": []}
        self.vision_model_id = vision_model_id
        self.vision_api_key = vision_api_key
        self.vision_base_url = vision_base_url

        # 论文类型检测
        if thesis_type == "auto":
            self.thesis_type = self._detect_thesis_type()
        else:
            self.thesis_type = thesis_type

        print(f"[LogicAgent] 论文类型: {self.thesis_type}")
        print(
            f"[LogicAgent] Thesis Type: {'程序开发类 (System Development)' if self.thesis_type == 'system' else '算法理论类 (Algorithm Research)'}"
        )

    def _extract_title_from_outline(self) -> str:
        """从XML大纲中提取论文标题"""
        try:
            root = self.doc_agent.doc_reader.root
            # 尝试多种方式提取标题
            for section in root.findall(".//Section"):
                for child in section:
                    if child.tag in ["Title", "Heading"]:
                        text = (child.text or "").strip()
                        # 过滤掉"封面"、"目录"等非标题内容
                        if (
                            text
                            and len(text) > 5
                            and "封面" not in text
                            and "目录" not in text
                        ):
                            return text

            # 如果找不到，返回空字符串
            return ""
        except Exception as e:
            print(f"[Type Detection] 提取标题失败: {e}")
            return ""

    def _get_vision_client(self):
        client = self.doc_agent.client
        using_qwen = "qwen" in self.vision_model_id.lower()
        if using_qwen:
            key = self.vision_api_key or self.doc_agent.client.api_key
            if key is None:
                raise ValueError(
                    "vision_api_key is required when using Qwen vision models. "
                    "Please set DASHSCOPE_API_KEY or pass --vision-api-key."
                )
            client = OpenAI(
                api_key=key,
                base_url=self.vision_base_url or self.doc_agent.client.base_url,
            )
        elif self.vision_api_key or self.vision_base_url:
            client = OpenAI(
                api_key=self.vision_api_key or self.doc_agent.client.api_key,
                base_url=self.vision_base_url or self.doc_agent.client.base_url,
            )
        return client

    def _extract_title_from_cover_with_vision(self) -> str:
        media_type, base64_img, error = self.doc_agent.doc_reader.get_page_image(1)
        if error:
            raise RuntimeError(f"封面图片读取失败: {error}")
        client = self._get_vision_client()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": cover_title_vision_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{base64_img}"},
                    },
                ],
            }
        ]
        response = client.chat.completions.create(
            model=self.vision_model_id,
            messages=messages,
            max_tokens=256,
            temperature=0.0,
        )
        title = (response.choices[0].message.content or "").strip()
        if not title:
            raise RuntimeError("封面题目识别失败: 视觉模型未返回题目")
        print(f"[Type Detection] 提取的题目: {title}")
        return title

    def _detect_thesis_type_by_title(self, title: str) -> tuple:
        """
        基于论文题目快速判断类型
        返回: (类型, 置信度)
        """
        if not title:
            return "unknown", 0.0

        title_lower = title.lower().replace(" ", "")

        # 程序开发类关键词
        system_keywords = [
            "系统",
            "平台",
            "管理系统",
            "网站",
            "应用",
            "app",
            "商城",
            "电商",
            "教务",
            "图书馆",
            "仓库",
            "管理",
            "springboot",
            "spring",
            "vue",
            "django",
            "flask",
            "react",
            "开发",
            "设计与实现",
            "实现",
            "bs架构",
            "cs架构",
            "unity3d",
            "unity",
            "游戏",
            "小程序",
            "微信",
            "android",
            "ios",
        ]

        # 算法类关键词
        algorithm_keywords = [
            "算法",
            "模型",
            "优化",
            "改进",
            "检测",
            "方法",
            "识别",
            "分类",
            "预测",
            "深度学习",
            "机器学习",
            "神经网络",
            "cnn",
            "lstm",
            "yolo",
            "bert",
            "transformer",
            "性能分析",
            "复杂度",
            "准确率",
            "召回率",
            "目标检测",
            "图像",
            "语音",
            "自然语言",
            "推荐算法",
            "聚类",
            "回归",
        ]

        system_score = sum(1 for kw in system_keywords if kw in title_lower)
        algorithm_score = sum(1 for kw in algorithm_keywords if kw in title_lower)

        print(
            f"[Type Detection] 程序开发类得分: {system_score}, 算法理论类得分: {algorithm_score}"
        )

        if system_score > algorithm_score:
            confidence = min(0.95, 0.6 + system_score * 0.15)
            return "system", confidence
        elif algorithm_score > system_score:
            confidence = min(0.95, 0.6 + algorithm_score * 0.15)
            return "algorithm", confidence
        else:
            return "unknown", 0.3

    def _extract_chapter_titles(self) -> List[str]:
        """提取目录中的章节标题"""
        try:
            root = self.doc_agent.doc_reader.root
            titles = []
            for section in root.findall(".//Section"):
                level = section.get("level", "")
                if level == "1":  # 只取一级标题
                    for child in section:
                        if child.tag in ["Heading", "Title"] and child.text:
                            titles.append(child.text.strip())
            return titles[:10]  # 最多取前10个章节
        except Exception as e:
            print(f"[Type Detection] 提取章节标题失败: {e}")
            return []

    def _detect_thesis_type_deep(self, title: str) -> tuple:
        """
        基于题目+摘要+目录的深度判断
        返回: (类型, 置信度)
        """
        print("[Type Detection] 启动深度分析...")

        # 提取摘要内容
        abstract_content = ""
        try:
            root = self.doc_agent.doc_reader.root
            for section in root.findall(".//Section"):
                for child in section:
                    if child.tag in ["Heading", "Title"]:
                        heading_text = (child.text or "").strip().lower()
                        if "摘要" in heading_text or "abstract" in heading_text:
                            # 提取摘要的段落内容
                            for para in section.findall(".//Text"):
                                if para.text:
                                    abstract_content += para.text + " "
                            break
                if abstract_content:
                    break
            abstract_content = abstract_content[:800]  # 限制长度
        except Exception as e:
            print(f"[Type Detection] 提取摘要失败: {e}")

        # 提取目录结构
        chapter_titles = self._extract_chapter_titles()
        chapter_titles_str = (
            "\n".join([f"- {t}" for t in chapter_titles])
            if chapter_titles
            else "（无法提取目录）"
        )

        # 使用LLM判断
        detection_prompt = f"""
你是一个论文类型分类专家。请根据论文题目、摘要和目录判断这是"程序开发类(system)"还是"算法理论类(algorithm)"论文。

【判断标准】

**程序开发类特征**：
- 题目包含：系统、平台、管理、网站、应用、开发、设计与实现
- 摘要提到：Spring Boot、Vue、MySQL、Django、Flask、Unity3D、BS架构、CS架构
- 摘要提到：用户管理、订单管理、商品管理等功能模块
- 目录包含：需求分析、系统设计、数据库设计、系统实现、系统测试

**算法理论类特征**：
- 题目包含：算法、模型、优化、检测、识别、分类、预测
- 摘要提到：深度学习、机器学习、神经网络、准确率、召回率
- 摘要提到：数据集、实验、对比、性能提升
- 目录包含：算法设计、模型构建、实验设计、性能分析

【输入信息】
题目：{title}

摘要片段：
{abstract_content if abstract_content else "（无法提取摘要）"}

目录结构：
{chapter_titles_str}

只输出JSON:
{{
    "type": "system" | "algorithm",
    "confidence": 0.0-1.0,
    "reason": "判断理由（2-3句话）",
    "evidence": ["证据1", "证据2", "证据3"]
}}
"""

        try:
            messages = [{"role": "system", "content": detection_prompt}]
            response = self.doc_agent._call_llm(
                messages, max_tokens=800, temperature=0.0
            )
            raw_response = response.choices[0].message.content
            result = self.doc_agent._parse_json(raw_response)

            thesis_type = result.get("type", "system")
            confidence = result.get("confidence", 0.5)
            reason = result.get("reason", "")
            evidence = result.get("evidence", [])

            print(f"[Type Detection] 深度判断结果: {thesis_type}")
            print(f"[Type Detection] 置信度: {confidence:.2f}")
            print(f"[Type Detection] 理由: {reason}")
            if evidence:
                print(f"[Type Detection] 证据: {', '.join(evidence)}")

            return thesis_type, confidence

        except Exception as e:
            print(f"[Type Detection] 深度判断失败: {e}")
            # 失败时默认为system（程序开发类更常见）
            return "system", 0.5

    def _detect_thesis_type(self) -> str:
        """
        自动检测论文类型（两阶段）
        返回: "system" 或 "algorithm"
        """
        print("\n" + "=" * 60)
        print("[Type Detection] 开始论文类型检测...")
        print("=" * 60)

        # 使用视觉模型从封面提取题目（失败直接报错）
        title = self._extract_title_from_cover_with_vision()

        # 阶段1: 快速判断（基于题目）
        thesis_type, confidence = self._detect_thesis_type_by_title(title)

        print(f"[Type Detection] 快速判断: {thesis_type} (置信度: {confidence:.2f})")

        # 如果置信度高，直接返回
        if confidence >= 0.7:
            print(
                f"[Type Detection] [OK] 置信度足够，确定为: {'程序开发类' if thesis_type == 'system' else '算法理论类'}"
            )
            print("=" * 60 + "\n")
            return thesis_type

        # 阶段2: 深度判断（基于题目+摘要+目录）
        print(f"[Type Detection] 置信度不足 ({confidence:.2f} < 0.7)，启动深度分析...")
        thesis_type, confidence = self._detect_thesis_type_deep(title)

        if confidence < 0.6:
            print(
                f"[Type Detection] [WARN] 置信度较低 ({confidence:.2f})，建议人工确认"
            )
            print(f"[Type Detection] 可使用 --thesis-type 参数手动指定类型")
        else:
            print(
                f"[Type Detection] [OK] 深度判断完成，确定为: {'程序开发类' if thesis_type == 'system' else '算法理论类'}"
            )

        print("=" * 60 + "\n")
        return thesis_type

    def run(self) -> Dict[str, Any]:
        res = self.run_hierarchical_logic_review()
        raw = res.get("raw", "")
        thinking = res.get("thinking", "")
        parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}

        # 处理两种可能的返回格式：{"issues": [...]} 或 [{...}, {...}]
        if isinstance(parsed, list):
            issues = parsed
            parsed = {"issues": issues}
        elif isinstance(parsed, dict):
            issues = parsed.get("issues", [])
            if not isinstance(issues, list):
                parsed["issues"] = []
        else:
            parsed = {"issues": []}

        return {
            "raw": raw,
            "parsed": parsed,
            "thinking": thinking,
            "errors": [],
        }

    def run_logic_review(self) -> Dict[str, Any]:
        """逻辑审查，不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Logic Review...")
        return self.doc_agent._run_simple_review(logic_prompt)

    def _extract_chapter_facts(self, chapter_content, chapter_info):
        """
        从章节内容中提取细粒度事实（用于跨章节冲突检测）
        """
        print(
            f"[Fact Extraction] Extracting facts from: {chapter_info.get('title', 'Unknown')}"
        )

        fact_extraction_prompt = """
你是一个精确的事实提取专家。请从以下章节内容中提取关键事实，用于后续的跨章节一致性验证。

请提取以下类型的事实：

1. **实体（Entities）**：人名、角色、机构、公司等
   - 示例：{"type": "人物角色", "key": "甲方", "value": "张三"}
   - 示例：{"type": "机构", "key": "项目单位", "value": "XX科技有限公司"}

2. **数值（Numbers）**：性能指标、实验数据、统计数字等
   - 示例：{"type": "性能指标", "key": "准确率", "value": 95.5, "unit": "%"}
   - 示例：{"type": "实验数据", "key": "样本数量", "value": 1000, "unit": "个"}
   - **重要**：不要提取参考文献中的期刊卷期号、期次、页码等引用信息（如"第52卷"、"第3期"、"pp.123-456"等）

3. **时间（Dates）**：日期、时间节点、时间段等
   - 示例：{"type": "时间节点", "key": "项目启动", "value": "2023年3月"}

4. **重要论断（Claims）**：关键结论、核心观点（限5条最重要的）
   - 示例：{"claim": "算法A在准确率上优于算法B", "type": "比较结论"}

**注意**：
- 只提取明确的事实，不要推断
- 保留原文上下文片段（用于定位）
- 如果某类事实不存在，返回空数组
- **重要**：跳过参考文献区域的卷期号、期次等引用格式信息

输出JSON格式：
{
  "entities": [
    {"type": "人物角色", "key": "甲方", "value": "张三", "context": "根据合同约定，甲方为张三"}
  ],
  "numbers": [
    {"type": "性能指标", "key": "准确率", "value": 95.5, "unit": "%", "context": "实验结果显示准确率达到95.5%"}
  ],
  "dates": [
    {"type": "时间节点", "key": "项目启动", "value": "2023年3月", "context": "项目于2023年3月正式启动"}
  ],
  "claims": [
    {"claim": "算法A优于算法B", "type": "比较结论", "context": "综合实验结果表明，算法A在各项指标上均优于算法B"}
  ]
}
"""

        content_snippet = chapter_content[:8000]

        messages = [
            {"role": "system", "content": fact_extraction_prompt},
            {
                "role": "user",
                "content": f"章节标题：{chapter_info.get('title', 'Unknown')}\n\n章节内容：\n{content_snippet}\n\n请提取关键事实。",
            },
        ]

        try:
            response = self.doc_agent._call_llm(
                messages, max_tokens=4096, temperature=0.0
            )
            raw_response = response.choices[0].message.content
            facts = self.doc_agent._parse_json(raw_response)

            # 确保返回字典格式
            if not isinstance(facts, dict):
                facts = {"entities": [], "numbers": [], "dates": [], "claims": []}

            print(
                f"[Fact Extraction] Extracted: {len(facts.get('entities', []))} entities, "
                f"{len(facts.get('numbers', []))} numbers, "
                f"{len(facts.get('dates', []))} dates, "
                f"{len(facts.get('claims', []))} claims"
            )

            return facts

        except Exception as e:
            print(f"[Fact Extraction] Failed: {e}")
            return {"entities": [], "numbers": [], "dates": [], "claims": []}

    def _store_facts(self, facts, chapter_info):
        """将提取的事实存储到 fact_store 中。"""
        chapter_label = f"{chapter_info.get('title', 'Unknown')} (第{chapter_info.get('start_page_num', '?')}页)"

        for entity in facts.get("entities", []):
            key = entity.get("key")
            if key:
                if key not in self.fact_store["entities"]:
                    self.fact_store["entities"][key] = []
                self.fact_store["entities"][key].append(
                    {
                        "value": entity.get("value"),
                        "type": entity.get("type"),
                        "context": entity.get("context", ""),
                        "source": chapter_label,
                        "page": chapter_info.get("start_page_num"),
                    }
                )

        for number in facts.get("numbers", []):
            key = number.get("key")
            if key:
                if key not in self.fact_store["numbers"]:
                    self.fact_store["numbers"][key] = []
                self.fact_store["numbers"][key].append(
                    {
                        "value": number.get("value"),
                        "unit": number.get("unit", ""),
                        "type": number.get("type"),
                        "context": number.get("context", ""),
                        "source": chapter_label,
                        "page": chapter_info.get("start_page_num"),
                    }
                )

        for date in facts.get("dates", []):
            key = date.get("key")
            if key:
                if key not in self.fact_store["dates"]:
                    self.fact_store["dates"][key] = []
                self.fact_store["dates"][key].append(
                    {
                        "value": date.get("value"),
                        "context": date.get("context", ""),
                        "source": chapter_label,
                        "page": chapter_info.get("start_page_num"),
                    }
                )

        for claim in facts.get("claims", []):
            self.fact_store["claims"].append(
                {
                    "claim": claim.get("claim"),
                    "type": claim.get("type"),
                    "context": claim.get("context", ""),
                    "source": chapter_label,
                    "page": chapter_info.get("start_page_num"),
                }
            )

    def _detect_fact_conflicts(self):
        """检测 fact_store 中的冲突。"""
        print("[Fact Conflict Detection] Analyzing cross-chapter conflicts...")
        conflicts = []

        def _verify_entity_conflict(entity_key, occurrences):
            """用 LLM 复核实体冲突，确认是否为真正矛盾。"""
            try:
                sample = []
                for occ in occurrences[:6]:
                    sample.append(
                        {
                            "value": occ.get("value"),
                            "source": occ.get("source"),
                            "context": occ.get("context", "")[:200],
                        }
                    )

                prompt = """
你是学术文本一致性审查助手。请判断以下“同一实体键”的不同表述是否构成真正冲突。

规则：
1) 如果只是“别名/简称/同一公司不同写法”或“上下文不同但可兼容”，则不算冲突。
2) 只有在明确相互矛盾（同一实体键被赋予不同且不可兼容的值）时，才算冲突。
3) 请基于上下文判断是否可兼容。

请输出严格 JSON：
{"is_conflict": true/false, "reason": "..."}
"""
                messages = [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"entity_key": entity_key, "occurrences": sample},
                            ensure_ascii=False,
                        ),
                    },
                ]
                response = self.doc_agent._call_llm(
                    messages, max_tokens=800, temperature=0.0
                )
                raw = response.choices[0].message.content
                data = self.doc_agent._parse_json(raw)
                # 确保返回字典格式
                if not isinstance(data, dict):
                    return True, ""
                if "is_conflict" in data:
                    return bool(data.get("is_conflict")), data.get("reason", "")
            except Exception as e:
                error_text = str(e)
                if "Insufficient Balance" in error_text or "402" in error_text:
                    print(
                        "[Fact Conflict Verification] Skipped (insufficient balance for verification)"
                    )
                    return False, ""
                print(f"[Fact Conflict Verification] Failed: {e}")
            return True, ""

        # 1. 检测实体冲突（如"甲方"在不同章节有不同的值）- 已关闭

        # 2. 检测数值冲突（如"准确率"在不同章节有明显差异）
        # 排除参考文献引用信息（期刊卷期、期次、页码等）
        reference_keywords = ["卷", "期", "页", "volume", "issue", "pp", "p.", "页码"]

        for metric_key, occurrences in self.fact_store["numbers"].items():
            # 检查是否为参考文献引用信息
            is_reference_info = any(
                keyword in metric_key.lower()
                or keyword in occ.get("context", "").lower()
                for keyword in reference_keywords
                for occ in occurrences
            )

            # 如果是参考文献引用信息，跳过检测
            if is_reference_info:
                print(
                    f"[Fact Conflict Detection] Skipping reference info: '{metric_key}'"
                )
                continue

            if len(occurrences) > 1:
                values = [
                    occ["value"]
                    for occ in occurrences
                    if isinstance(occ["value"], (int, float))
                ]
                if len(values) > 1:
                    max_val = max(values)
                    min_val = min(values)
                    if max_val > 0 and (max_val - min_val) / max_val > 0.05:
                        evidence_list = []
                        for occ in occurrences[:5]:
                            evidence_list.append(
                                {
                                    "source": occ.get("source"),
                                    "value": occ.get("value"),
                                    "unit": occ.get("unit", ""),
                                    "page": occ.get("page"),
                                    "context": occ.get("context", ""),
                                }
                            )

                        evidence_quote = ""
                        for ev in evidence_list:
                            if ev.get("context"):
                                evidence_quote = str(ev.get("context", "")).strip()
                                break
                        if not evidence_quote and evidence_list:
                            ev0 = evidence_list[0]
                            evidence_quote = (
                                f"{ev0.get('source', '未知来源')} "
                                f"{ev0.get('value', '')}{ev0.get('unit', '')}"
                            ).strip()

                        conflicts.append(
                            {
                                "issue_type": "逻辑性-数值冲突",
                                "severity": "High",
                                "section": "跨章节",
                                "page": evidence_list[0].get("page")
                                if evidence_list
                                else occurrences[0].get("page"),
                                # 兼容旧展示/下游：quote 仍保留，但优先使用可追溯证据
                                "quote": evidence_quote
                                or f"'{metric_key}' 在不同位置有不同的数值",
                                "diagnosis": f"'{metric_key}' 在不同位置有不同的数值",
                                "evidence_quote": evidence_quote,
                                "evidence_status": (
                                    "verifiable" if evidence_quote else "unverifiable"
                                ),
                                "evidence_list": evidence_list,
                                "suggestion": (
                                    f"'{metric_key}' 的数值在文档中不一致（范围：{min_val}-{max_val}）。"
                                    f"出现位置："
                                    + "; ".join(
                                        [
                                            f"{occ.get('source', '未知来源')}为{occ.get('value', '')}{occ.get('unit', '')}"
                                            for occ in evidence_list[:3]
                                        ]
                                    )
                                ),
                            }
                        )

        print(f"[Fact Conflict Detection] Found {len(conflicts)} conflicts")
        return conflicts

    def _xml_to_plain_text(self, xml_text: str, max_chars: int = 24000) -> str:
        if not isinstance(xml_text, str) or not xml_text.strip():
            return ""
        text = re.sub(r"<[^>]+>", " ", xml_text)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    def _is_background_section(self, title: str) -> bool:
        t = str(title or "").lower()
        return any(
            k in t
            for k in [
                "目录",
                "参考文献",
                "致谢",
                "摘要",
                "abstract",
                "研究现状",
                "国内外",
                "相关研究",
                "相关工作",
                "绪论",
                "引言",
            ]
        )

    def _is_project_scope_context(self, section_title: str, context: str) -> bool:
        ctx = str(context or "")
        title = str(section_title or "")
        strong_positive = [
            "本系统",
            "本论文",
            "系统采用",
            "系统使用",
            "前端采用",
            "后端采用",
            "采用",
            "使用",
            "基于",
            "实现",
        ]
        negative_hint = [
            "现有系统",
            "已有系统",
            "他人研究",
            "文献",
            "例如",
            "如",
            "国外",
            "国内",
            "相关研究",
            "相关工作",
        ]
        has_positive = any(k in ctx for k in strong_positive) or any(
            k in title for k in ["系统设计", "系统实现", "需求", "测试", "结论"]
        )
        has_negative = any(k in ctx for k in negative_hint) or self._is_background_section(
            section_title
        )
        return bool(has_positive and not has_negative)

    def _iter_pattern_contexts(
        self, text: str, pattern: str, flags: int = re.IGNORECASE, window: int = 44
    ):
        for m in re.finditer(pattern, text, flags):
            s = max(0, m.start() - window)
            e = min(len(text), m.end() + window)
            context = text[s:e].strip()
            yield m, context

    def _to_ms(self, value: float, unit: str) -> float:
        u = str(unit or "").lower()
        if u in {"s", "sec", "second", "seconds", "秒"}:
            return float(value) * 1000.0
        return float(value)

    def _verify_consistency_candidate_with_llm(
        self, candidate: Dict[str, Any]
    ) -> tuple[bool | None, str]:
        """
        规则触发后再做 LLM 复核，降低误报。
        返回:
          - True: 确认冲突
          - False: 复核为非冲突
          - None: 无法复核（网络/余额等），按低置信待复核处理
        """
        try:
            prompt = """
你是毕业论文一致性审查复核助手。请判断“规则触发的候选冲突”是否成立。

判定原则：
1) 仅当同一“项目范围”内存在不可兼容陈述时，判定冲突。
2) 若是背景综述/他人研究/举例，不算项目冲突。
3) 若上下文可解释为不同阶段、迁移计划或非同一对象，不算冲突。
4) 输出必须是 JSON，格式：
{"is_conflict": true/false, "reason": "..."}
"""
            payload = {
                "type": candidate.get("type"),
                "diagnosis": candidate.get("diagnosis"),
                "evidence_list": candidate.get("evidence_list", [])[:6],
            }
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            response = self.doc_agent._call_llm(messages, max_tokens=700, temperature=0.0)
            raw = response.choices[0].message.content
            data = self.doc_agent._parse_json(raw)
            if isinstance(data, dict) and "is_conflict" in data:
                return bool(data.get("is_conflict")), str(data.get("reason", "") or "")
            return None, "LLM 复核返回格式异常"
        except Exception as e:
            error_text = str(e)
            if "Insufficient Balance" in error_text or "402" in error_text:
                print("[Consistency Verification] Skipped (insufficient balance)")
                return None, "LLM 余额不足，未完成复核"
            print(f"[Consistency Verification] Failed: {e}")
            return None, f"LLM 复核失败: {e}"

    def _collect_consistency_mentions(self, chapters: List[Dict[str, Any]]) -> Dict[str, Any]:
        tech_mentions: List[Dict[str, Any]] = []
        security_mentions: List[Dict[str, Any]] = []
        perf_mentions: List[Dict[str, Any]] = []

        for chap in chapters:
            title = str(chap.get("title", "") or "")
            if not title:
                continue
            if any(x in title for x in ["目录", "参考文献", "致谢", "诚信承诺"]):
                continue
            page = chap.get("start_page_num")
            text = self._xml_to_plain_text(str(chap.get("content_xml", "") or ""))
            if not text:
                continue

            # === 技术栈版本 ===
            vue_patterns = [
                r"\bvue\s*([23])(?:\.\d+){0,2}\b",
                r"\bvue([23])\b",
            ]
            for vp in vue_patterns:
                for m, ctx in self._iter_pattern_contexts(text, vp):
                    major = m.group(1) if m.lastindex else None
                    if not major:
                        continue
                    tech_mentions.append(
                        {
                            "component": "vue",
                            "value": f"v{major}",
                            "context": ctx,
                            "source": title,
                            "page": page,
                            "project_scope": self._is_project_scope_context(title, ctx),
                        }
                    )

            for m, ctx in self._iter_pattern_contexts(text, r"element\s*ui"):
                tech_mentions.append(
                    {
                        "component": "ui_lib",
                        "value": "element-ui",
                        "context": ctx,
                        "source": title,
                        "page": page,
                        "project_scope": self._is_project_scope_context(title, ctx),
                    }
                )
            for m, ctx in self._iter_pattern_contexts(text, r"element\s*plus"):
                tech_mentions.append(
                    {
                        "component": "ui_lib",
                        "value": "element-plus",
                        "context": ctx,
                        "source": title,
                        "page": page,
                        "project_scope": self._is_project_scope_context(title, ctx),
                    }
                )

            for m, ctx in self._iter_pattern_contexts(
                text, r"spring\s*boot\s*([123])(?:\.\d+){0,2}"
            ):
                major = m.group(1)
                tech_mentions.append(
                    {
                        "component": "spring_boot",
                        "value": f"v{major}",
                        "context": ctx,
                        "source": title,
                        "page": page,
                        "project_scope": self._is_project_scope_context(title, ctx),
                    }
                )

            # === 安全方案（密码相关）===
            for m, ctx in self._iter_pattern_contexts(
                text, r"\b(bcrypt|md5|sha-?1|sha-?256|argon2|pbkdf2)\b"
            ):
                algo = m.group(1).lower().replace("-", "")
                if not any(k in ctx for k in ["密码", "口令", "加密", "哈希", "鉴权", "存储"]):
                    continue
                # 否定句不参与“采用冲突”判定
                left_ctx = ctx[: max(0, ctx.lower().find(m.group(1).lower()))]
                negated = bool(re.search(r"(不|避免|弃用|禁止|不使用|不再使用)$", left_ctx[-8:]))
                security_mentions.append(
                    {
                        "algo": algo,
                        "context": ctx,
                        "source": title,
                        "page": page,
                        "project_scope": self._is_project_scope_context(title, ctx),
                        "negated": negated,
                    }
                )

            # === 性能承诺/测试证据 ===
            is_test_chapter = any(k in title for k in ["测试", "实验", "评估"])

            for m, ctx in self._iter_pattern_contexts(
                text, r"(?:并发(?:用户)?|用户并发)\D{0,8}(\d{2,6})|(\d{2,6})\s*并发(?:用户)?"
            ):
                val = None
                if m.lastindex:
                    for gi in range(1, m.lastindex + 1):
                        if m.group(gi) and str(m.group(gi)).isdigit():
                            val = int(m.group(gi))
                            break
                if val is None:
                    continue
                mode = "test" if is_test_chapter or any(k in ctx for k in ["测试", "压测", "JMeter", "结果"]) else "promise"
                perf_mentions.append(
                    {
                        "metric": "concurrency",
                        "value": float(val),
                        "unit": "users",
                        "mode": mode,
                        "context": ctx,
                        "source": title,
                        "page": page,
                        "project_scope": self._is_project_scope_context(title, ctx),
                    }
                )

            for m, ctx in self._iter_pattern_contexts(
                text,
                r"(?:响应时间|时延|延迟|耗时)\D{0,10}([0-9]+(?:\.[0-9]+)?)\s*(ms|毫秒|s|秒)",
            ):
                val = float(m.group(1))
                unit = m.group(2)
                mode = "test" if is_test_chapter or any(k in ctx for k in ["测试", "压测", "JMeter", "结果", "平均"]) else "promise"
                perf_mentions.append(
                    {
                        "metric": "latency",
                        "value": self._to_ms(val, unit),
                        "unit": "ms",
                        "mode": mode,
                        "context": ctx,
                        "source": title,
                        "page": page,
                        "project_scope": self._is_project_scope_context(title, ctx),
                    }
                )

        return {
            "tech_mentions": tech_mentions,
            "security_mentions": security_mentions,
            "perf_mentions": perf_mentions,
        }

    def _build_consistency_issue_from_candidate(
        self, candidate: Dict[str, Any], verify_result: tuple[bool | None, str]
    ) -> Dict[str, Any] | None:
        verdict, verify_reason = verify_result
        if verdict is False:
            return None

        evidence_list = candidate.get("evidence_list", [])[:6]
        evidence_quote = ""
        for ev in evidence_list:
            if ev.get("context"):
                evidence_quote = str(ev.get("context", "")).strip()
                break
        page = evidence_list[0].get("page") if evidence_list else None

        if verdict is None:
            severity = "Low"
            evidence_status = "synthetic"
            suggestion = (
                candidate.get("suggestion", "")
                + "（规则已触发，但 LLM 复核未完成，建议人工复核后再下结论）"
            )
            diagnosis = candidate.get("diagnosis", "") + "（待复核）"
        else:
            severity = candidate.get("severity", "Medium")
            evidence_status = "verifiable" if evidence_quote else "unverifiable"
            suggestion = candidate.get("suggestion", "")
            diagnosis = candidate.get("diagnosis", "")

        if verify_reason:
            diagnosis = f"{diagnosis} 复核说明：{verify_reason}".strip()

        return {
            "issue_type": "逻辑性-跨章节一致性",
            "severity": severity,
            "section": "跨章节一致性",
            "page": page,
            "quote": evidence_quote or candidate.get("diagnosis", ""),
            "diagnosis": diagnosis,
            "evidence_quote": evidence_quote,
            "evidence_status": evidence_status,
            "evidence_mode": "rule_plus_llm",
            "consistency_category": candidate.get("type", ""),
            "evidence_list": evidence_list,
            "suggestion": suggestion,
        }

    def _detect_targeted_consistency_conflicts(
        self, chapters: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        跨章节一致性专项检查（窄而准）：
        1) 技术栈版本冲突
        2) 安全方案冲突（密码加密）
        3) 性能承诺 vs 测试证据
        """
        print("[Consistency] Running targeted cross-chapter consistency checks...")
        signals = self._collect_consistency_mentions(chapters)
        tech_mentions = [m for m in signals["tech_mentions"] if m.get("project_scope")]
        security_mentions = [
            m
            for m in signals["security_mentions"]
            if m.get("project_scope") and not m.get("negated")
        ]
        perf_mentions = [m for m in signals["perf_mentions"] if m.get("project_scope")]

        candidates: List[Dict[str, Any]] = []

        # === 1) 技术栈版本冲突 ===
        vue_vers = sorted({m["value"] for m in tech_mentions if m.get("component") == "vue"})
        if len(vue_vers) > 1:
            ev = [m for m in tech_mentions if m.get("component") == "vue"][:6]
            candidates.append(
                {
                    "type": "tech_stack_version_conflict",
                    "severity": "High",
                    "diagnosis": f"前端框架 Vue 版本前后不一致：{', '.join(vue_vers)}",
                    "suggestion": "统一 Vue 主版本并同步核对相关依赖（如路由、状态管理、UI 组件库）。",
                    "evidence_list": ev,
                }
            )

        has_vue3 = any(m.get("component") == "vue" and m.get("value") == "v3" for m in tech_mentions)
        has_vue2 = any(m.get("component") == "vue" and m.get("value") == "v2" for m in tech_mentions)
        has_element_ui = any(
            m.get("component") == "ui_lib" and m.get("value") == "element-ui"
            for m in tech_mentions
        )
        has_element_plus = any(
            m.get("component") == "ui_lib" and m.get("value") == "element-plus"
            for m in tech_mentions
        )
        if has_vue3 and has_element_ui:
            ev = [
                m
                for m in tech_mentions
                if (m.get("component") == "vue" and m.get("value") == "v3")
                or (m.get("component") == "ui_lib" and m.get("value") == "element-ui")
            ][:6]
            candidates.append(
                {
                    "type": "tech_stack_compatibility_conflict",
                    "severity": "High",
                    "diagnosis": "检测到 Vue3 与 Element UI 并存，存在技术栈兼容性风险。",
                    "suggestion": "若使用 Vue3，建议统一改为 Element Plus；若保留 Element UI，建议统一为 Vue2。",
                    "evidence_list": ev,
                }
            )
        if has_vue2 and has_element_plus:
            ev = [
                m
                for m in tech_mentions
                if (m.get("component") == "vue" and m.get("value") == "v2")
                or (m.get("component") == "ui_lib" and m.get("value") == "element-plus")
            ][:6]
            candidates.append(
                {
                    "type": "tech_stack_compatibility_conflict",
                    "severity": "High",
                    "diagnosis": "检测到 Vue2 与 Element Plus 并存，存在技术栈兼容性风险。",
                    "suggestion": "若使用 Vue2，建议统一为 Element UI；若使用 Element Plus，建议统一升级到 Vue3。",
                    "evidence_list": ev,
                }
            )

        # === 2) 安全方案冲突（密码加密）===
        algos = sorted({m["algo"] for m in security_mentions})
        if len(algos) > 1:
            ev = security_mentions[:6]
            candidates.append(
                {
                    "type": "security_scheme_conflict",
                    "severity": "High",
                    "diagnosis": f"密码安全方案前后不一致：{', '.join(algos)} 并存。",
                    "suggestion": "统一密码存储方案并在设计/实现/测试章节保持一致，避免出现多种不兼容算法描述。",
                    "evidence_list": ev,
                }
            )

        # === 3) 性能承诺 vs 测试证据 ===
        promise_conc = [m for m in perf_mentions if m["metric"] == "concurrency" and m["mode"] == "promise"]
        test_conc = [m for m in perf_mentions if m["metric"] == "concurrency" and m["mode"] == "test"]
        promise_latency = [m for m in perf_mentions if m["metric"] == "latency" and m["mode"] == "promise"]
        test_latency = [m for m in perf_mentions if m["metric"] == "latency" and m["mode"] == "test"]

        if promise_conc and not test_conc:
            candidates.append(
                {
                    "type": "performance_evidence_gap",
                    "severity": "Medium",
                    "diagnosis": "存在并发能力承诺，但未检索到可核验的并发测试证据。",
                    "suggestion": "在测试章节补充并发压测数据（并发量、成功率、平均/分位响应时间、错误率）。",
                    "evidence_list": promise_conc[:4],
                }
            )
        elif promise_conc and test_conc:
            promised = max(m["value"] for m in promise_conc)
            tested = max(m["value"] for m in test_conc)
            if tested < promised * 0.8:
                ev = sorted(promise_conc + test_conc, key=lambda x: float(x.get("value", 0)), reverse=True)[:6]
                candidates.append(
                    {
                        "type": "performance_claim_conflict",
                        "severity": "High",
                        "diagnosis": f"并发承诺（{int(promised)}）与测试证据（最高 {int(tested)}）存在明显落差。",
                        "suggestion": "统一承诺口径与测试结果；若测试覆盖不足，请补充相同场景下的压测证据。",
                        "evidence_list": ev,
                    }
                )

        if promise_latency and not test_latency:
            candidates.append(
                {
                    "type": "performance_evidence_gap",
                    "severity": "Medium",
                    "diagnosis": "存在响应时间承诺，但未检索到可核验的性能测试证据。",
                    "suggestion": "在测试章节补充响应时间测试（平均值、P95/P99、测试环境和并发条件）。",
                    "evidence_list": promise_latency[:4],
                }
            )
        elif promise_latency and test_latency:
            promise_limit = min(m["value"] for m in promise_latency)  # 越小越严格
            tested_worst = max(m["value"] for m in test_latency)
            if tested_worst > promise_limit * 1.2:
                ev = sorted(promise_latency + test_latency, key=lambda x: float(x.get("value", 0)), reverse=True)[:6]
                candidates.append(
                    {
                        "type": "performance_claim_conflict",
                        "severity": "High",
                        "diagnosis": (
                            f"响应时间承诺（≤{int(promise_limit)}ms）与测试证据（最差约 {int(tested_worst)}ms）不一致。"
                        ),
                        "suggestion": "统一性能承诺与测试结果；必要时拆分场景并分别给出指标与边界条件。",
                        "evidence_list": ev,
                    }
                )

        # === 规则触发 -> LLM 复核 ===
        issues: List[Dict[str, Any]] = []
        for candidate in candidates:
            verify_result = self._verify_consistency_candidate_with_llm(candidate)
            issue = self._build_consistency_issue_from_candidate(candidate, verify_result)
            if issue:
                issues.append(issue)

        print(f"[Consistency] Added {len(issues)} targeted consistency issues")
        return issues

    def _normalize_claim_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = text.lower().strip()
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`]", "", text)
        return text

    def _has_claim_overlap(self, core_claims, fact_claims) -> bool:
        if not isinstance(core_claims, list) or not isinstance(fact_claims, list):
            return False
        normalized_core = [self._normalize_claim_text(c) for c in core_claims if c]
        normalized_fact = [
            self._normalize_claim_text(c.get("claim", "")) for c in fact_claims if c
        ]
        normalized_core = [c for c in normalized_core if c]
        normalized_fact = [c for c in normalized_fact if c]
        if not normalized_core or not normalized_fact:
            return False
        for core in normalized_core:
            for fact in normalized_fact:
                if core in fact or fact in core:
                    return True
        return False

    def _is_logic_skeleton_stable(self, logic_skeleton, stability_check) -> bool:
        if not isinstance(logic_skeleton, dict):
            return False
        chapter_role = logic_skeleton.get("chapter_role")
        core_claims = logic_skeleton.get("core_claims")
        if (
            not chapter_role
            or not isinstance(core_claims, list)
            or len(core_claims) < 1
        ):
            return False
        if isinstance(stability_check, dict):
            if stability_check.get("is_stable") is False:
                return False
        return True

    def _build_global_context(self, mem_list):
        global_context = ""
        for i, res in enumerate(mem_list):
            logic_skeleton = res.get("logic_skeleton") or {}
            core_claims = logic_skeleton.get("core_claims") or []
            if not core_claims:
                continue
            chapter_role = logic_skeleton.get("chapter_role") or "UNKNOWN"
            base_weight = self.ROLE_WEIGHTS.get(chapter_role, 0.6)
            confidence = res.get("confidence", "HIGH")
            weight = base_weight * (0.5 if confidence == "LOW" else 1.0)
            global_context += f"【章节 {i+1}】{res.get('title','未知章节')}\n"
            global_context += f"【章节角色】{chapter_role}\n"
            global_context += f"【权重】{weight}\n"
            global_context += "【核心论断】\n"
            for claim in core_claims:
                global_context += f"- {claim}\n"
            dependencies = logic_skeleton.get("dependencies") or []
            outputs = logic_skeleton.get("outputs") or []
            if dependencies:
                global_context += "【依赖】\n"
                for dep in dependencies:
                    global_context += f"- {dep}\n"
            if outputs:
                global_context += "【产出】\n"
                for out in outputs:
                    global_context += f"- {out}\n"
            global_context += f"【稳定性】{confidence}\n\n"
        return global_context

    def _get_outermost_section_ids_with_merge(self) -> List[tuple]:
        """
        获取真实章节的顶层Section ID，并收集需要合并的"伪章节"
        返回: [(真实章节ID, [需要合并的伪章节ID列表]), ...]

        规则：
        - 真实章节：标题以数字开头（如「7 系统实现」「1 绪论」）或是特殊章节（摘要、目录等）
        - 伪章节：不以数字开头的 section（如「DESIGN」「需求分析」无编号时），归入前面最近的真实章节
        """
        abstract_start_index = None

        # 第一遍：找到摘要的位置
        for idx, child in enumerate(self.doc_agent.doc_reader.root):
            if child.tag != "Section":
                continue

            title_text = None
            for node in child:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text.strip()
                    break

            if title_text:
                normalized = title_text.lower().replace(" ", "")
                if any(key in normalized for key in ["摘要", "abstract"]):
                    abstract_start_index = idx
                    break

        # 特殊章节：这些章节无论是否包含数字都要保留
        SPECIAL_TITLES = [
            "摘要",
            "abstract",
            "目录",
            "目 录",
            "参考文献",
            "致谢",
            "references",
            "acknowledgement",
            "acknowledgements",
        ]

        # 第二遍：收集真实章节和伪章节
        chapter_list = []  # [(section_id, title, is_real_chapter, idx)]

        for idx, child in enumerate(self.doc_agent.doc_reader.root):
            if child.tag != "Section" or not child.get("section_id"):
                continue

            # 跳过摘要之前的Section
            if abstract_start_index is not None and idx < abstract_start_index:
                continue

            # 检查 level 属性
            level = child.get("level")
            if level != "1":
                continue

            # 检查是否包含真实标题
            has_heading = False
            title_text = None
            for node in child:
                if node.tag in ["Heading", "Title"] and node.text:
                    title_text = node.text.strip()
                    has_heading = True
                    break

            # 必须有Heading才算章节
            if not has_heading:
                continue

            if title_text:
                normalized_title = title_text.lower().replace(" ", "")

                # 检查是否是特殊章节
                is_special = any(
                    special in normalized_title for special in SPECIAL_TITLES
                )

                # 检查标题是否以数字开头（如「第7章」「7 系统实现」「1 绪论」），非数字开头不单独成章
                stripped_title = title_text.strip()
                starts_with_number = bool(
                    re.match(
                        r"^\s*([\d一二三四五六七八九十]+|第\s*[\d一二三四五六七八九十]+)",
                        stripped_title,
                    )
                )

                # 检查是否是多级编号（如1.1、4.2、3.1.2等）
                # 多级编号视为子章节，不是真实的顶层章节，归入上一级
                is_multilevel = bool(re.match(r"^\d+\.\d+", stripped_title))

                # 判断是否是真实的顶层章节（单独切片）
                # 规则：特殊章节 或 (以数字开头 且 不是多级编号)
                is_real_chapter = is_special or (
                    starts_with_number and not is_multilevel
                )

                chapter_list.append(
                    {
                        "section_id": child.get("section_id"),
                        "title": title_text,
                        "is_real": is_real_chapter,
                        "idx": idx,
                        "is_multilevel": is_multilevel,
                    }
                )

        # 第三遍：将伪章节合并到前面的真实章节
        result = []
        current_real_chapter = None
        merge_list = []

        for item in chapter_list:
            if item["is_real"]:
                # 遇到真实章节，先保存之前的合并结果
                if current_real_chapter is not None:
                    result.append(
                        (current_real_chapter["section_id"], merge_list.copy())
                    )
                    print(f"[Logic] 真实章节: {current_real_chapter['title']}")
                    if merge_list:
                        for mid in merge_list:
                            merged_item = next(
                                c for c in chapter_list if c["section_id"] == mid
                            )
                            merge_type = (
                                "子章节(多级编号)"
                                if merged_item.get("is_multilevel", False)
                                else "伪章节(无编号)"
                            )
                            print(
                                f"[Logic]   ├─ 合并{merge_type}: {merged_item['title']}"
                            )

                # 开始新的真实章节
                current_real_chapter = item
                merge_list = []
            else:
                # 伪章节或子章节，加入待合并列表
                if current_real_chapter is not None:
                    merge_list.append(item["section_id"])
                else:
                    # 如果还没有遇到真实章节，忽略这个章节
                    chapter_type = (
                        "子章节" if item.get("is_multilevel", False) else "伪章节"
                    )
                    print(
                        f"[Logic] 忽略孤立{chapter_type}: {item['title']} (前面没有真实章节可合并)"
                    )

        # 保存最后一个真实章节
        if current_real_chapter is not None:
            result.append((current_real_chapter["section_id"], merge_list.copy()))
            print(f"[Logic] 真实章节: {current_real_chapter['title']}")
            if merge_list:
                for mid in merge_list:
                    merged_item = next(
                        c for c in chapter_list if c["section_id"] == mid
                    )
                    merge_type = (
                        "子章节(多级编号)"
                        if merged_item.get("is_multilevel", False)
                        else "伪章节(无编号)"
                    )
                    print(f"[Logic]   ├─ 合并{merge_type}: {merged_item['title']}")

        return result

    def _get_toc_final_suggestion(
        self, current_outline: List[str], toc_issues: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """根据当前目录与目录问题，调用 LLM 生成总建议与修改后的推荐目录。"""
        if not current_outline:
            return {"summary": "", "suggested_outline": []}
        issues_text = "无"
        if toc_issues:
            issues_text = "\n".join(
                f"- [{i.get('severity', '')}] {i.get('suggestion', i.get('quote', ''))}"
                for i in toc_issues
            )
        outline_text = "\n".join(current_outline)
        rule_hints = self._retrieve_rule_hints_for_toc(current_outline, toc_issues)
        user_content = f"""当前目录：
{outline_text}

已发现的目录问题：
{issues_text}

规则库命中条款（请严格遵循）：
{rule_hints}

请按 prompt 要求输出 JSON（仅含 summary 与 suggested_outline）。"""
        messages = [{"role": "system", "content": toc_final_suggestion_prompt}]

        # 受控生成：失败反馈重试，降低遗漏关键小节的概率
        feedback = ""
        latest_data: Dict[str, Any] = {"summary": "", "suggested_outline": []}
        for _ in range(2):
            try:
                response = self.doc_agent._call_llm(
                    messages
                    + [
                        {
                            "role": "user",
                            "content": user_content
                            + (f"\n\n上次输出问题（请修复后重答）：\n{feedback}" if feedback else ""),
                        }
                    ],
                    max_tokens=2048,
                    temperature=0.0,
                )
                raw = response.choices[0].message.content or ""
                data = self.doc_agent._parse_json(raw)
                if not isinstance(data, dict):
                    feedback = "输出不是合法 JSON 对象。"
                    continue
                latest_data = {
                    "summary": data.get("summary", ""),
                    "suggested_outline": data.get("suggested_outline") or [],
                }
                ok, problems = self._validate_toc_suggested_outline(
                    latest_data.get("suggested_outline") or []
                )
                if ok:
                    return latest_data
                feedback = "；".join(problems)
            except Exception as e:
                print(f"[Logic] 目录总结建议生成失败: {e}")
                feedback = f"调用失败：{e}"

        # 最后兜底：程序化补齐关键目录规范
        repaired_outline = self._repair_toc_outline(
            latest_data.get("suggested_outline") or current_outline
        )
        return {
            "summary": latest_data.get("summary", ""),
            "suggested_outline": repaired_outline,
        }

    def _load_system_rules(self) -> Dict[str, Any]:
        """从 rules/*.docx 提取开发类论文目录规则。"""
        cache = getattr(self, "_system_rules_cache", None)
        if isinstance(cache, dict):
            return cache

        rules: Dict[str, Any] = {
            "source": "",
            "raw_lines": [],
            "top_required": [1, 2, 3, 4, 5, 6, 7],
            "required_subsections": set(),
            "optional_subsections": set(),
            "chapter_titles": {},
        }

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rules_dir = os.path.join(base_dir, "rules")
        docx_candidates: List[str] = []
        if os.path.isdir(rules_dir):
            for name in os.listdir(rules_dir):
                if name.lower().endswith(".docx"):
                    docx_candidates.append(os.path.join(rules_dir, name))
        if not docx_candidates:
            self._system_rules_cache = rules
            return rules

        preferred = [
            p
            for p in docx_candidates
            if "开发类论文目录结构" in os.path.basename(p)
        ]
        docx_path = preferred[0] if preferred else docx_candidates[0]
        rules["source"] = docx_path

        try:
            with zipfile.ZipFile(docx_path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
            text = re.sub(r"<[^>]+>", "\n", xml)
            text = re.sub(r"\n+", "\n", text)
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            rules["raw_lines"] = lines

            chapter_titles: Dict[int, str] = {}
            required_subsections = set()
            optional_subsections = set()

            for line in lines:
                m_top = re.match(r"^第\s*([一二三四五六七])\s*章\s*(.+)$", line)
                if m_top:
                    num_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
                    n = num_map.get(m_top.group(1))
                    if n:
                        chapter_titles[n] = m_top.group(2).strip()
                    continue

                m_sub = re.match(r"^(\d+\.\d+)\s*(.+)$", line)
                if m_sub:
                    num = m_sub.group(1)
                    if "可选" in line:
                        optional_subsections.add(num)
                    else:
                        required_subsections.add(num)

            if chapter_titles:
                rules["chapter_titles"] = chapter_titles
            if required_subsections:
                rules["required_subsections"] = required_subsections
            if optional_subsections:
                rules["optional_subsections"] = optional_subsections
        except Exception as e:
            print(f"[Logic] 规则文档解析失败，回退默认规则: {e}")

        self._system_rules_cache = rules
        return rules

    def _retrieve_rule_hints_for_toc(
        self, current_outline: List[str], toc_issues: List[Dict[str, Any]]
    ) -> str:
        """RAG-lite：从规则文档中按关键词召回与当前目录最相关的条款。"""
        if self.thesis_type != "system":
            return "（非程序开发类论文，不启用开发类目录规则库）"

        rules = self._load_system_rules()
        lines = rules.get("raw_lines") or []
        if not lines:
            return "（未找到规则文档，使用内置目录规范）"

        query_text = "\n".join(current_outline) + "\n" + "\n".join(
            str(i.get("suggestion", "")) for i in toc_issues
        )
        tokens = [t for t in ["绪论", "需求", "设计", "实现", "测试", "总结", "展望", "1.1", "1.2", "1.3", "1.4", "6.3", "数据库"] if t in query_text]
        if not tokens:
            tokens = ["绪论", "需求", "设计", "实现", "测试"]

        scored = []
        for line in lines:
            score = sum(1 for t in tokens if t in line)
            if score > 0:
                scored.append((score, line))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [x[1] for x in scored[:12]]
        if not top:
            top = lines[:8]
        return "\n".join(f"- {x}" for x in top)

    def _validate_toc_suggested_outline(self, outline: List[str]) -> tuple[bool, List[str]]:
        """校验推荐目录是否满足最基本结构约束。"""
        if not outline:
            return False, ["suggested_outline 为空"]

        lines = [str(x).strip() for x in outline if str(x).strip()]
        top_nums = set()
        sub_nums = set()
        has_reference = any("参考文献" in x for x in lines)
        has_ack = any("致谢" in x for x in lines)

        for line in lines:
            m = re.match(r"^(\d+(?:\.\d+)*)\s+", line)
            if not m:
                continue
            num = m.group(1)
            if "." in num:
                sub_nums.add(num)
            else:
                top_nums.add(int(num))

        problems: List[str] = []
        # 程序开发类：1~7章与关键小节是强约束
        if self.thesis_type == "system":
            rules = self._load_system_rules()
            top_required = rules.get("top_required") or [1, 2, 3, 4, 5, 6, 7]
            required_subsections = set(rules.get("required_subsections") or [])
            optional_subsections = set(rules.get("optional_subsections") or [])

            for n in top_required:
                if n not in top_nums:
                    problems.append(f"缺少第{n}章")

            # 关键章节不应仅有大标题
            for n in [2, 3, 4, 5, 6, 7]:
                if n in top_nums and not any(s.startswith(f"{n}.") for s in sub_nums):
                    problems.append(f"第{n}章缺少二级小节")

            # 开发类目录模板硬约束（来源：rules/*.docx）
            for num in sorted(required_subsections):
                if num not in sub_nums:
                    problems.append(f"缺少必备小节 {num}")

            for num in sorted(optional_subsections):
                if num not in sub_nums:
                    problems.append(f"建议补充小节 {num}")

        if not has_reference:
            problems.append("缺少参考文献")
        if not has_ack:
            problems.append("缺少致谢")

        # “建议补充”不作为硬失败
        hard_problems = [p for p in problems if not p.startswith("建议补充")]
        return len(hard_problems) == 0, problems

    def _repair_toc_outline(self, outline: List[str]) -> List[str]:
        """程序化兜底：补齐关键章节，尽量少改原输出。"""
        lines = [str(x).strip() for x in outline if str(x).strip()]
        if not lines:
            lines = []

        def _has_prefix(prefix: str) -> bool:
            return any(x.startswith(prefix + " ") or x.startswith(prefix + ".") for x in lines)

        def _ensure_line(target: str):
            if target not in lines:
                lines.append(target)

        if self.thesis_type == "system":
            rules = self._load_system_rules()
            # 程序开发类兜底：确保 1~7 一级章存在
            top_titles = rules.get("chapter_titles") or {}
            top_defaults = {
                1: f"1 {top_titles.get(1, '绪论')}",
                2: f"2 {top_titles.get(2, '关键技术与工具')}",
                3: f"3 {top_titles.get(3, '系统需求分析')}",
                4: f"4 {top_titles.get(4, '系统设计')}",
                5: f"5 {top_titles.get(5, '系统实现')}",
                6: f"6 {top_titles.get(6, '系统测试')}",
                7: f"7 {top_titles.get(7, '总结与展望')}",
            }
            for n in (rules.get("top_required") or [1, 2, 3, 4, 5, 6, 7]):
                if not _has_prefix(str(n)):
                    _ensure_line(top_defaults[n])

            # 按规则补齐必备/可选小节。标题优先沿用规则原文，否则用通用占位。
            subsection_title_defaults = {
                "1.1": "选题背景及意义",
                "1.2": "国内外研究现状",
                "1.3": "主要研究内容",
                "1.4": "论文组织结构",
                "2.1": "关键技术与工具概述",
                "3.1": "可行性分析",
                "3.2": "功能需求分析",
                "3.3": "非功能需求分析",
                "3.4": "业务流程与数据流程分析",
                "4.1": "系统架构设计",
                "4.2": "功能模块设计",
                "4.3": "系统核心业务流程设计",
                "4.4": "数据库设计",
                "4.5": "接口设计",
                "5.1": "开发环境",
                "5.2": "系统功能实现",
                "6.1": "测试目的与环境",
                "6.2": "系统功能测试",
                "6.3": "系统性能测试",
                "6.4": "测试结果",
                "7.1": "总结",
                "7.2": "展望",
            }
            all_subs = sorted(
                set(rules.get("required_subsections") or [])
                | set(rules.get("optional_subsections") or [])
            )
            for num in all_subs:
                if not _has_prefix(num):
                    title = subsection_title_defaults.get(num, "小节")
                    _ensure_line(f"{num} {title}")

        _ensure_line("参考文献")
        _ensure_line("致谢")

        # 按层级与编号排序，非编号项放在末尾保持稳定
        def _sort_key(line: str):
            m = re.match(r"^(\d+(?:\.\d+)*)\s+", line)
            if not m:
                tail_order = 0
                if "参考文献" in line:
                    tail_order = 1
                elif "致谢" in line:
                    tail_order = 2
                elif "附录" in line:
                    tail_order = 3
                return (999, tail_order, line)
            nums = [int(x) for x in m.group(1).split(".")]
            return (0, nums, line)

        # 先去重
        dedup = []
        seen = set()
        for x in lines:
            if x not in seen:
                seen.add(x)
                dedup.append(x)
        lines = dedup

        numbered = [x for x in lines if re.match(r"^\d+(?:\.\d+)*\s+", x)]
        tails = [x for x in lines if x not in numbered]
        numbered.sort(key=_sort_key)
        tails.sort(key=_sort_key)
        return numbered + tails

    def _cn_num_to_int(self, s: str) -> int | None:
        if not s:
            return None
        s = s.strip()
        if s.isdigit():
            return int(s)
        mapping = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        if s in mapping:
            return mapping[s]
        if s.startswith("十") and len(s) == 2 and s[1] in mapping:
            return 10 + mapping[s[1]]
        if s.endswith("十") and len(s) == 2 and s[0] in mapping:
            return mapping[s[0]] * 10
        return None

    def normalize_toc_entries(self, section_xml: str) -> List[Dict[str, Any]]:
        """清洗目录 XML 并拆分为结构化条目。"""
        entries: List[Dict[str, Any]] = []
        if not section_xml:
            return entries
        try:
            root = ET.fromstring(section_xml)
        except Exception:
            return entries

        raw_lines: List[str] = []
        for node in root.iter():
            if node.tag in {"Heading", "Title", "Paragraph"} and node.text:
                txt = node.text.strip()
                if txt:
                    raw_lines.extend([x.strip() for x in txt.splitlines() if x.strip()])

        for line in raw_lines:
            if "杭州电子科技大学继续教育学院" in line:
                continue
            if re.fullmatch(r"\d+", line):
                continue
            cleaned = re.sub(r"[.\u00b7•…]{2,}", " ", line)
            cleaned = cleaned.replace("：", " ").replace(":", " ")
            cleaned = re.sub(r"\s+\d+\s*$", "", cleaned)
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            if not cleaned:
                continue

            m_sub = re.match(r"^\s*(\d+(?:\.\d+)+)\s+(.+)$", cleaned)
            if m_sub:
                entries.append(
                    {
                        "raw": line,
                        "normalized": cleaned,
                        "level": m_sub.group(1).count(".") + 1,
                        "chapter_num": int(m_sub.group(1).split(".")[0]),
                        "number_text": m_sub.group(1),
                        "title": m_sub.group(2).strip(),
                    }
                )
                continue

            m_top = re.match(
                r"^\s*(?:第\s*)?([0-9一二三四五六七八九十]{1,2})\s*(?:章)?(?:[、.\s]+)(.+)$",
                cleaned,
            )
            if m_top:
                chapter_num = self._cn_num_to_int(m_top.group(1))
                if chapter_num is not None:
                    entries.append(
                        {
                            "raw": line,
                            "normalized": cleaned,
                            "level": 1,
                            "chapter_num": chapter_num,
                            "number_text": str(chapter_num),
                            "title": m_top.group(2).strip(),
                        }
                    )

        return entries

    def _extract_clean_toc_lines(self, section_xml: str) -> List[str]:
        """目录清洗：去页眉页脚/纯页码/点线噪声，输出给 LLM 的目录文本行。"""
        cleaned_lines: List[str] = []
        if not section_xml:
            return cleaned_lines
        try:
            root = ET.fromstring(section_xml)
        except Exception:
            return cleaned_lines

        for node in root.iter():
            if node.tag not in {"Heading", "Title", "Paragraph"}:
                continue
            if not node.text:
                continue
            text = node.text.strip()
            if not text:
                continue
            for raw_line in [x.strip() for x in text.splitlines() if x.strip()]:
                if not raw_line:
                    continue
                # 目录专用页眉页脚过滤（避免误用全局规则误删正文目录项）
                if re.search(
                    r"(杭州电子科技大学继续教育学院|本科毕业论文|第\s*\d+\s*页|^\s*-\s*\d+\s*-\s*$)",
                    raw_line,
                ):
                    continue
                if re.fullmatch(r"\d+", raw_line):
                    continue
                # 清理目录引导点、尾部页码、奇异符号，保留主体文字
                line = re.sub(r"[.\u00b7•…]{2,}", " ", raw_line)
                line = line.replace("：", " ").replace(":", " ")
                line = re.sub(r"\s+\d+\s*$", "", line)
                line = re.sub(r"\s{2,}", " ", line).strip()
                if not line:
                    continue
                cleaned_lines.append(line)

        return cleaned_lines

    def toc_rule_check(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """基于结构化目录条目做规则校验，输出 exists/missing。"""
        required = [1, 2, 3, 4, 5, 6, 7]
        top_level_present = sorted(
            {
                int(e["chapter_num"])
                for e in entries
                if e.get("level") == 1 and e.get("chapter_num") is not None
            }
        )
        exists = {str(n): (n in top_level_present) for n in required}
        missing = [str(n) for n in required if n not in top_level_present]
        return {
            "exists": exists,
            "missing": missing,
            "present": [str(n) for n in top_level_present],
        }

    def _extract_missing_chapter_num_from_issue(self, issue: Dict[str, Any]) -> int | None:
        text = " ".join(
            [
                str(issue.get("quote", "")),
                str(issue.get("suggestion", "")),
                str(issue.get("section", "")),
            ]
        )
        m = re.search(
            r"(?:缺少|缺失|未见|没有)\s*第?\s*([0-9一二三四五六七八九十]{1,2})\s*章?",
            text,
        )
        if not m:
            return None
        return self._cn_num_to_int(m.group(1))

    def _merge_toc_rule_result(self, map_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        exists_agg: Dict[str, bool] = {}
        present = set()
        for res in map_results:
            rule = res.get("toc_rule_result")
            if not isinstance(rule, dict):
                continue
            exists = rule.get("exists") or {}
            if isinstance(exists, dict):
                for k, v in exists.items():
                    exists_agg[str(k)] = bool(exists_agg.get(str(k), False) or bool(v))
            for p in rule.get("present") or []:
                present.add(str(p))
        missing = [k for k, v in exists_agg.items() if not v]
        return {"exists": exists_agg, "present": sorted(present), "missing": sorted(missing)}

    def _apply_toc_conflict_downgrade(
        self, toc_issues: List[Dict[str, Any]], toc_rule_result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """exists 与缺章判断冲突时，执行冲突降级（High/Medium -> Low）。"""
        exists = toc_rule_result.get("exists") or {}
        if not isinstance(exists, dict):
            return toc_issues

        downgraded = 0
        output: List[Dict[str, Any]] = []
        for issue in toc_issues:
            chapter_num = self._extract_missing_chapter_num_from_issue(issue)
            if chapter_num is None:
                output.append(issue)
                continue
            if exists.get(str(chapter_num), False):
                new_issue = dict(issue)
                new_issue["severity"] = "Low"
                new_issue["issue_type"] = "目录结构"
                original_quote = str(new_issue.get("quote", "")).strip()
                original_evidence_quote = str(new_issue.get("evidence_quote", "")).strip()
                base_quote = str(new_issue.get("quote", "")).strip()
                new_issue["quote"] = (
                    f"[规则复核] 目录条目中检测到第{chapter_num}章。"
                    + (f" 原始描述：{base_quote}" if base_quote else "")
                )
                # 保留可追溯证据：目录规则复核属于结构证据，不把规则说明当作原文证据。
                new_issue["evidence_quote"] = original_evidence_quote or original_quote
                new_issue["evidence_status"] = (
                    "verifiable" if new_issue["evidence_quote"] else "synthetic"
                )
                new_issue["evidence_mode"] = "structural_rule"
                new_issue["suggestion"] = (
                    f"规则校验显示第{chapter_num}章已存在，当前缺章结论可能受 OCR/目录排版噪声影响。"
                    "建议人工复核目录页后再决定是否修改目录结构。"
                )
                output.append(new_issue)
                downgraded += 1
            else:
                output.append(issue)
        if downgraded:
            print(f"[Logic] 目录冲突降级：{downgraded} 条")
        return output


    def run_hierarchical_logic_review(self) -> Dict[str, Any]:
        """
        层次化逻辑审查 (Map-Reduce)。
        """
        print("[Agent] Starting Hierarchical Logic Review...")
        self.logic_memory = []
        self.fact_store = {"entities": {}, "numbers": {}, "dates": {}, "claims": []}
        print("[Fact Store] Initialized for cross-chapter conflict detection")

        top_sections_with_merge = self._get_outermost_section_ids_with_merge()
        print(f"[Logic] Selected {len(top_sections_with_merge)} real chapters")

        chapters = []

        if not top_sections_with_merge:
            print("[Logic] No outermost sections found, fallback to LLM selection.")
            # 回退到旧逻辑
            use_ids = self.doc_agent.select_top_sections(
                max_sections=8, skip_front_matter=True
            )
            for sid in use_ids:
                try:
                    sec_root = self.doc_agent.doc_reader.get_section_content(sid)
                except Exception:
                    continue
                title_text = f"Section {sid}"
                for node in sec_root:
                    if node.tag in ["Heading", "Title"] and node.text:
                        title_text = node.text
                        break
                filtered_root = self.doc_agent._filter_header_footer_from_section(
                    sec_root
                )
                content_xml = ET.tostring(
                    filtered_root, encoding="unicode", method="xml"
                )
                chapters.append(
                    {
                        "section_id": sid,
                        "title": title_text,
                        "content_xml": content_xml,
                        "start_page_num": sec_root.get("start_page_num"),
                    }
                )
        else:
            # 使用新逻辑：合并伪章节到真实章节
            for main_sid, merge_sids in top_sections_with_merge:
                try:
                    # 获取主章节内容
                    main_sec_root = self.doc_agent.doc_reader.get_section_content(
                        main_sid
                    )

                    # 获取标题
                    title_text = f"Section {main_sid}"
                    for node in main_sec_root:
                        if node.tag in ["Heading", "Title"] and node.text:
                            title_text = node.text
                            break

                    # 过滤页眉页脚
                    filtered_main = self.doc_agent._filter_header_footer_from_section(
                        main_sec_root
                    )

                    # 如果有需要合并的伪章节，追加到主章节内容中
                    if merge_sids:
                        for merge_sid in merge_sids:
                            try:
                                merge_sec_root = (
                                    self.doc_agent.doc_reader.get_section_content(
                                        merge_sid
                                    )
                                )
                                filtered_merge = (
                                    self.doc_agent._filter_header_footer_from_section(
                                        merge_sec_root
                                    )
                                )

                                # 将伪章节的内容追加到主章节
                                for child in filtered_merge:
                                    filtered_main.append(child)
                            except Exception as e:
                                print(
                                    f"[Logic] Failed to merge section {merge_sid}: {e}"
                                )

                    # 转换为XML字符串
                    content_xml = ET.tostring(
                        filtered_main, encoding="unicode", method="xml"
                    )

                    chapters.append(
                        {
                            "section_id": main_sid,
                            "title": title_text,
                            "content_xml": content_xml,
                            "start_page_num": main_sec_root.get("start_page_num"),
                        }
                    )
                except Exception as e:
                    print(f"[Logic] Failed to process section {main_sid}: {e}")
                    continue

        if not chapters:
            print("[Logic] No chapters found, aborting logic review.")
            return {
                "raw": json.dumps({"issues": []}),
                "thinking": "[Error] No chapters to review",
            }

        print(f"[Logic] Will review {len(chapters)} selected chapters.")
        map_results = []
        full_thinking_log = "=== 分章节审查 (Local Review) ===\n"

        for i, chap in enumerate(chapters):
            print(f"[Logic] Reviewing Chapter {i+1}: {chap['title']}")
            content_snippet = chap["content_xml"][:12000]

            user_content = (
                f"章节标题：{chap['title']}\n"
                f"章节XML内容：\n{content_snippet}\n\n"
                "请按约定输出 JSON，必须包含 local_summary、logic_skeleton、stability_check。"
                "注意直接使用 XML 节点中的 page_num，如果节点没有 page_num 再用章节 start_page_num 兜底，不要猜测页码。"
            )

            toc_rule_result = None
            is_toc_chapter = False
            try:

                def _run_local_review(system_prompt):
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ]
                    response = self.doc_agent._call_llm(
                        messages, max_tokens=8192, temperature=0.0
                    )
                    raw = response.choices[0].message.content
                    data = self.doc_agent._parse_json(raw)
                    thinking_match = re.search(
                        r"<thinking>(.*?)</thinking>", raw, re.DOTALL
                    )
                    thinking = thinking_match.group(1).strip() if thinking_match else ""
                    return raw, data, thinking

                # 根据论文类型和章节内容选择合适的prompt
                chapter_title_lower = chap["title"].lower()
                selected_prompt = local_chapter_review_prompt  # 默认使用通用prompt
                is_toc_chapter = False

                # 检查是否是目录章节
                if (
                    "目录" in chap["title"]
                    or "contents" in chapter_title_lower
                    or "目 录" in chap["title"]
                ):
                    # 开发类论文：使用开发类论文目录检测正式 prompt；否则使用通用目录 prompt
                    if self.thesis_type == "system":
                        selected_prompt = system_development_structure_check_prompt
                        print(f"[Logic] 使用开发类论文目录检测正式prompt")
                    else:
                        selected_prompt = table_of_contents_check_prompt
                        print(f"[Logic] 使用通用目录结构审查prompt")
                    is_toc_chapter = True

                    toc_xml_for_review = chap["content_xml"]
                    try:
                        if chap.get("section_id"):
                            # 目录章节使用原始 section（保留完整目录文本），避免通用过滤误删目录行
                            toc_root = self.doc_agent.doc_reader.get_section_content(
                                chap["section_id"]
                            )
                            toc_xml_for_review = ET.tostring(
                                toc_root, encoding="unicode", method="xml"
                            )
                    except Exception as e:
                        print(f"[Logic] 读取目录原始section失败，回退已过滤内容: {e}")

                    normalized_entries = self.normalize_toc_entries(toc_xml_for_review)
                    toc_clean_lines = self._extract_clean_toc_lines(toc_xml_for_review)
                    toc_clean_text = "\n".join(toc_clean_lines)
                    toc_rule_result = self.toc_rule_check(normalized_entries)
                    structured_lines = [
                        f"{e.get('number_text', '')} {e.get('title', '')}".strip()
                        for e in normalized_entries
                    ]

                    user_content = (
                        f"章节标题：{chap['title']}\n"
                        "目录清洗文本（已过滤页眉页脚与页码噪声）：\n"
                        + (toc_clean_text if toc_clean_text else "（无可用目录文本）")
                        + "\n\n"
                        f"章节XML内容（原始复核用）：\n{toc_xml_for_review}\n\n"
                        "目录结构化条目（normalize_toc_entries）：\n"
                        + (
                            "\n".join(structured_lines)
                            if structured_lines
                            else "（无可解析条目）"
                        )
                        + "\n\n"
                        "规则校验结果（toc_rule_check, exists/missing）：\n"
                        + json.dumps(toc_rule_result, ensure_ascii=False)
                        + "\n\n"
                        "请先基于目录清洗文本判断目录结构，再用原始 XML 与结构化条目交叉复核。"
                        "若判定“缺少某章”，必须引用清洗文本中的直接证据；若证据不足请不要下缺章结论。"
                    )

                # 如果是程序开发类论文，针对特定章节使用专用prompt
                elif self.thesis_type == "system":
                    if "摘要" in chap["title"] or "abstract" in chapter_title_lower:
                        # 使用程序开发类摘要审查prompt
                        selected_prompt = system_development_abstract_check_prompt
                        print(f"[Logic] 使用程序开发类摘要审查prompt")
                    elif any(
                        keyword in chap["title"]
                        for keyword in ["需求", "设计", "实现", "测试"]
                    ):
                        # 对于需求分析、系统设计、系统实现、系统测试章节，追加结构检查提示
                        selected_prompt = (
                            local_chapter_review_prompt
                            + "\n\n"
                            + system_development_chapter_hint
                        )
                        print(f"[Logic] 使用程序开发类增强审查prompt")

                raw_res, data, thinking = _run_local_review(selected_prompt)

                print(f"[Logic Debug] Chapter {i+1} Raw Response (first 500 chars):")
                print(raw_res[:500])
                print("---")
                if not isinstance(data, dict):
                    data = {}

                logic_skeleton = data.get("logic_skeleton") or {}
                stability_check = data.get("stability_check") or {}

                # 目录章节单独处理：不触发 retry，保留目录结构问题
                if not is_toc_chapter and not self._is_logic_skeleton_stable(
                    logic_skeleton, stability_check
                ):
                    print(
                        f"[Logic WARNING] Chapter {i+1} logic_skeleton unstable, retrying once"
                    )
                    raw_res, data, thinking = _run_local_review(
                        f"{local_chapter_review_prompt}\n\n{local_chapter_review_retry_prompt}"
                    )
                    if not isinstance(data, dict):
                        data = {}
                    logic_skeleton = data.get("logic_skeleton") or {}
                    stability_check = data.get("stability_check") or {}

                low_confidence = not self._is_logic_skeleton_stable(
                    logic_skeleton, stability_check
                )
                if low_confidence:
                    logic_skeleton["confidence"] = "LOW"

                full_thinking_log += (
                    f"\n#### Chapter {i+1}: {chap['title']}\n{thinking}\n"
                )

                local_summary = (
                    data.get("local_summary") if isinstance(data, dict) else ""
                )
                if not local_summary or local_summary == "None":
                    if is_toc_chapter:
                        local_summary = "[目录章节] 已进行目录结构审查"
                    else:
                        local_summary = f"[摘要解析失败] 第{i+1}章《{chap['title']}》的摘要未能正确生成，可能因为模型输出不完整或JSON格式错误。"
                    print(
                        f"[Logic WARNING] Chapter {i+1} local_summary 为空或None，已使用兜底文本"
                    )

                print(f"[Logic Debug] Chapter {i+1} Parsed Summary: '{local_summary}'")
                print(f"[Logic Debug] Summary Length: {len(local_summary)}")

                memory_entry = {
                    "section_id": chap.get("section_id"),
                    "title": chap["title"],
                    "local_summary": local_summary,
                    "logic_skeleton": logic_skeleton,
                    "confidence": "LOW" if low_confidence else "HIGH",
                }
                self.logic_memory.append(memory_entry)

                print(
                    f"[Logic Debug] Stored in logic_memory: section_id={memory_entry['section_id']}, title={memory_entry['title']}, confidence={memory_entry['confidence']}"
                )

                try:
                    if not is_toc_chapter:
                        chapter_facts = self._extract_chapter_facts(
                            chapter_content=chap["content_xml"],
                            chapter_info={
                                "title": chap["title"],
                                "section_id": chap.get("section_id"),
                                "start_page_num": chap.get("start_page_num"),
                            },
                        )
                        self._store_facts(
                            chapter_facts,
                            chapter_info={
                                "title": chap["title"],
                                "start_page_num": chap.get("start_page_num"),
                            },
                        )
                        core_claims = logic_skeleton.get("core_claims") or []
                        if core_claims and not self._has_claim_overlap(
                            core_claims, chapter_facts.get("claims", [])
                        ):
                            logic_skeleton["confidence"] = "LOW"
                            low_confidence = True
                except Exception as fact_error:
                    print(f"[Fact Extraction] Failed for chapter {i+1}: {fact_error}")

                map_results.append(
                    {
                        "title": chap["title"],
                        "local_summary": local_summary,
                        "logic_skeleton": logic_skeleton,
                        "confidence": "LOW" if low_confidence else "HIGH",
                        "issues": data.get("issues", []),
                        "start_page_num": chap.get("start_page_num"),
                        "section_id": chap.get("section_id"),
                        "toc_rule_result": toc_rule_result if is_toc_chapter else None,
                        "toc_entries": normalized_entries if is_toc_chapter else [],
                    }
                )

            except Exception as e:
                print(f"[Logic] Failed to review chapter {i+1}: {e}")
                fallback_summary = f"[审查失败] 第{i+1}章《{chap['title']}》审查过程中出现异常：{str(e)}"

                self.logic_memory.append(
                    {
                        "section_id": chap.get("section_id"),
                        "title": chap["title"],
                        "local_summary": fallback_summary,
                        "logic_skeleton": {},
                        "confidence": "LOW",
                    }
                )
                print(
                    "[Logic Debug] Exception fallback: Added to logic_memory with error summary"
                )

                map_results.append(
                    {
                        "title": chap["title"],
                        "local_summary": fallback_summary,
                        "logic_skeleton": {},
                        "confidence": "LOW",
                        "issues": [],
                        "start_page_num": chap.get("start_page_num"),
                        "section_id": chap.get("section_id"),
                        "toc_rule_result": toc_rule_result if is_toc_chapter else None,
                        "toc_entries": normalized_entries if is_toc_chapter else [],
                    }
                )

        for res in map_results:
            start_page = res.get("start_page_num")
            if start_page:
                for issue in res["issues"]:
                    if not issue.get("page"):
                        issue["page"] = start_page

        print("[Logic] Starting Global Reduction...")

        print("\n[Logic Debug] === Logic Memory Content ===")
        print(f"[Logic Debug] Total entries in logic_memory: {len(self.logic_memory)}")
        for idx, mem_entry in enumerate(self.logic_memory):
            print(f"[Logic Debug] Entry {idx+1}:")
            print(f"  - section_id: {mem_entry.get('section_id')}")
            print(f"  - title: {mem_entry.get('title')}")
            print(
                f"  - local_summary: {mem_entry.get('local_summary')[:100]}..."
                if len(mem_entry.get("local_summary", "")) > 100
                else f"  - local_summary: {mem_entry.get('local_summary')}"
            )
            print(f"  - confidence: {mem_entry.get('confidence')}")
        print("[Logic Debug] =============================\n")

        title_page_map = {}
        section_page_map = {}
        mem_list = self.logic_memory if self.logic_memory else map_results
        global_context = self._build_global_context(mem_list)
        for res in map_results:
            if res.get("start_page_num"):
                title_page_map[res["title"]] = res["start_page_num"]
            if res.get("section_id") and res.get("start_page_num"):
                section_page_map[res["section_id"]] = res["start_page_num"]
        fallback_page = list(title_page_map.values())[0] if title_page_map else None

        print("\n[Logic Debug] === Global Context (sent to LLM) ===")
        print(global_context)
        print("[Logic Debug] =====================================\n")

        messages = [
            {
                "role": "system",
                "content": global_logic_review_prompt.format(
                    global_context=global_context
                ),
            },
            {"role": "user", "content": "请基于以上各章摘要进行全局逻辑一致性检查。"},
        ]

        try:
            response = self.doc_agent._call_llm(
                messages, max_tokens=8192, temperature=0.0
            )
            raw_res = response.choices[0].message.content

            print("[Logic Debug] Global LLM Raw Response (first 1000 chars):")
            print(raw_res[:1000])
            print("---")

            global_data = self.doc_agent._parse_json(raw_res)

            # 处理两种可能的返回格式：{"issues": [...]} 或 [{...}, {...}]
            if isinstance(global_data, list):
                global_issues = global_data
            elif isinstance(global_data, dict):
                global_issues = global_data.get("issues", [])
            else:
                global_issues = []

            thinking_match = re.search(
                r"<thinking>(.*?)</thinking>", raw_res, re.DOTALL
            )
            global_thinking = thinking_match.group(1).strip() if thinking_match else ""

            full_thinking_log += (
                f"\n=== 全局一致性审查 (Global Review) ===\n{global_thinking}\n"
            )

            all_issues = []
            toc_issues = []
            other_map_issues = []
            for res in map_results:
                for issue in res["issues"]:
                    if issue.get("issue_type") == "目录结构":
                        toc_issues.append(issue)
                    else:
                        other_map_issues.append(issue)
            merged_toc_rule = self._merge_toc_rule_result(map_results)
            toc_issues = self._apply_toc_conflict_downgrade(toc_issues, merged_toc_rule)
            # 目录问题放在逻辑问题开头
            all_issues = toc_issues + other_map_issues
            # 全局阶段的目录问题也执行同样的冲突降级，避免“规则已检出存在”却仍报缺章
            global_toc_issues = []
            global_other_issues = []
            for issue in global_issues:
                if issue.get("issue_type") == "目录结构":
                    global_toc_issues.append(issue)
                else:
                    global_other_issues.append(issue)
            global_toc_issues = self._apply_toc_conflict_downgrade(
                global_toc_issues, merged_toc_rule
            )
            all_issues.extend(global_toc_issues + global_other_issues)

            # 目录检测总结：优先使用目录章节的完整层级条目（如 1/1.1/1.1.1），兜底再用顶层标题
            current_outline: List[str] = []
            for res in map_results:
                for e in res.get("toc_entries") or []:
                    number_text = str(e.get("number_text", "")).strip()
                    title_text = str(e.get("title", "")).strip()
                    if number_text and title_text:
                        current_outline.append(f"{number_text} {title_text}")
                    elif title_text:
                        current_outline.append(title_text)

            # 去重并保持顺序
            if current_outline:
                seen = set()
                dedup_outline = []
                for line in current_outline:
                    key = line.strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    dedup_outline.append(key)
                current_outline = dedup_outline
            else:
                current_outline = [res["title"] for res in map_results]

            toc_suggestion = self._get_toc_final_suggestion(current_outline, toc_issues)
            if toc_suggestion.get("summary") or toc_suggestion.get("suggested_outline"):
                print("\n[目录检测] ========== AI 总建议 ==========")
                print(toc_suggestion.get("summary", ""))
                print("[目录检测] ========== 修改后的推荐目录 ==========")
                for line in toc_suggestion.get("suggested_outline") or []:
                    print(f"  {line}")
                print("[目录检测] ====================================\n")

            print(
                "\n[Fact Conflict Detection] Starting cross-chapter fact verification..."
            )
            fact_conflicts = self._detect_fact_conflicts()
            all_issues.extend(fact_conflicts)
            print(
                f"[Fact Conflict Detection] Added {len(fact_conflicts)} conflict issues\n"
            )

            print(
                "\n[Consistency] Starting targeted cross-chapter consistency checks..."
            )
            targeted_consistency_issues = self._detect_targeted_consistency_conflicts(
                chapters
            )
            all_issues.extend(targeted_consistency_issues)
            print(
                f"[Consistency] Added {len(targeted_consistency_issues)} consistency issues\n"
            )

            for issue in all_issues:
                if not issue.get("page"):
                    sec_title = issue.get("section")
                    sec_id = issue.get("section_id") or issue.get("section")
                    if sec_title and sec_title in title_page_map:
                        issue["page"] = title_page_map[sec_title]
                    elif sec_id and sec_id in section_page_map:
                        issue["page"] = section_page_map[sec_id]
                    else:
                        quote_page = self.doc_agent._find_page_by_quote(
                            issue.get("quote")
                        )
                        if not quote_page:
                            quote_page = self.doc_agent._find_page_by_fuzzy_quote(
                                issue.get("quote"), threshold=0.6
                            )
                        if quote_page:
                            issue["page"] = quote_page
                        elif fallback_page:
                            issue["page"] = fallback_page

            return {
                "raw": json.dumps(
                    {"issues": all_issues, "toc_suggestion": toc_suggestion},
                    ensure_ascii=False,
                    indent=2,
                ),
                "thinking": full_thinking_log,
            }

        except Exception as e:
            print(f"[Logic] Global reduction failed: {e}")
            return {
                "raw": json.dumps(
                    {
                        "issues": [],
                        "toc_suggestion": {"summary": "", "suggested_outline": []},
                    }
                ),
                "thinking": full_thinking_log
                + "\n=== 全局一致性审查 (Global Review) ===\n"
                + f"[Error] 全局分析失败：{e}",
            }

    # NOTE: run_normative_logic_review() has been removed
    # This method is deprecated as normative and logic reviews have been separated
    # into NormativeAgent and LogicAgent respectively.
