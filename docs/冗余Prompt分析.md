# 冗余 Prompt 分析

本文档基于当前 `agent/prompts.py` 及各处引用，分析**语义重复、规则重叠、可合并或可抽公共部分**的 prompt，便于后续精简与维护。

---

## 一、结论概览

| 类型 | 说明 |
|------|------|
| **未使用的 prompt** | 无。`prompts.py` 中当前所有 prompt 均有被引用。 |
| **规则/文案重复** | 逻辑审查三件套、目录类审查存在明显重复，适合抽公共块或合并。 |
| **职责重叠** | 目录审查上，`table_of_contents_check_prompt` 与 `system_development_structure_check_prompt` 在「程序开发类」部分重叠，易产生重复问题表述。 |

---

## 二、引用关系速查

| Prompt | 定义位置 | 引用位置 | 作用 |
|--------|----------|----------|------|
| system_prompt | prompts.py | doc_agent, logic_agent | 文档问答系统角色；逻辑审查时作 system |
| actor_prompt_template | prompts.py | doc_agent | 主问答流程模板 |
| reviewer_prompt | prompts.py | doc_agent | 验证答案阶段 |
| reflection_prompt_template | prompts.py | doc_agent | 反思/guideline 更新 |
| chapter_selection_prompt | prompts.py | doc_agent | 选章节 section_id |
| normative_prompt | prompts.py | normative_agent | 规范性审查 |
| vision_verify_prompt | prompts.py | normative_agent | 截图验证某条规范问题是否误报 |
| local_chapter_review_prompt | prompts.py | logic_agent | 分章微观逻辑 + 骨架 |
| local_chapter_review_retry_prompt | prompts.py | logic_agent | 分章审查失败时重试 |
| global_logic_review_prompt | prompts.py | logic_agent | 全局逻辑（摘要-正文-结论闭环等） |
| logic_prompt | prompts.py | logic_agent | 简单逻辑审查（run_logic_review） |
| table_of_contents_check_prompt | prompts.py | logic_agent | 通用目录结构审查 |
| system_development_structure_check_prompt | prompts.py | logic_agent | 程序开发类七章目录结构 |
| system_development_abstract_check_prompt | prompts.py | logic_agent | 程序开发类摘要 |
| toc_final_suggestion_prompt | prompts.py | logic_agent | 目录总建议 + 推荐目录 |
| cover_title_vision_prompt | prompts.py | logic_agent | 封面题目识别 |
| argument_role_prompt | prompts.py | vision_agent | 图片论证角色 |
| local_stage_alignment_prompt | prompts.py | vision_agent | 论证阶段 METHOD/EVIDENCE/INTERPRETATION |
| image_capacity_prompt | prompts.py | vision_agent | 图像信息容量是否足够 |

以上均在用，**无完全未使用的 prompt**。

---

## 三、冗余分析

### 3.1 逻辑审查三件套（规则与文案重复）

涉及：

- **logic_prompt**：简单逻辑审查（run_logic_review 单次调用）
- **local_chapter_review_prompt**：分章微观逻辑 + 骨架摘要
- **global_logic_review_prompt**：全局结构逻辑（摘要-正文-结论闭环、方法-实验-结果等）

重复内容大致包括：

- 输出格式：`<thinking>` + `<json>`，且仅此两块
- JSON 结构：`issues` 数组，字段 `issue_type / severity / section / page / quote / suggestion`
- **suggestion 写作规则**：三部分（① 原因 ② 修改位置 ③ 示例改写）、禁止抽象/裁决式表述
- **严重程度**：High/Medium/Low 的判定标准（摘要结论不符、论证跳跃、口语化等）
- **风格**：指导型语气、可执行建议、本科论文表达规范

建议：

- 将「输出格式 + suggestion 规则 + 严重程度 + 风格」抽成**公共说明块**（如 `_LOGIC_OUTPUT_RULES`），三个 prompt 各自保留**职责与范围**描述，末尾引用该块，减少维护成本与不一致。

---

### 3.2 目录类审查（程序开发类规则重复）

涉及：

- **table_of_contents_check_prompt**：通用目录审查，内含「程序开发类论文特殊检查」一段（绪论 1.1–1.4、需求/设计/实现/测试、数据库设计等）
- **system_development_structure_check_prompt**：仅针对程序开发类，详细规定七章及每章必需小节

现状：

- 对**程序开发类**论文，目录章节会按 `thesis_type == "system"` 选用 `system_development_structure_check_prompt`，**不会**用 `table_of_contents_check_prompt` 的目录段（见 logic_agent 1148–1161 行）。
- 但 `table_of_contents_check_prompt` 里仍保留了一整段与程序开发类高度重合的规则（绪论四小节、需求/设计/实现/测试、数据库设计、章节顺序等），和 `system_development_structure_check_prompt` 形成**文案与规则重复**。

建议：

- **方案 A**：在 `table_of_contents_check_prompt` 中删除或大幅压缩「程序开发类特殊检查」，改为一句说明：「程序开发类论文的目录结构由专门的七章结构审查负责，此处不重复列出。」这样程序开发类规则只维护在 `system_development_structure_check_prompt` 一处。
- **方案 B**：若希望通用目录 prompt 也能对程序开发类做轻量检查，则把「程序开发类」规则抽成公共常量，`table_of_contents_check_prompt` 与 `system_development_structure_check_prompt` 共同引用，避免两处描述不一致。

---

### 3.3 程序开发类「分章审查」的重复提示

在 logic_agent 中，对需求/设计/实现/测试等章节会使用：

```text
local_chapter_review_prompt + "\n\n" + 【程序开发类论文特殊提示】
```

该段内联提示（约 1179–1185 行）再次列举：需求分析要含可行性/用例图/流程图、系统设计要含架构图/数据库设计、实现要含代码与截图、测试要含测试用例表、技术栈一致等。这些与以下内容重叠：

- **system_development_structure_check_prompt**（目录结构）
- **system_development_abstract_check_prompt**（摘要）

建议：

- 将「程序开发类论文特殊提示」抽成 `prompts.py` 中的常量（如 `SYSTEM_DEV_CHAPTER_HINTS`），供 logic_agent 拼接使用；若将来与目录/摘要的规则统一成一份「程序开发类规范」，可进一步共用，减少三处描述不一致。

---

### 3.4 视觉相关（无明显冗余）

- **argument_role_prompt**（角色：PROBLEM / METHOD_REFERENCE / RESULT_CLAIM / …）
- **local_stage_alignment_prompt**（阶段：METHOD / EVIDENCE / INTERPRETATION）
- **image_capacity_prompt**（信息容量是否足够）

三者输入/输出与用途不同，仅在同一流水线中顺序使用，**不视为冗余**。若未来要合并「角色」与「阶段」为单一分类，再考虑合并 prompt。

---

### 3.5 其他

- **normative_prompt** 与 **vision_verify_prompt**：一个产出规范性 issues，一个用截图验证单条是否误报，职责清晰，不冗余。
- **toc_final_suggestion_prompt** 与 **table_of_contents_check_prompt**：前者做「总建议 + 推荐目录」，后者做「目录问题列表」，互补，不冗余。
- **logic_prompt** 与 **global_logic_review_prompt**：前者用于简单单次逻辑审查，后者用于带上下文的全局逻辑审查，调用场景不同；二者在规则和文案上可与 `local_chapter_review_prompt` 一起纳入「逻辑审查公共规则」统一维护（见 3.1）。

---

## 四、可选落地顺序

1. **低风险**：在 `prompts.py` 中抽「逻辑审查公共输出规则」常量，让 logic_prompt、local_chapter_review_prompt、global_logic_review_prompt 引用，减少三处复制。
2. **中风险**：在 `table_of_contents_check_prompt` 中删减或收缩「程序开发类特殊检查」，避免与 `system_development_structure_check_prompt` 重复（需确认没有其他路径依赖这段描述）。
3. **低风险**：将 logic_agent 中的「程序开发类论文特殊提示」挪到 `prompts.py` 为常量，便于与目录/摘要规范统一表述。

---

## 五、未在 prompts.py 中的 prompt

- **logic_agent** 内有一处内联 **fact_extraction_prompt**（事实提取，用于跨章节一致性），未在 `prompts.py` 中定义。若希望所有 prompt 集中管理，可将其迁入 `prompts.py` 并在此表中补充说明；不作为「冗余」处理，仅为集中化管理建议。
