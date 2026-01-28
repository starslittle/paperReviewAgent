from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .prompts import (
    context_fitness_prompt,
    image_evidence_prompt,
    judge_prompt,
    text_claim_prompt,
    vision_description_prompt,
)


class VisionAgent:
    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent
        self.doc_reader = doc_agent.doc_reader
        self.client = doc_agent.client
        self.model_id = doc_agent.model_id

    def run(
        self,
        vision_model_id: str,
        vision_api_key: Optional[str],
        vision_base_url: Optional[str],
        include_page_image: bool = True,
        parallel: bool = True,
        max_workers: int = 3,
    ) -> Dict[str, Any]:
        res_list = self.run_vision_review(
            vision_model_id=vision_model_id,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            parallel=None,
            max_workers=None,
        )

        issues: List[Dict[str, Any]] = []
        thinking_list: List[str] = []

        for res in res_list:
            if not isinstance(res, dict) or "error" in res:
                continue
            raw = res.get("raw", "")

            if "text_analysis" in res and res["text_analysis"]:
                text_issues = res["text_analysis"].get("issues", [])
                if isinstance(text_issues, list):
                    for iss in text_issues:
                        if isinstance(iss, dict):
                            if not iss.get("page"):
                                iss["page"] = res.get("page")
                            if not iss.get("image_id"):
                                iss["image_id"] = res.get("image_id")
                            if not iss.get("caption"):
                                iss["caption"] = res.get("caption", "")
                            if not iss.get("section"):
                                iss["section"] = res.get("section")
                    issues.extend([iss for iss in text_issues if isinstance(iss, dict)])
            else:
                parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}
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
                    issues.extend([iss for iss in parsed_issues if isinstance(iss, dict)])

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

    def _build_figure_unit(
        self, img_id: str, image_info: Dict, base64_img: str, media_type: str
    ) -> Optional[Dict]:
        page_num = image_info.get("page_num")
        caption = image_info.get("caption", "")

        section_info = self.doc_reader.find_section_by_page(page_num)
        if not section_info:
            print(f"[Figure Unit] ✗ 图片 {img_id}: 未找到所属章节 (页码: {page_num})")
            return None

        section_elem = section_info.get("section_elem")
        if section_elem is not None:
            section_xml = ET.tostring(section_elem, encoding="unicode", method="xml")
        else:
            section_xml = ""

        reference_texts = self._extract_reference_texts(img_id, caption, section_info)
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
        reference_texts = []
        section_elem = section_info.get("section_elem")
        if not section_elem:
            return reference_texts

        figure_num_match = re.search(r"[图圖](\d+\.?\d*)", caption)
        if figure_num_match:
            figure_num = figure_num_match.group(1)
        else:
            figure_num_match = re.search(r"(\d+\.?\d*)", img_id)
            figure_num = figure_num_match.group(1) if figure_num_match else None

        if not figure_num:
            return reference_texts

        patterns = [
            rf"[如如]图[圖圖]?\s*{re.escape(figure_num)}[所示]",
            rf"见图[圖圖]?\s*{re.escape(figure_num)}",
            rf"Figure\s+{re.escape(figure_num)}",
            rf"图[圖圖]?\s*{re.escape(figure_num)}[显示示]",
        ]

        section_text = "".join(section_elem.itertext())
        for pattern in patterns:
            matches = re.finditer(pattern, section_text, re.IGNORECASE)
            for match in matches:
                start = max(0, match.start() - 50)
                end = min(len(section_text), match.end() + 50)
                context = section_text[start:end].strip()
                if context and context not in reference_texts:
                    reference_texts.append(context)

        return reference_texts

    def _extract_context_around_image(
        self, page_num: int, section_info: Dict
    ) -> tuple:
        context_before = ""
        context_after = ""

        section_elem = section_info.get("section_elem")
        if not section_elem:
            return context_before, context_after

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

        try:
            page_int = int(float(page_num))
        except (ValueError, TypeError):
            return context_before, context_after

        before_paragraphs = [
            p[1] for p in paragraphs if p[0] <= page_int and p[0] >= page_int - 1
        ][-3:]
        context_before = "\n".join(before_paragraphs)

        after_paragraphs = [
            p[1] for p in paragraphs if p[0] > page_int and p[0] <= page_int + 1
        ][:3]
        context_after = "\n".join(after_paragraphs)

        return context_before, context_after

    def _extract_text_claims(self, figure_unit: Dict) -> List[Dict]:
        chapter_text = figure_unit["local_context"]
        reference_texts = figure_unit["reference_texts"]
        caption = figure_unit["caption"]

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
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()

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
            import traceback

            traceback.print_exc()
            return []

    def _analyze_image_evidence(
        self, client, vision_model_id: str, figure_unit: Dict
    ) -> Optional[Dict]:
        img_data = figure_unit["image"]
        caption = figure_unit["caption"]

        messages = [
            {"role": "system", "content": image_evidence_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请分析以下图片的证据能力。\n\n图片标题："
                        f"{caption}\n\n请按照要求输出JSON格式的证据能力描述。",
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

            evidence_data = self._parse_json_from_response(raw_content)

            print(f"\n{'='*80}")
            print(
                f"[Image Evidence Agent] 证据能力完整输出 (图片 {figure_unit['figure_id']})"
            )
            print(f"{'='*80}")
            print(json.dumps(evidence_data, ensure_ascii=False, indent=2))
            print(f"{'='*80}\n")

            print("[Image Evidence Agent] ✓ 证据能力分析完成")
            print(f"  → 图片类型: {evidence_data.get('image_type', 'unknown')}")
            capabilities = evidence_data.get("evidence_capabilities", {})
            enabled = [k for k, v in capabilities.items() if v]
            print(f"  → 支持的证据类型: {', '.join(enabled) if enabled else '无'}")

            return evidence_data

        except Exception as e:
            print(f"[Image Evidence Agent] ✗ 分析失败: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _analyze_context_fitness(self, figure_unit: Dict, image_evidence: Dict) -> Dict:
        chapter_title = figure_unit["chapter_title"]
        chapter_context = figure_unit["local_context"]
        image_type = image_evidence.get("image_type", "unknown")

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
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()

            fitness_data = self._parse_json_from_response(raw_content)

            print("[Context Agent] ✓ 适配性分析完成")
            print(f"  → 适配性: {fitness_data.get('fitness', 'unknown')}")
            print(f"  → 图片角色: {fitness_data.get('figure_role', 'unknown')}")

            return fitness_data

        except Exception as e:
            print(f"[Context Agent] ✗ 分析失败: {e}")
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
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()

            verdict = self._parse_json_from_response(raw_content)

            print("[Judge Agent] ✓ 裁决完成")
            print(f"  → 裁决结果: {verdict.get('verdict', 'unknown')}")
            print(
                f"  → 支持的主张: {len(verdict.get('supported_claims', []))}"
            )
            print(
                f"  → 不支持的主张: {len(verdict.get('unsupported_claims', []))}"
            )
            print(f"  → 发现问题数: {len(verdict.get('issues', []))}")

            return verdict

        except Exception as e:
            print(f"[Judge Agent] ✗ 裁决失败: {e}")
            import traceback

            traceback.print_exc()
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

    def _parse_json_from_response(self, raw_content: str) -> Dict:
        result = self.doc_agent._parse_json(raw_content)
        if result == {"issues": []} and raw_content:
            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw_content[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return result if result else {}

    def _extract_vision_description(
        self, client, vision_model_id, img_id, base64_img, media_type, caption
    ):
        messages = [
            {"role": "system", "content": vision_description_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请提取以下图片的结构化描述。\n\n图片标题："
                        f"{caption}\n\n请按照要求输出JSON格式的结构化描述。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{base64_img}"},
                    },
                ],
            },
        ]

        try:
            response = client.chat.completions.create(
                model=vision_model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()

            json_match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL
            )
            if json_match:
                try:
                    raw_content = json_match.group(1)
                except IndexError:
                    pass

            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1 and end > start:
                raw_content = raw_content[start : end + 1]
            else:
                json_match = re.search(
                    r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw_content, re.DOTALL
                )
                if json_match:
                    raw_content = json_match.group(0)

            try:
                description = json.loads(raw_content)
            except json.JSONDecodeError as json_err:
                cleaned = re.sub(r"//.*?$", "", raw_content, flags=re.MULTILINE)
                cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
                try:
                    description = json.loads(cleaned)
                except json.JSONDecodeError:
                    raise ValueError(
                        f"Failed to parse JSON: {json_err}. Raw content: {raw_content[:200]}"
                    )

            print(f"\n{'='*80}")
            print(f"[视觉大模型] Memory内容完整输出 (图片 {img_id})")
            print(f"{'='*80}")
            print(json.dumps(description, ensure_ascii=False, indent=2))
            print(f"{'='*80}\n")

            return description
        except Exception as e:
            print(
                f"[Vision Description] Failed to extract description for image {img_id}: {e}"
            )
            return None

    def _analyze_image_text_consistency_structured(
        self, figure_unit: Dict, client, vision_model_id: str
    ) -> Dict:
        print(f"\n{'='*80}")
        print(f"[结构化审查] 开始处理图片 {figure_unit['figure_id']}")
        print(f"{'='*80}\n")

        text_claims = self._extract_text_claims(figure_unit)
        image_evidence = self._analyze_image_evidence(
            client, vision_model_id, figure_unit
        )
        if not image_evidence:
            return {
                "img_id": figure_unit["figure_id"],
                "error": "Failed to analyze image evidence",
                "parsed": {"issues": []},
                "thinking": "图像证据能力分析失败",
            }

        context_fitness = self._analyze_context_fitness(figure_unit, image_evidence)
        judge_verdict = self._judge_consistency(
            text_claims, image_evidence, context_fitness, figure_unit
        )

        issues = []
        for issue in judge_verdict.get("issues", []):
            issues.append(
                {
                    "issue_type": "图文一致性",
                    "severity": issue.get("severity", "Medium"),
                    "section": figure_unit.get("chapter_title", ""),
                    "page": figure_unit["image"].get("page_num"),
                    "image_id": figure_unit["figure_id"],
                    "caption": figure_unit.get("caption", ""),
                    "quote": issue.get("description", ""),
                    "suggestion": issue.get("suggestion", ""),
                }
            )

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
            "figure_unit": figure_unit,
            "text_claims": text_claims,
            "image_evidence": image_evidence,
            "context_fitness": context_fitness,
            "judge_verdict": judge_verdict,
        }

    def _analyze_image_text_consistency(
        self, img_id, vision_description, section_info, caption, context
    ):
        return {"thinking": "使用旧方法（已废弃）", "parsed": {"issues": []}}

    def run_vision_review(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        include_page_image: bool = True,
        parallel=None,
        max_workers=None,
    ):
        results = []

        client = self.client
        using_qwen = "qwen" in vision_model_id.lower()
        if using_qwen:
            key = vision_api_key or self.client.api_key
            if key is None:
                raise ValueError(
                    "vision_api_key is required when using Qwen vision models. "
                    "Please set DASHSCOPE_API_KEY or pass --vision-api-key."
                )
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=vision_base_url or self.client.base_url,
            )
        elif vision_api_key or vision_base_url:
            from openai import OpenAI

            client = OpenAI(
                api_key=vision_api_key or self.client.api_key,
                base_url=vision_base_url or self.client.base_url,
            )

        image_info_map = {}
        parent_map = {c: p for p in self.doc_reader.root.iter() for c in p}

        for elem in self.doc_reader.root.iter("Image"):
            img_id = elem.get("image_id")
            page_num = elem.get("page_num")
            caption_text = ""
            context_text = []
            caption_pattern = re.compile(
                r"^(figure|fig\.?|图)\s*[\d\-\.]+", re.IGNORECASE
            )

            for child in elem:
                if child.tag == "Caption" and child.text:
                    caption_text = child.text

            parent = parent_map.get(elem)
            if parent:
                try:
                    children = list(parent)
                    idx = children.index(elem)
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
                image_filename = ""
                if img_id in self.doc_reader.image_path_dict:
                    image_filename = os.path.basename(
                        self.doc_reader.image_path_dict.get(img_id, "")
                    )
                image_info_map[img_id] = {
                    "page_num": page_num,
                    "caption": caption_text,
                    "context": context_str,
                    "image_name": image_filename,
                }

        count = 0
        total_images = len(self.doc_reader.image_path_dict)
        process_limit = min(max_images, total_images)
        print(
            f"[Agent] 发现 {total_images} 张图片，将审查前 {process_limit} 张（结构化串行模式）"
        )
        print(
            "  → 处理流程: Step 1 (Figure Unit构建) → Step 2 (Text Claims抽取) → Step 3 (Image Evidence分析) → Step 4 (Context Fitness分析) → Step 5 (Judge裁决) → Step 6 (格式化输出)"
        )

        for img_id, filename in self.doc_reader.image_path_dict.items():
            if count >= max_images:
                break

            media_type, base64_img, error = self.doc_reader.get_image(img_id)
            if error:
                print(f"[Error] Failed to load image {img_id}: {error}")
                continue

            meta = image_info_map.get(
                img_id,
                {
                    "page_num": "?",
                    "caption": "Unknown",
                    "context": "",
                    "image_name": os.path.basename(filename) if filename else "",
                },
            )

            print(
                f"[Agent] 分析图片 {img_id} ({meta.get('image_name', '')}) "
                f"(第 {meta['page_num']} 页): {meta['caption'][:30]}..."
            )

            try:
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

                text_analysis = self._analyze_image_text_consistency_structured(
                    figure_unit=figure_unit,
                    client=client,
                    vision_model_id=vision_model_id,
                )

                text_issues = text_analysis.get("parsed", {}).get("issues", [])
                for issue in text_issues:
                    if isinstance(issue, dict):
                        if not issue.get("caption"):
                            issue["caption"] = meta["caption"]
                        if not issue.get("image_name"):
                            issue["image_name"] = meta.get("image_name", "")

                results.append(
                    {
                        "image_id": img_id,
                        "page": meta["page_num"],
                        "caption": meta["caption"],
                        "image_name": meta.get("image_name", ""),
                        "raw": text_analysis.get("raw", ""),
                        "thinking": text_analysis.get("thinking", ""),
                        "text_analysis": text_analysis.get(
                            "parsed", {"issues": text_issues}
                        ),
                        "section": figure_unit.get("chapter_title", ""),
                        "figure_unit": figure_unit,
                        "text_claims": text_analysis.get("text_claims", []),
                        "image_evidence": text_analysis.get("image_evidence", {}),
                        "judge_verdict": text_analysis.get("judge_verdict", {}),
                    }
                )

                count += 1

            except Exception as e:
                print(f"[Error] Analysis failed for image {img_id}: {e}")
                import traceback

                traceback.print_exc()
                results.append({"image_id": img_id, "error": str(e)})

        print(f"\n[Agent] ✅ 完成 {count} 张图片的结构化分析")
        print(
            "  → 所有图片均使用6步结构化流程：Figure Unit → Text Claims → Image Evidence → Context Fitness → Judge → 格式化输出"
        )
        print(
            "  → 发现问题: 共 "
            f"{sum(len(r.get('text_analysis', {}).get('issues', [])) for r in results if r.get('text_analysis'))} 个图文一致性问题"
        )
        return results

    def run_vision_review_parallel(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_workers=3,
    ):
        raise NotImplementedError(
            "并行模式已废弃。请使用串行模式 run_vision_review()，"
            "它使用6步结构化流程：Figure Unit构建 → Text Claims抽取 → "
            "Image Evidence分析 → Context Fitness分析 → Judge裁决 → 格式化输出"
        )
