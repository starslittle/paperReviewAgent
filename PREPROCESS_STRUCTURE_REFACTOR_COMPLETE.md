# 预处理结构重构完成报告

## 重构概览

本次重构完成了预处理流程的文件结构优化，消除了编号冗余，明确了主流程与独立工具的区分。

## 执行的变更

### 1. 文件重命名（Phase 1）

| 旧文件名                      | 新文件名                   | 类型          |
| ----------------------------- | -------------------------- | ------------- |
| `1_run_file_extract.py`       | `step1_extract.py`         | 主流程步骤 1  |
| `2_process_extracted_data.py` | `step2_process.py`         | 主流程步骤 2  |
| `3_make_page_images.py`       | `tool_make_page_images.py` | 独立工具      |
| `4_build_xml_tree.py`         | **已删除**                 | 合并到 Step 3 |
| `parse_layout_demo.py`        | `example_parse_layout.py`  | 示例脚本      |

### 2. 合并 Step 3 + 4（Phase 2）

- **新增文件**: `step3_build_docir.py`
  - 整合了原 `4_build_xml_tree.py` 的完整功能
  - 直接调用 `doc_ir_builder.py` 构建 DocIR
  - 生成完整 XML 树 (`tree_{doc_id}.xml`)
  - 生成简化 Outline (`outline_{doc_id}.xml`，可选)
- **删除文件**: `4_build_xml_tree.py`
  - 功能已完全合并到 `step3_build_docir.py`

### 3. 创建统一入口（Phase 3）

- **新增文件**: `preprocess/run_pipeline.py`
  - 一键运行完整预处理流程
  - 支持步骤跳过控制（`--skip-extract`, `--skip-process`, `--skip-build`）
  - 支持可选工具调用（`--with-page-images`, `--only-page-images`）
  - 统一的错误处理与日志输出

**用法示例**:

```bash
# 运行完整流程
python preprocess/run_pipeline.py --doc-id bylw-zx --with-page-images

# 跳过提取（已有 extract_output）
python preprocess/run_pipeline.py --doc-id bylw-zx --skip-extract

# 只生成页面图
python preprocess/run_pipeline.py --doc-id bylw-zx --only-page-images
```

### 4. 更新调用脚本（Phase 4）

#### 更新的文件:

1. **`scripts/run_pipeline.ps1`**
   - 替换分散的 4 步调用为统一的 `run_pipeline.py` 调用
   - 简化脚本逻辑

2. **`scripts/activate_env.ps1`**
   - 更新帮助文档中的命令示例
   - 添加新的统一入口用法说明
   - 更新分步执行命令为新文件名

3. **`scripts/run_ocr_process.ps1`**
   - 更新引用的文件名: `2_process_extracted_data.py` → `step2_process.py`

4. **`scripts/analyze_pkl.py`**
   - 更新注释中的文件名引用

5. **`命令清单.md`**
   - 完整更新预处理章节
   - 新增"推荐方式"（使用统一入口）
   - 保留"传统方式"（分步执行）作为参考

## 重构后的文件结构

```
preprocess/
├── # 主流程（按序执行）
├── step1_extract.py              # 提取（MinerU API）
├── step2_process.py              # 清洗与标准化
├── step3_build_docir.py          # 构建 DocIR + 输出 XML
│
├── # 统一入口
├── run_pipeline.py               # 一键运行主流程
│
├── # 独立工具（可选执行）
├── tool_make_page_images.py      # 生成页面图
│
├── # 库/模块（被主流程调用）
├── doc_ir_builder.py             # DocIR 构建器
├── doc_ir.py                     # DocIR 数据类
├── doc_reader.py                 # DocIR 读取器
│
├── # 示例/调试
└── example_parse_layout.py       # layout.json 解析示例
```

## 命名规范

| 前缀       | 用途       | 执行方式       | 示例                       |
| ---------- | ---------- | -------------- | -------------------------- |
| `step{N}_` | 主流程步骤 | 必须按序执行   | `step1_extract.py`         |
| `tool_`    | 独立工具   | 可选/独立执行  | `tool_make_page_images.py` |
| `example_` | 示例/调试  | 手动运行学习   | `example_parse_layout.py`  |
| 无前缀     | 库/模块    | 被其他脚本调用 | `doc_ir_builder.py`        |

## 优势总结

### ✅ 清晰度提升

- 主流程从 4 步变为 **3 步**（合并了 Step 3+4）
- 独立工具明确标识（`tool_*`）
- 示例脚本独立（`example_*`）

### ✅ 维护性提升

- 新人一眼看出执行顺序
- 添加新工具不会干扰主流程编号
- 统一入口降低使用复杂度

### ✅ 扩展性提升

- 新增独立工具: 直接加 `tool_*.py`
- 新增主流程步骤: 按序增加 `step{N}_*.py`
- 不会再有"插入中间导致重新编号"的问题

## 兼容性说明

### 已更新的引用

- ✅ `scripts/run_pipeline.ps1`
- ✅ `scripts/activate_env.ps1`
- ✅ `scripts/run_ocr_process.ps1`
- ✅ `scripts/analyze_pkl.py`
- ✅ `命令清单.md`

### 旧文件已删除

- ❌ `1_run_file_extract.py` → 已重命名为 `step1_extract.py`
- ❌ `2_process_extracted_data.py` → 已重命名为 `step2_process.py`
- ❌ `3_make_page_images.py` → 已重命名为 `tool_make_page_images.py`
- ❌ `4_build_xml_tree.py` → **已删除**（功能合并到 `step3_build_docir.py`）
- ❌ `parse_layout_demo.py` → 已重命名为 `example_parse_layout.py`

### 注意事项

如果有外部脚本或文档引用了旧文件名，需要手动更新为新文件名。

## 使用建议

### 推荐方式（新用户）

使用统一入口 `run_pipeline.py`:

```bash
python preprocess/run_pipeline.py --doc-id <your-doc-id> --with-page-images
```

### 高级用户

如需精细控制各步骤，可分别调用 `step1_extract.py`, `step2_process.py`, `step3_build_docir.py`。

### 独立工具

页面图生成可随时独立运行:

```bash
python preprocess/tool_make_page_images.py --raw-data-dir ./data --save-dir preprocess/processed_output/MinerU
```

## 验证清单

- [x] Phase 1: 文件重命名完成
- [x] Phase 2: Step 3+4 合并完成
- [x] Phase 3: 统一入口创建完成
- [x] Phase 4: 调用脚本更新完成
- [x] 文档更新完成 (`命令清单.md`)
- [x] 文件结构验证完成

## 下一步建议

1. **测试验证**: 对重构后的流程进行完整测试

   ```bash
   python preprocess/run_pipeline.py --doc-id <test-doc> --with-page-images
   ```

2. **文档更新**: 检查其他可能引用旧文件名的文档（如 README.md）

3. **Git 提交**: 将重构变更提交到版本控制
   ```bash
   git add preprocess/ scripts/ 命令清单.md
   git commit -m "重构预处理流程结构: 统一命名规范，合并Step3+4，添加统一入口"
   ```

---

**重构完成时间**: 2026-02-03  
**重构依据**: `PREPROCESS_STRUCTURE_REFACTOR.md`
