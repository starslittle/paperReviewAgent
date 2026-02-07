# 预处理链路完整重构方案（设计文档）

> **目标**：统一图/表处理逻辑，完全依赖 MinerU 语义真源，消除启发式判断，使链路清晰可维护。

---

## 1. 现状问题分析

### 1.1 Image 和 Table 处理不一致

**Image 处理（现状）**：

- 直接从 `content_list.json` 读取 `img_path` 和 `image_caption`
- 结构简单：`{ "type": "image", "img_path": "...", "image_caption": [...] }`
- 无额外推断逻辑

**Table 处理（现状）**：

- 需要从嵌套 `blocks` 中解析 `table_body` / `table_caption` / `image_path`
- 在 `doc_ir_builder` 中还会用正则匹配"附近的表题"作为 fallback
- 如果没有表题，会被当成 Image 处理
- 逻辑复杂、容易出错

### 1.2 Caption 获取方式混乱

**当前方式**：

1. MinerU 原始数据有 `image_caption` / `table_caption`（**真源**）
2. `doc_ir_builder` 用正则 + 距离计算"最近的图/表标题"（**启发式**）
3. 两套逻辑并存，优先级不清晰

### 1.3 页眉页脚已解决

- ✅ 已统一为 `type=discarded` → `style=Discarded`
- ✅ 不再启发式判断

---

## 2. 重构核心原则

### 2.1 语义真源唯一化

- **所有语义判断只依赖 MinerU 的 `type` 字段**
- 不再使用正则 / 位置 / 内容规则推断类型

### 2.2 图表结构对齐

- **Image 和 Table 使用完全一致的数据结构**
- 都包含：`type` + `img_path` + `caption`（图/表说明）
- 处理逻辑统一

### 2.3 Caption 来源统一

- **只使用 MinerU 提供的 `image_caption` / `table_caption`**
- 删除所有"查找最近标题"的逻辑

---

## 3. 统一数据契约（重新设计）

### 3.1 MinerU 原始字段（语义真源）

```json
// Image 元素
{
  "type": "image",
  "img_path": "images/xxx.jpg",
  "image_caption": ["图 2-1 Scrapy 框架结构"],
  "bbox": [...],
  "page_idx": 10
}

// Table 元素
{
  "type": "table",
  "img_path": "images/xxx.jpg",          // 表格图
  "table_caption": ["表 4-1 NBA 赛事数据表"],
  "table_body": "<html>...</html>",      // 表格内容（HTML）
  "bbox": [...],
  "page_idx": 20
}
```

### 3.2 2_process 输出统一结构

**Image 行**：

```python
{
  "style": "Image",
  "para_text": {
    "path": "figures/xxx.jpg",        # 统一用 figures/
    "alt_text": "图 2-1 Scrapy 框架结构"
  },
  "bbox": [...],
  "page_idx": 10
}
```

**Table 行**：

```python
{
  "style": "Table",
  "para_text": {
    "content": "<table>...</table>",   # 表格HTML/文本
    "image_path": "figures/xxx.jpg",   # 表格图（与Image一致）
    "alt_text": "表 4-1 NBA 赛事数据表"
  },
  "bbox": [...],
  "page_idx": 20
}
```

**关键对齐点**：

- 图/表的**图片路径**都用 `figures/` 前缀
- 图/表的**说明文字**都用 `alt_text`
- Table 多一个 `content` 字段存表格内容

---

## 4. 链路重构方案（分步）

### Step A：Extract (MinerU) - 不动

**保持现状**：MinerU 输出的 `content_list.json` 已经包含所有所需字段。

---

### Step B：2_process_extracted_data.py - 重构

#### 4.1 Image 处理（保持不变）

```python
if etype == "image":
    img_path = element.get("img_path", f"image_{image_count}.png")
    caption = element.get("image_caption", [])
    caption_text = caption[0] if caption else None

    return "Image", {
        "path": img_path,
        "alt_text": caption_text
    }
```

#### 4.2 Table 处理（对齐 Image）

**当前问题**：

- 从嵌套 `blocks` 解析 `table_body` / `caption` / `image_path`
- 逻辑复杂，容易出错

**重构方案**：

```python
if etype == "table":
    # 1. 表格内容：优先用顶层字段，兼容嵌套 blocks
    table_html = element.get("table_body") or ""
    if not table_html:
        table_html = _extract_table_body_from_blocks(element)

    # 2. 表格说明：只用 MinerU 提供的 table_caption
    caption = element.get("table_caption", [])
    caption_text = caption[0] if caption else None

    # 3. 表格图：与 Image 一致，用 img_path
    img_path = element.get("img_path")

    return "Table", {
        "content": table_html,
        "image_path": f"figures/{os.path.basename(img_path)}" if img_path else None,
        "alt_text": caption_text
    }
```

**关键变化**：

- ✅ `table_caption` 直接读取，不再猜测
- ✅ `img_path` 直接用顶层字段（与 Image 一致）
- ✅ 统一用 `figures/` 前缀

#### 4.3 Caption 处理（独立类型，可选）

**现状**：`type=caption` 单独作为一行输出

**重构建议**：

- 如果 MinerU 已经把 caption 写入 image/table 的字段，**不再需要单独的 Caption 行**
- 简化为：直接跳过 `type=caption`

---

### Step C：3_make_page_images.py - 不动

**保持独立**：页面图生成与语义链路无关。

---

### Step D：doc_ir_builder.py - 重构

#### 4.4 删除启发式 Caption 查找

**当前逻辑**：

- `_build_table_caption_index()` / `_build_figure_caption_index()`
- `_find_nearest_table_caption()` / `_find_nearest_figure_caption()`
- 用正则匹配"表X" / "图X"，计算距离找最近的

**重构方案**：

- ✅ **全部删除**
- Caption 已在 `para_text.alt_text` 中，直接使用

#### 4.5 Table 处理统一

**当前问题**：

- 如果没有 `table_alt_text`，会被当成 Image 处理
- 逻辑分支复杂

**重构方案**：

```python
elif style == "Table":
    table_content = row["para_text"]
    table_alt_text = None
    table_image_path = None

    if isinstance(table_content, dict):
        table_alt_text = table_content.get("alt_text")
        table_image_path = table_content.get("image_path")
        table_text = table_content.get("content", "")
    else:
        table_text = str(table_content)

    # 直接构建 Table 节点，不再判断"是否有表题"
    table_attrs = {
        "table_id": str(table_count),
        "page_num": str(current_page),
    }
    if table_image_path:
        table_attrs["image_path"] = table_image_path

    table = ET.SubElement(current_section_node, "CSV_Table", table_attrs)
    table.text = table_text

    if table_alt_text:
        alt_node = ET.SubElement(table, "Alt_Text")
        alt_node.text = str(table_alt_text)

    tables.append(TableNode(...))
    table_count += 1
```

**关键变化**：

- ✅ 不再判断"是否有表题"来决定是 Table 还是 Image
- ✅ `type=table` 就是表格，无表题也是表格（只是 `alt_text` 为空）
- ✅ 删除"当成 Image"的分支

---

### Step E：doc_reader.py - 不动

**保持现状**：只消费 DocIR 输出，不做语义判断。

---

## 5. 数据流对比（重构前后）

### 5.1 Image 数据流

**重构前**：

```
MinerU: {type: image, img_path, image_caption}
  ↓
2_process: {style: Image, para_text: {path, alt_text}}
  ↓
DocIRBuilder: 直接使用 para_text
  ↓
XML: <Image image_path="..."><Alt_Text>...</Alt_Text></Image>
```

**重构后**：

- ✅ **不变**（Image 逻辑已经是对的）

### 5.2 Table 数据流

**重构前**：

```
MinerU: {type: table, blocks: [...]}
  ↓
2_process: 从 blocks 解析 table_body/caption/image_path
  ↓
DocIRBuilder: 正则查找"最近的表题"作为 fallback
            → 无表题时当成 Image
  ↓
XML: <CSV_Table> 或 <Image>（不确定）
```

**重构后**：

```
MinerU: {type: table, table_body, img_path, table_caption}
  ↓
2_process: {style: Table, para_text: {content, image_path, alt_text}}
  ↓
DocIRBuilder: 直接使用 para_text（与 Image 一致）
  ↓
XML: <CSV_Table image_path="..."><Alt_Text>...</Alt_Text></CSV_Table>
```

**关键改进**：

- ✅ 路径统一（`table_body` / `img_path` / `table_caption` 直接读顶层）
- ✅ 无启发式逻辑
- ✅ Table 永远是 Table

---

## 6. 删除的逻辑清单

### 6.1 2_process_extracted_data.py

- ✅ 已删除：`_collect_header_footer_candidates()`
- 🔄 待删除：`_extract_table_data()` 的复杂嵌套解析逻辑（改为直接读顶层字段）

### 6.2 doc_ir_builder.py

- ✅ 已删除：
  - `_build_header_footer_index()`
  - `_classify_header_footer_text()`
  - `_append_header_footer()`
- 🔄 待删除：
  - `_build_table_caption_index()`
  - `_build_figure_caption_index()`
  - `_find_nearest_table_caption()`
  - `_find_nearest_figure_caption()`
  - `_is_table_caption()` / `_is_figure_caption()`

---

## 7. 兼容性与回退

### 7.1 如果 MinerU 未提供 caption

**场景**：旧版 MinerU 或某些文件 `image_caption` / `table_caption` 为空

**方案**：

- `alt_text` 直接为 `None`
- 不再回退到"查找最近标题"

### 7.2 如果 table_body 在嵌套 blocks 中

**场景**：部分文件仍用嵌套结构

**方案**：

- 保留 `_extract_table_body_from_blocks()` 作为兼容
- 但优先用顶层 `table_body`

---

## 8. 实施步骤

1. ✅ **Phase 1**：页眉页脚重构（已完成）
   - 删除启发式页眉页脚判断
   - 只依赖 `type=discarded`

2. 🔄 **Phase 2**：Table 结构对齐（待实施）
   - 修改 `2_process_extracted_data.py` 的 `_extract_table_data()`
   - 直接读 `table_caption` / `img_path`

3. 🔄 **Phase 3**：删除 Caption 查找逻辑（待实施）
   - 删除 `doc_ir_builder.py` 中所有 `_build_*_caption_index` 函数
   - Table 不再判断"是否有表题"

4. 🔄 **Phase 4**：测试与验证
   - 用现有文件测试
   - 确保 Image/Table 输出一致

---

## 9. 预期效果

### 9.1 代码量减少

- **删除函数**：8+ 个启发式函数
- **代码行数**：-200+ 行

### 9.2 逻辑清晰

- Image 和 Table 处理逻辑完全一致
- 无分支判断、无正则匹配

### 9.3 可维护性提升

- 语义真源唯一（MinerU type）
- 新人理解成本降低

---

## 10. 风险与对策

### 10.1 MinerU caption 质量不稳定

**风险**：某些文件 `image_caption` / `table_caption` 可能为空或错误

**对策**：

- `alt_text` 允许为 `None`
- 不影响主流程

### 10.2 旧数据兼容

**风险**：已处理的 `data.pkl` 可能用旧格式

**对策**：

- 增加版本号检测
- 或重新运行 `2_process`

---

## 11. 总结

| 项目           | 重构前                | 重构后               |
| -------------- | --------------------- | -------------------- |
| **页眉页脚**   | 启发式（位置+正则）   | `type=discarded`     |
| **图片说明**   | MinerU + 正则查找     | 只用 `image_caption` |
| **表格说明**   | 嵌套解析 + 正则查找   | 只用 `table_caption` |
| **表格图片**   | 嵌套解析 `image_path` | 直接用 `img_path`    |
| **Table 判定** | 有表题才是表格        | `type=table` 即表格  |
| **代码复杂度** | 高（多层判断）        | 低（直接映射）       |

**核心收益**：

- ✅ 图表逻辑统一
- ✅ 无启发式判断
- ✅ 完全依赖 MinerU 语义真源
- ✅ 代码量减少 200+ 行
