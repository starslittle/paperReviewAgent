# 已不使用的 Prompt 分析

基于对 `agent/` 下各模块 **import 与引用** 的检索，以下 prompt 在 **当前代码中未被任何 Agent 引用**，属于**已不使用**的定义。

---

## 一、未使用的 Prompt 列表（共 7 个）

| 名称 | 定义位置（prompts.py） | 原设计用途简述 |
|------|------------------------|----------------|
| **vision_prompt** | 第 440 行 | 本科生毕业论文**整图视觉审查**：内容一致性、图题规范性、图表元素完整性、学术规范性；输出 `<thinking>` + JSON 图文一致性问题。 |
| **vision_description_prompt** | 第 613 行 | 描述图片内容（旧版图文一致性流程中的“图片描述”步骤）。 |
| **text_analysis_prompt** | 第 648 行 | 从正文中提取与图片相关的**文本主张**（旧版“文字分析”步骤）。 |
| **text_claim_prompt** | 第 737 行 | 文本主张相关判断（旧版）。 |
| **image_evidence_prompt** | 第 782 行 | 图像证据相关判断（旧版）。 |
| **context_fitness_prompt** | 第 834 行 | 上下文与图片/主张的**适配度**判断（旧版）。 |
| **judge_prompt** | 第 867 行 | 基于结构化信息做**图文一致性裁决**，输出 issues（旧版“裁决”步骤）。 |

---

## 二、为何未使用

- **视觉审查流程已改为 ARG 多步流程**：  
  当前 VisionAgent 使用 **argument_role_prompt** → **local_stage_alignment_prompt** → **image_capacity_prompt**，再配合规则与 **build_suggestion_prompt** 生成修改建议，不再对整图做一段 `vision_prompt` 的“描述+裁决”。
- **vision_description / text_analysis / text_claim / image_evidence / context_fitness / judge** 属于旧版“描述→主张→证据→适配→裁决”流水线中的步骤；该流水线已被 ARG（角色→阶段→容量→规则聚合）替代，因此这 6 个 prompt 从未被 VisionAgent 或其它模块 import。

---

## 三、检索依据

- **已使用的 prompt**（在以下文件中被 `from .prompts import ...` 或直接引用）：  
  - **normative_agent.py**：normative_prompt, vision_verify_prompt  
  - **logic_agent.py**：global_logic_review_prompt, local_chapter_review_prompt, local_chapter_review_retry_prompt, logic_prompt, system_development_structure_check_prompt, system_development_abstract_check_prompt, table_of_contents_check_prompt, toc_final_suggestion_prompt, cover_title_vision_prompt  
  - **vision_agent.py**：argument_role_prompt, image_capacity_prompt, local_stage_alignment_prompt  
  - **doc_agent.py**：actor_prompt_template, chapter_selection_prompt, reflection_prompt_template, reviewer_prompt, system_prompt, available_tools  

- **未在上述任何文件中出现**的 prompt 变量即判定为**未使用**，结果即上表 7 个。

---

## 四、建议

1. **保留不动**：若考虑以后做“旧版视觉流程”对比实验或复用其中文案，可保留并加注释注明“已废弃，当前视觉审查使用 ARG 流程”。
2. **移至废弃区**：在 `prompts.py` 末尾或单独文件（如 `prompts_deprecated.py`）中集中放置，并注明“Deprecated: 视觉审查已改为 ARG 流程，以下 prompt 未被引用”。
3. **删除**：若确定不再需要，可直接删除上述 7 段定义以减轻维护成本；删除前建议做一次全文搜索，确认无其它脚本或文档引用其名称。

如需，我可以按你选的方案（注释 / 移动 / 删除）给出具体修改示例（如 diff 或步骤说明）。
