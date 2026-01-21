"""
Run normative + logic review on preprocessed documents.
Outputs JSON issues per document.
"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from agent import doc_agent
from agent import doc_reader


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
        default=os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"),
        help="API key (env DEEPSEEK_API_KEY/OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        help="Base URL for the API",
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
            model_id="deepseek-chat",
            api_key=args.api_key,
            base_url=args.base_url,
        )

        # 1. 规范性审查
        print(f"[Agent] [Normative] Starting...")
        norm_res = agent.run_normative_review()

        # 2. 逻辑审查 (Map-Reduce 层次化)
        print(f"[Agent] [Logic] Starting...")
        logic_res = agent.run_hierarchical_logic_review()

        def _parse(res):
            content = res.get("raw", "")
            try:
                if not content:
                    return {"issues": []}
                json_block = re.search(r"<json>(.*?)</json>", content, flags=re.DOTALL)
                if json_block:
                    content = json_block.group(1).strip()
                cleaned = re.sub(
                    r"<thinking>.*?</thinking>", "", content, flags=re.DOTALL
                ).strip()
                cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
                decoder = json.JSONDecoder()
                for candidate in [cleaned, content]:
                    for idx in range(len(candidate)):
                        if candidate[idx] not in "{[":
                            continue
                        try:
                            parsed, _ = decoder.raw_decode(candidate[idx:])
                            if "issues" in parsed and not isinstance(parsed["issues"], list):
                                parsed["issues"] = []
                            return parsed
                        except Exception:
                            continue
                parsed = json.loads(cleaned)
                if "issues" in parsed and not isinstance(parsed["issues"], list):
                    parsed["issues"] = []
                return parsed
            except Exception:
                return {"issues": [], "error": "parse_failed"}

        vision_data = {"issues": []}
        vision_thinking = ""
        if reader.image_path_dict or reader.table_image_path_dict:
            print(f"[Agent] [Vision] Starting...")
            vision_res = agent.run_vision_review(
                vision_model_id=args.vision_model,
                vision_api_key=args.vision_api_key,
                vision_base_url=args.vision_base_url,
                include_page_image=True,
            )
            vision_issues = []
            vision_thinking_list = []
            for v in vision_res:
                if "error" in v:
                    continue
                v_data = _parse(v)
                v_issues = v_data.get("issues", [])
                if isinstance(v_issues, list):
                    for iss in v_issues:
                        if isinstance(iss, dict) and not iss.get("page"):
                            iss["page"] = v.get("page")
                    vision_issues.extend([iss for iss in v_issues if isinstance(iss, dict)])
                if v.get("thinking"):
                    # 统一使用明显的 Markdown 标题格式
                    header = f"### 🖼️ 图片分析: {v.get('image_id', 'unknown')} (第 {v.get('page', '?')} 页)"
                    vision_thinking_list.append(f"{header}\n\n{v.get('thinking')}")
            vision_data = {"issues": vision_issues}
            if vision_thinking_list:
                vision_thinking = "\n".join(vision_thinking_list)

        norm_data = _parse(norm_res)
        logic_data = _parse(logic_res)
        table_caption_issues = _collect_table_caption_issues(reader.root)
        norm_issues = norm_data.get("issues", [])
        if not isinstance(norm_issues, list):
            norm_issues = []
        norm_issues.extend(table_caption_issues)
        norm_data["issues"] = norm_issues

        final_result = {
            "doc_id": doc_id,
            "normative_thinking": norm_res.get("thinking", ""),
            "logic_thinking": logic_res.get("thinking", ""),
            "vision_thinking": vision_thinking,
            "issues": [],
        }
        final_result["issues"].extend(norm_data.get("issues", []))
        final_result["issues"].extend(logic_data.get("issues", []))
        final_result["issues"].extend(vision_data.get("issues", []))

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
            model_id="deepseek-chat",
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

        # 1. 规范性审查
        print(f"[Agent] [Normative] Starting...")
        norm_res = agent.run_normative_review()

        # 2. 逻辑审查 (Map-Reduce 层次化)
        print(f"[Agent] [Logic] Starting...")
        logic_res = agent.run_hierarchical_logic_review()

        # 3. 视觉审查
        print(f"[Agent] [Vision] Starting...")
        vision_res = agent.run_vision_review(
            vision_model_id=args.vision_model,
            vision_api_key=args.vision_api_key,
            vision_base_url=args.vision_base_url,
            include_page_image=True,  # 启用三页窗口辅助定位
        )

        if logic_res.get("thinking"):
            print(f"\n[Thinking - Logic]:\n{logic_res['thinking']}")

        def _parse(res):
            content = res.get("raw", "")
            try:
                json_block = re.search(r"<json>(.*?)</json>", content, flags=re.DOTALL)
                if json_block:
                    content = json_block.group(1).strip()
                content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
                # Find the JSON part
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    json_str = content[start : end + 1]
                    parsed = json.loads(json_str)
                    # 确保 issues 字段是列表
                    if "issues" in parsed and not isinstance(parsed["issues"], list):
                        print(
                            f"[Warning] 'issues' field is not a list, converting: {type(parsed['issues'])}"
                        )
                        parsed["issues"] = []
                    return parsed
                return json.loads(content)
            except Exception as e:
                print(f"[Warning] JSON parse failed: {str(e)[:100]}")
                print(
                    f"[Warning] Content preview: {content[:200] if content else '(empty)'}..."
                )
                return {"issues": [], "error": "parse_failed"}

        norm_data = _parse(norm_res)
        logic_data = _parse(logic_res)
        table_caption_issues = _collect_table_caption_issues(reader.root)
        norm_issues = norm_data.get("issues", [])
        if not isinstance(norm_issues, list):
            norm_issues = []
        norm_issues.extend(table_caption_issues)
        norm_data["issues"] = norm_issues

        # 调试信息
        print(f"[Debug] Normative issues count: {len(norm_data.get('issues', []))}")
        print(f"[Debug] Logic issues count: {len(logic_data.get('issues', []))}")

        vision_issues = []
        vision_thinking_list = []
        for v in vision_res:
            if "error" in v:
                continue

            # Debug: 标记是哪个图片的JSON解析
            img_id = v.get("image_id", "unknown")
            print(f"[Debug] Parsing vision result for image {img_id}")

            v_data = _parse(v)
            v_issues = v_data.get("issues", [])

            # 健壮性检查：确保 v_issues 是列表且包含字典
            if not isinstance(v_issues, list):
                print(
                    f"[Warning] Vision issues is not a list for image {v.get('image_id')}: {type(v_issues)}"
                )
                continue

            # 补全页码
            for idx, iss in enumerate(v_issues):
                # 确保 iss 是字典
                if not isinstance(iss, dict):
                    print(
                        f"[Warning] Skipping non-dict issue for image {img_id}, index {idx}: {type(iss)}, value={str(iss)[:100]}"
                    )
                    continue
                if not iss.get("page"):
                    iss["page"] = v.get("page")

            # 只添加有效的字典类型 issue
            valid_issues = [iss for iss in v_issues if isinstance(iss, dict)]
            vision_issues.extend(valid_issues)

            if v.get("thinking"):
                # 将图片 ID 和页码加入 thinking 展示，使用统一标题格式
                header = f"### 🖼️ 图片分析: {v.get('image_id')} (第 {v.get('page')} 页)"
                vision_thinking_list.append(f"{header}\n\n{v.get('thinking')}")

        vision_thinking_str = "\n\n".join(vision_thinking_list)

        merged = {
            "doc_id": doc_id,
            "normative_thinking": norm_res.get("thinking"),
            "logic_thinking": logic_res.get("thinking"),
            "vision_thinking": vision_thinking_str,
            "normative_issues": norm_data.get("issues", []),
            "logic_issues": logic_data.get("issues", []),
            "vision_issues": vision_issues,
            "issues": norm_data.get("issues", [])
            + logic_data.get("issues", [])
            + vision_issues,
        }

        out_path = Path(args.save_dir) / f"review_{doc_id}.json"
        out_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] saved -> {out_path}")


if __name__ == "__main__":
    main()
