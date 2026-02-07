## 代码链路重构设计文档（不改链路）

目标：在**不改变现有执行顺序**与**不修改功能行为**的前提下，明确各步骤职责、输入输出、语义真源与数据契约，形成可维护、可迭代的结构化说明。

---

## 1. 范围与约束

- **不改链路**：现有脚本与调用顺序保持一致。
- **不改行为**：不调整算法与判定逻辑，仅做结构化定义与文档化。
- **语义真源**：以 MinerU 的 `content_list.json` 中 `type` 字段为唯一语义来源。
- **产出**：结构化设计文档 + 统一字段映射与数据契约。

---

## 2. 现有链路全景

### 2.1 Step A：Extract (MinerU)

**输入**：PDF/原始文件  
**输出目录**：`preprocess/extract_output/MinerU/<doc_id>/`

**核心文件**

- `*_content_list.json`（语义真源）
- `layout.json`（结构补充）
- `images/`（图像文件）

**语义真源规则（必须遵守）**

- `"type": "discarded"` → 页眉/页脚
- `"type": "image"` → 图
- `"type": "table"` → 表
- 其它文本类型 → 正文/段落

---

### 2.2 Step B：Normalize & Flatten（`preprocess/2_process_extracted_data.py`）

**输入**：`*_content_list.json` + `layout.json`  
**输出**：`processed_output/.../data.pkl` + `data.csv` + `figures/`

**职责**

- 结构扁平化（输出统一 DataFrame）
- 标题层级纠正（使用 `text_level`）
- 图/表/文本统一为 `style` 与 `para_text`
- 将 `images/` 复制到 `figures/`，统一资源访问入口

**关键契约**

- `data.pkl` 是后续步骤唯一结构化入口
- `figures/` 是图像与表格图的统一资源目录

---

### 2.3 Step C：Generate Page Images（独立步骤）

**脚本**：`preprocess/3_make_page_images.py`  
**输入**：原始 PDF  
**输出**：`page_images/page_XXXX.png`

**定位**

- **独立媒体步骤**，不参与语义判定
- 仅用于页面图片展示/可视化或后续视觉模型输入

---

### 2.4 Step D：Build DocIR（`preprocess/doc_ir_builder.py`）

**输入**：`processed_output/.../data.pkl`  
**输出**：DocIR + XML outline

**职责**

- 构建层级结构（Section / Paragraph / Image / CSV_Table）
- 基于 `style` 与 `para_text` 进行结构拼装

**约束**

- 不再进行语义判断（语义已在 Step A/B 固化）
- 图/表图片路径直接使用 `para_text.image_path`

---

### 2.5 Step E：Read & Query（`agent/doc_reader.py`）

**输入**：DocIR + XML  
**输出**：检索/章节/图片/表格查询

**职责**

- 按 `image_path` / `table_image_path` 读取图片
- 不做语义判定，仅消费既有结构

---

## 3. 统一数据契约（核心字段映射）

### 3.1 MinerU → 语义映射

| MinerU type                          | 语义分类  | 目标 style |
| ------------------------------------ | --------- | ---------- |
| discarded                            | 页眉/页脚 | Discarded  |
| image                                | 图        | Image      |
| table                                | 表        | Table      |
| text/paragraph                       | 正文      | Normal     |
| caption/figure_caption/table_caption | 图表说明  | Caption    |

### 3.2 data.pkl 行结构约定

- `style`: `"Heading N" | "Normal" | "Image" | "Table" | "Caption" | "Discarded" | ...`
- `para_text`:
  - **Image**：`{ "path": <image_file>, "alt_text": ... }`
  - **Table**：`{ "content": <table_text>, "alt_text": ..., "image_path": "figures/xxx" }`
  - **Normal/Heading/Caption**：文本字符串

---

## 4. 页眉页脚处理重构（不改代码）

**统一策略**

- 页眉页脚来源：`content_list.json` 中 `type=discarded`
- 不再依赖“文本长度/正则”猜测页眉页脚
- Step B 输出中保留页眉页脚标记（逻辑层面约定）

**建议落地方式（文档约束，不改代码）**

- 在数据使用方侧，若发现 `discarded` 元素则从正文索引中剔除
- 不再二次启发式判断页眉页脚

---

## 5. 图片与表格图路径策略

**原则**

- 图/表图片路径以 `figures/` 为统一入口
- `para_text.image_path` 保留完整相对路径（如 `figures/xxx.jpg`）
- 不再生成裁剪表格图（裁剪已删除）

**目录关系**

- `extract_output/.../images/` → 复制到 → `processed_output/.../figures/`

---

## 6. 责任边界（不变但更清晰）

| 步骤               | 责任              | 不做的事       |
| ------------------ | ----------------- | -------------- |
| Extract            | 语义与原始结构    | 不做标题修正   |
| 2_process          | 扁平化 & 标题修正 | 不做语义判断   |
| 3_make_page_images | 页面图生成        | 不参与语义链路 |
| DocIRBuilder       | 结构拼装          | 不做语义推断   |
| DocReader          | 查询/读取         | 不做语义加工   |

---

## 7. 交付物清单（文档层）

- `REFACTOR_PLAN.md`（本文件）
- “字段映射表”（本文件 3.1/3.2）
- “责任边界表”（本文件第 6 节）

---

## 8. 后续迭代建议（可选）

仅建议，不实施：

- 将 `discarded` 页眉页脚标记写入 `data.pkl` 的显式字段
- 统一 `content_list` 与 `layout` 的表格字段规范
- 输出一个 `manifest.json` 描述每一步输入输出
