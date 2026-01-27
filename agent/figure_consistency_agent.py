"""
基于证据能力约束的图文一致性审查 Agent

核心设计原则：
- 所有Agent在同一个"中间语义空间"里协作
- 使用结构化输出，避免自由文本导致的对齐问题
- 串行执行，确保每一步的输出都是下一步的可靠输入
"""

import json
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
from openai import OpenAI


class FigureConsistencyAgent:
    """
    图文一致性审查 Agent（结构化版本）
    
    流程：
    1. Figure Unit 构建
    2. Text Claim Agent（文本主张抽取）
    3. Image Evidence Agent（图像证据能力建模）
    4. Context Agent（章节-图像适配性分析）
    5. Judge Agent（裁决）
    6. 结构化输出
    """
    
    def __init__(
        self,
        doc_reader,
        text_model_id="deepseek-v3.2",
        vision_model_id="qwen3-vl-flash",
        text_api_key=None,
        text_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        self.doc_reader = doc_reader
        self.text_model_id = text_model_id
        self.vision_model_id = vision_model_id
        # 如果没有提供text_api_key，使用DASHSCOPE_API_KEY
        if text_api_key is None:
            import os
            text_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.text_client = OpenAI(api_key=text_api_key, base_url=text_base_url)
        self.vision_client = OpenAI(api_key=vision_api_key, base_url=vision_base_url)
    
    # ==================== Step 1: Figure Unit 构建 ====================
    
    def _build_figure_unit(self, img_id: str, image_info: Dict) -> Optional[Dict]:
        """
        构建Figure Unit（核心数据结构）
        
        Args:
            img_id: 图片ID
            image_info: 图片信息字典，包含 page_num, caption, context 等
        
        Returns:
            FigureUnit 字典，如果构建失败返回 None
        """
        page_num = image_info.get('page_num')
        caption = image_info.get('caption', '')
        context = image_info.get('context', '')
        
        # 1. 查找所属章节
        section_info = self.doc_reader.find_section_by_page(page_num)
        if not section_info:
            print(f"[Figure Unit] ✗ 图片 {img_id}: 未找到所属章节 (页码: {page_num})")
            return None
        
        # 2. 提取章节全文（XML格式）
        section_elem = section_info.get('section_elem')
        if section_elem is not None:
            section_xml = ET.tostring(section_elem, encoding='unicode', method='xml')
        else:
            section_xml = ""
        
        # 3. 搜索引用文本
        reference_texts = self._extract_reference_texts(
            img_id, caption, section_info
        )
        
        # 4. 提取上下文（图片前后的段落）
        context_before, context_after = self._extract_context_around_image(
            page_num, section_info
        )
        
        # 5. 获取图片数据
        try:
            base64_img, media_type, _ = self.doc_reader.get_image(img_id)
        except Exception as e:
            print(f"[Figure Unit] ✗ 图片 {img_id}: 获取图片数据失败: {e}")
            return None
        
        figure_unit = {
            "figure_id": img_id,
            "chapter_id": section_info.get('section_id', ''),
            "chapter_title": section_info.get('title', ''),
            "caption": caption,
            "image": {
                "img_id": img_id,
                "base64_img": base64_img,
                "media_type": media_type,
                "page_num": page_num
            },
            "reference_texts": reference_texts,
            "local_context": section_xml,
            "context_before": context_before,
            "context_after": context_after
        }
        
        print(f"[Figure Unit] ✓ 图片 {img_id} 构建完成")
        print(f"  → 章节: {figure_unit['chapter_title']}")
        print(f"  → 引用文本数量: {len(reference_texts)}")
        
        return figure_unit
    
    def _extract_reference_texts(
        self, img_id: str, caption: str, section_info: Dict
    ) -> List[str]:
        """
        提取正文中引用该图片的文本片段
        
        搜索模式：
        - "如图X-X所示"
        - "见图X-X"
        - "Figure X-X"
        - 图片标题中的编号（如 "图4.2"）
        """
        reference_texts = []
        section_elem = section_info.get('section_elem')
        if not section_elem:
            return reference_texts
        
        # 从caption中提取图片编号（如 "图4.2" -> "4.2"）
        figure_num_match = re.search(r'[图圖](\d+\.?\d*)', caption)
        if figure_num_match:
            figure_num = figure_num_match.group(1)
        else:
            # 尝试从img_id中提取
            figure_num_match = re.search(r'(\d+\.?\d*)', img_id)
            figure_num = figure_num_match.group(1) if figure_num_match else None
        
        if not figure_num:
            return reference_texts
        
        # 搜索引用模式
        patterns = [
            rf'[如如]图[圖圖]?\s*{re.escape(figure_num)}[所示]',
            rf'见图[圖圖]?\s*{re.escape(figure_num)}',
            rf'Figure\s+{re.escape(figure_num)}',
            rf'图[圖圖]?\s*{re.escape(figure_num)}[显示示]',
        ]
        
        # 在章节文本中搜索
        section_text = ''.join(section_elem.itertext())
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
    ) -> tuple[str, str]:
        """
        提取图片前后的段落文本作为上下文
        """
        context_before = ""
        context_after = ""
        
        section_elem = section_info.get('section_elem')
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
            p[1] for p in paragraphs 
            if p[0] <= page_int and p[0] >= page_int - 1
        ][-3:]
        context_before = "\n".join(before_paragraphs)
        
        # 提取图片后的段落（最多3段）
        after_paragraphs = [
            p[1] for p in paragraphs 
            if p[0] > page_int and p[0] <= page_int + 1
        ][:3]
        context_after = "\n".join(after_paragraphs)
        
        return context_before, context_after
    
    # ==================== Step 2: Text Claim Agent ====================
    
    def _extract_text_claims(self, figure_unit: Dict) -> List[Dict]:
        """
        抽取文本主张（Text Claim Agent）
        
        职责：从章节文本中抽取可被图像验证的结构化主张
        
        约束：
        - 不能看到图片内容
        - 只能看到文本和caption
        - 必须输出结构化JSON
        """
        from .prompts import text_claim_prompt
        
        # 构建输入
        chapter_text = figure_unit['local_context']
        reference_texts = figure_unit['reference_texts']
        caption = figure_unit['caption']
        
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

请按照要求输出JSON格式的主张列表。"""
            }
        ]
        
        try:
            print(f"[Text Claim Agent] 正在抽取文本主张 (图片: {figure_unit['figure_id']})...")
            response = self.text_client.chat.completions.create(
                model=self.text_model_id,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            claims_data = self._parse_json_from_response(raw_content)
            claims = claims_data.get("claims", [])
            
            print(f"[Text Claim Agent] ✓ 抽取到 {len(claims)} 个文本主张")
            for i, claim in enumerate(claims, 1):
                print(f"  → C{i}: {claim.get('type', 'unknown')} - {claim.get('assertion', '')[:50]}")
            
            return claims
            
        except Exception as e:
            print(f"[Text Claim Agent] ✗ 抽取失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    # ==================== Step 3: Image Evidence Agent ====================
    
    def _analyze_image_evidence(self, figure_unit: Dict) -> Optional[Dict]:
        """
        分析图像证据能力（Image Evidence Agent）
        
        职责：分析图片"客观上"能支持哪些类型的事实
        
        约束：
        - 不能看到文本主张（claims）
        - 只能看到caption和图片本身
        - 必须输出结构化JSON
        """
        from .prompts import image_evidence_prompt
        
        img_data = figure_unit['image']
        caption = figure_unit['caption']
        
        messages = [
            {"role": "system", "content": image_evidence_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"请分析以下图片的证据能力。\n\n图片标题：{caption}\n\n请按照要求输出JSON格式的证据能力描述。"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img_data['media_type']};base64,{img_data['base64_img']}"
                        }
                    }
                ]
            }
        ]
        
        try:
            print(f"[Image Evidence Agent] 正在分析图像证据能力 (图片: {figure_unit['figure_id']})...")
            response = self.vision_client.chat.completions.create(
                model=self.vision_model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            evidence_data = self._parse_json_from_response(raw_content)
            
            print(f"[Image Evidence Agent] ✓ 证据能力分析完成")
            print(f"  → 图片类型: {evidence_data.get('image_type', 'unknown')}")
            capabilities = evidence_data.get('evidence_capabilities', {})
            enabled = [k for k, v in capabilities.items() if v]
            print(f"  → 支持的证据类型: {', '.join(enabled) if enabled else '无'}")
            
            return evidence_data
            
        except Exception as e:
            print(f"[Image Evidence Agent] ✗ 分析失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    # ==================== Step 4: Context Agent ====================
    
    def _analyze_context_fitness(
        self, figure_unit: Dict, image_evidence: Dict
    ) -> Dict:
        """
        分析章节-图像适配性（Context Agent）
        
        职责：判断图片在该章节中的适配性
        """
        from .prompts import context_fitness_prompt
        
        chapter_title = figure_unit['chapter_title']
        chapter_context = figure_unit['local_context']
        image_type = image_evidence.get('image_type', 'unknown')
        
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

请按照要求输出JSON格式的适配性分析结果。"""
            }
        ]
        
        try:
            print(f"[Context Agent] 正在分析章节适配性 (图片: {figure_unit['figure_id']})...")
            response = self.text_client.chat.completions.create(
                model=self.text_model_id,
                messages=messages,
                max_tokens=2048,
                temperature=0.0,
            )
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
                "reason": f"分析失败: {str(e)}"
            }
    
    # ==================== Step 5: Judge Agent ====================
    
    def _judge_consistency(
        self,
        text_claims: List[Dict],
        image_evidence: Dict,
        context_fitness: Dict,
        figure_unit: Dict
    ) -> Dict:
        """
        裁决（Judge Agent）
        
        职责：基于所有结构化信息，做出最终判断
        """
        from .prompts import judge_prompt
        
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

请按照要求输出JSON格式的裁决结果。"""
            }
        ]
        
        try:
            print(f"[Judge Agent] 正在进行最终裁决 (图片: {figure_unit['figure_id']})...")
            response = self.text_client.chat.completions.create(
                model=self.text_model_id,
                messages=messages,
                max_tokens=4096,
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 解析JSON
            verdict = self._parse_json_from_response(raw_content)
            
            print(f"[Judge Agent] ✓ 裁决完成")
            print(f"  → 裁决结果: {verdict.get('verdict', 'unknown')}")
            print(f"  → 支持的主张: {len(verdict.get('supported_claims', []))}")
            print(f"  → 不支持的主张: {len(verdict.get('unsupported_claims', []))}")
            print(f"  → 发现问题数: {len(verdict.get('issues', []))}")
            
            return verdict
            
        except Exception as e:
            print(f"[Judge Agent] ✗ 裁决失败: {e}")
            import traceback
            traceback.print_exc()
            # 返回默认值
            return {
                "figure_id": figure_unit['figure_id'],
                "verdict": "unknown",
                "supported_claims": [],
                "unsupported_claims": [c.get('claim_id', '') for c in text_claims],
                "placement_fitness": context_fitness.get('fitness', 'medium'),
                "issues": [
                    {
                        "type": "analysis_error",
                        "severity": "Medium",
                        "description": f"裁决过程出错: {str(e)}",
                        "suggestion": "请检查输入数据或重新分析"
                    }
                ]
            }
    
    # ==================== Step 6: 格式化输出 ====================
    
    def _format_final_output(
        self, judge_verdict: Dict, figure_unit: Dict, 
        text_claims: List[Dict], image_evidence: Dict, context_fitness: Dict
    ) -> Dict:
        """
        格式化最终输出（与现有系统兼容）
        """
        # 转换issues格式
        issues = []
        for issue in judge_verdict.get('issues', []):
            issues.append({
                "issue_type": "图文一致性",
                "severity": issue.get('severity', 'Medium'),
                "section": figure_unit.get('chapter_title', ''),
                "page": figure_unit['image'].get('page_num'),
                "image_id": figure_unit['figure_id'],
                "quote": issue.get('description', ''),
                "suggestion": issue.get('suggestion', '')
            })
        
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
        
        return {
            "img_id": figure_unit['figure_id'],
            "meta": {
                "page_num": figure_unit['image']['page_num'],
                "caption": figure_unit['caption'],
                "context": figure_unit.get('context_before', '') + "\n" + figure_unit.get('context_after', '')
            },
            "thinking": thinking,
            "parsed": {
                "issues": issues
            },
            "raw": json.dumps(judge_verdict, ensure_ascii=False),
            # 保留中间结果用于调试
            "figure_unit": figure_unit,
            "text_claims": text_claims,
            "image_evidence": image_evidence,
            "context_fitness": context_fitness,
            "judge_verdict": judge_verdict
        }
    
    # ==================== 主流程 ====================
    
    def run_structured_review(
        self,
        img_id: str,
        image_info: Dict,
    ) -> Dict:
        """
        执行完整的结构化图文一致性审查流程
        
        Args:
            img_id: 图片ID
            image_info: 图片信息字典
        
        Returns:
            审查结果字典
        """
        print(f"\n{'='*80}")
        print(f"[结构化审查] 开始处理图片 {img_id}")
        print(f"{'='*80}\n")
        
        # Step 1: 构建Figure Unit
        figure_unit = self._build_figure_unit(img_id, image_info)
        if not figure_unit:
            return {
                "img_id": img_id,
                "error": "Failed to build figure unit",
                "parsed": {"issues": []}
            }
        
        # Step 2: 抽取文本主张
        text_claims = self._extract_text_claims(figure_unit)
        
        # Step 3: 分析图像证据能力
        image_evidence = self._analyze_image_evidence(figure_unit)
        if not image_evidence:
            return {
                "img_id": img_id,
                "error": "Failed to analyze image evidence",
                "parsed": {"issues": []}
            }
        
        # Step 4: 分析章节适配性
        context_fitness = self._analyze_context_fitness(figure_unit, image_evidence)
        
        # Step 5: 裁决
        judge_verdict = self._judge_consistency(
            text_claims, image_evidence, context_fitness, figure_unit
        )
        
        # Step 6: 格式化输出
        final_output = self._format_final_output(
            judge_verdict, figure_unit, text_claims, image_evidence, context_fitness
        )
        
        print(f"\n{'='*80}")
        print(f"[结构化审查] ✓ 图片 {img_id} 处理完成")
        print(f"{'='*80}\n")
        
        return final_output
    
    # ==================== 工具方法 ====================
    
    def _parse_json_from_response(self, raw_content: str) -> Dict:
        """
        从LLM响应中解析JSON
        """
        # 方法1: 尝试从markdown代码块中提取
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 方法2: 找到第一个 { 到最后一个 } 之间的内容
        start = raw_content.find('{')
        end = raw_content.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw_content[start:end+1])
            except json.JSONDecodeError:
                pass
        
        # 方法3: 尝试直接解析
        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            print(f"[JSON解析] 失败，原始内容: {raw_content[:200]}...")
            return {}
