import base64
import copy
import glob
import os
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

from PIL import Image

from preprocess.doc_ir_builder import DocIRBuilder


def process_image(image_path: str) -> Tuple[str, str, Optional[str]]:

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

        # compress the image

        return media_type, base64_image, None

    except Exception as e:
        return "", "", f"Error processing image: {str(e)}"


class DocReader:
    """
    A class to read and process document data, converting it into an XML structure.
    Attributes:
    -----------
    data_path : str
        The path to the directory containing the document data.
    data : pandas.DataFrame
        The data read from the pickle file.
    root : xml.etree.ElementTree.Element
        The root element of the XML structure.
    image_count : int
        Counter for the number of images.
    table_count : int
        Counter for the number of tables.
    para_count : int
        Counter for the number of paragraphs.
    section_dict : dict
        Dictionary mapping section IDs to their corresponding XML elements.
    image_path_dict : dict
        Dictionary mapping image IDs to their file paths.
    table_image_path_dict : dict
        Dictionary mapping table IDs to their image file paths.
    num_page : int
        The number of pages in the document.
    Methods:
    --------
    __init__(data_path):
        Initializes the DocReader with the given data path and processes the document data.
    get_outline_root():
        Returns a deep copy of the root element with the tag changed to "Outline" and paragraphs modified.
    get_section_content(section_id):
        Returns the XML element corresponding to the given section ID.
    get_image(image_id):
        Returns the processed image for the given image ID.
    get_page_image(page_num):
        Returns the processed image for the given page number.
    get_table_image(table_id):
        Returns the processed image for the given table ID.
    search(key_word):
        Searches for the given keyword in the document and returns an XML element with the search results.
    """

    def __init__(self, data_path, max_section_depth=10):
        self.data_path = data_path
        builder = DocIRBuilder(max_section_depth=max_section_depth)
        result = builder.build_from_pkl(data_path)

        self.doc_ir = result.doc_ir
        self.root = result.root
        self.section_dict = result.section_dict
        self.image_path_dict = result.image_path_dict
        self.table_image_path_dict = result.table_image_path_dict
        self.num_page = result.num_page
        self.image_count = result.image_count
        self.table_count = result.table_count
        self.para_count = result.para_count
        self.max_section_depth = max_section_depth

    def get_outline_root(
        self, skip_para_after_page=100, disable_caption_after_page=False
    ):
        def iterator(parent):
            for child in reversed(parent):
                if len(child) >= 1 and child.tag == "Section":
                    iterator(child)
                if child.tag == "Paragraph":
                    if (
                        int(float(child.get("page_num"))) > skip_para_after_page
                    ):  # avoid too long outline
                        parent.remove(child)
                    else:
                        # 保留完整段落内容，不再截断为 first_sentence
                        pass
                if child.tag == "CSV_Table":
                    if (
                        int(float(child.get("page_num"))) > skip_para_after_page
                    ):  # avoid too long outline
                        child.text = None
                if child.tag == "Image" and disable_caption_after_page:
                    if int(float(child.get("page_num"))) > disable_caption_after_page:
                        for sub_child in child:
                            if (
                                sub_child.tag == "Caption"
                                and sub_child.text is not None
                            ):
                                # Truncate caption text to 20 characters to save context length
                                sub_child.text = sub_child.text[:20]

        root = copy.deepcopy(self.root)
        root.tag = "Outline"
        iterator(root)

        return root

    def get_section_content(self, section_id):
        return self.section_dict[section_id]

    def get_chapters(self):
        """
        Splits the document into chapters based on top-level sections (Heading 1).
        Returns a list of dicts: [{'title': '...', 'content': '...', 'section_id': '...'}]
        """
        chapters = []
        # Find all top-level sections (usually direct children of Root or Outline)
        # Assuming the structure is Root -> Section (level 1) -> ...
        # Based on preprocess logic, Section elements are nested.
        # We want the top-most Section elements.

        # In current XML structure:
        # <Document>
        #   <Section section_id="1" ...>
        #     <Title>...</Title>
        #     <Paragraph>...</Paragraph>
        #     <Section section_id="1.1" ...>

        for child in self.root:
            if child.tag == "Section":
                # This is a top-level chapter
                sec_id = child.get("section_id")

                # Extract title
                title_text = "Unknown Chapter"
                for node in child:
                    if (
                        node.tag == "Heading" and node.text
                    ):  # Heading tag inside Section
                        title_text = node.text
                        break

                # Extract content (recursively or just plain text of this subtree)
                # We need a method to get full text of a subtree
                content_text = "".join(child.itertext())

                chapters.append(
                    {"section_id": sec_id, "title": title_text, "content": content_text}
                )

        # If no sections found (e.g. flat structure), treat whole doc as one chapter
        if not chapters:
            full_text = "".join(self.root.itertext())
            chapters.append(
                {"section_id": "Full", "title": "Full Document", "content": full_text}
            )

        return chapters

    def get_image(self, image_id):

        image_path = self.data_path + "/figures/" + self.image_path_dict[image_id]
        return process_image(image_path)

    def get_page_image(self, page_num):

        index_string = "%04d" % (int(page_num) - 1)
        image_path = self.data_path + "/page_images/page_" + index_string + ".png"
        return process_image(image_path)

    def get_table_image(self, table_id):
        raw_path = self.table_image_path_dict[table_id]
        if os.path.isabs(raw_path):
            image_path = raw_path
        else:
            candidate = os.path.join(self.data_path, raw_path)
            if os.path.exists(candidate):
                image_path = candidate
            else:
                image_path = os.path.join(self.data_path, "table_images", raw_path)
        return process_image(image_path)

    def search(self, key_word):
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
                ):  # heading
                    item = ET.SubElement(
                        result_root,
                        "Item",
                        type="Section",
                        section_id=curr_section_id,
                        page_num=curr.get("start_page_num"),
                    )
                    item.text = curr[0].text  # get heading

            elif curr.tag in ["Paragraph", "CSV_Table"]:
                if key_word in curr.text.lower():
                    item = ET.SubElement(
                        result_root,
                        "Item",
                        type=curr.tag,
                        section_id=curr_section_id,
                        page_num=curr.get("page_num"),
                    )
                    item.text = curr.text

            elif curr.tag == "Image":
                keyword_found = False
                for child in curr:
                    if key_word in child.text.lower():
                        keyword_found = True
                        break
                if keyword_found:
                    item = ET.SubElement(
                        result_root,
                        "Image",
                        type=curr.tag,
                        image_id=curr.get("image_id"),
                        section_id=curr_section_id,
                        page_num=curr.get("page_num"),
                    )
                    for child in curr:
                        sub_item = ET.SubElement(item, child.tag)
                        sub_item.text = child.text

        return result_root


class OutlineOnlyReader:
    """仅基于 outline XML 的轻量读取器（不依赖 data.pkl）"""

    def __init__(self, outline_path: str, data_path: Optional[str] = None):
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
        self.table_source_dir = None
        self.page_images_dir = None
        self.num_page = 0
        self.image_count = 0
        self.table_count = 0
        self.para_count = 0
        self._init_assets()

    def _build_section_dict(self):
        for section in self.root.iter("Section"):
            sec_id = section.get("section_id")
            if sec_id:
                self.section_dict[sec_id] = section

    def _init_assets(self):
        max_page = 0
        for node in self.root.iter():
            page_num = node.get("page_num")
            if page_num:
                try:
                    max_page = max(max_page, int(float(page_num)))
                except Exception:
                    pass
        self.num_page = max_page

        doc_id = os.path.basename(self.outline_path).replace("outline_", "").replace(".xml", "")
        if self.data_path:
            self.page_images_dir = os.path.join(self.data_path, "page_images")
            self.table_source_dir = os.path.join(self.data_path, "table_images")

        repo_root = (
            os.path.abspath(os.path.join(self.data_path, "..", "..", "..", ".."))
            if self.data_path
            else os.path.abspath(os.path.join(os.path.dirname(self.outline_path), ".."))
        )
        image_candidate_dirs = [
            os.path.join(repo_root, "preprocess", "extract_output", "MinerU", doc_id, "images"),
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
            if self.table_source_dir:
                page_num = elem.get("page_num", "0")
                try:
                    page_num_int = int(float(page_num))
                except Exception:
                    page_num_int = 0
                fallback = f"table_{int(table_id):04d}_page_{page_num_int:04d}.png"
                self.table_image_path_dict[str(table_id)] = fallback

        self.image_count = len(self.image_path_dict)
        self.table_count = len(self.table_image_path_dict)

    def get_outline_root(self, skip_para_after_page=100, disable_caption_after_page=False):
        root = copy.deepcopy(self.root)
        return root

    def get_section_content(self, section_id):
        return self.section_dict[section_id]

    def get_chapters(self):
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
        if image_id not in self.image_path_dict or not self.image_source_dir:
            return "", "", "Outline-only reader does not support images"
        image_path = self.image_path_dict[image_id]
        if not os.path.isabs(image_path):
            image_path = os.path.join(self.image_source_dir, image_path)
        return process_image(image_path)

    def get_page_image(self, page_num):
        if not self.page_images_dir:
            return "", "", "Outline-only reader does not support page images"
        index_string = "%04d" % (int(page_num) - 1)
        image_path = os.path.join(self.page_images_dir, f"page_{index_string}.png")
        return process_image(image_path)

    def get_table_image(self, table_id):
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
            elif self.table_source_dir:
                image_path = os.path.join(self.table_source_dir, raw_path)
            else:
                image_path = candidate
        return process_image(image_path)

    def search(self, key_word):
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
