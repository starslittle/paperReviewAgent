# 图文一致性审查 Agent 重构方案

## 一、核心设计原则

> **不要让"图像 Agent"和"文本 Agent"各自输出自由文本结论。它们必须被约束在同一个"中间语义空间"里。**

### 问题诊断
- ❌ **之前的问题**：视觉模型和文本模型各自输出自由文本，导致"根本不对齐"
- ✅ **解决方案**：使用结构化输出，所有Agent在同一个语义空间协作

---

## 二、整体架构（串行流程）

```text
Step 0: 文档结构解析（已有）
        ↓
Step 1: Figure Unit 构建（核心，新增）
        ↓
Step 2: Text Claim Agent（文本主张抽取）
        ↓
Step 3: Image Evidence Agent（图像证据能力建模）
        ↓
Step 4: Context Agent（章节-图像适配性分析）
        ↓
Step 5: Judge Agent（裁决）
        ↓
Step 6: 结构化输出（最终结果）
```

**全部串行执行，不并行，确保每一步的输出都是下一步的可靠输入。**

---

## 三、数据结构定义

### 3.1 Figure Unit（核心数据结构）

```python
FigureUnit = {
    "figure_id": str,              # 如 "Figure 4.2"
    "chapter_id": str,             # 章节ID，如 "4.1"
    "chapter_title": str,          # 章节标题，如 "实验结果分析"
    "caption": str,                # 图片标题
    "image": {                     # 图片数据
        "img_id": str,
        "base64_img": str,
        "media_type": str,
        "page_num": int
    },
    "reference_texts": [str],      # 正文中引用该图的文本片段列表
    "local_context": str,          # 章节全文（XML格式）
    "context_before": str,         # 图片前的段落文本
    "context_after": str            # 图片后的段落文本
}
```

### 3.2 Text Claim（文本主张）

```python
TextClaim = {
    "claim_id": str,               # 如 "C1", "C2"
    "type": str,                   # "trend" | "value" | "comparison" | "interpretation" | "causal" | "other"
    "subject": str,                # 主体（如 "F1-score"）
    "condition": str,              # 条件（如 "threshold → 1"）
    "assertion": str,              # 断言（如 "decreases significantly"）
    "source_text": str,            # 来源文本片段
    "verifiable_by_image": bool    # 是否可被图像验证
}
```

### 3.3 Image Evidence Capability（图像证据能力）

```python
ImageEvidenceCapability = {
    "evidence_capabilities": {
        "quantitative_trend": bool,      # 能否展示数量趋势
        "exact_value": bool,              # 能否展示精确数值
        "causal_inference": bool,         # 能否支持因果推断
        "model_explanation": bool,        # 能否解释模型
        "comparison": bool,               # 能否进行对比
        "process_flow": bool              # 能否展示流程
    },
    "detected_elements": [str],          # 检测到的元素列表
    "image_type": str,                   # 图片类型
    "key_visual_features": str           # 关键视觉特征描述
}
```

### 3.4 Context Fitness（章节适配性）

```python
ContextFitness = {
    "chapter_intent": str,               # 章节意图（自动摘要）
    "figure_role": str,                  # 图片在该章节中的角色
    "fitness": str,                      # "high" | "medium" | "low"
    "reason": str                        # 适配性判断理由
}
```

### 3.5 Judge Verdict（裁决结果）

```python
JudgeVerdict = {
    "figure_id": str,
    "verdict": str,                      # "consistent" | "partially_consistent" | "inconsistent"
    "supported_claims": [str],           # 支持的claim_id列表
    "unsupported_claims": [str],         # 不支持的claim_id列表
    "placement_fitness": str,            # "high" | "medium" | "low"
    "issues": [
        {
            "claim_id": str,             # 关联的claim_id（如果有）
            "type": str,                  # "over-interpretation" | "mismatch" | "placement" | "missing_reference"
            "severity": str,              # "High" | "Medium" | "Low"
            "description": str,
            "suggestion": str
        }
    ]
}
```

---

## 四、各步骤详细设计

### Step 1: Figure Unit 构建

**职责**：从文档中提取图片及其上下文，构建结构化分析单元

**输入**：
- 文档结构（XML）
- 图片列表（img_id, page_num, caption等）

**处理逻辑**：
1. 根据 `page_num` 查找图片所属章节
2. 提取章节全文（XML格式）
3. 搜索正文中引用该图片的文本（如"如图X-X所示"）
4. 提取图片前后的段落文本作为上下文

**输出**：`FigureUnit` 对象

**实现位置**：`DocAgent._build_figure_unit(img_id, image_info)`

**关键代码**：
```python
def _build_figure_unit(self, img_id, image_info):
    """构建Figure Unit"""
    # 1. 获取图片基本信息
    page_num = image_info['page_num']
    caption = image_info['caption']
    
    # 2. 查找所属章节
    section_info = self.doc_reader.find_section_by_page(page_num)
    if not section_info:
        return None
    
    # 3. 提取章节全文
    section_xml = ET.tostring(section_info['section_elem'], encoding='unicode')
    
    # 4. 搜索引用文本
    reference_texts = self._extract_reference_texts(img_id, caption, section_info)
    
    # 5. 提取上下文
    context_before, context_after = self._extract_context_around_image(
        page_num, section_info
    )
    
    # 6. 获取图片数据
    base64_img, media_type, _ = self.doc_reader.get_image(img_id)
    
    return {
        "figure_id": img_id,
        "chapter_id": section_info['section_id'],
        "chapter_title": section_info['title'],
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
```

---

### Step 2: Text Claim Agent（文本主张抽取）

**职责**：从章节文本中抽取可被图像验证的结构化主张

**输入**：
- `FigureUnit.local_context`（章节全文）
- `FigureUnit.reference_texts`（引用文本）

**处理逻辑**：
1. 分析章节文本，识别所有论断性陈述
2. 将每个论断转换为结构化主张（claim）
3. 判断每个主张是否可被图像验证

**输出**：`TextClaim[]` 列表

**约束**：
- ❌ **不能**看到图片内容
- ✅ **只能**看到文本和caption
- ✅ **必须**输出结构化JSON

**Prompt设计**：
```python
text_claim_prompt = """
你是一个文本主张抽取专家。你的任务是从章节文本中抽取可被图像验证的结构化主张。

【输入】
- 章节全文
- 图片引用文本列表

【任务】
1. 识别章节中所有论断性陈述
2. 将每个论断转换为结构化主张
3. 判断每个主张是否可被图像验证

【输出格式】
{
  "claims": [
    {
      "claim_id": "C1",
      "type": "trend|value|comparison|interpretation|causal|other",
      "subject": "主体（如：F1-score）",
      "condition": "条件（如：threshold → 1）",
      "assertion": "断言（如：decreases significantly）",
      "source_text": "来源文本片段",
      "verifiable_by_image": true/false
    }
  ]
}

【注意】
- 不是所有主张都必须被图像验证
- 只抽取与图片相关的主张
- 必须输出JSON格式
"""
```

**实现位置**：`DocAgent._extract_text_claims(figure_unit)`

---

### Step 3: Image Evidence Agent（图像证据能力建模）

**职责**：分析图片"客观上"能支持哪些类型的事实

**输入**：
- `FigureUnit.image`（图片数据）
- `FigureUnit.caption`（图片标题）

**处理逻辑**：
1. 分析图片类型（流程图、数据图、示意图等）
2. 检测图片中的关键元素
3. 判断图片能支持哪些类型的证据（趋势、精确值、因果推断等）

**输出**：`ImageEvidenceCapability` 对象

**约束**：
- ❌ **不能**看到文本主张（claims）
- ✅ **只能**看到caption和图片本身
- ✅ **必须**输出结构化JSON

**Prompt设计**：
```python
image_evidence_prompt = """
你是一个图像证据能力分析专家。你的任务是分析图片"客观上"能支持哪些类型的事实。

【输入】
- 图片（base64编码）
- 图片标题（Caption）

【任务】
1. 识别图片类型
2. 检测图片中的关键元素
3. 判断图片能支持哪些类型的证据

【输出格式】
{
  "evidence_capabilities": {
    "quantitative_trend": true/false,
    "exact_value": true/false,
    "causal_inference": true/false,
    "model_explanation": true/false,
    "comparison": true/false,
    "process_flow": true/false
  },
  "detected_elements": ["元素1", "元素2", ...],
  "image_type": "流程图|数据图|示意图|架构图|截图|其他",
  "key_visual_features": "关键视觉特征描述"
}

【注意】
- 这里不做"是否一致"的判断
- 只分析图片的客观能力
- 必须输出JSON格式
"""
```

**实现位置**：`DocAgent._analyze_image_evidence(figure_unit)`

---

### Step 4: Context Agent（章节-图像适配性分析）

**职责**：判断图片在该章节中的适配性

**输入**：
- `FigureUnit.chapter_title`（章节标题）
- `FigureUnit.local_context`（章节全文摘要）
- `ImageEvidenceCapability.image_type`（图片类型）

**处理逻辑**：
1. 分析章节的论证功能（摘要章节意图）
2. 分析图片在该章节中的角色
3. 判断适配性（high/medium/low）

**输出**：`ContextFitness` 对象

**Prompt设计**：
```python
context_fitness_prompt = """
你是一个章节-图像适配性分析专家。你的任务是判断图片在该章节中的适配性。

【输入】
- 章节标题
- 章节内容摘要
- 图片类型

【任务】
1. 分析章节的论证功能（章节意图）
2. 分析图片在该章节中的角色
3. 判断适配性

【输出格式】
{
  "chapter_intent": "章节意图描述",
  "figure_role": "图片在该章节中的角色",
  "fitness": "high|medium|low",
  "reason": "适配性判断理由"
}

【注意】
- 这里不判断"对不对"，只判断"合不合适"
- 必须输出JSON格式
"""
```

**实现位置**：`DocAgent._analyze_context_fitness(figure_unit, image_evidence)`

---

### Step 5: Judge Agent（裁决）

**职责**：基于所有结构化信息，做出最终判断

**输入**：
- `TextClaim[]`（文本主张列表）
- `ImageEvidenceCapability`（图像证据能力）
- `ContextFitness`（章节适配性）

**处理逻辑**：
1. **主张验证**：判断每个主张是否可被该图像支持
   - 检查 `claim.type` 是否匹配 `evidence_capabilities`
   - 检查 `detected_elements` 是否支持该主张
2. **适配性判断**：结合 `ContextFitness` 判断位置是否合适
3. **问题识别**：
   - 过度解读（图只能展示趋势，文本却下了因果结论）
   - 图文不匹配（主张与图像证据能力不符）
   - 位置不合理（适配性低）
   - 缺少引用（reference_texts为空）

**输出**：`JudgeVerdict` 对象

**Prompt设计**：
```python
judge_prompt = """
你是一个图文一致性裁决专家。基于结构化信息，做出最终判断。

【输入】
- 文本主张列表（claims）
- 图像证据能力（evidence_capabilities）
- 章节适配性（context_fitness）

【裁决规则】
1. 主张是否可被该图像支持？
   - 检查 claim.type 是否匹配 evidence_capabilities
   - 检查 detected_elements 是否支持该主张
2. 图像是否真的支持该主张？
   - 基于 detected_elements 判断
3. 主张是否越权？
   - 例如：图只能展示趋势，文本却下了因果结论

【输出格式】
{
  "figure_id": "图片ID",
  "verdict": "consistent|partially_consistent|inconsistent",
  "supported_claims": ["C1", ...],
  "unsupported_claims": ["C2", ...],
  "placement_fitness": "high|medium|low",
  "issues": [
    {
      "claim_id": "C2",
      "type": "over-interpretation|mismatch|placement|missing_reference",
      "severity": "High|Medium|Low",
      "description": "问题描述",
      "suggestion": "改进建议"
    }
  ]
}

【注意】
- 必须基于结构化信息做判断，不是"感觉"
- 必须输出JSON格式
"""
```

**实现位置**：`DocAgent._judge_consistency(text_claims, image_evidence, context_fitness, figure_unit)`

---

### Step 6: 结构化输出

**职责**：将裁决结果转换为最终输出格式

**输入**：`JudgeVerdict`

**输出**：与现有系统兼容的格式

**实现位置**：`DocAgent._format_final_output(judge_verdict)`

---

## 五、实现计划

### 5.1 文件结构

```
agent/
├── doc_agent.py              # 主Agent类（重构）
├── prompts.py                # 新增prompts
└── figure_unit.py            # Figure Unit数据结构（可选，或放在doc_agent.py中）
```

### 5.2 重构步骤

1. **第一步**：实现 `_build_figure_unit()` 方法
2. **第二步**：实现 `_extract_text_claims()` 方法（Text Claim Agent）
3. **第三步**：重构 `_extract_vision_description()` 为 `_analyze_image_evidence()`（Image Evidence Agent）
4. **第四步**：实现 `_analyze_context_fitness()` 方法（Context Agent）
5. **第五步**：实现 `_judge_consistency()` 方法（Judge Agent）
6. **第六步**：重构 `run_vision_review()` 方法，实现串行流程
7. **第七步**：实现 `_format_final_output()` 方法

### 5.3 关键方法签名

```python
class DocAgent:
    def _build_figure_unit(self, img_id, image_info) -> Optional[Dict]:
        """Step 1: 构建Figure Unit"""
        pass
    
    def _extract_text_claims(self, figure_unit: Dict) -> List[Dict]:
        """Step 2: 抽取文本主张"""
        pass
    
    def _analyze_image_evidence(self, figure_unit: Dict) -> Optional[Dict]:
        """Step 3: 分析图像证据能力"""
        pass
    
    def _analyze_context_fitness(self, figure_unit: Dict, image_evidence: Dict) -> Dict:
        """Step 4: 分析章节适配性"""
        pass
    
    def _judge_consistency(
        self, 
        text_claims: List[Dict], 
        image_evidence: Dict, 
        context_fitness: Dict,
        figure_unit: Dict
    ) -> Dict:
        """Step 5: 裁决"""
        pass
    
    def _format_final_output(self, judge_verdict: Dict) -> Dict:
        """Step 6: 格式化最终输出"""
        pass
    
    def run_vision_review_structured(
        self,
        vision_model_id="qwen3-vl-flash",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        """新的串行流程入口"""
        pass
```

---

## 六、与现有系统的兼容性

### 6.1 输出格式兼容

最终输出需要转换为现有格式：

```python
{
    "img_id": "...",
    "meta": {...},
    "thinking": "...",
    "parsed": {
        "issues": [
            {
                "issue_type": "图文一致性",
                "severity": "High|Medium|Low",
                "section": "...",
                "page": ...,
                "image_id": "...",
                "quote": "...",
                "suggestion": "..."
            }
        ]
    },
    "raw": "...",
    "vision_description": {...}  # 保留用于调试
}
```

### 6.2 向后兼容

- 保留原有的 `run_vision_review()` 方法（标记为deprecated）
- 新增 `run_vision_review_structured()` 方法
- 在 `vision_agent.py` 中可以选择使用新方法

---

## 七、测试计划

### 7.1 单元测试

- `_build_figure_unit()`: 测试Figure Unit构建
- `_extract_text_claims()`: 测试文本主张抽取
- `_analyze_image_evidence()`: 测试图像证据能力分析
- `_analyze_context_fitness()`: 测试章节适配性分析
- `_judge_consistency()`: 测试裁决逻辑

### 7.2 集成测试

- 完整串行流程测试
- 输出格式验证
- 与现有系统的集成测试

---

## 八、学术价值

### 8.1 方法论创新

- **结构化约束**：所有Agent在同一个语义空间协作
- **证据能力建模**：图像证据能力与文本主张分离
- **可解释性**：每个步骤的输出都是结构化的，可追溯

### 8.2 论文表述

> **"一种基于证据能力约束的图文一致性审查方法"**

**核心贡献**：
1. 提出了Figure Unit作为图文一致性分析的基础单元
2. 设计了文本主张抽取和图像证据能力建模的分离机制
3. 实现了基于结构化信息的可解释裁决流程

---

## 九、实施优先级

1. **P0（必须）**：Step 1 (Figure Unit构建) + Step 3 (Image Evidence Agent)
2. **P1（重要）**：Step 2 (Text Claim Agent) + Step 5 (Judge Agent)
3. **P2（优化）**：Step 4 (Context Agent) + Step 6 (格式化输出)

---

## 十、预期效果

### 10.1 解决的问题

- ✅ **对齐问题**：所有Agent输出结构化数据，不再有"根本不对齐"
- ✅ **可解释性**：每个判断都有明确的依据
- ✅ **可扩展性**：每个步骤独立，易于优化和扩展

### 10.2 性能考虑

- **串行执行**：虽然比并行慢，但更稳定、更可靠
- **可优化点**：Step 2和Step 3可以并行（因为它们互不依赖）

---

## 十一、下一步行动

1. 确认方案是否符合要求
2. 开始实施：按优先级逐步实现
3. 测试验证：确保每个步骤输出正确
4. 集成测试：确保与现有系统兼容
5. 文档更新：更新README和代码注释
