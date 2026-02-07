# LogicAgent 章节切分优化

## 📋 问题描述

### 原问题
LogicAgent 在进行层次化逻辑审查时，会将**正文中提及的章节标题**误当作真实章节，导致：
- ❌ 目录中的"1 绪论"、"1.1 背景"等被当作真实章节
- ❌ 正文引用的章节也可能被误判
- ❌ 审查了大量非实质内容的Section

### 示例
```xml
<Section section_id="7" start_page_num="5">
  <Heading>目 录</Heading>
  <Paragraph>1 绪论...</Paragraph>  ← 这是目录条目，不是真实章节
  <Paragraph>1.1 背景..</Paragraph>
</Section>

<Section section_id="8" start_page_num="7">
  <Heading>1 绪论</Heading>  ← 这才是真实的章节标题
  <Paragraph>正文内容...</Paragraph>
</Section>
```

---

## 💡 解决方案

### 方案组合：方案3（预处理添加level属性）+ 方案1（基于Heading标签）

#### 核心改动
1. **预处理层面**（`doc_ir_builder.py`）：
   - 为每个 `<Section>` 添加 `level` 属性
   - `level="1"` 表示顶层章节
   - `level="2"` 表示二级章节
   - `level="3"` 表示三级章节

2. **审查层面**（`logic_agent.py`）：
   - 只选择 `level="1"` 的Section
   - 必须包含真实的 `<Heading>` 或 `<Title>` 标签
   - 从「摘要」开始
   - 黑名单排除：目录、封面、承诺、致谢

---

## 🔧 代码修改

### 1. 预处理：添加level属性

**文件**: `preprocess/doc_ir_builder.py`

**修改位置1**: 第495-506行（创建真实章节Section）
```python
# 3. 创建新的 Section（添加 level 属性）
current_section_node = ET.SubElement(
    parent_node,
    "Section",
    section_id=current_section_id,
    level=str(heading_level),  # ← 新增：添加 level 属性
    start_page_num=str(current_page),
)
```

**修改位置2**: 第437-445行（创建目录Section）
```python
toc_section_node = ET.SubElement(
    root,
    "Section",
    section_id=current_section_id,
    level="1",  # ← 新增：添加 level 属性
    start_page_num=str(current_page),
)
```

**修改位置3**: 第570-580行（创建默认Section for Normal段落）
```python
current_section_node = ET.SubElement(
    root,
    "Section",
    section_id=current_section_id,
    level="1",  # ← 新增：添加 level 属性
    start_page_num=str(current_page),
)
```

**修改位置4**: 第371-380行（创建默认Section for Header/Footer）
```python
current_section_node = ET.SubElement(
    root,
    "Section",
    section_id=current_section_id,
    level="0",  # ← 新增：level="0" 表示特殊Section
    start_page_num=str(current_page),
)
```

---

### 2. LogicAgent：基于level和Heading选择章节

**文件**: `agent/logic_agent.py`

**修改**: `_get_outermost_section_ids()` 方法（第378-402行 → 第378-450行）

```python
def _get_outermost_section_ids(self) -> List[str]:
    """
    获取真实章节的顶层Section ID（结合 level 属性和 Heading 标签）
    - 只选择 level="1" 的顶层Section
    - 必须包含真实 <Heading> 标签
    - 从「摘要」开始
    - 排除目录、封面、承诺等非内容Section
    """
    section_ids = []
    abstract_start_index = None
    
    # 第一遍：找到摘要的位置
    for idx, child in enumerate(self.doc_agent.doc_reader.root):
        if child.tag != "Section":
            continue
        
        # 检查是否包含真实标题
        title_text = None
        for node in child:
            if node.tag in ["Heading", "Title"] and node.text:
                title_text = node.text.strip()
                break
        
        if title_text:
            normalized = title_text.lower().replace(" ", "")
            if any(key in normalized for key in ["摘要", "abstract"]):
                abstract_start_index = idx
                break
    
    # 黑名单：排除这些Section（即使有Heading和level=1）
    SKIP_TITLES = ["目录", "目 录", "封面", "诚信承诺", "致谢", "contents", "tableofcontents"]
    
    # 第二遍：收集真实的Level 1章节
    for idx, child in enumerate(self.doc_agent.doc_reader.root):
        if child.tag != "Section" or not child.get("section_id"):
            continue
        
        # 跳过摘要之前的Section
        if abstract_start_index is not None and idx < abstract_start_index:
            continue
        
        # 检查 level 属性 → 只选择 level="1"
        level = child.get("level")
        if level != "1":
            continue
        
        # 检查是否包含真实标题
        has_heading = False
        title_text = None
        for node in child:
            if node.tag in ["Heading", "Title"] and node.text:
                title_text = node.text.strip()
                has_heading = True
                break
        
        # 必须有Heading才算章节
        if not has_heading:
            continue
        
        # 排除黑名单中的Section
        if title_text:
            normalized_title = title_text.lower().replace(" ", "")
            if any(skip in normalized_title for skip in SKIP_TITLES):
                print(f"[Logic] Skip non-content section: {title_text}")
                continue
        
        # 通过所有检查，加入章节列表
        section_ids.append(child.get("section_id"))
    
    return section_ids
```

---

## 📊 效果对比

### 修改前
```python
# 选择所有顶层Section（包括目录）
sections = ["5", "6", "7", "8", "27", "36", ...]
# Section 7 = 目录（误选）
# Section 8 = 1 绪论 ✓
```

### 修改后
```python
# 只选择 level="1" + 有Heading + 不在黑名单
sections = ["5", "6", "8", "27", "36", ...]  # Section 7（目录）已排除
# Section 5 = 摘要 ✓
# Section 6 = ABSTRACT ✓
# Section 8 = 1 绪论 ✓
# Section 27 = 2 需求分析 ✓
```

---

## 📝 XML 示例

### 修改后的XML格式
```xml
<Document>
  <!-- 封面（level=0 或 level=1，但会被黑名单排除） -->
  <Section section_id="1" level="1" start_page_num="1">
    <Heading>杭州电子科技大学继续教育学院</Heading>
  </Section>
  
  <!-- 目录（level=1 + Heading="目 录" → 黑名单排除） -->
  <Section section_id="7" level="1" start_page_num="5">
    <Heading>目 录</Heading>
    <Paragraph>1 绪论...</Paragraph>
    <Paragraph>1.1 背景..</Paragraph>
  </Section>
  
  <!-- 摘要（level=1 + Heading="摘 要" → ✅ 选中） -->
  <Section section_id="5" level="1" start_page_num="3">
    <Heading>摘 要</Heading>
    <Paragraph>本项目...</Paragraph>
  </Section>
  
  <!-- 真实章节（level=1 → ✅ 选中） -->
  <Section section_id="8" level="1" start_page_num="7">
    <Heading level="1">1 绪论</Heading>
    <Paragraph>正文内容...</Paragraph>
    
    <!-- 子章节（level=2 → ❌ 不选） -->
    <Section section_id="9" level="2" start_page_num="7">
      <Heading level="2">1.1 背景</Heading>
      <Paragraph>...</Paragraph>
      
      <!-- 三级章节（level=3 → ❌ 不选） -->
      <Section section_id="10" level="3" start_page_num="7">
        <Heading level="3">1.1.1 国外研究现状</Heading>
        <Paragraph>...</Paragraph>
      </Section>
    </Section>
  </Section>
</Document>
```

---

## ✅ 优势

| 方面 | 改进 |
|-----|------|
| **准确性** | ⭐⭐⭐⭐⭐ 只审查真实的顶层章节 |
| **性能** | ⭐⭐⭐⭐⭐ level属性直接读取，无需遍历 |
| **可维护性** | ⭐⭐⭐⭐⭐ 清晰的层级标记 |
| **兼容性** | ⭐⭐⭐⭐ 需要重新预处理文档 |
| **可扩展性** | ⭐⭐⭐⭐⭐ 未来可基于level实现更多功能 |

---

## 🚀 使用方法

### 1. 重新预处理文档
修改后需要重新运行预处理流程：

```bash
cd preprocess
python run_pipeline.py --doc-id bylw-zx
```

### 2. 运行审查
```bash
python run_review.py --doc-id bylw-zx
```

### 3. 查看日志
审查日志中会显示：
```
[Logic] Selected outermost sections: ['5', '6', '8', '27', '36', ...]
[Logic] Skip non-content section: 目 录
```

---

## 🔍 验证方法

### 检查XML中的level属性
```bash
# 查看生成的XML大纲
cat sample_results/outline_bylw-zx.xml | grep -A 2 "Section.*level"
```

### 检查LogicAgent选择的章节
```python
# 在 review_runner.py 中添加打印
print(f"[Debug] Logic章节: {logic_agent._get_outermost_section_ids()}")
```

---

## 📌 注意事项

1. **向后兼容性**：
   - ⚠️ 已生成的XML文件**没有**level属性
   - ✅ 需要重新运行预处理

2. **黑名单维护**：
   - 目前黑名单：`["目录", "目 录", "封面", "诚信承诺", "致谢", "contents", "tableofcontents"]`
   - 如遇到新的非内容Section，手动添加到黑名单

3. **特殊情况**：
   - 如果论文没有明确的章节编号（如"1 绪论"），可能需要调整 `_get_heading_level()` 逻辑

---

## 🎯 下一步优化

- [ ] 支持用户自定义黑名单
- [ ] 基于level属性实现分层审查（如只审查Level 1和Level 2）
- [ ] 在报告中显示章节层级信息
- [ ] 为VisionAgent也添加基于level的过滤

---

**修改时间**: 2026-02-04  
**修改人**: AI Assistant  
**相关Issue**: LogicAgent章节切分误判问题
