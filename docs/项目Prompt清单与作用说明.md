# 项目 Prompt 清单与作用说明

本文档列出 `agent/prompts.py` 及逻辑/视觉 Agent 内联中出现的**全部 prompt**，并简要说明各自**作用**与**是否被当前流程使用**。

---

## 一、规范性审查（NormativeAgent）

| Prompt 名称 | 定义位置 | 作用 | 是否使用 |
|-------------|----------|------|----------|
| **normative_prompt** | prompts.py 第 37 行 | 扮演“格式规范”审查员，只检查版式/结构/编号/引用/页眉页脚/摘要关键词等，输出 `<thinking>` + `<json>` 的规范性问题列表（issue_type 固定为「规范性」）。与 LogicAgent 边界：不评价内容、逻辑、语言。 | ✅ 使用：NormativeAgent 滑动窗口审查 |
| **vision_verify_prompt** | prompts.py 第 111 行 | 用**页面截图**判断“格式问题”是否真实存在（如“缺少章节 X.X”是否为 PDF 解析误报），输出 `is_real`、`reason`，用于过滤误报。 | ✅ 使用：NormativeAgent 对部分规范问题的视觉二次验证 |

---

## 二、逻辑审查（LogicAgent）

| Prompt 名称 | 定义位置 | 作用 | 是否使用 |
|-------------|----------|------|----------|
| **local_chapter_review_prompt** | prompts.py 第 145 行 | 对**单章内容**做微观逻辑审查：论证跳跃/矛盾、语言学术性、段落衔接、**段落/小节内语义重复**；摘要章做背景→方案→成果与关键词检查；输出 local_summary、logic_skeleton、stability_check、issues（逻辑性/语言/连贯性/语义重复）。 | ✅ 使用：分章审查默认 prompt，或与「程序开发类特殊提示」拼接 |
| **local_chapter_review_retry_prompt** | prompts.py 第 238 行 | 当某章 logic_skeleton 不稳定时**重试**用：强调必须输出 logic_skeleton、core_claims、stability_check、issues，不得空泛表述。 | ✅ 使用：与 local_chapter_review_prompt 拼接后重试 |
| **global_logic_review_prompt** | prompts.py 第 248 行 | 基于各章**逻辑骨架**做**全局一致性**审查：摘要—正文—结论闭环、引言问题是否解决、方法—实验—结果因果链、章节功能错位、结构断层或重复；输出 2～6 条全局问题，suggestion 需含原因+应改位置+示例修改文本；指导型语气。 | ✅ 使用：LogicAgent Reduce 阶段 |
| **chapter_selection_prompt** | prompts.py 第 334 行 | 从 XML 大纲中选出**最重要的大章节 section_id**（最多 8 个），跳过封面/诚信声明，必须包含目录；输出 JSON 数组如 `["5","7","8","9"]`。 | ✅ 使用：DocAgent.select_chapters()，由 LogicAgent 层次化审查调用 |
| **logic_prompt** | prompts.py 第 347 行 | **单次、不分章**的整体逻辑审查：全局一致性、论证与论据、语言学术性；输出 `<thinking>` + JSON issues；指导型语气、建议含示例修改。 | ✅ 使用：LogicAgent.run_logic_review()（非层次化入口） |
| **system_development_structure_check_prompt** | prompts.py 第 993 行 | **程序开发类论文**的**目录结构**审查：七章结构、绪论 1.1～1.4、需求/设计/实现/测试等必需小节；输出目录结构类 issues（issue_type 为「目录结构」）。 | ✅ 使用：当章节为「目录」且 thesis_type==system 时 |
| **system_development_abstract_check_prompt** | prompts.py 第 1212 行 | **程序开发类论文**的**摘要**审查：背景→方案→成果、关键词数量与格式；输出逻辑/语言/连贯类 issues。 | ✅ 使用：当章节为「摘要」且 thesis_type==system 时 |
| **table_of_contents_check_prompt** | prompts.py 第 1340 行 | **通用**目录结构审查（非程序开发类）：检查目录是否缺少必要章节、编号是否连续等；输出目录结构类 issues。 | ✅ 使用：当章节为「目录」且 thesis_type!=system 时 |
| **toc_final_suggestion_prompt** | prompts.py 第 1485 行 | 根据**当前目录**和**已发现的目录问题**，输出**总建议**（summary）和**修改后的推荐目录**（suggested_outline）；要求编号统一、包含所有子章节。 | ✅ 使用：LogicAgent._get_toc_final_suggestion() |
| **cover_title_vision_prompt** | prompts.py 第 1520 行 | 根据**封面图片**仅识别**论文题目**，不输出学院/姓名/日期等；只输出题目正文一行。 | ✅ 使用：LogicAgent 论文类型自动检测时（thesis_type==auto） |
| **论文类型检测 prompt（内联）** | logic_agent.py 第 304～338 行 | 根据题目、摘要、目录判断论文是「程序开发类(system)」还是「算法理论类(algorithm)」，输出 type、confidence、reason、evidence。 | ✅ 使用：thesis_type==auto 时 |
| **事实提取 prompt（内联）** | logic_agent.py 第 444 行起 | 从章节内容中提取**细粒度事实**（实体、数值、时间、论断），供跨章节冲突检测使用。 | ✅ 使用：Map 阶段每章审查后 |
| **冲突检测 prompt（内联）** | logic_agent.py 第 599 行起 | 判断同一实体键的多个表述是否**真正冲突**（非别名/可兼容），输出 is_conflict、reason。 | ✅ 使用：Reduce 之后的 fact conflict 检测 |

---

## 三、视觉审查（VisionAgent）

| Prompt 名称 | 定义位置 | 作用 | 是否使用 |
|-------------|----------|------|----------|
| **argument_role_prompt** | prompts.py 第 928 行 | 仅根据「明确引用图片的句子」判断图片在论证中的**角色**（PROBLEM/METHOD_REFERENCE/RESULT_CLAIM 等），输出 role、confidence、evidence_sentence。 | ✅ 使用：VisionAgent 每张图的角色分类 |
| **local_stage_alignment_prompt** | prompts.py 第 947 行 | 根据引用句+局部段落判断该图引用所在的**论证阶段**（METHOD/EVIDENCE/INTERPRETATION），输出 stage、confidence。 | ✅ 使用：VisionAgent 阶段对齐 |
| **image_capacity_prompt** | prompts.py 第 965 行 | 判断图片是否具备完成其**论证角色**所需的**信息容量**（sufficient true/false），输出 sufficient、reason 等。 | ✅ 使用：VisionAgent 每张图的容量评估 |
| **build_suggestion_prompt（函数）** | vision_agent.py 第 189 行 | 根据 role、stage、decision、context **动态拼接**一条「修改建议」的 prompt，再调用 LLM 生成可执行的改图/改文建议。 | ✅ 使用：VisionAgent 生成 modification_advice |

---

## 四、DocAgent（QA / 工具调用流程）

| Prompt 名称 | 定义位置 | 作用 | 是否使用 |
|-------------|----------|------|----------|
| **system_prompt** | prompts.py 第 3 行 | 定义 Actor 身份：基于文档内容回答问题的研究助手。 | ✅ 使用：run_actor() 的 system 消息 |
| **actor_prompt_template** | prompts.py 第 8 行 | 构造 Actor 首条 user 消息：大纲 + 问题 + 记忆；要求用工具检索、在 `<quote>` 中给出引用、在 `<final_result>` 中给出最终答案；支持 OCR 误差提示。 | ✅ 使用：run_actor(question, memory) |
| **reviewer_prompt** | prompts.py 第 26 行 | 要求 Reviewer 用工具验证答案，并在 `<final_result>` 中返回最终答案。 | ✅ 使用：run_reviewer() |
| **reflection_prompt_template** | prompts.py 第 505 行 | 根据当前 memory（guideline）更新反思/指南，在 `<updated_guideline>` 中输出，且与原文最多差一句。 | ✅ 使用：run_reflection() |

---

## 五、当前未在代码中使用的 Prompt（仅定义在 prompts.py）

| Prompt 名称 | 定义位置 | 作用（按定义内容） | 使用情况 |
|-------------|----------|--------------------|----------|
| **vision_prompt** | prompts.py 第 440 行 | 本科生毕业论文**视觉审查**：内容一致性、图题规范性、图表元素完整性、学术规范性；输出 `<thinking>` + JSON 图文一致性问题。 | ❌ 未使用：视觉审查已改为 ARG 多步流程（argument_role → stage → image_capacity → 规则聚合） |
| **vision_description_prompt** | prompts.py 第 613 行 | 描述图片内容（用于旧版图文一致性流程）。 | ❌ 未使用 |
| **text_analysis_prompt** | prompts.py 第 648 行 | 从正文中提取与图片相关的主张（旧版）。 | ❌ 未使用 |
| **text_claim_prompt** | prompts.py 第 737 行 | 文本主张相关（旧版）。 | ❌ 未使用 |
| **image_evidence_prompt** | prompts.py 第 782 行 | 图像证据相关（旧版）。 | ❌ 未使用 |
| **context_fitness_prompt** | prompts.py 第 834 行 | 上下文适配判断（旧版）。 | ❌ 未使用 |
| **judge_prompt** | prompts.py 第 867 行 | 图文一致性裁决（旧版）；基于结构化信息输出 issues。 | ❌ 未使用 |

---

## 六、按功能分类汇总

- **规范性**：normative_prompt、vision_verify_prompt  
- **逻辑—分章**：local_chapter_review_prompt、local_chapter_review_retry_prompt、system_development_structure_check_prompt、system_development_abstract_check_prompt、table_of_contents_check_prompt  
- **逻辑—全局与目录**：global_logic_review_prompt、chapter_selection_prompt、toc_final_suggestion_prompt、logic_prompt  
- **逻辑—辅助**：cover_title_vision_prompt、论文类型检测（内联）、事实提取（内联）、冲突检测（内联）  
- **视觉**：argument_role_prompt、local_stage_alignment_prompt、image_capacity_prompt、build_suggestion_prompt（vision_agent 内）  
- **QA 流程**：system_prompt、actor_prompt_template、reviewer_prompt、reflection_prompt_template  
- **未使用**：vision_prompt、vision_description_prompt、text_analysis_prompt、text_claim_prompt、image_evidence_prompt、context_fitness_prompt、judge_prompt  

以上即为项目中**所有 prompt 的清单与作用**；若需启用未使用的 prompt，需在对应 Agent 中 import 并在相应步骤中传入 LLM。
