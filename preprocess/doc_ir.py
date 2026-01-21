from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class DocIRMeta:
    doc_id: str
    source_type: str
    created_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z"
    )
    page_count: int = 0
    input_files: List[str] = field(default_factory=list)


@dataclass
class SectionNode:
    section_id: str
    title: str
    level: int
    start_page_num: Optional[int] = None
    end_page_num: Optional[int] = None


@dataclass
class TextStyle:
    font_size: Optional[float] = None
    font_family: Optional[str] = None


@dataclass
class TextBlock:
    block_id: str
    block_type: str
    text: str
    page_num: Optional[int] = None
    section_id: Optional[str] = None
    style: TextStyle = field(default_factory=TextStyle)


@dataclass
class FigureNode:
    figure_id: str
    page_num: Optional[int]
    caption: Optional[str] = None
    image_path: Optional[str] = None
    alt_text: Optional[str] = None


@dataclass
class TableNode:
    table_id: str
    page_num: Optional[int]
    caption: Optional[str] = None
    content: Optional[str] = None
    image_path: Optional[str] = None
    alt_text: Optional[str] = None


@dataclass
class DocIR:
    meta: DocIRMeta
    sections: List[SectionNode] = field(default_factory=list)
    blocks: List[TextBlock] = field(default_factory=list)
    figures: List[FigureNode] = field(default_factory=list)
    tables: List[TableNode] = field(default_factory=list)
