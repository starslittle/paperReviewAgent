"""
Run normative + logic review on preprocessed documents.
Outputs JSON issues per document.
"""

import argparse
import json
import os
from pathlib import Path
import concurrent.futures

from dotenv import load_dotenv

from agent import doc_agent
from agent import doc_reader
from agent.logic_agent import LogicAgent
from agent.normative_agent import NormativeAgent
from agent.vision_agent import VisionAgent


def parse_args():
    # Force UTF-8 to avoid BOM/UTF-16 issues when reading .env
    load_dotenv(override=True, encoding="utf-8")
    parser = argparse.ArgumentParser(description="Logic-only review runner")
    parser.add_argument(
        "--preprocessed-data-dir",
        type=str,
        default="./preprocess/processed_output/MinerU/",
        help="Directory containing processed documents (each doc in a subfolder)",
    )
    parser.add_argument(
        "--save-dir",
        type=str,
        default="./sample_results/",
        help="Directory to save review JSON files",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        default=None,
        help="Specific document id (subfolder name). If not set, review all docs.",
    )
    parser.add_argument(
        "--outline-path",
        type=str,
        default=None,
        help="Use outline XML directly (skip data.pkl). When set, vision review is disabled.",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("DASHSCOPE_API_KEY"),
        help="API key for text model (env DASHSCOPE_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        help="Base URL for text model API",
    )
    parser.add_argument(
        "--vision-model",
        type=str,
        default="qwen3-vl-flash",
        help="Model ID for vision review",
    )
    parser.add_argument(
        "--vision-api-key",
        type=str,
        default=os.getenv("DASHSCOPE_API_KEY"),
        help="API key for vision model (default: DASHSCOPE_API_KEY for Qwen)",
    )
    parser.add_argument(
        "--vision-base-url",
        type=str,
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        help="Base URL for vision model",
    )
    # 已废弃：不再支持并行模式，只使用串行结构化流程
    # parser.add_argument(
    #     "--no-parallel",
    #     dest="parallel",
    #     action="store_false",
    #     default=True,
    #     help="Disable parallel processing (use serial mode). Default: parallel mode enabled.",
    # )
    # parser.add_argument(
    #     "--max-workers",
    #     type=int,
    #     default=3,
    #     help="Number of parallel workers for vision review (default: 3)",
    # )
    return parser.parse_args()


def get_doc_list(pre_dir: str, doc_id: str | None):
    if doc_id:
        return [doc_id]
    items = []
    for name in os.listdir(pre_dir):
        if os.path.isdir(os.path.join(pre_dir, name)):
            items.append(name)
    return sorted(items)


def main():
    args = parse_args()
    os.makedirs(args.save_dir, exist_ok=True)

    def _collect_table_caption_issues(root):
        issues = []

        def _section_title(section):
            for child in list(section):
                if child.tag == "Heading" and (child.text or "").strip():
                    return (child.text or "").strip()
            return section.get("section_id", "未知章节")

        def _walk_section(section):
            title = _section_title(section)
            for child in list(section):
                if child.tag == "CSV_Table":
                    alt_text_node = child.find("Alt_Text")
                    alt_text = (
                        (alt_text_node.text or "").strip()
                        if alt_text_node is not None
                        else ""
                    )
                    if not alt_text:
                        page_num = (
                            child.get("page_num")
                            or section.get("start_page_num")
                            or "N/A"
                        )
                        table_id = child.get("table_id", "")
                        issues.append(
                            {
                                "issue_type": "规范性",
                                "severity": "Medium",
                                "page": page_num,
                                "section": title,
                                "quote": f"表格 {table_id} 缺少标题/Alt_Text"
                                if table_id
                                else "表格缺少标题/Alt_Text",
                                "suggestion": "为该表格补充标题（Alt_Text），例如“表 2-1 …”。",
                                "table_id": table_id,
                            }
                        )
                elif child.tag == "Section":
                    _walk_section(child)

        for sec in root.findall("Section"):
            _walk_section(sec)
        return issues

    if not args.outline_path and args.doc_id:
        auto_outline = Path(args.save_dir) / f"outline_{args.doc_id}.xml"
        if auto_outline.exists():
            args.outline_path = str(auto_outline)

    if args.outline_path:
        doc_id = Path(args.outline_path).stem.replace("outline_", "")
        print(f"[Review] {doc_id} (outline-only)")
        data_path = os.path.join(args.preprocessed_data_dir, doc_id)
        if not os.path.isdir(data_path):
            data_path = None
        reader = doc_reader.OutlineOnlyReader(
            outline_path=args.outline_path,
            data_path=data_path,
        )
        agent = doc_agent.DocAgent(
            reader,
            model_id="deepseek-v3.2",
            api_key=args.api_key,
            base_url=args.base_url,
        )

        print("[Agent] [Normative] Starting...")
        normative_out = NormativeAgent(agent).run()
        normative_data = normative_out.get("parsed", {"issues": []})
        normative_thinking = normative_out.get("thinking", "")

        print("[Agent] [Logic] Starting...")
        logic_out = LogicAgent(agent).run()
        logic_data = logic_out.get("parsed", {"issues": []})
        logic_thinking = logic_out.get("thinking", "")

        if not (reader.image_path_dict or reader.table_image_path_dict):
            vision_data = {"issues": []}
            vision_thinking = ""
        else:
            print("[Agent] [Vision] Starting...")
            vision_agent = VisionAgent(agent)
            vision_out = vision_agent.run(
                vision_model_id=args.vision_model,
                vision_api_key=args.vision_api_key,
                vision_base_url=args.vision_base_url,
                include_page_image=True,
                parallel=None,  # 已废弃，不再使用并行模式
                max_workers=None,  # 已废弃，不再使用并行模式
            )
            vision_data = vision_out.get("parsed", {"issues": []})
            vision_thinking = vision_out.get("thinking", "")

        final_result = {
            "doc_id": doc_id,
            "normative_thinking": normative_thinking,
            "logic_thinking": logic_thinking,
            "vision_thinking": vision_thinking,
            "normative_issues": normative_data.get("issues", []),
            "logic_issues": logic_data.get("issues", []),
            "vision_issues": vision_data.get("issues", []),
            "issues": (
                normative_data.get("issues", [])
                + logic_data.get("issues", [])
                + vision_data.get("issues", [])
            ),
        }

        result_file = Path(args.save_dir) / f"review_{doc_id}.json"
        result_file.write_text(
            json.dumps(final_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[✓] Outline-only review saved -> {result_file}")
        return

    docs = get_doc_list(args.preprocessed_data_dir, args.doc_id)
    for doc_id in docs:
        data_path = os.path.join(args.preprocessed_data_dir, doc_id)
        if not os.path.exists(os.path.join(data_path, "data.pkl")):
            print(f"[Skip] {doc_id}: missing data.pkl")
            continue

        print(f"[Review] {doc_id}")
        reader = doc_reader.DocReader(data_path=data_path)
        agent = doc_agent.DocAgent(
            reader,
            model_id="deepseek-v3.2",
            api_key=args.api_key,
            base_url=args.base_url,
        )

        # 保存大纲 XML 以供参考
        try:
            outline_xml = agent.get_outline()
            outline_path = Path(args.save_dir) / f"outline_{doc_id}.xml"
            outline_path.write_text(outline_xml, encoding="utf-8")
            print(f"[Debug] Outline saved -> {outline_path}")
        except Exception as e:
            print(f"[Warning] Failed to save outline: {e}")

        print("[Agent] [Normative] Starting...")
        normative_out = NormativeAgent(agent).run()
        normative_issues = normative_out.get("parsed", {}).get("issues", [])
        normative_thinking_str = normative_out.get("thinking", "")

        print("[Agent] [Logic] Starting...")
        logic_out = LogicAgent(agent).run()
        logic_issues = logic_out.get("parsed", {}).get("issues", [])
        logic_thinking_str = logic_out.get("thinking", "")

        print("[Agent] [Vision] Starting...")
        vision_agent = VisionAgent(agent)
        vision_out = vision_agent.run(
            vision_model_id=args.vision_model,
            vision_api_key=args.vision_api_key,
            vision_base_url=args.vision_base_url,
            include_page_image=True,  # 启用三页窗口辅助定位
            parallel=None,  # 已废弃，不再使用并行模式
            max_workers=None,  # 已废弃，不再使用并行模式
        )

        vision_issues = vision_out.get("parsed", {}).get("issues", [])
        vision_thinking_str = vision_out.get("thinking", "")

        # 调试信息
        print(f"[Debug] Vision issues count: {len(vision_issues)}")

        merged = {
            "doc_id": doc_id,
            "normative_thinking": normative_thinking_str,
            "logic_thinking": logic_thinking_str,
            "vision_thinking": vision_thinking_str,
            "normative_issues": normative_issues,
            "logic_issues": logic_issues,
            "vision_issues": vision_issues,
            "issues": normative_issues + logic_issues + vision_issues,
        }

        out_path = Path(args.save_dir) / f"review_{doc_id}.json"
        out_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] saved -> {out_path}")


if __name__ == "__main__":
    main()
