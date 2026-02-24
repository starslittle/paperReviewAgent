# Prompt 使用时机详解

本文档说明 `agent/prompts.py` 中定义的各 prompt 以及逻辑/视觉 Agent 中的**内联 prompt** 在**何时、被谁、如何**调用。

---

## 一、总览：按模块分类

| 模块 | 使用的 Prompt | 调用时机简述 |
|------|----------------|--------------|
| **NormativeAgent** | `normative_prompt`, `vision_verify_prompt` | 规范性审查（滑动窗口）、规范问题视觉二次验证 |
| **LogicAgent** | `logic_prompt`, `local_chapter_review_prompt`, `local_chapter_review_retry_prompt`, `global_logic_review_prompt`, `chapter_selection_prompt`, `toc_final_suggestion_prompt`, `system_development_structure_check_prompt`, `system_development_abstract_check_prompt`, `cover_title_vision_prompt` | 逻辑审查、章节选择、分章审查、目录检测、总建议、论文类型检测（内联）、事实提取/冲突检测（内联） |
| **VisionAgent** | `argument_role_prompt`, `local_stage_alignment_prompt`, `image_capacity_prompt` | 图文一致性：角色分类、阶段对齐、图像容量评估；修改建议由 `build_suggestion_prompt()` 动态生成 |
| **DocAgent** | `system_prompt`, `actor_prompt_template`, `reviewer_prompt`, `reflection_prompt_template`, `chapter_selection_prompt` | QA 流程：Actor 答题、Reviewer 验证、Reflection 更新记忆；章节选择由 LogicAgent 内部调用 |

**未在代码中引用的 prompt**（仅定义在 `prompts.py` 或仅出现在文档中）：  
`vision_prompt`, `vision_description_prompt`, `text_analysis_prompt`, `text_claim_prompt`, `image_evidence_prompt`, `context_fitness_prompt`, `judge_prompt` —— 当前视觉审查已改为 ARG 流程，这些为旧版/预留。

---

## 二、规范性审查（NormativeAgent）

### 2.1 `normative_prompt`

- **定义位置**：`prompts.py` 第 37 行起。
- **调用位置**：`normative_agent.py` → `run_normative_review()`。
- **何时使用**：  
  运行**规范性审查**时使用。NormativeAgent 在**滑动窗口**下多次调用 `doc_agent._run_simple_review(normative_prompt, ...)`，每次传入「大纲 + 正文片段」；窗口从摘要开始、按 `char_offset`/`char_limit` 滑动，直到 `is_end`。
- **输入**：大纲 XML + 正文片段（`_extract_plain_text_from_abstract` 或 `_extract_plain_text`）。
- **输出**：`<thinking>` + `<json>`，其中 `issues` 为规范性问题列表（格式/结构/编号/引用/页眉页脚/摘要关键词等）。

### 2.2 `vision_verify_prompt`

- **定义位置**：`prompts.py` 第 111 行起。
- **调用位置**：`normative_agent.py` → `_verify_issue_with_vision()`。
- **何时使用**：  
  当规范性审查产出**需要视觉二次验证**的问题时（例如「缺少章节 X.X」）。对每条此类问题，用**当前问题页的页面截图** + 本 prompt 调用**视觉模型**（如 qwen3-vl-flash），判断问题是否真实存在（`is_real`），用于过滤误报。
- **输入**：`issue_description`（问题描述）+ 页面图片。
- **输出**：JSON `{ "is_real": true/false, "reason": "..." }`。

---

## 三、逻辑审查（LogicAgent）

### 3.1 论文类型检测（内联 prompt，非 prompts.py）

- **定义位置**：`logic_agent.py` 第 304–338 行，`detection_prompt` 字符串。
- **何时使用**：  
  在**层次化逻辑审查**开始时，若 `thesis_type == "auto"`，会先用**封面图**识别题目（`cover_title_vision_prompt`），再取摘要和目录，用本 prompt 调用 LLM 判断论文是 **程序开发类(system)** 还是 **算法理论类(algorithm)**。
- **输入**：题目、摘要片段、目录结构。
- **输出**：JSON `{ "type": "system"|"algorithm", "confidence", "reason", "evidence" }`，用于后续选择「目录/摘要/章节」用哪一套 prompt。

### 3.2 `cover_title_vision_prompt`

- **定义位置**：`prompts.py` 第 1463 行起。
- **调用位置**：`logic_agent.py` → `_detect_thesis_type_deep()` 开头。
- **何时使用**：  
  仅在 **thesis_type == "auto"** 时，用**第 1 页（封面）图片**调用视觉模型，从封面中识别**论文题目**，再与摘要、目录一起送给上面的 `detection_prompt` 做类型判断。
- **输入**：封面页图片。
- **输出**：纯文本，即识别出的题目。

### 3.3 `chapter_selection_prompt`

- **定义位置**：`prompts.py` 第 304 行起。
- **调用位置**：`doc_agent.py` → `select_chapters()`；该方法由 **LogicAgent** 在层次化审查中调用。
- **何时使用**：  
  **层次化逻辑审查**的 Map 阶段之前：根据**大纲 XML** 让 LLM 选出「最重要的若干章节」的 `section_id`（如最多 8 个），跳过封面/诚信承诺等，但**必须包含目录**。选出的章节才会进入后续分章审查。
- **输入**：大纲 XML + 最大章节数等说明。
- **输出**：JSON 数组或 `{ "sections": [...] }`，即 `section_id` 列表。

### 3.4 分章审查：三选一 + 可选增强

对**每一个被选中的章节**，LogicAgent 会根据**章节标题**和 **thesis_type** 选择**一个** system prompt，再视情况追加「程序开发类特殊提示」：

| 条件 | 使用的 Prompt | 说明 |
|------|----------------|------|
| 章节标题含「目录」「目 录」或 "contents" | **thesis_type == "system"** → `system_development_structure_check_prompt` | 程序开发类论文的**目录结构**审查（七章结构、绪论 1.1–1.4、需求/设计/实现/测试等） |
| 同上 | **thesis_type != "system"** → `table_of_contents_check_prompt` | **通用**目录结构审查 |
| 章节标题含「摘要」或 "abstract" 且 **thesis_type == "system"** | `system_development_abstract_check_prompt` | 程序开发类**摘要**审查（背景→方案→成果、关键词等） |
| 章节标题含「需求」「设计」「实现」「测试」且 **thesis_type == "system"** | `local_chapter_review_prompt` + **程序开发类特殊提示**（内联追加） | 通用分章审查 + 需求/设计/实现/测试的额外检查点 |
| 以上都不满足 | `local_chapter_review_prompt` | **通用**分章逻辑/语言/连贯性审查 |

- **`local_chapter_review_prompt`**  
  - 定义：`prompts.py` 第 145 行起。  
  - 使用：如上表，作为默认或与「程序开发类特殊提示」拼接后，作为 system prompt；user 为「章节 XML 内容 + 输出要求」。

- **`local_chapter_review_retry_prompt`**  
  - 定义：`prompts.py` 第 227 行起。  
  - 使用：当某章（**非目录章**）的 `logic_skeleton` 不稳定时（`_is_logic_skeleton_stable` 为 False），**重试一次**该章审查，此时 system prompt 为 `local_chapter_review_prompt + "\n\n" + local_chapter_review_retry_prompt`。

- **`system_development_structure_check_prompt`**  
  - 定义：`prompts.py` 第 936 行起。  
  - 使用：仅当**目录章**且 **thesis_type == "system"** 时，作为该章的 system prompt。

- **`table_of_contents_check_prompt`**  
  - 定义：`prompts.py` 第 1283 行起。  
  - 使用：仅当**目录章**且 **thesis_type != "system"** 时，作为该章的 system prompt。

- **`system_development_abstract_check_prompt`**  
  - 定义：`prompts.py` 第 1155 行起。  
  - 使用：仅当**摘要章**且 **thesis_type == "system"** 时，作为该章的 system prompt。

### 3.5 `global_logic_review_prompt`

- **定义位置**：`prompts.py` 第 237 行起，内含占位符 `{global_context}`。
- **调用位置**：`logic_agent.py` → `run_hierarchical_logic_review()` 的 Reduce 阶段。
- **何时使用**：  
  分章审查（Map）全部完成后，将各章的 **local_summary**（及必要元数据）拼成 `global_context`，用本 prompt 做**全局一致性审查**：摘要 vs 结论、方法 vs 实验、章节逻辑流等。
- **输入**：`global_context`（各章摘要等）+ user 固定句「请基于以上各章摘要进行全局逻辑一致性检查。」
- **输出**：`<thinking>` + `<json>`，`issues` 为全局逻辑问题。

### 3.6 `toc_final_suggestion_prompt`

- **定义位置**：`prompts.py` 第 1428 行起。
- **调用位置**：`logic_agent.py` → `_get_toc_final_suggestion()`。
- **何时使用**：  
  在层次化逻辑审查中，若存在**目录结构类问题**（`toc_issues`），在全局审查之后调用：根据**当前目录**和**已发现的目录问题**，让 LLM 生成「总建议」和「修改后的推荐目录」。
- **输入**：当前目录文本 + 目录问题列表；user 要求输出仅含 `summary` 与 `suggested_outline` 的 JSON。
- **输出**：`{ "summary": "...", "suggested_outline": ["1 绪论", "1.1 ...", ...] }`，用于报告中的「目录检测」区块。

### 3.7 `logic_prompt`

- **定义位置**：`prompts.py` 第 317 行起。
- **调用位置**：`logic_agent.py` → `run_logic_review()`。
- **何时使用**：  
  **非层次化**的简单逻辑审查入口（单次、不分章）：直接 `doc_agent._run_simple_review(logic_prompt)`，传入大纲 + 正文片段。当前主流程使用 `run_hierarchical_logic_review()`，因此 `logic_prompt` 多在「仅做一次整体逻辑检查」时使用。
- **输入**：大纲 + 正文片段（与规范性类似的 user 内容）。
- **输出**：`<thinking>` + `<json>`，`issues` 为逻辑/语言/连贯性问题。

### 3.8 事实提取与冲突检测（内联 prompt，logic_agent.py）

- **事实提取**：`logic_agent.py` 第 444 行起，`fact_extraction_prompt` 内联字符串。  
  - **何时使用**：Map 阶段每章审查完成后，对章节内容做**细粒度事实提取**（实体、数值、时间、论断），写入 fact_store，供后续冲突检测使用。
- **冲突检测**：`logic_agent.py` 第 599 行起，内联 `prompt`。  
  - **何时使用**：Reduce 之后，对 fact_store 中同一 key 的多个 value 判断是否**真正冲突**（非别名/可兼容），只输出 `is_conflict` 与 `reason`。

---

## 四、视觉审查（VisionAgent）

视觉审查采用 **ARG 流程**（FigureNode → Role → Stage → Image 容量 → 规则聚合），**不会**对整图做一大段「vision_prompt」描述；而是分步骤用以下 prompt。

### 4.1 `argument_role_prompt`

- **定义位置**：`prompts.py` 第 871 行起。
- **调用位置**：`vision_agent.py` → `_classify_role()`。
- **何时使用**：  
  对**每张图**，在得到其「引用语义作用域」文本后，用本 prompt 让 LLM 判断该图在论证中的**角色**（如 EVIDENCE、METHOD、RESULT 等）及置信度、evidence_sentence。
- **输入**：图片引用语义作用域文本（ref_text）。
- **输出**：JSON，含 `role`, `confidence`, `evidence_sentence` 等，供后续 Stage 对齐与容量评估使用。

### 4.2 `local_stage_alignment_prompt`

- **定义位置**：`prompts.py` 第 890 行起。
- **调用位置**：`vision_agent.py` → `_llm_stage_disambiguation()`。
- **何时使用**：  
  当需要根据**引用句 + 段落上下文**判断该图处于论证的哪个**阶段**（如 EVIDENCE/METHOD/RESULT）时调用，用于和 argument_role 的预期阶段对齐，发现 mismatch。
- **输入**：引用句子 + 局部段落上下文。
- **输出**：JSON，含 `stage` 等，用于 StageAlignment。

### 4.3 `image_capacity_prompt`

- **定义位置**：`prompts.py` 第 908 行起。
- **调用位置**：`vision_agent.py` → `_analyze_image_capacity()`。
- **何时使用**：  
  对**每张图**，在已有 role 与 scope 的前提下，结合**图片本身**（Base64）与 Caption/Scope/Role，判断图片是否具备**足够信息容量**完成其论证角色（sufficient true/false）。
- **输入**：Caption、Scope、Role + 图片。
- **输出**：JSON，含 `sufficient`、`reason` 等，用于后续决策（如优先改图还是改文）和生成修改建议。

### 4.4 修改建议（非 prompts.py）

- **定义位置**：`vision_agent.py` 中 `build_suggestion_prompt(context, decision)`。
- **何时使用**：  
  在已得到 role、stage_alignment、image_capacity、issue 和 modification_target 后，根据**决策结果**和**上下文**动态拼出一段 prompt，再调用 LLM 生成一条**可执行的修改建议**（改图或改文）；若引用句过短，会在该 prompt 后追加「重要提示：当前引用句过短…」。
- **输入**：context（figure_role, expected_stage, actual_stage, strengths, quote 等）+ decision（modification_target, reason 等）。
- **输出**：纯文本修改建议，写入 issue 的 `modification_advice.suggestion` 等。

---

## 五、DocAgent（QA / 工具调用流程）

以下 prompt 用于**基于文档的问答**（Actor–Reviewer–Reflection），不是「审查报告」主流程，但在 `run_actor` / `run_reviewer` / `run_reflection` 被调用时会用到。

### 5.1 `system_prompt`

- **定义位置**：`prompts.py` 第 3 行。
- **调用位置**：`doc_agent.py` → `run_actor()`。
- **何时使用**：  
  作为 **Actor** 的 system prompt，与 `actor_prompt_template` 一起使用，指导「根据文档回答问题」。
- **输入**：无占位符，固定句。
- **输出**：不直接产出；与 user 消息一起驱动多轮工具调用与最终答案。

### 5.2 `actor_prompt_template`

- **定义位置**：`prompts.py` 第 8 行，含 `{document_outline}`, `{question}`, `{memory}`。
- **调用位置**：`doc_agent.py` → `run_actor(question, memory, tools)`。
- **何时使用**：  
  构造 Actor 的**首条 user 消息**：传入大纲、问题和记忆，让 Agent 用工具检索文档并作答。
- **输入**：由 DocAgent 格式化 document_outline、question、memory。
- **输出**：与 system_prompt 一起作为初始 messages，进入 `run_agent()`。

### 5.3 `reviewer_prompt`

- **定义位置**：`prompts.py` 第 26 行。
- **调用位置**：`doc_agent.py` → `run_reviewer()`。
- **何时使用**：  
  在 Actor 给出答案后，**Reviewer** 用工具验证答案并输出最终结果时，作为追加的 user 消息（「请验证答案…」）。
- **输入**：无占位符。
- **输出**：驱动 Reviewer 的多轮调用与 `<final_result>` 提取。

### 5.4 `reflection_prompt_template`

- **定义位置**：`prompts.py` 第 448 行，含 `{memory}`。
- **调用位置**：`doc_agent.py` → `run_reflection()`。
- **何时使用**：  
  在 Reviewer 结束后，用当前 **memory** 更新反思/指南，生成 `updated_guideline`，供后续问答使用。
- **输入**：memory 字符串。
- **输出**：与前面消息一起送入 `run_agent()`，用 `extract_regex` 提取 `<updated_guideline>`。

### 5.5 `chapter_selection_prompt`

- 已在 **3.3** 说明：由 DocAgent 的 `select_chapters()` 使用，而 `select_chapters()` 由 LogicAgent 在层次化审查中调用，故**实际使用场景是逻辑审查的章节选择**。

---

## 六、未在代码中使用的 Prompt（prompts.py 中仅定义）

以下在 `prompts.py` 中有定义，但**当前 agent 代码中未 import 也未使用**：

- **`vision_prompt`**（第 383 行）：文档中曾描述为「视觉审查」的 system prompt；现视觉审查已改为 ARG 多步流程，不再使用单一 vision_prompt。
- **`vision_description_prompt`**、**`text_analysis_prompt`**、**`text_claim_prompt`**、**`image_evidence_prompt`**、**`context_fitness_prompt`**、**`judge_prompt`**：均为旧版或预留的图文一致性相关 prompt，当前 VisionAgent 未引用。

若需启用或复用，需在对应 Agent 中 import 并在相应步骤中传入 LLM。

---

## 七、按「审查流程」串起来的顺序（主报告流程）

1. **规范性**  
   - 滑动窗口每次：`normative_prompt`；  
   - 对部分规范问题做视觉验证：`vision_verify_prompt`。

2. **逻辑（层次化）**  
   - 若 `thesis_type == "auto"`：封面题目 → `cover_title_vision_prompt`；类型判断 → 内联 `detection_prompt`。  
   - 选章：`chapter_selection_prompt`。  
   - 每章审查：根据章节是「目录/摘要/需求|设计|实现|测试」及 thesis_type，在 `local_chapter_review_prompt`、`system_development_structure_check_prompt`、`table_of_contents_check_prompt`、`system_development_abstract_check_prompt` 或「local + 程序开发类特殊提示」中选一；不稳定时重试用 `local_chapter_review_retry_prompt`。  
   - 事实提取：内联 `fact_extraction_prompt`。  
   - 全局审查：`global_logic_review_prompt`。  
   - 若有目录问题：`toc_final_suggestion_prompt`。  
   - 冲突检测：内联冲突判断 prompt。

3. **视觉**  
   - 每张图：`argument_role_prompt` → `local_stage_alignment_prompt`（若需）→ `image_capacity_prompt`；  
   - 生成修改建议：`build_suggestion_prompt()`（vision_agent 内）。

4. **QA 流程（若调用）**  
   - Actor：`system_prompt` + `actor_prompt_template`；  
   - Reviewer：`reviewer_prompt`；  
   - Reflection：`reflection_prompt_template`。

以上即为「什么时候会用什么 prompt」的完整说明；若某 prompt 未出现在本表或上述流程中，即表示当前主报告流程未使用该 prompt。
