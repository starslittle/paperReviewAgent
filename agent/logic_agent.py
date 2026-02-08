from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
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
                f"[Type Detection] ✓ 置信度足够，确定为: {'程序开发类' if thesis_type == 'system' else '算法理论类'}"
            )
            print("=" * 60 + "\n")
            return thesis_type

        # 阶段2: 深度判断（基于题目+摘要+目录）
        print(f"[Type Detection] 置信度不足 ({confidence:.2f} < 0.7)，启动深度分析...")
        thesis_type, confidence = self._detect_thesis_type_deep(title)

        if confidence < 0.6:
            print(
                f"[Type Detection] ⚠️ 警告：置信度较低 ({confidence:.2f})，建议人工确认"
            )
            print(f"[Type Detection] 可使用 --thesis-type 参数手动指定类型")
        else:
            print(
                f"[Type Detection] ✓ 深度判断完成，确定为: {'程序开发类' if thesis_type == 'system' else '算法理论类'}"
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
                        conflicts.append(
                            {
                                "issue_type": "逻辑性-数值冲突",
                                "severity": "High",
                                "section": "跨章节",
                                "page": occurrences[0]["page"],
                                "quote": f"'{metric_key}' 在不同位置有不同的数值",
                                "suggestion": (
                                    f"'{metric_key}' 的数值在文档中不一致（范围：{min_val}-{max_val}）。"
                                    f"出现位置："
                                    + "; ".join(
                                        [
                                            f"{occ['source']}为{occ['value']}{occ.get('unit', '')}"
                                            for occ in occurrences[:3]
                                        ]
                                    )
                                ),
                            }
                        )

        print(f"[Fact Conflict Detection] Found {len(conflicts)} conflicts")
        return conflicts

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
        user_content = f"""当前目录：
{outline_text}

已发现的目录问题：
{issues_text}

请按 prompt 要求输出 JSON（仅含 summary 与 suggested_outline）。"""
        messages = [
            {"role": "system", "content": toc_final_suggestion_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            response = self.doc_agent._call_llm(
                messages, max_tokens=2048, temperature=0.0
            )
            raw = response.choices[0].message.content or ""
            data = self.doc_agent._parse_json(raw)
            if isinstance(data, dict):
                return {
                    "summary": data.get("summary", ""),
                    "suggested_outline": data.get("suggested_outline") or [],
                }
        except Exception as e:
            print(f"[Logic] 目录总结建议生成失败: {e}")
        return {"summary": "", "suggested_outline": []}

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
                            + """
【程序开发类论文特殊提示】
本论文为程序开发类论文，请额外关注：
- 需求分析章节：是否包含可行性分析、用例图、业务流程图
- 系统设计章节：是否包含架构图、功能模块图、数据库设计（E-R图+表结构）
- 系统实现章节：是否包含关键代码片段和功能截图
- 系统测试章节：是否包含测试用例表格（输入、操作、预期、实际）
- 技术栈一致性：摘要、设计、实现中提到的技术栈是否一致
"""
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
            # 目录问题放在逻辑问题开头
            all_issues = toc_issues + other_map_issues
            all_issues.extend(global_issues)

            # 目录检测总结：总建议 + 修改后的推荐目录
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
