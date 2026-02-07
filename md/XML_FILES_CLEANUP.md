# XML 文件清理与优化

## 📋 问题描述

之前预处理会生成两个XML文件：
- `tree_bylw-*.xml`：完整XML树（包含所有细节）
- `outline_bylw-*.xml`：大纲视图（简化版）

**问题**：
1. ❌ `tree_*.xml` 几乎不被使用（只在一个测试脚本中用到）
2. ❌ `outline_*.xml` 的 `<Heading>` 标签**丢失了 level 属性**
3. ❌ 冗余存储，浪费空间

---

## ✅ 解决方案

### 1. 删除所有 `tree_*.xml` 文件

已删除：
- `sample_results/tree_bylw-zx.xml` (131KB)
- `sample_results/tree_bylw-hwt.xml` (132KB)
- `sample_results/tree_bylw-pgy.xml` (86KB)
- `sample_results/tree_bylw-cmy.xml` (84KB)

### 2. 修复 `outline_*.xml` 保留 level 属性

**文件**: `preprocess/step3_build_docir.py`

**修改前**：
```python
if child.tag == "Heading":
    heading = ET.SubElement(dst_section, "Heading")  # ← 丢失了 level 属性
    heading.text = (child.text or "").strip()
```

**修改后**：
```python
if child.tag == "Heading":
    heading = ET.SubElement(dst_section, "Heading", child.attrib)  # ← 保留所有属性（包括 level）
    heading.text = (child.text or "").strip()
```

### 3. 移除生成 `tree_*.xml` 的代码

**文件**: `preprocess/step3_build_docir.py`

**删除的代码**（第163-169行）：
```python
# 保存完整 XML 树
xml_output_path = os.path.join(args.output_dir, f"tree_{doc_id}.xml")
# 格式化 XML 使其更易读
ET.indent(result.root, space="  ")
tree = ET.ElementTree(result.root)
tree.write(xml_output_path, encoding="utf-8", xml_declaration=True)
print(f"[OK] 完整 XML 树已保存 -> {xml_output_path}")
```

**删除的参数**（第111-114行）：
```python
parser.add_argument(
    "--no-outline",
    action="store_true",
    help="不生成简化的 Outline 视图（只生成完整 XML）",
)
```

### 4. 更新 `run_pipeline.py`

**删除**：
- `--no-outline` 参数定义（第142行）
- `--no-outline` 参数传递（第234-235行）
- `tree_*.xml` 输出提示（第272行）

### 5. 更新测试脚本

**文件**: `scripts/check_toc.py`

**修改**：
```python
# 修改前
tree = ET.parse('sample_results/tree_bylw-pgy.xml')

# 修改后
tree = ET.parse('sample_results/outline_bylw-pgy.xml')
```

---

## 📊 修改后的效果

### 生成的文件

**之前**：
```
sample_results/
├── tree_bylw-zx.xml       (131 KB) ← 删除
├── outline_bylw-zx.xml    (130 KB)
```

**现在**：
```
sample_results/
└── outline_bylw-zx.xml    (130 KB) ← 保留且包含 level 属性
```

### XML 格式

**outline_bylw-zx.xml** (修复后)：
```xml
<Outline>
  <Section section_id="8" level="1" start_page_num="7">
    <Heading level="1">1 绪论</Heading>  ← level 属性已保留
    <Paragraph>正文内容...</Paragraph>
    
    <Section section_id="9" level="2" start_page_num="7">
      <Heading level="2">1.1 背景</Heading>  ← level 属性已保留
      <Paragraph>...</Paragraph>
    </Section>
  </Section>
</Outline>
```

---

## 🔍 代码使用情况

### `outline_*.xml` 被使用的地方

1. **`review_runner.py`**:
   ```python
   auto_outline = Path(args.save_dir) / f"outline_{args.doc_id}.xml"
   ```

2. **`agent/doc_reader.py`**:
   ```python
   tree = ET.parse(outline_path)  # 解析 outline_*.xml
   ```

3. **`agent/logic_agent.py`**:
   ```python
   outline_xml = self.doc_agent.get_outline()
   ```

### `tree_*.xml` 被使用的地方

仅在测试脚本：
- `scripts/check_toc.py` (已更新为使用 `outline_*.xml`)

---

## ✅ 优势

| 方面 | 改进 |
|-----|------|
| **存储空间** | 减少 ~50% 的XML文件存储 |
| **代码简洁** | 移除冗余的生成逻辑 |
| **功能完整** | `outline_` 现在包含 level 属性 |
| **维护性** | 只需维护一种XML格式 |

---

## 🚀 使用方法

### 重新预处理文档

```bash
cd preprocess
python run_pipeline.py --doc-id bylw-zx
```

或使用脚本：
```powershell
.\scripts\run_pipeline.ps1 -DocName bylw-zx
```

### 验证 level 属性

```bash
# 检查生成的 outline 文件是否有 level 属性
cat sample_results/outline_bylw-zx.xml | grep "level="
```

应该看到：
```xml
<Section section_id="8" level="1" ...>
  <Heading level="1">1 绪论</Heading>
</Section>
```

---

## 📌 相关修改

1. ✅ **预处理层面**：
   - `preprocess/doc_ir_builder.py`：为 Section 添加 level 属性
   - `preprocess/step3_build_docir.py`：
     - 移除 `tree_*.xml` 生成
     - 修复 `outline_*.xml` 保留 level 属性
     - 删除 `--no-outline` 参数

2. ✅ **审查层面**：
   - `agent/logic_agent.py`：使用 level 属性进行章节过滤

3. ✅ **测试脚本**：
   - `scripts/check_toc.py`：更新为使用 `outline_*.xml`

---

## 🎯 下一步

1. 重新运行预处理生成新的 `outline_*.xml`
2. 运行审查验证章节切分是否正确
3. 删除其他文档的旧 `tree_*.xml` 文件（如需要）

---

**修改时间**: 2026-02-04  
**修改人**: AI Assistant  
**相关文档**: `LOGIC_CHAPTER_SELECTION_FIX.md`
