# Agent 解耦重构方案

## 目标
将三个Agent（NormativeAgent、LogicAgent、VisionAgent）的专用功能从DocAgent中解耦出来，DocAgent只保留公共功能。

## 功能归属分析

### DocAgent保留（公共功能）
这些功能被多个Agent共享使用：

1. **LLM调用相关**：
   - `_call_llm()` - 统一的LLM API调用
   - `_extract_thinking()` - 提取thinking部分
   - `client` - OpenAI客户端
   - `model_id`, `temperature`, `max_tokens` - 模型配置

2. **JSON解析**：
   - `_parse_json()` - JSON解析（所有Agent都需要）
   - `_parse_json_from_response()` - 简化版JSON解析
   - `_clean_json_strings()` - JSON字符串清理

3. **文档工具**：
   - `get_outline()` - 获取文档大纲
   - `_extract_plain_text()` - 提取纯文本
   - `_find_page_by_quote()` - 根据引用查找页码
   - `_find_page_by_fuzzy_quote()` - 模糊匹配查找页码

4. **页眉页脚处理**：
   - `_is_header_footer()` - 判断是否为页眉页脚
   - `_filter_header_footer_from_section()` - 过滤页眉页脚
   - `_normalize_header_footer_text()` - 标准化文本
   - `_build_header_footer_index()` - 构建页眉页脚索引

5. **文档读取器**：
   - `doc_reader` - 文档读取器实例（所有Agent都需要访问文档）

### NormativeAgent专用功能（需要移出）
这些功能只在规范性审查中使用：

1. `run_normative_review()` - 规范性审查主逻辑
2. `_needs_vision_verification()` - 判断是否需要视觉验证
3. `verify_with_vision()` - 使用视觉模型验证问题
4. `_run_simple_review()` - 简单审查（可能可以复用DocAgent的公共方法）

**依赖的公共功能**：
- `_call_llm()` - 调用LLM
- `_parse_json()` - 解析JSON
- `_extract_thinking()` - 提取thinking
- `get_outline()` - 获取大纲
- `_extract_plain_text()` - 提取文本
- `doc_reader.get_page_image()` - 获取页面图片

### LogicAgent专用功能（需要移出）
这些功能只在逻辑性审查中使用：

1. `run_hierarchical_logic_review()` - 层次化逻辑审查主逻辑
2. `select_top_sections()` - 选择重要章节
3. `_extract_chapter_facts()` - 提取章节事实
4. `_store_facts()` - 存储事实到fact_store
5. `_detect_fact_conflicts()` - 检测事实冲突
6. `logic_memory` - 逻辑内存（存储章节摘要）
7. `fact_store` - 事实存储（实体、数值、时间、论断）

**依赖的公共功能**：
- `_call_llm()` - 调用LLM
- `_parse_json()` - 解析JSON
- `get_outline()` - 获取大纲
- `_filter_header_footer_from_section()` - 过滤页眉页脚
- `doc_reader.get_section_content()` - 获取章节内容

### VisionAgent专用功能（需要移出）
这些功能只在图文一致性审查中使用：

1. `run_vision_review()` - 图文审查主逻辑
2. `_build_figure_unit()` - 构建Figure Unit
3. `_extract_reference_texts()` - 提取引用文本
4. `_extract_context_around_image()` - 提取图片上下文
5. `_extract_text_claims()` - 抽取文本主张
6. `_analyze_image_evidence()` - 分析图像证据能力
7. `_analyze_context_fitness()` - 分析上下文适配性
8. `_judge_consistency()` - 一致性裁决
9. `_analyze_image_text_consistency_structured()` - 结构化分析流程
10. `_extract_vision_description()` - 提取视觉描述（已废弃但保留）

**依赖的公共功能**：
- `_call_llm()` - 调用LLM
- `_parse_json()` / `_parse_json_from_response()` - 解析JSON
- `doc_reader.find_section_by_page()` - 根据页码查找章节
- `doc_reader.get_image()` - 获取图片
- `doc_reader.image_path_dict` - 图片路径字典

## 重构步骤

### 步骤1：创建BaseAgent基类（可选）
可以创建一个BaseAgent基类，包含公共功能的接口。

### 步骤2：重构NormativeAgent
1. 将`run_normative_review()`移入NormativeAgent
2. 将`_needs_vision_verification()`移入NormativeAgent
3. 将`verify_with_vision()`移入NormativeAgent
4. 通过依赖注入使用DocAgent的公共方法

### 步骤3：重构LogicAgent
1. 将`run_hierarchical_logic_review()`移入LogicAgent
2. 将`select_top_sections()`移入LogicAgent
3. 将`_extract_chapter_facts()`移入LogicAgent
4. 将`_store_facts()`移入LogicAgent
5. 将`_detect_fact_conflicts()`移入LogicAgent
6. 将`logic_memory`和`fact_store`移入LogicAgent作为实例变量

### 步骤4：重构VisionAgent
1. 将`run_vision_review()`移入VisionAgent
2. 将所有figure相关方法移入VisionAgent
3. 通过依赖注入使用DocAgent的公共方法

### 步骤5：精简DocAgent
1. 删除已移出的专用方法
2. 保留公共功能
3. 确保接口清晰

## 新的架构

```
┌─────────────────────────────────────────┐
│         DocAgent (公共功能)              │
│  - _call_llm()                         │
│  - _parse_json()                       │
│  - get_outline()                       │
│  - _extract_plain_text()               │
│  - doc_reader                          │
│  - client                              │
└──────────────┬──────────────────────────┘
               │ 依赖注入
               │
    ┌──────────┼──────────┐
    │          │          │
    ▼          ▼          ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│Normative│ │ Logic   │ │ Vision  │
│ Agent   │ │ Agent   │ │ Agent   │
│         │ │         │ │         │
│自己的   │ │自己的   │ │自己的   │
│业务逻辑 │ │业务逻辑 │ │业务逻辑 │
└─────────┘ └─────────┘ └─────────┘
```

## 注意事项

1. **向后兼容**：确保现有调用代码不需要大幅修改
2. **依赖注入**：各Agent通过构造函数接收DocAgent实例
3. **测试**：重构后需要测试每个Agent的功能是否正常
4. **文档**：更新相关文档说明新的架构
