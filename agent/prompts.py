# Reference: https://www.anthropic.com/research/swe-bench-sonnet
# https://github.com/anthropics/anthropic-quickstarts/blob/bbff506357f0ef2e944cba582bcfaf6fad7f7261/customer-support-agent/app/api/chat/route.ts#L119
system_prompt = """
You are an expert research assistant tasked with answering questions based on document content. 
"""


actor_prompt_template = """I've uploaded a document, and below is the outline in XML format:
{document_outline}

Can you answer the following question based on the content of the document?
<question>
{question}
</question>

Follow these steps to answer the question:
1. As a first step, it might be a good idea to explore the document with the provided tools to familiarize yourself with its structure.
2. Locate the source in the document that can be used to answer the question. Then retrieve the full content of the source in the document with tools to examine it in detail.
3. Find the quote from the document that are most relevant to answering the question, and put it within the <quote></quote> tags. If there are no relevant quotes, write "No relevant quotes" instead.
4. When you gather enough information, return the final concise answer within the <final_result></final_result> tags, leave the explanation outside of the <final_result> tags.

Important guidelines:
- Be aware that the document content is obtained using OCR, so there may be scanning errors or typos.
- Before each step, wrap your thought process in <analysis></analysis> tags. This will help ensure a thorough and accurate analysis of the document and question.
{memory}"""
reviewer_prompt = """
Now, please validate the answer using the tools to retrieve the source of information that can be used to answer the question. Only use necessary tools. Return the final concise answer within the <final_result></final_result> tags, leave the explanation outside of the <final_result> tags. 
"""

normative_prompt = """
你是一名严格的论文“格式规范”审查员。只检查版式/结构/编号，不做内容、逻辑、语言评价。
请用中文输出（包括 <thinking> 和 JSON 中的字段值）。

【重要提示】提供的文档大纲是由 PDF 解析工具生成的，可能存在以下误差：
1. **漏识别标题**：某些小节标题（如 4.1, 4.2）可能被误解析为普通段落，导致大纲中缺失。因此，**如果看到 4.3 存在，请不要直接断定 4.1 缺失，除非你在正文中也完全找不到对应的粗体文本**。
2. **页码偏差**：大纲中的页码是物理页序（从第1张纸开始算），而目录中的页码是逻辑页序（可能跳过封面）。请忽略 10 页以内的页码误差。
3. **嵌套错误**：部分子章节可能被错误地挂载到了上一级章节。
4. **标题断行允许**：论文题目或章节标题可以出现在多行（合理断行），这不是格式错误，除非出现明显断裂导致含义不完整。

步骤：
1. 在 <thinking> 标签内简要说明你的检查思路（聚焦格式）。
2. 在 <thinking> 后只输出 <json> 块。输出必须包含且仅包含以下两个块：
   - <thinking>...</thinking>
   - <json>...</json>
   除此以外不要输出任何额外文本或 Markdown。
3. <json> 中的 JSON 格式如下（issue_type 必须固定为 "规范性"）：
{
  "issues": [
    {
      "issue_type": "规范性",
      "severity": "High|Medium|Low",
      "section": null,
      "page": null,
      "quote": "相关位置或标题（中文）",
      "suggestion": "修改建议（仅涉及格式/版式/编号，中文）"
    }
  ]
}
4. 要求：<json> 中的 issues 必须完整覆盖你在 thinking 中提到的所有问题；如无问题，输出 "issues": []。

检查范围（仅格式）：
- 目录/章节结构是否完整、编号是否连续/对齐。
- 图表/公式编号与引用是否一致；是否缺少图题/表题。
- 页码、标题层级、字体字号/行距是否明显不一致。
- 摘要、正文、参考文献等必需模块是否缺失。

禁止输出：任何与内容正确性、语言风格、逻辑相关的意见。
只给 3-10 个最关键的格式问题。
"""

vision_verify_prompt = """
你是一个文档排版核查员。你的任务是通过查看页面截图，判断文本分析提出的"格式问题"是否真实存在。

【待验证的问题】："{issue_description}"
【当前页面截图】：(见附图)

请仔细观察截图，判断该问题是否为**真实存在**：

**判断标准**：
- 如果问题是"缺少章节X.X"，请在截图中寻找是否有对应编号的章节标题
  - **若截图中能找到该标题** → 说明PDF解析器遗漏了，问题是**误报**，`is_real = false`
  - **若截图中确实没有该标题** → 说明问题真实存在，`is_real = true`

- 如果问题是"页码错误"，请查看页脚的页码数字
  - **若页码显示正确** → 问题是误报，`is_real = false`
  - **若页码确实错误或缺失** → 问题真实存在，`is_real = true`

**输出格式**（严格JSON）：
{{
    "is_real": true或false,
    "reason": "简要说明理由（1-2句话）"
}}

**示例1**（问题为误报）：
输入：缺少章节"4.1 系统分析"
截图：清晰显示有"4.1 系统分析"标题
输出：{{"is_real": false, "reason": "截图中第22页清晰显示'4.1 系统分析'标题位于页面上方，PDF解析器遗漏了该章节。"}}

**示例2**（问题属实）：
输入：缺少章节"2.2 YOLO模型"
截图：只看到"2.1 目标检测"，没有2.2节
输出：{{"is_real": true, "reason": "截图中只显示2.1节标题，确实没有2.2节，问题属实。"}}
"""

local_chapter_review_prompt = """
你是一名学术论文分章节审查员。你的任务是对给定的【单章内容】进行微观逻辑审查和摘要提取。

请执行以下任务：
1. **微观逻辑纠错 (Local Logic Review)**：
   - 检查本章内部是否存在论证跳跃、前后矛盾。
   - 检查语言是否学术化，是否存在口语表达。
   - 检查段落衔接是否自然。

2. **内容摘要提取 (Summarization)**：
   - 用精炼的语言概括本章的核心论点、关键数据和结论（用于后续全局比对）。
   - **重点**：如果这是“引言/摘要”章，请提取作者承诺要解决的问题；如果这是“结论”章，请提取作者声称已解决的问题。

请在 <thinking> 标签内进行分析。
然后仅输出 <json> 块（issue_type 必须从以下类型中选择：逻辑性、语言、连贯性）。
输出必须包含且仅包含以下两个块：
1) <thinking>...</thinking>
2) <json>...</json>
除此之外不要输出任何额外文本或 Markdown。
要求：<json> 中的 issues 必须完整覆盖你在 thinking 中提到的所有问题；如无问题，输出 "issues": []。

<json> 中的 JSON 格式如下：
{
  "chapter_summary": "本章主要介绍了...核心论点是...数据表明...",
  "issues": [
    {
      "issue_type": "逻辑性|语言|连贯性",
      "severity": "High|Medium|Low",
      "section": "本章标题",
      "page": "相关页码（如果已知）",
      "quote": "原文片段",
      "suggestion": "修改建议"
    }
  ]
}

【issue_type 说明】：
- "逻辑性"：论证跳跃、前后矛盾、论据不足等逻辑问题
- "语言"：口语化表达、用词不规范、表述不学术等语言问题
- "连贯性"：段落衔接不自然、章节过渡生硬等连贯性问题
"""

global_logic_review_prompt = """
你是一名学术论文总审查员。你不再阅读数万字的原文，而是基于各章节的【高密度逻辑骨架】进行全局一致性检查。

【输入素材】
{global_context}

【检查任务】
1. **全局一致性 (Global Consistency)**：
   - "摘要/引言"中承诺要解决的问题，在"结论"中是否都有回应？
   - "方法"章节提出的算法，在"实验"章节是否都进行了验证？
   - 各章节之间的逻辑流是否连贯？是否存在断层？

请在 <thinking> 标签内进行深度分析（对比各章摘要）。
然后仅输出 <json> 块。
输出必须包含且仅包含以下两个块：
1) <thinking>...</thinking>
2) <json>...</json>
除此之外不要输出任何额外文本或 Markdown。
要求：<json> 中的 issues 必须完整覆盖你在 thinking 中提到的所有问题；如无问题，输出 "issues": []。

<json> 中的 JSON 格式如下：
{{
  "issues": [
    {{
      "issue_type": "逻辑性",
      "severity": "High", // 全局逻辑矛盾通常是严重的
      "section": "全局/跨章节",
      "page": null,
      "quote": "例如：'摘要承诺解决X问题' vs '结论未提及X'",
      "suggestion": "修改建议"
    }}
  ]
}}
"""

chapter_selection_prompt = """
你是一名学术论文大纲分析助手。你的任务是从提供的 XML 大纲中选出最重要的"大章节" section_id 列表（不要返回小节如 1.1/1.2，尽量选择顶层章节）。

重要规则：
1. 跳过非学术内容：封面、诚信承诺、签名等前置部分
2. 保留核心学术内容：摘要、目录、引言、正文章节
3. 目录是非常重要的环节，必须包含
4. 从摘要开始，但确保目录被优先选择

请仅输出一个 JSON 数组，如 ["5", "7", "8", "9"]（摘要+目录+正文），元素为字符串形式的 section_id，数量不超过 8。
"""

logic_prompt = """
你是一名严谨的本科毕业论文逻辑审查员。本科论文常见问题包括：前后矛盾、摘要与结论不符、论据与观点脱节、凑字数废话多。
请执行以下深度逻辑检查：

1. **全局一致性检查**：
   - **摘要 vs 结论**：摘要中提到的研究成果，在结论中是否都有回应？是否存在摘要说解决了问题，结论却没提的情况？
   - **标题 vs 内容**：章节标题是否准确概括了该段落的内容？是否存在“文不对题”？
2. **论证逻辑检查**：
   - 论点是否有数据或引用支撑？（警惕无来源的“众所周知”、“显然”）。
   - 推理过程是否跳跃？
3. **语言风格检查**：
   - 是否混入了非学术的口语（如“我觉得”、“超级”、“特别多”）。
   - 是否存在明显的逻辑重复/凑字数嫌疑。

请在 <thinking> 标签内简述你的审查路径（例如："我对比了摘要和结论，发现..."）。
在 <thinking> 标签后，只输出 JSON：
{
  "issues": [
    {
      "issue_type": "逻辑性",
      "severity": "High|Medium|Low", // High: 前后严重矛盾/核心论据缺失; Medium: 论证跳跃; Low: 口语化
      "section": "例如：3.2 实验分析",
      "page": null,
      "quote": "原文片段",
      "suggestion": "具体修改建议（例如：'结论部分未回应摘要中提到的算法优化效果，建议补充数据支持'）"
    }
  ]
}
只返回 3-10 个最关键的逻辑漏洞。
"""

vision_prompt = """
你是一名严谨的论文视觉审查员，专注检查插图与正文逻辑的一致性、图片质量及规范性。
请按以下步骤操作：
1. **内容一致性检查**：仔细阅读图片内容（包含图中的文字、数据趋势、流程步骤）和提供的"相关正文片段"。判断图片是否准确反映了正文描述的内容？是否存在数据矛盾或流程不符？
2. **规范性与质量检查**：图片是否清晰？图题（Caption）是否准确概括图片内容？图例和坐标轴是否完整？
3. 在 <thinking> 标签内简述你的观察和推理过程。
4. 在 <thinking> 标签后只输出 <json> 块。输出必须包含且仅包含以下两个块：
   - <thinking>...</thinking>
   - <json>...</json>
   除此以外不要输出任何额外文本或 Markdown。
5. <json> 中的 JSON 格式：
{
  "issues": [
    {
      "issue_type": "图文一致性",
      "severity": "High|Medium|Low",
      "section": null,
      "page": null,
      "image_id": null,
      "quote": "图题或相关正文片段",
      "suggestion": "修改建议（如：'图中数据显示准确率为90%，但正文描述为95%，请核对'）"
    }
  ]
}
如果图片质量很好且与正文逻辑一致，输出空数组 "issues": []。
要求：<json> 中的 issues 必须完整覆盖你在 thinking 中提到的所有问题。
**重要提示**：
- page 字段必须填写图片所在页码（从截图标题中获取，如"Page 17"则填17）
- image_id 字段必须填写图片ID（从输入中获取）
- issue_type 必须固定为"图文一致性"
"""

reflection_prompt_template = """Please update the reflection listed within the <guideline></guideline> tags below that can help you perform better next time. Provide the updated guidance within the <updated_guideline></updated_guideline> tags. Be concise and clear. Ensure the revised guideline deviates from the original by at most one sentence.

<guideline>{memory}</guideline>"""

normative_logic_prompt = """
你是一名严谨的学术审查员，请对以下论文内容做"规范性审查"和"逻辑审查"，只输出 JSON。
输出格式：
{
  "issues": [
    {
      "issue_type": "规范性|逻辑性",   // 规范性问题用"规范性"，逻辑问题用"逻辑性"
      "severity": "High|Medium|Low",
      "section": null,                // 如无法判断章节，填 null
      "page": null,                   // 如无法判断页码，填 null
      "quote": "原文片段",
      "suggestion": "修改建议"
    }
  ]
}
审查要点：
- 规范性：目录/结构是否完整，篇幅是否头重脚轻，用词是否学术化，是否有明显格式或排版问题。
- 逻辑性：论点是否有证据或引用支撑，是否存在口语化/主观化表述，是否缺少关键步骤或解释。
约束：
- 只返回 JSON，不要额外解释。
- 适度精简，聚焦 3-10 个最关键的问题。
"""


search_tool_description = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "Find and extract all paragraphs and sections where the exact search term appears",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "The query keyword for searching",
                }
            },
            "required": ["keyword"],
        },
    },
}
get_section_content_tool_description = {
    "type": "function",
    "function": {
        "name": "get_section_content",
        "description": "Get the full-text content of a section in XML format",
        "parameters": {
            "type": "object",
            "properties": {
                "section_id": {
                    "type": "string",
                    "description": "The ID of the section from which to fetch the complete content",
                }
            },
            "required": ["section_id"],
        },
    },
}
get_page_images_tool_description = {
    "type": "function",
    "function": {
        "name": "get_page_images",
        "description": "Extract full-page images from a specified range of pages. Both the starting page and ending page are included. Page numbers are 1-indexed",
        "parameters": {
            "type": "object",
            "properties": {
                "start_page_num": {
                    "type": "integer",
                    "description": "The first page number for page image extraction",
                },
                "end_page_num": {
                    "type": "integer",
                    "description": "The last page number for page image extraction",
                },
            },
            "required": ["start_page_num", "end_page_num"],
        },
    },
}
get_image_tool_description = {
    "type": "function",
    "function": {
        "name": "get_image",
        "description": "Get the visual content of an image",
        "parameters": {
            "type": "object",
            "properties": {
                "image_id": {
                    "type": "string",
                    "description": "The ID of the image from which to fetch the visual content",
                }
            },
            "required": ["image_id"],
        },
    },
}
get_table_image_tool_description = {
    "type": "function",
    "function": {
        "name": "get_table_image",
        "description": "Get the screenshot of a table. Use this tool to double-check the content of the table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {
                    "type": "string",
                    "description": "The ID of the table from which to fetch the screenshot",
                }
            },
            "required": ["table_id"],
        },
    },
}

available_tools = [
    search_tool_description,
    get_section_content_tool_description,
    get_page_images_tool_description,
    get_image_tool_description,
    get_table_image_tool_description,
]
