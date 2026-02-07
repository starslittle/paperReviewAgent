# 结构化图文一致性审查 Agent 使用说明

## 一、概述

这是基于**证据能力约束**的图文一致性审查方案，解决了之前"根本不对齐"的问题。

### 核心特点

1. **结构化输出**：所有Agent在同一个"中间语义空间"里协作
2. **串行执行**：确保每一步的输出都是下一步的可靠输入
3. **可解释性**：每个判断都有明确的依据和可追溯的中间结果

---

## 二、快速开始

### 2.1 基本使用

```python
from agent.figure_consistency_agent import FigureConsistencyAgent
from agent.doc_reader import DocReader

# 初始化
doc_reader = DocReader(data_path="data/bylw-pgy")
agent = FigureConsistencyAgent(
    doc_reader=doc_reader,
    text_model_id="deepseek-chat",
    vision_model_id="qwen3-vl-flash",
    text_api_key="your-deepseek-key",
    vision_api_key="your-dashscope-key",
)

# 处理单张图片
image_info = {
    "page_num": 17,
    "caption": "图4.2 模型在不同阈值下的F1-score变化曲线",
    "context": "前后段落文本..."
}

result = agent.run_structured_review(
    img_id="img_001",
    image_info=image_info
)

# 查看结果
print(f"发现问题数: {len(result['parsed']['issues'])}")
for issue in result['parsed']['issues']:
    print(f"- {issue['severity']}: {issue['suggestion']}")
```

### 2.2 批量处理

```python
# 获取所有图片
images = doc_reader.get_all_images()  # 需要实现这个方法

results = []
for img_id, image_info in images.items():
    result = agent.run_structured_review(img_id, image_info)
    results.append(result)
```

---

## 三、输出结构

### 3.1 完整输出格式

```python
{
    "img_id": "img_001",
    "meta": {
        "page_num": 17,
        "caption": "图4.2 ...",
        "context": "..."
    },
    "thinking": "完整的分析流程和中间结果...",
    "parsed": {
        "issues": [
            {
                "issue_type": "图文一致性",
                "severity": "High|Medium|Low",
                "section": "章节名称",
                "page": 17,
                "image_id": "img_001",
                "quote": "问题描述",
                "suggestion": "改进建议"
            }
        ]
    },
    "raw": "JSON格式的裁决结果",
    # 以下字段用于调试和分析
    "figure_unit": {...},      # Step 1的输出
    "text_claims": [...],       # Step 2的输出
    "image_evidence": {...},    # Step 3的输出
    "context_fitness": {...},   # Step 4的输出
    "judge_verdict": {...}      # Step 5的输出
}
```

### 3.2 中间结果说明

#### Figure Unit (Step 1)
```python
{
    "figure_id": "img_001",
    "chapter_id": "4.1",
    "chapter_title": "实验结果分析",
    "caption": "图4.2 ...",
    "image": {...},
    "reference_texts": ["如图4.2所示，..."],
    "local_context": "<XML格式的章节内容>",
    "context_before": "图片前的段落",
    "context_after": "图片后的段落"
}
```

#### Text Claims (Step 2)
```python
[
    {
        "claim_id": "C1",
        "type": "trend",
        "subject": "F1-score",
        "condition": "threshold → 1",
        "assertion": "decreases significantly",
        "source_text": "原文片段",
        "verifiable_by_image": true
    }
]
```

#### Image Evidence (Step 3)
```python
{
    "evidence_capabilities": {
        "quantitative_trend": true,
        "exact_value": false,
        "causal_inference": false,
        "model_explanation": false,
        "comparison": false,
        "process_flow": false
    },
    "detected_elements": ["line chart", "x-axis: threshold", "y-axis: F1-score"],
    "image_type": "数据图",
    "key_visual_features": "折线图，显示下降趋势"
}
```

#### Context Fitness (Step 4)
```python
{
    "chapter_intent": "analyze experimental performance trends",
    "figure_role": "performance trend visualization",
    "fitness": "high",
    "reason": "图片直接可视化了本章节讨论的实验指标"
}
```

#### Judge Verdict (Step 5)
```python
{
    "figure_id": "img_001",
    "verdict": "partially_consistent",
    "supported_claims": ["C1"],
    "unsupported_claims": ["C2"],
    "placement_fitness": "high",
    "issues": [
        {
            "claim_id": "C2",
            "type": "over-interpretation",
            "severity": "Medium",
            "description": "图片只能展示趋势，但文本却下了因果结论",
            "suggestion": "修改文本，避免从趋势图中得出因果结论"
        }
    ]
}
```

---

## 四、与现有系统集成

### 4.1 在 vision_agent.py 中使用

```python
# agent/vision_agent.py
from agent.figure_consistency_agent import FigureConsistencyAgent

class VisionAgent:
    def __init__(self, agent):
        self.agent = agent
        # 可以选择使用新的结构化Agent
        self.structured_agent = FigureConsistencyAgent(
            doc_reader=agent.doc_reader,
            text_model_id=agent.model_id,
            text_api_key=agent.client.api_key,
            # ... 其他参数
        )
    
    def run(self, ..., use_structured=True):
        if use_structured:
            # 使用新的结构化方法
            return self._run_structured(...)
        else:
            # 使用旧方法（向后兼容）
            return self._run_legacy(...)
```

### 4.2 在 review_runner.py 中添加选项

```python
# review_runner.py
parser.add_argument(
    '--structured',
    action='store_true',
    help='使用结构化图文一致性审查方法（推荐）'
)

# 在main函数中
if args.structured:
    # 使用新的结构化Agent
    structured_agent = FigureConsistencyAgent(...)
    results = structured_agent.run_structured_review(...)
else:
    # 使用旧方法
    vision_agent = VisionAgent(agent)
    results = vision_agent.run(...)
```

---

## 五、调试和分析

### 5.1 查看中间结果

```python
result = agent.run_structured_review(img_id, image_info)

# 查看Figure Unit
print(json.dumps(result['figure_unit'], ensure_ascii=False, indent=2))

# 查看文本主张
for claim in result['text_claims']:
    print(f"{claim['claim_id']}: {claim['assertion']}")

# 查看图像证据能力
print(f"支持的证据类型: {result['image_evidence']['evidence_capabilities']}")

# 查看裁决结果
print(f"裁决: {result['judge_verdict']['verdict']}")
print(f"支持的主张: {result['judge_verdict']['supported_claims']}")
```

### 5.2 日志输出

运行时会输出详细的日志：

```
================================================================================
[结构化审查] 开始处理图片 img_001
================================================================================

[Figure Unit] ✓ 图片 img_001 构建完成
  → 章节: 实验结果分析
  → 引用文本数量: 2

[Text Claim Agent] 正在抽取文本主张 (图片: img_001)...
[Text Claim Agent] ✓ 抽取到 3 个文本主张
  → C1: trend - decreases significantly
  → C2: interpretation - model recall is insufficient
  → C3: value - accuracy is 90%

[Image Evidence Agent] 正在分析图像证据能力 (图片: img_001)...
[Image Evidence Agent] ✓ 证据能力分析完成
  → 图片类型: 数据图
  → 支持的证据类型: quantitative_trend

[Context Agent] 正在分析章节适配性 (图片: img_001)...
[Context Agent] ✓ 适配性分析完成
  → 适配性: high
  → 图片角色: performance trend visualization

[Judge Agent] 正在进行最终裁决 (图片: img_001)...
[Judge Agent] ✓ 裁决完成
  → 裁决结果: partially_consistent
  → 支持的主张: 1
  → 不支持的主张: 2
  → 发现问题数: 2

================================================================================
[结构化审查] ✓ 图片 img_001 处理完成
================================================================================
```

---

## 六、性能考虑

### 6.1 执行时间

- **单张图片**：约 15-30 秒（取决于API响应速度）
- **串行执行**：虽然比并行慢，但更稳定、更可靠

### 6.2 优化建议

1. **缓存中间结果**：可以缓存 `image_evidence`（图片证据能力不依赖文本）
2. **批量处理**：虽然串行，但可以批量处理多张图片
3. **错误重试**：对API调用失败的情况进行重试

---

## 七、常见问题

### Q1: 为什么不用并行？

**A**: 串行执行确保每一步的输出都是下一步的可靠输入。虽然慢一些，但更稳定、更可靠。如果确实需要并行，可以考虑：
- Step 2 (Text Claims) 和 Step 3 (Image Evidence) 可以并行（它们互不依赖）
- 但 Step 5 (Judge) 必须等待前面所有步骤完成

### Q2: 如何判断图片是否被引用？

**A**: 系统会自动搜索正文中的引用模式：
- "如图X-X所示"
- "见图X-X"
- "Figure X-X"

如果 `reference_texts` 为空，Judge Agent 会标记为 `missing_reference` 问题。

### Q3: 如果图片没有对应的文本主张怎么办？

**A**: 如果 `text_claims` 为空，Judge Agent 会：
- `verdict`: "consistent"（因为没有主张需要验证）
- `issues`: 可能包含 `missing_reference` 问题（如果图片未被引用）

### Q4: 如何自定义裁决规则？

**A**: 修改 `judge_prompt` 中的裁决规则部分，或者在 `_judge_consistency()` 方法中添加自定义逻辑。

---

## 八、下一步

1. **测试验证**：使用实际论文数据测试
2. **性能优化**：考虑部分步骤并行
3. **扩展功能**：添加更多问题类型和诊断信息
4. **集成到主系统**：替换现有的图文一致性审查方法

---

## 九、参考文献

- 重构方案文档：`REFACTOR_PLAN.md`
- 核心实现：`agent/figure_consistency_agent.py`
- Prompts定义：`agent/prompts.py`
