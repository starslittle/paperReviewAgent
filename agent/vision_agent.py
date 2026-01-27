from __future__ import annotations

import json
import os
import re
import traceback
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .prompts import (
    context_fitness_prompt,
    image_evidence_prompt,
    judge_prompt,
    text_claim_prompt,
)


class VisionAgent:
    def __init__(self, doc_agent: Any):
        """
        初始化图文一致性审查Agent
        
        Args:
            doc_agent: DocAgent实例，提供公共功能（LLM调用、JSON解析、文档读取等）
        """
        self.doc_agent = doc_agent

    def _parse_json_from_response(self, raw_content: str) -> Dict:
        """从LLM响应中解析JSON（简化版，复用DocAgent的_parse_json）"""
        result = self.doc_agent._parse_json(raw_content)
        # 如果_parse_json返回的是{"issues": []}格式，尝试提取实际内容
        if result == {"issues": []} and raw_content:
            # 快速尝试：直接查找JSON对象
            start = raw_content.find('{')
            end = raw_content.rfind('}')
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw_content[start:end+1])
                except json.JSONDecodeError:
                    pass
        return result if result else {}

    def _build_figure_unit(
        self, img_id: str, image_info: Dict, base64_img: str, media_type: str
    ) -> Optional[Dict]:
        """
        Step 1: 构建Figure Unit（核心数据结构）
        
        Args:
            img_id: 图片ID
            image_info: 图片信息字典，包含 page_num, caption, context 等
            base64_img: 图片的base64编码
            media_type: 图片媒体类型
        
        Returns:
            FigureUnit 字典，如果构建失败返回 None
        """
        page_num = image_info.get("page_num")
        caption = image_info.get("caption", "")
        context = image_info.get("context", "")

        # 1. 查找所属章节
        section_info = self.doc_agent.doc_reader.find_section_by_page(page_num)
        if not section_info:
            print(f"[Figure Unit] ✗ 图片 {img_id}: 未找到所属章节 (页码: {page_num})")
            return None

        # 2. 提取章节全文（XML格式）
        section_elem = section_info.get("section_elem")
        if section_elem is not None:
            section_xml = ET.tostring(section_elem, encoding="unicode", method="xml")
        else:
            section_xml = ""

        # 3. 搜索引用文本
        reference_texts = self._extract_reference_texts(img_id, caption, section_info)

        # 4. 提取上下文（图片前后的段落）
        context_before, context_after = self._extract_context_around_image(
            page_num, section_info
        )

        figure_unit = {
            "figure_id": img_id,
            "chapter_id": section_info.get("section_id", ""),
            "chapter_title": section_info.get("title", ""),
            "caption": caption,
            "image": {
                "img_id": img_id,
                "base64_img": base64_img,
                "media_type": media_type,
                "page_num": page_num,
            },
            "reference_texts": reference_texts,
            "local_context": section_xml,
            "context_before": context_before,
            "context_after": context_after,
        }

        print(f"[Figure Unit] ✓ 图片 {img_id} 构建完成")
        print(f"  → 章节: {figure_unit['chapter_title']}")
        print(f"  → 引用文本数量: {len(reference_texts)}")

        return figure_unit

    def _extract_reference_texts(
        self, img_id: str, caption: str, section_info: Dict
    ) -> List[str]:
        """提取正文中引用该图片的文本片段"""
        reference_texts = []
        section_elem = section_info.get("section_elem")
        if not section_elem:
            return reference_texts

        # 从caption中提取图片编号（如 "图4.2" -> "4.2"）
        figure_num_match = re.search(r"[图圖](\d+\.?\d*)", caption)
        if figure_num_match:
            figure_num = figure_num_match.group(1)
        else:
            # 尝试从img_id中提取
            figure_num_match = re.search(r"(\d+\.?\d*)", img_id)
            figure_num = figure_num_match.group(1) if figure_num_match else None

        if not figure_num:
            return reference_texts

        # 搜索引用模式
        patterns = [
            rf"[如如]图[圖圖]?\s*{re.escape(figure_num)}[所示]",
            rf"见图[圖圖]?\s*{re.escape(figure_num)}",
            rf"Figure\s+{re.escape(figure_num)}",
            rf"图[圖圖]?\s*{re.escape(figure_num)}[显示示]",
        ]

        # 在章节文本中搜索
        section_text = "".join(section_elem.itertext())
        for pattern in patterns:
            matches = re.finditer(pattern, section_text, re.IGNORECASE)
            for match in matches:
                # 提取匹配位置前后的上下文（各50字符）
                start = max(0, match.start() - 50)
                end = min(len(section_text), match.end() + 50)
                context = section_text[start:end].strip()
                if context and context not in reference_texts:
                    reference_texts.append(context)

        return reference_texts

    def _extract_context_around_image(
        self, page_num: int, section_info: Dict
    ) -> tuple:
        """提取图片前后的段落文本作为上下文"""
        context_before = ""
        context_after = ""

        section_elem = section_info.get("section_elem")
        if not section_elem:
            return context_before, context_after

        # 查找包含该页码的段落
        paragraphs = []
        for elem in section_elem.iter():
            if elem.tag == "Paragraph" and elem.text:
                elem_page = elem.get("page_num")
                if elem_page:
                    try:
                        elem_page_int = int(float(elem_page))
                        paragraphs.append((elem_page_int, elem.text))
                    except (ValueError, TypeError):
                        continue

        # 找到图片所在页码附近的段落
        try:
            page_int = int(float(page_num))
        except (ValueError, TypeError):
            return context_before, context_after

        # 提取图片前的段落（最多3段）
        before_paragraphs = [
            p[1] for p in paragraphs if p[0] <= page_int and p[0] >= page_int - 1
        ][-3:]
        context_before = "\n".join(before_paragraphs)

        # 提取图片后的段落（最多3段）
        after_paragraphs = [
            p[1] for p in paragraphs if p[0] > page_int and p[0] <= page_int + 1
        ][:3]
        context_after = "\n".join(after_paragraphs)

        return context_before, context_after

    def _extract_text_claims(self, figure_unit: Dict) -> List[Dict]:
        """
        Step 2: 抽取文本主张（Text Claim Agent）
        
        职责：从章节文本中抽取可被图像验证的结构化主张
        """
        # 构建输入
        chapter_text = figure_unit["local_context"]
        reference_texts = figure_unit["reference_texts"]
        caption = figure_unit["caption"]

        # 如果章节文本过长，截断（保留前15000字符）
        if len(chapter_text) > 15000:
            chapter_text = chapter_text[:15000] + "\n[内容已截断...]"

        messages = [
            {"role": "system", "content": text_claim_prompt},
            {
                "role": "user",
                "content": f"""请从以下章节文本中抽取可被图像验证的结构化主张。

【章节标题】{figure_unit['chapter_title']}

【图片标题】{caption}

【章节内容】
{chapter_text}

【图片引用文本】
{chr(10).join(reference_texts) if reference_texts else "（未找到明确的图片引用）"}

请按照要求输出JSON格式的主张列表。""",
            },
        ]

        try:
            print(
                f"[Text Claim Agent] 正在抽取文本主张 (图片: {figure_unit['figure_id']})..."
            )
            response = self.doc_agent._call_llm(messages, max_tokens=4096, temperature=0.0)
            raw_content = response.choices[0].message.content.strip()

            # 解析JSON
            claims_data = self._parse_json_from_response(raw_content)
            claims = claims_data.get("claims", [])

            print(f"[Text Claim Agent] ✓ 抽取到 {len(claims)} 个文本主张")
            for i, claim in enumerate(claims, 1):
                print(
                    f"  → C{i}: {claim.get('type', 'unknown')} - {claim.get('assertion', '')[:50]}"
                )

            return claims

        except Exception as e:
            print(f"[Text Claim Agent] ✗ 抽取失败: {e}")
            traceback.print_exc()
            return []

    def _analyze_image_evidence(
        self, client, vision_model_id: str, figure_unit: Dict
    ) -> Optional[Dict]:
        """
        Step 3: 分析图像证据能力（Image Evidence Agent）
        
        职责：分析图片"客观上"能支持哪些类型的事实
        """
        img_data = figure_unit["image"]
        caption = figure_unit["caption"]

        messages = [
            {"role": "system", "content": image_evidence_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"请分析以下图片的证据能力。\n\n图片标题：{caption}\n\n请按照要求输出JSON格式的证据能力描述。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img_data['media_type']};base64,{img_data['base64_img']}"
                        },
                    },
                ],
            },
        ]

        try:
            print(
                f"[Image Evidence Agent] 正在分析图像证据能力 (图片: {figure_unit['figure_id']})..."
            )
            response = client.chat.completions.create(
                model=vision_model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()

            # 解析JSON
            evidence_data = self._parse_json_from_response(raw_content)

            # 完整打印视觉大模型生成的evidence内容
            print(f"\n{'='*80}")
            print(
                f"[Image Evidence Agent] 证据能力完整输出 (图片 {figure_unit['figure_id']})"
            )
            print(f"{'='*80}")
            print(json.dumps(evidence_data, ensure_ascii=False, indent=2))
            print(f"{'='*80}\n")

            print(f"[Image Evidence Agent] ✓ 证据能力分析完成")
            print(f"  → 图片类型: {evidence_data.get('image_type', 'unknown')}")
            capabilities = evidence_data.get("evidence_capabilities", {})
            enabled = [k for k, v in capabilities.items() if v]
            print(f"  → 支持的证据类型: {', '.join(enabled) if enabled else '无'}")

            return evidence_data

        except Exception as e:
            print(f"[Image Evidence Agent] ✗ 分析失败: {e}")
            traceback.print_exc()
            return None

    def _analyze_context_fitness(
        self, figure_unit: Dict, image_evidence: Dict
    ) -> Dict:
        """
        Step 4: 分析章节-图像适配性（Context Agent）
        
        职责：判断图片在该章节中的适配性
        """
        chapter_title = figure_unit["chapter_title"]
        chapter_context = figure_unit["local_context"]
        image_type = image_evidence.get("image_type", "unknown")

        # 如果章节文本过长，截断
        if len(chapter_context) > 10000:
            chapter_context = chapter_context[:10000] + "\n[内容已截断...]"

        messages = [
            {"role": "system", "content": context_fitness_prompt},
            {
                "role": "user",
                "content": f"""请分析以下图片在该章节中的适配性。

【章节标题】{chapter_title}

【章节内容摘要】
{chapter_context[:5000]}

【图片类型】{image_type}

请按照要求输出JSON格式的适配性分析结果。""",
            },
        ]

        try:
            print(
                f"[Context Agent] 正在分析章节适配性 (图片: {figure_unit['figure_id']})..."
            )
            response = self.doc_agent._call_llm(messages, max_tokens=2048, temperature=0.0)
            raw_content = response.choices[0].message.content.strip()

            # 解析JSON
            fitness_data = self._parse_json_from_response(raw_content)

            print(f"[Context Agent] ✓ 适配性分析完成")
            print(f"  → 适配性: {fitness_data.get('fitness', 'unknown')}")
            print(f"  → 图片角色: {fitness_data.get('figure_role', 'unknown')}")

            return fitness_data

        except Exception as e:
            print(f"[Context Agent] ✗ 分析失败: {e}")
            # 返回默认值
            return {
                "chapter_intent": "unknown",
                "figure_role": "unknown",
                "fitness": "medium",
                "reason": f"分析失败: {str(e)}",
            }

    def _judge_consistency(
        self,
        text_claims: List[Dict],
        image_evidence: Dict,
        context_fitness: Dict,
        figure_unit: Dict,
    ) -> Dict:
        """
        Step 5: 裁决（Judge Agent）
        
        职责：基于所有结构化信息，做出最终判断
        """
        # 构建输入
        claims_json = json.dumps(text_claims, ensure_ascii=False, indent=2)
        evidence_json = json.dumps(image_evidence, ensure_ascii=False, indent=2)
        fitness_json = json.dumps(context_fitness, ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": judge_prompt},
            {
                "role": "user",
                "content": f"""请基于以下结构化信息，对图片 {figure_unit['figure_id']} 进行图文一致性裁决。

【文本主张列表】
{claims_json}

【图像证据能力】
{evidence_json}

【章节适配性】
{fitness_json}

【图片信息】
- 图片ID: {figure_unit['figure_id']}
- 图片标题: {figure_unit['caption']}
- 章节: {figure_unit['chapter_title']}
- 引用文本数量: {len(figure_unit['reference_texts'])}

请按照要求输出JSON格式的裁决结果。""",
            },
        ]

        try:
            print(
                f"[Judge Agent] 正在进行最终裁决 (图片: {figure_unit['figure_id']})..."
            )
            response = self.doc_agent._call_llm(messages, max_tokens=4096, temperature=0.0)
            raw_content = response.choices[0].message.content.strip()

            # 解析JSON
            verdict = self._parse_json_from_response(raw_content)

            print(f"[Judge Agent] ✓ 裁决完成")
            print(f"  → 裁决结果: {verdict.get('verdict', 'unknown')}")
            print(f"  → 支持的主张: {len(verdict.get('supported_claims', []))}")
            print(
                f"  → 不支持的主张: {len(verdict.get('unsupported_claims', []))}"
            )
            print(f"  → 发现问题数: {len(verdict.get('issues', []))}")

            return verdict

        except Exception as e:
            print(f"[Judge Agent] ✗ 裁决失败: {e}")
            traceback.print_exc()
            # 返回默认值
            return {
                "figure_id": figure_unit["figure_id"],
                "verdict": "unknown",
                "supported_claims": [],
                "unsupported_claims": [c.get("claim_id", "") for c in text_claims],
                "placement_fitness": context_fitness.get("fitness", "medium"),
                "issues": [
                    {
                        "type": "analysis_error",
                        "severity": "Medium",
                        "description": f"裁决过程出错: {str(e)}",
                        "suggestion": "请检查输入数据或重新分析",
                    }
                ],
            }

    def _analyze_image_text_consistency_structured(
        self, figure_unit: Dict, client, vision_model_id: str
    ) -> Dict:
        """
        结构化图文一致性分析
        
        使用6步结构化流程：
        1. Figure Unit 构建（已完成，作为输入）
        2. Text Claim Agent（文本主张抽取）
        3. Image Evidence Agent（图像证据能力建模）
        4. Context Agent（章节-图像适配性分析）
        5. Judge Agent（裁决）
        6. 格式化输出
        
        Args:
            figure_unit: Figure Unit字典
            client: 视觉模型客户端
            vision_model_id: 视觉模型ID
        
        Returns:
            包含分析结果的字典
        """
        print(f"\n{'='*80}")
        print(f"[结构化审查] 开始处理图片 {figure_unit['figure_id']}")
        print(f"{'='*80}\n")

        # Step 2: 抽取文本主张
        text_claims = self._extract_text_claims(figure_unit)

        # Step 3: 分析图像证据能力
        image_evidence = self._analyze_image_evidence(client, vision_model_id, figure_unit)
        if not image_evidence:
            return {
                "img_id": figure_unit["figure_id"],
                "error": "Failed to analyze image evidence",
                "parsed": {"issues": []},
                "thinking": "图像证据能力分析失败",
            }

        # Step 4: 分析章节适配性
        context_fitness = self._analyze_context_fitness(figure_unit, image_evidence)

        # Step 5: 裁决
        judge_verdict = self._judge_consistency(
            text_claims, image_evidence, context_fitness, figure_unit
        )

        # Step 6: 格式化输出（转换为现有格式）
        issues = []
        for issue in judge_verdict.get("issues", []):
            issues.append(
                {
                    "issue_type": "图文一致性",
                    "severity": issue.get("severity", "Medium"),
                    "section": figure_unit.get("chapter_title", ""),
                    "page": figure_unit["image"].get("page_num"),
                    "image_id": figure_unit["figure_id"],
                    "caption": figure_unit.get("caption", ""),  # 添加图表名称
                    "quote": issue.get("description", ""),
                    "suggestion": issue.get("suggestion", ""),
                }
            )

        # 构建thinking（包含所有中间结果）
        thinking_parts = [
            "### 📊 结构化分析流程",
            "",
            "#### Step 1: Figure Unit",
            f"- 图片ID: {figure_unit['figure_id']}",
            f"- 章节: {figure_unit['chapter_title']}",
            f"- 引用文本数量: {len(figure_unit['reference_texts'])}",
            "",
            "#### Step 2: Text Claims",
            json.dumps(text_claims, ensure_ascii=False, indent=2),
            "",
            "#### Step 3: Image Evidence",
            json.dumps(image_evidence, ensure_ascii=False, indent=2),
            "",
            "#### Step 4: Context Fitness",
            json.dumps(context_fitness, ensure_ascii=False, indent=2),
            "",
            "#### Step 5: Judge Verdict",
            json.dumps(judge_verdict, ensure_ascii=False, indent=2),
        ]

        thinking = "\n".join(thinking_parts)

        print(f"\n{'='*80}")
        print(f"[结构化审查] ✓ 图片 {figure_unit['figure_id']} 处理完成")
        print(f"{'='*80}\n")

        return {
            "thinking": thinking,
            "parsed": {"issues": issues},
            "raw": json.dumps(judge_verdict, ensure_ascii=False),
            # 保留中间结果用于调试
            "figure_unit": figure_unit,
            "text_claims": text_claims,
            "image_evidence": image_evidence,
            "context_fitness": context_fitness,
            "judge_verdict": judge_verdict,
        }

    def run_vision_review(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        parallel=None,  # 已废弃，保留参数以保持兼容性
        max_workers=None,  # 已废弃，保留参数以保持兼容性
    ):
        """
        图文一致性审查（结构化串行版本）：
        使用6步结构化流程：
        1. Figure Unit构建
        2. Text Claims抽取
        3. Image Evidence分析
        4. Context Fitness分析
        5. Judge裁决
        6. 格式化输出

        输出语言：中文

        Args:
            vision_model_id: 视觉模型ID
            max_images: 最大处理图片数
            vision_api_key: 视觉模型API密钥
            vision_base_url: 视觉模型API Base URL
            parallel: 已废弃，不再支持并行模式
            max_workers: 已废弃，不再支持并行模式
        """

        results = []

        # Determine client to use
        using_qwen = "qwen" in vision_model_id.lower()
        if using_qwen:
            key = vision_api_key or self.doc_agent.client.api_key
            if key is None:
                raise ValueError(
                    "vision_api_key is required when using Qwen vision models. "
                    "Please set DASHSCOPE_API_KEY or pass --vision-api-key."
                )
            client = OpenAI(api_key=key, base_url=vision_base_url or self.doc_agent.client.base_url)
        elif vision_api_key or vision_base_url:
            client = OpenAI(
                api_key=vision_api_key or self.doc_agent.client.api_key,
                base_url=vision_base_url or self.doc_agent.client.base_url,
            )
        else:
            client = self.doc_agent.client

        image_info_map = {}
        parent_map = {
            c: p
            for p in self.doc_agent.doc_reader.root.iter()
            for c in p
        }

        for elem in self.doc_agent.doc_reader.root.iter("Image"):
            img_id = elem.get("image_id")
            page_num = elem.get("page_num")
            caption_text = ""
            context_text = []
            # 修复：支持带连字符的图号，如 "图 2-5"、"Figure 3-2" 等
            caption_pattern = re.compile(
                r"^(figure|fig\.?|图)\s*[\d\-\.]+", re.IGNORECASE
            )

            # 1. Get Caption
            for child in elem:
                if child.tag == "Caption" and child.text:
                    caption_text = child.text

            # 2. Get Context（优化：减少混入其他图片描述）
            parent = parent_map.get(elem)
            if parent:
                try:
                    children = list(parent)
                    idx = children.index(elem)
                    # 优先提取图片前面的2个段落和后面的1个段落，减少混入风险
                    start_idx = max(0, idx - 2)
                    end_idx = min(len(children), idx + 2)

                    for i in range(start_idx, end_idx):
                        node = children[i]
                        if node.tag == "Paragraph" and node.text:
                            text = node.text.strip()
                            if self.doc_agent._is_header_footer(
                                text, node.get("page_num") or page_num
                            ):
                                continue
                            # 如果段落中包含其他图号（且不是当前图号），跳过以避免混淆
                            current_fig_num = None
                            if caption_text:
                                fig_match = re.search(r"图\s*([\d\-\.]+)", caption_text)
                                if fig_match:
                                    current_fig_num = fig_match.group(1)

                            other_fig_match = re.search(r"图\s*([\d\-\.]+)", text)
                            if other_fig_match and current_fig_num:
                                other_fig_num = other_fig_match.group(1)
                                if other_fig_num != current_fig_num:
                                    text = f"[⚠️可能引用其他图] {text}"

                            # If no caption captured yet, try to detect from nearby paragraphs
                            if not caption_text and caption_pattern.match(text):
                                caption_text = text
                            context_text.append(text)
                        elif node.tag == "Heading" and node.text:
                            heading_text = node.text.strip()
                            if self.doc_agent._is_header_footer(
                                heading_text, node.get("page_num") or page_num
                            ):
                                continue
                            context_text.append(f"[Heading: {heading_text}]")
                except ValueError:
                    pass

            context_str = "\n".join(context_text)

            if img_id:
                image_info_map[img_id] = {
                    "page_num": page_num,
                    "caption": caption_text,
                    "context": context_str,
                }

        # Process images
        count = 0
        total_images = len(self.doc_agent.doc_reader.image_path_dict)
        process_limit = min(max_images, total_images)
        print(
            f"[VisionAgent] 发现 {total_images} 张图片，将审查前 {process_limit} 张（结构化串行模式）"
        )
        print(
            f"  → 处理流程: Step 1 (Figure Unit构建) → Step 2 (Text Claims抽取) → Step 3 (Image Evidence分析) → Step 4 (Context Fitness分析) → Step 5 (Judge裁决) → Step 6 (格式化输出)"
        )

        for img_id, filename in self.doc_agent.doc_reader.image_path_dict.items():
            if count >= max_images:
                break

            media_type, base64_img, error = self.doc_agent.doc_reader.get_image(img_id)
            if error:
                print(f"[Error] Failed to load image {img_id}: {error}")
                continue

            meta = image_info_map.get(
                img_id, {"page_num": "?", "caption": "Unknown", "context": ""}
            )

            print(
                f"[VisionAgent] 分析图片 {img_id} (第 {meta['page_num']} 页): {meta['caption'][:30]}..."
            )

            try:
                # Step 1: 构建Figure Unit
                print(f"[Step 1/6] [Figure Unit] 构建图片 {img_id} 的分析单元...")
                figure_unit = self._build_figure_unit(
                    img_id=img_id,
                    image_info=meta,
                    base64_img=base64_img,
                    media_type=media_type,
                )

                if not figure_unit:
                    print(f"[Error] 无法构建Figure Unit，跳过图片 {img_id}")
                    results.append(
                        {
                            "image_id": img_id,
                            "page": meta["page_num"],
                            "caption": meta["caption"],
                            "error": "Failed to build figure unit",
                        }
                    )
                    count += 1
                    continue

                # Step 2-6: 执行结构化分析流程
                text_analysis = self._analyze_image_text_consistency_structured(
                    figure_unit=figure_unit, client=client, vision_model_id=vision_model_id
                )

                # 格式化输出（兼容现有格式）
                # 确保text_analysis中的issues也包含caption信息
                text_issues = text_analysis.get("parsed", {}).get("issues", [])
                for issue in text_issues:
                    if isinstance(issue, dict):
                        if not issue.get("caption"):
                            issue["caption"] = meta["caption"]

                results.append(
                    {
                        "image_id": img_id,
                        "page": meta["page_num"],
                        "caption": meta["caption"],
                        "raw": text_analysis.get("raw", ""),
                        "thinking": text_analysis.get("thinking", ""),
                        "text_analysis": text_analysis.get("parsed", {"issues": text_issues}),
                        "section": figure_unit.get("chapter_title", ""),
                        # 保留中间结果用于调试
                        "figure_unit": figure_unit,
                        "text_claims": text_analysis.get("text_claims", []),
                        "image_evidence": text_analysis.get("image_evidence", {}),
                        "judge_verdict": text_analysis.get("judge_verdict", {}),
                    }
                )

                count += 1

            except Exception as e:
                print(f"[Error] Analysis failed for image {img_id}: {e}")
                traceback.print_exc()
                results.append({"image_id": img_id, "error": str(e)})

        # 返回结果
        print(f"\n[VisionAgent] ✅ 完成 {count} 张图片的结构化分析")
        print(
            f"  → 所有图片均使用6步结构化流程：Figure Unit → Text Claims → Image Evidence → Context Fitness → Judge → 格式化输出"
        )
        print(
            f"  → 发现问题: 共 {sum(len(r.get('text_analysis', {}).get('issues', [])) for r in results if r.get('text_analysis'))} 个图文一致性问题"
        )
        return results

    def run(
        self,
        vision_model_id: str,
        vision_api_key: Optional[str],
        vision_base_url: Optional[str],
        include_page_image: bool = True,  # 保留参数以保持接口兼容，但不传递给底层方法
        parallel: bool = True,  # 已废弃，保留以保持兼容性
        max_workers: int = 3,  # 已废弃，保留以保持兼容性
    ) -> Dict[str, Any]:
        """运行图文一致性审查并返回标准格式结果"""
        res_list = self.run_vision_review(
            vision_model_id=vision_model_id,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            parallel=None,  # 已废弃，不再使用
            max_workers=None,  # 已废弃，不再使用
        )

        issues: List[Dict[str, Any]] = []
        thinking_list: List[str] = []

        for res in res_list:
            if not isinstance(res, dict) or "error" in res:
                continue
            raw = res.get("raw", "")

            # 处理增强流程的text_analysis结果
            if "text_analysis" in res and res["text_analysis"]:
                text_issues = res["text_analysis"].get("issues", [])
                if isinstance(text_issues, list):
                    for iss in text_issues:
                        if isinstance(iss, dict):
                            # 确保page、image_id、caption和section正确填充
                            if not iss.get("page"):
                                iss["page"] = res.get("page")
                            if not iss.get("image_id"):
                                iss["image_id"] = res.get("image_id")
                            if not iss.get("caption"):
                                iss["caption"] = res.get("caption", "")
                            if not iss.get("section"):
                                iss["section"] = res.get("section")
                    issues.extend(
                        [iss for iss in text_issues if isinstance(iss, dict)]
                    )
            else:
                # 原始流程：解析raw content
                parsed = (
                    self.doc_agent._parse_json(raw) if raw else {"issues": []}
                )
                if isinstance(parsed, list):
                    parsed_issues = parsed
                elif isinstance(parsed, dict):
                    parsed_issues = parsed.get("issues", [])
                else:
                    parsed_issues = []
                if isinstance(parsed_issues, list):
                    for iss in parsed_issues:
                        if isinstance(iss, dict) and not iss.get("page"):
                            iss["page"] = res.get("page")
                    issues.extend(
                        [iss for iss in parsed_issues if isinstance(iss, dict)]
                    )

            if res.get("thinking"):
                header = (
                    f"### 🖼️ 图片分析: {res.get('image_id', 'unknown')} "
                    f"(第 {res.get('page', '?')} 页)"
                )
                thinking_list.append(f"{header}\n\n{res.get('thinking')}")

        thinking = "\n\n".join(thinking_list)
        return {
            "raw": res_list,
            "parsed": {"issues": issues},
            "thinking": thinking,
            "errors": [],
        }
