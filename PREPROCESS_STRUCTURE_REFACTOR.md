# 预处理流程结构重构方案

> **目标**：消除冗余、明确主流程与可选步骤、统一命名规范、降低理解成本

---

## 1. 当前问题分析

### 1.1 文件组织混乱

**当前文件**：

```
preprocess/
├── 1_run_file_extract.py          # 主流程：抽取
├── 2_process_extracted_data.py    # 主流程：清洗
├── 3_make_page_images.py          # 独立步骤：页面图生成
├── 4_build_xml_tree.py            # 主流程：构建XML
├── doc_ir_builder.py              # 工具类：被4调用
├── doc_ir.py                      # 数据类：DocIR定义
├── doc_reader.py                  # 工具类：读取XML
└── parse_layout_demo.py           # 示例/调试脚本
```

**问题**：

- 编号混乱：`3_make_page_images` 是独立步骤，但插在主流程中间
- `4_build_xml_tree` 调用 `doc_ir_builder`，层级不清晰
- `doc_reader` 也是主流程输出，但没有编号
- 主流程 vs 工具类 vs 示例脚本，没有明确分类

### 1.2 调用关系不清晰

**当前调用链**：

```
1_run_file_extract.py
  → MinerU API
  → 输出: extract_output/

2_process_extracted_data.py
  → 读取: extract_output/
  → 输出: processed_output/data.pkl

3_make_page_images.py (独立)
  → 读取: 原始PDF
  → 输出: page_images/

4_build_xml_tree.py
  → 调用: doc_ir_builder.py
  → 读取: processed_output/data.pkl
  → 输出: outline.xml
```

**问题**：

- `4` 依赖 `doc_ir_builder`，但后者没有编号，容易被忽略
- `3` 不依赖 `1/2`，但编号在中间
- 缺少统一的入口脚本

---

## 2. 重构目标

### 2.1 文件分类原则

| 类别           | 定义                     | 命名规范            | 例子                       |
| -------------- | ------------------------ | ------------------- | -------------------------- |
| **主流程步骤** | 必须按顺序执行的核心步骤 | `step{N}_{name}.py` | `step1_extract.py`         |
| **独立工具**   | 可选/独立执行的辅助步骤  | `tool_{name}.py`    | `tool_make_page_images.py` |
| **库/模块**    | 被主流程调用的类/函数    | `{module_name}.py`  | `doc_ir_builder.py`        |
| **示例/调试**  | 演示或调试用脚本         | `example_{name}.py` | `example_parse_layout.py`  |

### 2.2 主流程定义

**核心问题**：从原始文件到 Agent 可用的 XML，必须经过哪些步骤？

**答案**：

1. **Extract**：从 PDF/文件提取内容 (MinerU)
2. **Process**：清洗、标准化数据结构
3. **Build DocIR**：构建层次化文档结构
4. ~~Build XML~~：**合并到 Step 3**

**发现**：

- `4_build_xml_tree` 只是薄封装，调用 `doc_ir_builder` 输出 XML
- 可以合并为一步

---

## 3. 重构方案（推荐）

### 3.1 新文件结构

```
preprocess/
├── # 主流程（按序执行）
├── step1_extract.py              # 抽取（调用 MinerU）
├── step2_process.py              # 清洗与标准化
├── step3_build_docir.py          # 构建 DocIR + 输出 XML
│
├── # 独立工具（可选执行）
├── tool_make_page_images.py      # 生成页面图（可选）
│
├── # 库/模块（被主流程调用）
├── doc_ir_builder.py             # DocIR 构建器
├── doc_ir.py                     # DocIR 数据类定义
├── doc_reader.py                 # DocIR 读取器
│
├── # 示例/调试
├── example_parse_layout.py       # layout.json 解析示例
│
└── # 统一入口（新增）
    └── run_pipeline.py           # 一键运行主流程
```

### 3.2 主流程步骤详解

#### Step 1: Extract（抽取）

**文件名**：`step1_extract.py`  
**职责**：

- 调用 MinerU 提取 PDF/文件内容
- 输出原始结构化数据

**输入**：原始文件 (`data/`)  
**输出**：`extract_output/{doc_id}/`

- `*_content_list.json`
- `layout.json`
- `images/`

**重构变化**：

- 重命名：`1_run_file_extract.py` → `step1_extract.py`
- 无逻辑变更

---

#### Step 2: Process（清洗与标准化）

**文件名**：`step2_process.py`  
**职责**：

- 读取 MinerU 产物
- 统一数据结构（Image/Table/Heading/Normal）
- 输出标准化 DataFrame

**输入**：`extract_output/{doc_id}/`  
**输出**：`processed_output/{doc_id}/`

- `data.pkl`
- `data.csv`
- `figures/`（复制 images/）

**重构变化**：

- 重命名：`2_process_extracted_data.py` → `step2_process.py`
- 无逻辑变更

---

#### Step 3: Build DocIR（构建文档结构 + 输出 XML）

**文件名**：`step3_build_docir.py`  
**职责**：

- 调用 `doc_ir_builder.py` 构建 DocIR
- 生成完整 XML（`outline.xml`）
- 生成简化 Outline（`outline_simple.xml`，可选）

**输入**：`processed_output/{doc_id}/data.pkl`  
**输出**：`processed_output/{doc_id}/`

- `outline.xml`
- `outline_simple.xml`（可选）

**重构变化**：

- **合并**：`4_build_xml_tree.py` + `doc_ir_builder` 的调用逻辑
- **新增**：直接输出 `outline.xml`（不再需要单独的"第4步"）

**实现方式**：

```python
# step3_build_docir.py
from doc_ir_builder import DocIRBuilder

def main():
    builder = DocIRBuilder()
    result = builder.build_from_pkl(data_path)

    # 输出完整 XML
    tree = ET.ElementTree(result.root)
    tree.write(f"{data_path}/outline.xml", encoding="utf-8")

    # 输出简化 Outline（可选）
    outline = _build_outline_from_tree(result.root)
    outline_tree = ET.ElementTree(outline)
    outline_tree.write(f"{data_path}/outline_simple.xml", encoding="utf-8")
```

---

### 3.3 独立工具

#### Tool: Make Page Images（页面图生成）

**文件名**：`tool_make_page_images.py`  
**职责**：

- 从 PDF 生成页面图（PNG）
- 用于视觉展示或模型输入

**输入**：原始 PDF  
**输出**：`page_images/page_XXXX.png`

**特点**：

- **独立执行**：不依赖主流程
- **可选步骤**：不影响 XML 生成

**重构变化**：

- 重命名：`3_make_page_images.py` → `tool_make_page_images.py`
- 无逻辑变更

---

### 3.4 统一入口（新增）

#### Pipeline Runner（一键运行）

**文件名**：`run_pipeline.py`  
**职责**：

- 统一入口，按序调用主流程
- 参数传递与错误处理
- 可选步骤控制

**用法**：

```bash
# 运行完整流程
python run_pipeline.py --doc-id bylw-zx

# 跳过某些步骤
python run_pipeline.py --doc-id bylw-zx --skip-extract

# 只生成页面图
python run_pipeline.py --doc-id bylw-zx --only-page-images
```

**实现示例**：

```python
# run_pipeline.py
import argparse
from step1_extract import main as extract
from step2_process import main as process
from step3_build_docir import main as build_docir
from tool_make_page_images import main as make_page_images

def main():
    args = parse_args()

    if not args.skip_extract:
        extract(args.doc_id)

    if not args.skip_process:
        process(args.doc_id)

    if not args.skip_build:
        build_docir(args.doc_id)

    if args.with_page_images:
        make_page_images(args.doc_id)
```

---

## 4. 文件对比（重构前后）

| 重构前                        | 重构后                     | 类型     | 说明          |
| ----------------------------- | -------------------------- | -------- | ------------- |
| `1_run_file_extract.py`       | `step1_extract.py`         | 主流程   | 重命名        |
| `2_process_extracted_data.py` | `step2_process.py`         | 主流程   | 重命名        |
| `3_make_page_images.py`       | `tool_make_page_images.py` | 独立工具 | 移出主流程    |
| `4_build_xml_tree.py`         | `step3_build_docir.py`     | 主流程   | 合并到 Step 3 |
| `doc_ir_builder.py`           | `doc_ir_builder.py`        | 库/模块  | 不变          |
| `doc_ir.py`                   | `doc_ir.py`                | 库/模块  | 不变          |
| `doc_reader.py`               | `doc_reader.py`            | 库/模块  | 不变          |
| `parse_layout_demo.py`        | `example_parse_layout.py`  | 示例     | 重命名        |
| （无）                        | `run_pipeline.py`          | 入口     | 新增          |

---

## 5. 调用关系图（重构后）

```
run_pipeline.py (统一入口)
├── step1_extract.py
│   └── MinerU API
│       → extract_output/
│
├── step2_process.py
│   └── 读取: extract_output/
│       → processed_output/data.pkl
│
├── step3_build_docir.py
│   ├── 调用: doc_ir_builder.py
│   │   └── 读取: processed_output/data.pkl
│   └── 输出: outline.xml
│
└── tool_make_page_images.py (可选)
    └── 读取: 原始 PDF
        → page_images/
```

---

## 6. 实施步骤

### Phase 1: 重命名（无逻辑变更）

1. `1_run_file_extract.py` → `step1_extract.py`
2. `2_process_extracted_data.py` → `step2_process.py`
3. `3_make_page_images.py` → `tool_make_page_images.py`
4. `parse_layout_demo.py` → `example_parse_layout.py`

### Phase 2: 合并 Step 3 + 4

1. 创建 `step3_build_docir.py`
2. 将 `4_build_xml_tree.py` 的逻辑合并进来
3. 删除 `4_build_xml_tree.py`

### Phase 3: 创建统一入口

1. 创建 `run_pipeline.py`
2. 实现参数解析与步骤调用
3. 更新文档

### Phase 4: 更新调用方

1. 检查 `scripts/` 中的调用脚本
2. 更新为新的文件名
3. 更新 README

---

## 7. 优势与收益

### 7.1 清晰度提升

- **主流程明确**：`step1` → `step2` → `step3`
- **工具可选**：`tool_*` 一眼看出是独立的
- **示例独立**：`example_*` 不会被误认为主流程

### 7.2 维护性提升

- 新人理解成本降低：3 步主流程，清晰可见
- 添加新工具不会干扰主流程编号
- 统一入口降低调用复杂度

### 7.3 扩展性提升

- 新增独立工具：直接加 `tool_*.py`
- 新增主流程步骤：按序增加 `step{N}_*.py`
- 不会再有"插入中间导致重新编号"的问题

---

## 8. 风险与对策

### 8.1 兼容性问题

**风险**：已有脚本/文档引用旧文件名

**对策**：

- Phase 1 保留软链接（符号链接）兼容旧名称
- 更新所有 `scripts/` 中的调用
- README 中标注"旧文件名已废弃"

### 8.2 合并 Step 3+4 的复杂度

**风险**：`4_build_xml_tree` 有额外逻辑

**对策**：

- 先分析 `4` 的完整逻辑
- 确认只是薄封装后再合并
- 保留原文件备份

---

## 9. 替代方案（不推荐）

### 方案A：只重命名，不合并

**做法**：

- `1_extract.py`
- `2_process.py`
- `3_build_docir.py`（重命名 `4`）
- `tool_make_page_images.py`

**缺点**：

- `3` 和 `4` 的职责重叠不解决

### 方案B：完全扁平化（无编号）

**做法**：

- `extract.py`
- `process.py`
- `build_docir.py`

**缺点**：

- 丢失"顺序"信息
- 新人不知道执行顺序

---

## 10. 总结

| 项目           | 重构前             | 重构后                           |
| -------------- | ------------------ | -------------------------------- |
| **主流程步骤** | 4 步（含独立工具） | 3 步（纯主流程）                 |
| **独立工具**   | 混在编号中         | `tool_*` 明确标识                |
| **调用入口**   | 分散调用           | 统一 `run_pipeline.py`           |
| **文件命名**   | `{N}_{name}`       | `step{N}_{name}` / `tool_{name}` |
| **理解成本**   | 高（需猜测职责）   | 低（命名即文档）                 |

**核心收益**：

- ✅ 主流程 3 步，清晰可见
- ✅ 独立工具单独标识
- ✅ 统一入口降低调用复杂度
- ✅ 易于维护与扩展

**推荐实施**：Phase 1 → Phase 2 → Phase 3 → Phase 4
