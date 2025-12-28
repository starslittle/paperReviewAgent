import json
import re
import time
import traceback
import xml.dom.minidom
import xml.etree.ElementTree as ET

from openai import OpenAI

from prompts import (
    actor_prompt_template,
    available_tools,
    logic_prompt,
    normative_prompt,
    normative_logic_prompt,
    reflection_prompt_template,
    reviewer_prompt,
    system_prompt,
)


def clean_xml_string(xml_str):
    cleaned = "".join(char for char in xml_str if char.isprintable() or char.isspace())
    return cleaned


class DocAgent:
    def __init__(
        self,
        doc_reader,
        model_id="deepseek-chat",
        temperature=0.0,
        max_tokens=8192,
        api_key=None,
        base_url="https://api.deepseek.com",
        tool_call_wait_time=10,
    ):
        self.doc_reader = doc_reader
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.tool_call_wait_time = tool_call_wait_time

    def _extract_plain_text(self, char_limit=6000):
        """Extract plain text segments for lightweight review."""
        texts = []
        for elem in self.doc_reader.root.iter():
            if elem.text and elem.tag in ["Paragraph", "Title", "Caption"]:
                t = elem.text.strip()
                if t:
                    texts.append(t)
            if sum(len(x) for x in texts) > char_limit:
                break
        combined = "\n".join(texts)
        return combined[:char_limit]

    def _run_simple_review(self, prompt_template):
        outline_xml = self.get_outline()
        body_text = self._extract_plain_text()
        messages = [
            {"role": "system", "content": prompt_template},
            {
                "role": "user",
                "content": f"大纲：\n{outline_xml}\n\n正文片段：\n{body_text}\n\n请按约定输出 JSON。",
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=1500,  # Increased to allow for thinking process
                temperature=0.2,
            )
            raw_content = response.choices[0].message.content

            # Extract thinking and json
            thinking = ""
            thinking_match = re.search(
                r"<thinking>(.*?)</thinking>", raw_content, re.DOTALL
            )
            if thinking_match:
                thinking = thinking_match.group(1).strip()

            return {"raw": raw_content, "thinking": thinking}
        except Exception as e:
            print(traceback.format_exc())
            return {"raw": "", "thinking": "", "error": str(e)}

    def run_normative_review(self):
        """规范性审查（Format），不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Normative Review...")
        return self._run_simple_review(normative_prompt)

    def run_logic_review(self):
        """逻辑审查（Logic），不使用工具，返回包含 raw 和 thinking 的字典。"""
        print("[Agent] Starting Logic Review...")
        return self._run_simple_review(logic_prompt)

    def run_vision_review(
        self,
        vision_model_id="qwen-vl-max",
        max_images=50,
        vision_api_key=None,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        include_page_image=False,
    ):
        """
        视觉审查（Vision），遍历文档中的图片进行检查。
        输出语言：中文
        """
        results = []

        # 1. 定义中文的 System Prompt，覆盖导入的默认 prompt
        # 确保包含 <thinking> 标签的要求，以便后续解析
        vision_system_prompt_cn = """
    你是一个专业的学术论文视觉审查助手。你的任务是审查论文中的图片及其上下文。
    
    【核心原则】
    - 只关注“图片是否支持正文/标题的论述”。
    - 如果图片内容和正文/标题不冲突、不矛盾，则视为“无问题”。
    - 忽略所有与“图文一致性”无关的视觉瑕疵（如清晰度、美观度、水印、边框等）。

    请严格按以下维度检查：
    1. 图文一致性（唯一核心）：
       - 图片下方的标题（Caption）是否准确描述了图片内容？
       - 正文提到的关键数据/趋势（如“如图3所示，准确率达到95%”），在图中是否真的体现了？如果图中显示只有80%，请报错。
       - 图片中的关键文字/符号是否与正文描述矛盾？
    
    2. 规范性（仅限严重错误）：
       - 仅当缺失关键的坐标轴含义、单位、图例导致图片完全无法理解时，才报错。
       - 编号错误（如图3在文中写成图4）。

    【严厉禁止】
    - 禁止评论图片清晰度、分辨率、字号大小。
    - 禁止评论图片是否美观、配色是否合理。
    - 禁止评论水印、Logo、装饰元素。
    - 如果图片与论文学术内容无关（如装饰/Logo/广告/二维码），直接忽略，issues返回空。

    输出格式要求：
    1. 首先在 <thinking> 标签中进行思考分析。
    2. 然后输出一个 JSON 对象，包含 "issues" 列表。每个 issue 包含 "severity" (High/Medium/Low), "issue_type", "suggestion"。
    3. 如果图片没有明显逻辑矛盾，"issues" 列表为空。
    4. **请务必使用中文进行回复。**
    """

        # Determine client to use
        client = self.client
        using_qwen = "qwen" in vision_model_id.lower()
        if using_qwen:
            key = vision_api_key or self.client.api_key
            if key is None:
                raise ValueError(
                    "vision_api_key is required when using Qwen vision models. "
                    "Please set DASHSCOPE_API_KEY or pass --vision-api-key."
                )
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=vision_base_url or self.client.base_url,
            )
        elif vision_api_key or vision_base_url:
            from openai import OpenAI

            client = OpenAI(
                api_key=vision_api_key or self.client.api_key,
                base_url=vision_base_url or self.client.base_url,
            )

        image_info_map = {}
        parent_map = {c: p for p in self.doc_reader.root.iter() for c in p}

        for elem in self.doc_reader.root.iter("Image"):
            img_id = elem.get("image_id")
            page_num = elem.get("page_num")
            caption_text = ""
            context_text = []
            # Heuristic regex to detect captions in nearby paragraphs when extractor missed them
            caption_pattern = re.compile(r"^(figure|fig\.?|图)\s*\\d+", re.IGNORECASE)

            # 1. Get Caption
            for child in elem:
                if child.tag == "Caption" and child.text:
                    caption_text = child.text

            # 2. Get Context
            parent = parent_map.get(elem)
            if parent:
                try:
                    children = list(parent)
                    idx = children.index(elem)
                    start_idx = max(0, idx - 3)
                    end_idx = min(len(children), idx + 4)

                    for i in range(start_idx, end_idx):
                        node = children[i]
                        if node.tag == "Paragraph" and node.text:
                            # If no caption captured yet, try to detect from nearby paragraphs
                            if not caption_text and caption_pattern.match(
                                node.text.strip()
                            ):
                                caption_text = node.text.strip()
                            context_text.append(node.text)
                        elif node.tag == "Heading" and node.text:
                            context_text.append(f"[Heading: {node.text}]")
                except ValueError:
                    pass

            context_str = "\n".join(context_text)

            if img_id:
                image_info_map[img_id] = {
                    "page_num": page_num,
                    "caption": caption_text,
                    "context": context_str,
                }

        # Process images
        count = 0
        total_images = len(self.doc_reader.image_path_dict)
        process_limit = min(max_images, total_images)
        print(
            f"[Agent] Found {total_images} images, will review first {process_limit} images."
        )

        for img_id, filename in self.doc_reader.image_path_dict.items():
            if count >= max_images:
                break

            media_type, base64_img, error = self.doc_reader.get_image(img_id)
            if error:
                print(f"Error loading image {img_id}: {error}")
                continue

            meta = image_info_map.get(
                img_id, {"page_num": "?", "caption": "Unknown", "context": ""}
            )

            print(
                f"[Agent] [Vision] Reviewing image {img_id} (Page {meta['page_num']}): {meta['caption'][:30]}..."
            )

            # Optionally attach the full page image to help the model see captions/footers
            page_image_block = []
            if include_page_image and str(meta["page_num"]).isdigit():
                try:
                    page_media_type, page_base64_img, page_err = (
                        self.doc_reader.get_page_image(int(float(meta["page_num"])))
                    )
                    if not page_err:
                        page_image_block.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{page_media_type};base64,{page_base64_img}"
                                },
                            }
                        )
                except Exception as e:
                    print(
                        f"[Agent] [Vision] Failed to attach page image for page {meta['page_num']}: {e}"
                    )

            # Construct message for Vision Model
            messages = [
                {"role": "system", "content": vision_system_prompt_cn},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"图片信息 (Image Info):\n- ID: {img_id}\n- 页码: {meta['page_num']}\n- 标题 (Caption): {meta['caption']}\n\n相关正文上下文 (Context):\n{meta['context']}\n\n请结合上下文审查这张图片。请务必用中文回答，并在 <thinking> 标签后输出 JSON。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_img}"
                            },
                        },
                    ]
                    + page_image_block,
                },
            ]

            try:
                response = client.chat.completions.create(
                    model=vision_model_id,
                    messages=messages,
                    max_tokens=1000,
                    temperature=0.2,  # 温度稍微调低一点，保证 JSON 格式稳定
                )
                raw_content = response.choices[0].message.content

                # Extract thinking and json
                thinking = ""
                thinking_match = re.search(
                    r"<thinking>(.*?)</thinking>", raw_content, re.DOTALL
                )
                if thinking_match:
                    thinking = thinking_match.group(1).strip()

                results.append(
                    {
                        "image_id": img_id,
                        "page": meta["page_num"],
                        "caption": meta["caption"],
                        "raw": raw_content,
                        "thinking": thinking,
                    }
                )
                count += 1

            except Exception as e:
                print(f"Vision review failed for image {img_id}: {e}")
                results.append({"image_id": img_id, "error": str(e)})

        return results

    def run_normative_logic_review(self):
        """Lightweight normative + logic review without tool calls; returns JSON string."""
        outline_xml = self.get_outline()
        body_text = self._extract_plain_text()
        messages = [
            {"role": "system", "content": normative_logic_prompt},
            {
                "role": "user",
                "content": f"大纲：\n{outline_xml}\n\n正文片段：\n{body_text}\n\n请按约定输出 JSON。",
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=800,
                temperature=0.2,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(traceback.format_exc())
            return json.dumps(
                {"issues": [], "error": f"normative_logic_review_failed: {e}"}
            )

    def get_outline(self):

        outline = self.doc_reader.get_outline_root()

        xml_string = ET.tostring(outline, encoding="unicode", method="xml")
        xml_string = clean_xml_string(xml_string)
        dom = xml.dom.minidom.parseString(xml_string)
        xml_string = (
            dom.toprettyxml(indent="  ", newl="\n")
            .split("\n", 1)[1]
            .replace("&quot;", "")
        )
        return xml_string

    def run_actor(self, question, memory, tools=available_tools):
        xml_string = self.get_outline()
        initial_prompt = actor_prompt_template.format(
            document_outline=xml_string, question=question, memory=memory
        )

        initial_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_prompt},
        ]
        final_response, messages = self.run_agent(initial_messages, tools=tools)
        return final_response, messages

    def run_reviewer(
        self,
        initial_messages,
        initial_prompt=reviewer_prompt,
        tools=available_tools,
        extract_regex=r"<final_result>(.*)</final_result>",
    ):

        messages = []

        for item in initial_messages:
            # remove id, token_usage
            if "model" in item:  # from assistant
                messages.append(item["choices"][0]["message"])

            else:  # others
                messages.append(item)

        messages.append({"role": "user", "content": initial_prompt})

        final_response, messages = self.run_agent(
            messages, tools=tools, extract_regex=extract_regex
        )
        return final_response, messages

    def run_reflection(
        self,
        initial_messages,
        memory,
        tools=available_tools,
        extract_regex=r"<updated_guideline>(.*)</updated_guideline>",
    ):

        initial_prompt = reflection_prompt_template.format(memory=memory)

        messages = []

        for item in initial_messages:
            # remove id, token_usage
            if "model" in item:  # from assistant
                messages.append(item["choices"][0]["message"])

            else:  # others
                messages.append(item)

        messages.append({"role": "user", "content": initial_prompt})

        memory_new, messages_memory = self.run_agent(
            messages, tools=tools, extract_regex=extract_regex
        )
        return memory_new, messages_memory

    def run_agent(
        self,
        initial_messages,
        tools,
        extract_regex=r"<final_result>(.*)</final_result>",
        max_num_tool=10,
        max_round=10,
    ):

        messages = initial_messages
        messages_full = messages.copy()

        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=tools,
                tool_choice="auto",
            )

            # limit the number of tools called in one turn
            if (
                response.choices[0].message.tool_calls
                and len(response.choices[0].message.tool_calls) > max_num_tool
            ):
                response.choices[0].message.tool_calls = response.choices[
                    0
                ].message.tool_calls[:max_num_tool]

            messages_full.append(response.to_dict())
            messages.append(response.choices[0].message)

            # tools are callled
            num_round = 0
            while response.choices[0].message.tool_calls:
                # Wait to reduce rate limit errors
                time.sleep(self.tool_call_wait_time)

                # LLM can call multiple functions in one turn
                tool_response_tool, tool_response_user = [], []
                for tool_call in response.choices[0].message.tool_calls:
                    tool_response = self.get_reply_for_tool(
                        {
                            "type": "tool_use",
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "input": json.loads(tool_call.function.arguments),
                        }
                    )
                    if len(tool_response) > 1:  # tool reply with image
                        tool_response_tool.append(tool_response[0])
                        tool_response_user.extend(tool_response[1:])
                    else:
                        tool_response_tool.extend(tool_response)
                # tool calls must follow by tool response
                messages.extend(tool_response_tool + tool_response_user)
                messages_full.extend(tool_response_tool + tool_response_user)

                if num_round >= max_round:
                    tool_choice = "none"
                    print("Exceed max_round, stop calling tools")
                else:
                    tool_choice = "auto"
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    tools=tools,
                    tool_choice=tool_choice,
                )

                # limit the number of tools called in one turn
                if (
                    response.choices[0].message.tool_calls
                    and len(response.choices[0].message.tool_calls) > max_num_tool
                ):
                    response.choices[0].message.tool_calls = response.choices[
                        0
                    ].message.tool_calls[:max_num_tool]
                messages_full.append(response.to_dict())
                messages.append(response.choices[0].message)
                num_round += 1

            match_result = re.search(
                extract_regex, response.choices[0].message.content, re.DOTALL
            )
            if match_result is not None:
                final_response = match_result.group(1)
            else:
                final_response = response.choices[0].message.content

            return final_response.strip(), messages_full

        except Exception as e:
            print(traceback.format_exc())
            return str(e), messages_full

    def package_content(self, item, tool_use_id=None, image_content=None):
        if image_content is not None:  # tool reply with text and image
            if "deepseek" in self.model_id.lower():
                # DeepSeek-V3 is text-only, so we skip the image and provide a placeholder
                content = f"{item}\n[Note: An image was retrieved by the tool but is not displayed here because the current model is text-only.]"
                return [
                    {"role": "tool", "content": content, "tool_call_id": tool_use_id}
                ]

            content = [{"type": "text", "text": item}]
            for item in image_content:
                media_type, base64_image = item
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{base64_image}"
                        },
                    }
                )
            # As of Nov 2024, GPT-4o doesn't support tool response with image, therefore we package image in user message
            return [
                {
                    "role": "tool",
                    "content": "The result from tool is returned in the following user message:",
                    "tool_call_id": tool_use_id,
                },
                {"role": "user", "content": content, "tool_call_id": tool_use_id},
            ]
        else:  # tool only reply with text
            content = item
            return [{"role": "tool", "content": content, "tool_call_id": tool_use_id}]

    def get_reply_for_tool(self, item, max_search_results=24, max_page_images=20):

        if item["type"] == "tool_use":
            tool_use_id = item["id"]
            if item["name"] == "search":
                keyword = item["input"]["keyword"]
                search_root = self.doc_reader.search(keyword)
                if len(search_root) == 0:
                    result_text = f"We didn't find any section or paragraph that contains the keyword {keyword}"

                else:
                    if len(search_root) > max_search_results:
                        for subelement in search_root[max_search_results:]:
                            search_root.remove(subelement)

                        result_text = f"We found {str(len(search_root))} results that contain the keyword {keyword}. To shorten response, the first {max_search_results} results are listed below:\n"
                    else:
                        result_text = f"We found {str(len(search_root))} results that contain the keyword {keyword}, listed below:\n"
                    xml_string = ET.tostring(
                        search_root, encoding="unicode", method="xml"
                    )
                    xml_string = clean_xml_string(xml_string)
                    dom = xml.dom.minidom.parseString(xml_string)
                    xml_string = dom.toprettyxml(indent="  ", newl="\n").split("\n", 1)[
                        1
                    ]
                    result_text = result_text + xml_string

                return self.package_content(result_text, tool_use_id=tool_use_id)

            elif item["name"] == "get_section_content":
                section_id = str(item["input"]["section_id"])
                if section_id not in self.doc_reader.section_dict.keys():
                    result_text = f"The section_id {section_id} is not presented in the document, here is the full list of available section_id: {list(self.doc_reader.section_dict.keys())}. Please try again."

                else:
                    section_root = self.doc_reader.get_section_content(section_id)

                    xml_string = ET.tostring(
                        section_root, encoding="unicode", method="xml"
                    )
                    xml_string = clean_xml_string(xml_string)
                    dom = xml.dom.minidom.parseString(xml_string)
                    xml_string = dom.toprettyxml(indent="  ", newl="\n").split("\n", 1)[
                        1
                    ]
                    if len(xml_string) > 30000:
                        xml_string = (
                            xml_string[:30000]
                            + "\n...The content is too long. Try to get the content in sub sections."
                        )
                        result_text = (
                            f"Here is the text content of Section {section_id}:\n"
                            + xml_string
                        )
                    else:
                        result_text = (
                            f"Here is the full text content of Section {section_id}:\n"
                            + xml_string
                        )

                return self.package_content(result_text, tool_use_id=tool_use_id)

            elif item["name"] == "get_page_images":
                start_page_num = int(item["input"]["start_page_num"])

                end_page_num = int(item["input"]["end_page_num"]) + 1
                result_text = ""
                if start_page_num < 1:
                    result_text = (
                        result_text + "The start_page_num cannot be smaller than 1. "
                    )
                elif start_page_num > self.doc_reader.num_page:
                    result_text = (
                        result_text
                        + f"The start_page_num cannot be greater than max_page_num {str(self.doc_reader.num_page)}. "
                    )
                if end_page_num < 1:
                    result_text = (
                        result_text + "The end_page_num cannot be smaller than 1. "
                    )
                elif end_page_num > self.doc_reader.num_page:
                    result_text = (
                        result_text
                        + f"The end_page_num cannot be greater than max_page_num {str(self.doc_reader.num_page)}. "
                    )

                if len(result_text) > 0:
                    return self.package_content(
                        result_text + "Please try again",
                        tool_use_id=tool_use_id,
                    )

                else:
                    image_content = []
                    # end_page_num is included
                    for page_num in range(
                        start_page_num,
                        min(end_page_num + 1, start_page_num + max_page_images + 1),
                    ):
                        media_type, base64_image, error = (
                            self.doc_reader.get_page_image(page_num)
                        )
                        if error is not None:
                            raise Exception(
                                f"Error in extracting page_image {str(page_num)}: {str(error)}"
                            )
                        image_content.append([media_type, base64_image])
                    if end_page_num > start_page_num + max_page_images:
                        result_text = f"Here are the page images for page {str(start_page_num)} to page {str(start_page_num+max_page_images)}, as the number of page images exceeds the maximum limit of {str(max_page_images)}"
                    else:
                        result_text = f"Here are the page images for page {str(start_page_num)} to page {str(end_page_num)}"
                    return self.package_content(
                        result_text,
                        tool_use_id=tool_use_id,
                        image_content=image_content,
                    )

            elif item["name"] == "get_image":
                image_id = str(item["input"]["image_id"])
                if image_id not in self.doc_reader.image_path_dict:
                    result_text = f"The image_id {image_id} is not presented in the document, here is the full list of available image_id: {list(self.doc_reader.image_path_dict.keys())}. Please try again"

                    return self.package_content(result_text, tool_use_id=tool_use_id)

                else:
                    media_type, base64_image, error = self.doc_reader.get_image(
                        image_id
                    )
                    if error is not None:
                        raise Exception(
                            f"Error in extracting image {str(image_id)}: {str(error)}"
                        )
                    result_text = f"Here is the image content for image_id {image_id}"

                    return self.package_content(
                        result_text,
                        tool_use_id=tool_use_id,
                        image_content=[[media_type, base64_image]],
                    )

            elif item["name"] == "get_table_image":
                table_id = str(item["input"]["table_id"])
                if table_id not in self.doc_reader.table_image_path_dict:
                    result_text = f"The table {table_id} doesn't have a corresponding image, here is the full list of table_id that companies an image: {list(self.doc_reader.table_image_path_dict.keys())}. Please try again."

                    return self.package_content(result_text, tool_use_id=tool_use_id)

                else:
                    media_type, base64_image, error = self.doc_reader.get_table_image(
                        table_id
                    )
                    if error is not None:
                        raise Exception(
                            f"Error in extracting image for table {str(table_id)}: {str(error)}"
                        )
                    result_text = f"Here is the image content for table_id {table_id}"

                    return self.package_content(
                        result_text,
                        tool_use_id=tool_use_id,
                        image_content=[[media_type, base64_image]],
                    )

            else:
                result_text = (
                    "Tool "
                    f"{item['name']}"
                    " is not valid, here is the list of available tools:"
                    " [search, get_section_content, get_page_images, get_image, get_table_image]."
                    " Please try again."
                )
                return self.package_content(result_text, tool_use_id=tool_use_id)
