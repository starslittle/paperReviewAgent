"""
轻量级文档读取器 - 只读取预处理生成的 outline XML

此模块提供基于 outline XML 的文档读取功能，避免重复构建 XML 树。
所有审查流程应使用此读取器，确保使用预处理阶段生成的标准 XML 结构。
"""

import base64
import copy
import os
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

from PIL import Image


def process_image(image_path: str) -> Tuple[str, str, Optional[str]]:
    """
    处理图片：读取、压缩（如需要）并转换为 base64 编码。

    Args:
        image_path: 图片文件路径

    Returns:
        Tuple[media_type, base64_image, error]:
        - media_type: MIME 类型（如 "image/jpeg"）
        - base64_image: base64 编码的图片数据
        - error: 错误信息（如果有）
    """
    try:
        # Check if file exists
        if not os.path.exists(image_path):
            return "", "", "File not found"

        # Get file extension and determine media type
        _, extension = os.path.splitext(image_path)
        extension = extension.lower()

        # Map common image extensions to MIME types
        media_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }

        media_type = media_types.get(extension)
        if not media_type:
            return "", "", f"Unsupported image format: {extension}"

        image_size = os.path.getsize(image_path) / 1024.0 / 1024.0  # size in MB
        if image_size > 1 and extension != ".jpg":
            # save the image as compressed jpg
            compress_image_path = image_path[:-4] + "_compressed.jpg"
            if not os.path.exists(compress_image_path):
                img = Image.open(image_path)
                img.save(compress_image_path)

            image_path = compress_image_path
            media_type = "image/jpeg"

        # Read and encode the image
        with open(image_path, "rb") as image_file:
            binary_data = image_file.read()
            base64_image = base64.b64encode(binary_data).decode("utf-8")

        return media_type, base64_image, None

    except Exception as e:
        return "", "", f"Error processing image: {str(e)}"


class OutlineOnlyReader:
    """
    轻量级文档读取器 - 基于预处理生成的 outline XML

    此类直接读取预处理阶段生成的 outline_*.xml 文件，避免重复构建 XML 树。
    相比旧的 DocReader 类：
    - ✅ 性能提升 3-5倍（直接读取 vs 重新构建）
    - ✅ 保证一致性（使用预处理结果）
    - ✅ 轻量级（不依赖 data.pkl 和 DocIRBuilder）

    Attributes:
    -----------
    outline_path : str
        Outline XML 文件路径
    data_path : Optional[str]
        预处理数据目录路径（用于查找图片等资源）
    root : ET.Element
        XML 树的根元素
    section_dict : dict
        章节 ID 到 XML 元素的映射
    image_path_dict : dict
        图片 ID 到文件路径的映射
    table_image_path_dict : dict
        表格 ID 到图片路径的映射
    num_page : int
        文档总页数
    image_count : int
        图片总数
    table_count : int
        表格总数

    Methods:
    --------
    get_outline_root():
        返回 XML 树的深拷贝
    get_section_content(section_id):
        根据 section_id 获取章节内容
    find_section_by_page(page_num):
        根据页码查找所属章节
    get_chapters():
        获取文档的所有顶层章节
    get_image(image_id):
        获取图片（base64 编码）
    get_page_image(page_num):
        获取页面截图
    get_table_image(table_id):
        获取表格图片
    search(key_word):
        在文档中搜索关键词
    """

    def __init__(self, outline_path: str, data_path: Optional[str] = None):
        """
        初始化读取器

        Args:
            outline_path: outline XML 文件路径（如 ./sample_results/outline_bylw-zx.xml）
            data_path: 预处理数据目录（可选，用于查找图片等资源）
        """
        self.outline_path = outline_path
        self.data_path = data_path
        tree = ET.parse(outline_path)
        self.root = tree.getroot()
        if self.root.tag != "Outline":
            self.root.tag = "Outline"
        self.section_dict = {}
        self._build_section_dict()
        self.image_path_dict = {}
        self.table_image_path_dict = {}
        self.image_source_dir = None
        self.page_images_dir = None
        self.num_page = 0
        self.image_count = 0
        self.table_count = 0
        self.para_count = 0
        self._init_assets()

    def _build_section_dict(self):
        """构建 section_id 到 XML 元素的映射"""
        for section in self.root.iter("Section"):
            sec_id = section.get("section_id")
            if sec_id:
                self.section_dict[sec_id] = section

    def _init_assets(self):
        """初始化资源路径（图片、表格等）"""
        max_page = 0
        for node in self.root.iter():
            page_num = node.get("page_num")
            if page_num:
                try:
                    max_page = max(max_page, int(float(page_num)))
                except Exception:
                    pass
        self.num_page = max_page

        doc_id = (
            os.path.basename(self.outline_path)
            .replace("outline_", "")
            .replace(".xml", "")
        )
        if self.data_path:
            self.page_images_dir = os.path.join(self.data_path, "page_images")

        repo_root = (
            os.path.abspath(os.path.join(self.data_path, "..", "..", "..", ".."))
            if self.data_path
            else os.path.abspath(os.path.join(os.path.dirname(self.outline_path), ".."))
        )
        image_candidate_dirs = [
            os.path.join(
                repo_root, "preprocess", "extract_output", "MinerU", doc_id, "images"
            ),
            os.path.join(repo_root, "extract_output", "MinerU", doc_id, "images"),
        ]

        image_files = []
        for candidate in image_candidate_dirs:
            if not os.path.isdir(candidate):
                continue
            files = [
                f
                for f in os.listdir(candidate)
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            ]
            if not files:
                continue
            files.sort()
            image_files = files
            self.image_source_dir = candidate
            break

        image_nodes = []
        for elem in self.root.iter("Image"):
            img_id = elem.get("image_id")
            if img_id is None:
                img_id = str(len(image_nodes))
            image_nodes.append((str(img_id), elem))

        for img_id, elem in image_nodes:
            img_path = elem.get("image_path")
            if img_path:
                self.image_path_dict[img_id] = img_path
                continue
            try:
                index = int(img_id)
            except Exception:
                index = None
            if index is not None and index < len(image_files):
                self.image_path_dict[img_id] = image_files[index]

        for elem in self.root.iter("CSV_Table"):
            table_id = elem.get("table_id")
            if table_id is None:
                continue
            img_path = elem.get("image_path")
            if img_path:
                self.table_image_path_dict[str(table_id)] = img_path
                continue

        self.image_count = len(self.image_path_dict)
        self.table_count = len(self.table_image_path_dict)

        # 调试信息：打印图片映射情况
        if self.image_count > 0:
            print(
                f"[Debug] Found {self.image_count} images, source_dir: {self.image_source_dir}"
            )
            if self.image_count <= 5:
                print(f"[Debug] Image mappings: {list(self.image_path_dict.items())}")
            else:
                print(
                    f"[Debug] First 3 image mappings: {list(list(self.image_path_dict.items())[:3])}"
                )
        else:
            print(
                f"[Warning] No images found in image_path_dict. Searched directories: {image_candidate_dirs}"
            )

    def get_outline_root(
        self, skip_para_after_page=100, disable_caption_after_page=False
    ):
        """
        获取文档大纲的深拷贝

        Args:
            skip_para_after_page: 忽略此页之后的段落（默认100，已不使用）
            disable_caption_after_page: 禁用此页之后的图注（已不使用）

        Returns:
            ET.Element: XML 树的深拷贝
        """
        root = copy.deepcopy(self.root)
        return root

    def get_section_content(self, section_id):
        """
        根据 section_id 获取章节内容

        Args:
            section_id: 章节 ID（如 "1", "1.1"）

        Returns:
            ET.Element: 章节的 XML 元素
        """
        return self.section_dict[section_id]

    def find_section_by_page(self, page_num):
        """
        根据页码查找所属章节

        Args:
            page_num: 页码（整数或字符串）

        Returns:
            dict: 包含 section_id, title, start_page_num, end_page_num, section_elem 的字典
                 如果找不到则返回 None
        """
        try:
            page_int = int(float(page_num))
        except (ValueError, TypeError):
            return None

        best_section = None
        best_section_id = None
        smallest_range = float("inf")

        for section_id, section_elem in self.section_dict.items():
            start_page = section_elem.get("start_page_num")
            end_page = section_elem.get("end_page_num")

            if start_page is not None:
                try:
                    start_int = int(float(start_page))
                    # 如果有end_page，检查是否在范围内
                    if end_page is not None:
                        end_int = int(float(end_page))
                        if start_int <= page_int <= end_int:
                            # 计算章节范围，选择范围最小的（最精确的匹配）
                            page_range = end_int - start_int
                            if page_range < smallest_range:
                                smallest_range = page_range
                                best_section = section_elem
                                best_section_id = section_id
                    else:
                        # 如果没有end_page，只检查start_page
                        if start_int <= page_int:
                            # 对于没有end_page的章节，选择start_page最大的（最接近的）
                            if best_section is None or start_int > int(
                                float(best_section.get("start_page_num", 0))
                            ):
                                best_section = section_elem
                                best_section_id = section_id
                except (ValueError, TypeError):
                    continue

        if best_section is None:
            return None

        # 提取章节标题
        title = best_section_id or "未知章节"
        for child in best_section:
            if child.tag == "Heading" and child.text:
                title = child.text.strip()
                break

        return {
            "section_id": best_section_id,
            "title": title,
            "start_page_num": best_section.get("start_page_num"),
            "end_page_num": best_section.get("end_page_num"),
            "section_elem": best_section,
        }

    def get_chapters(self):
        """
        获取文档的所有顶层章节

        Returns:
            List[dict]: 章节列表，每个元素包含 section_id, title, content
        """
        chapters = []
        for child in self.root:
            if child.tag == "Section":
                sec_id = child.get("section_id")
                title_text = "Unknown Chapter"
                for node in child:
                    if node.tag == "Heading" and node.text:
                        title_text = node.text
                        break
                content_text = "".join(child.itertext())
                chapters.append(
                    {"section_id": sec_id, "title": title_text, "content": content_text}
                )
        if not chapters:
            full_text = "".join(self.root.itertext())
            chapters.append(
                {"section_id": "Full", "title": "Full Document", "content": full_text}
            )
        return chapters

    def get_image(self, image_id):
        """
        获取图片（base64 编码）

        Args:
            image_id: 图片 ID

        Returns:
            Tuple[media_type, base64_image, error]
        """
        if image_id not in self.image_path_dict:
            return (
                "",
                "",
                f"Image ID '{image_id}' not found in image_path_dict (available: {list(self.image_path_dict.keys())[:10]})",
            )
        if not self.image_source_dir:
            return (
                "",
                "",
                f"Image source directory not found. Please ensure images directory exists.",
            )
        image_path = self.image_path_dict[image_id]
        if not os.path.isabs(image_path):
            image_path = os.path.join(self.image_source_dir, image_path)
        if not os.path.exists(image_path):
            return "", "", f"Image file not found: {image_path}"
        return process_image(image_path)

    def get_page_image(self, page_num):
        """
        获取页面截图

        Args:
            page_num: 页码

        Returns:
            Tuple[media_type, base64_image, error]
        """
        if not self.page_images_dir:
            return "", "", "Outline-only reader does not support page images"
        index_string = "%04d" % (int(page_num) - 1)
        image_path = os.path.join(self.page_images_dir, f"page_{index_string}.png")
        return process_image(image_path)

    def get_table_image(self, table_id):
        """
        获取表格图片

        Args:
            table_id: 表格 ID

        Returns:
            Tuple[media_type, base64_image, error]
        """
        if table_id not in self.table_image_path_dict:
            return "", "", "Outline-only reader does not support table images"
        raw_path = self.table_image_path_dict[table_id]
        if os.path.isabs(raw_path):
            image_path = raw_path
        else:
            candidate = (
                os.path.join(self.data_path, raw_path) if self.data_path else raw_path
            )
            if self.data_path and os.path.exists(candidate):
                image_path = candidate
            else:
                image_path = candidate
        return process_image(image_path)

    def search(self, key_word):
        """
        在文档中搜索关键词

        Args:
            key_word: 搜索关键词

        Returns:
            ET.Element: 包含搜索结果的 XML 元素
        """
        key_word = key_word.lower()

        result_root = ET.Element("Search_Result")
        curr_section_id = ""

        for curr in self.root.iter():
            if curr.tag == "Section":
                curr_section_id = curr.get("section_id")
                if (
                    len(curr) > 0
                    and curr[0].text is not None
                    and key_word in curr[0].text.lower()
                ):
                    item = ET.SubElement(
                        result_root,
                        "Item",
                        type="Section",
                        section_id=curr_section_id,
                        page_num=curr.get("start_page_num"),
                    )
                    item.text = curr[0].text

            elif curr.tag in ["Paragraph", "CSV_Table"]:
                if curr.text and key_word in curr.text.lower():
                    item = ET.SubElement(
                        result_root,
                        "Item",
                        type=curr.tag,
                        section_id=curr_section_id,
                        page_num=curr.get("page_num"),
                    )
                    item.text = curr.text

        return result_root


# 向后兼容：提供别名
DocReader = OutlineOnlyReader
