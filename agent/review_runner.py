"""
Run normative + logic review on preprocessed documents.
Outputs JSON issues per document.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from . import doc_agent
from preprocess.doc_reader import DocReader


def parse_args():
    # Force UTF-8 to avoid BOM/UTF-16 issues when reading .env
    load_dotenv(override=True, encoding="utf-8")
    parser = argparse.ArgumentParser(description="Logic-only review runner")
    parser.add_argument(
        "--preprocessed-data-dir",
        type=str,
        default="./preprocess/processed_output/",
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

    docs = get_doc_list(args.preprocessed_data_dir, args.doc_id)
    for doc_id in docs:
        data_path = os.path.join(args.preprocessed_data_dir, doc_id)
        if not os.path.exists(os.path.join(data_path, "data.pkl")):
            print(f"[Skip] {doc_id}: missing data.pkl")
            continue

        print(f"[Review] {doc_id}")
        reader = DocReader(data_path=data_path)
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
                # 将图片 ID 和页码加入 thinking 展示
                header = f"#### Image {v.get('image_id')} (Page {v.get('page')})"
                vision_thinking_list.append(f"{header}\n{v.get('thinking')}")

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
