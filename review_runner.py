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

import doc_agent
import doc_reader


def parse_args():
    # Force UTF-8 to avoid BOM/UTF-16 issues when reading .env
    load_dotenv(override=True, encoding="utf-8")
    parser = argparse.ArgumentParser(description="Normative + Logic review runner")
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
        reader = doc_reader.DocReader(data_path=data_path)
        agent = doc_agent.DocAgent(
            reader,
            model_id="deepseek-chat",
            api_key=args.api_key,
            base_url=args.base_url,
        )
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_norm = executor.submit(agent.run_normative_review)
            future_logic = executor.submit(agent.run_logic_review)
            future_vision = executor.submit(
                agent.run_vision_review,
                vision_model_id=args.vision_model,
                vision_api_key=args.vision_api_key,
                vision_base_url=args.vision_base_url,
                include_page_image=True,
            )

            norm_res = future_norm.result()
            logic_res = future_logic.result()
            vision_res = future_vision.result()

        if norm_res.get("thinking"):
            print(f"\n[Thinking - Normative]:\n{norm_res['thinking']}")
        if logic_res.get("thinking"):
            print(f"\n[Thinking - Logic]:\n{logic_res['thinking']}")

        vision_thinking_str = ""
        for v_item in vision_res:
            if v_item.get("thinking"):
                vision_thinking_str += (
                    f"\n[Image {v_item['image_id']}]: {v_item['thinking']}"
                )
        if vision_thinking_str:
            print(f"\n[Thinking - Vision]:{vision_thinking_str}")

        def _parse(res):
            content = res.get("raw", "")
            try:
                # Find the JSON part
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    return json.loads(content[start : end + 1])
                return json.loads(content)
            except Exception:
                return {"issues": [], "error": "parse_failed"}

        norm_data = _parse(norm_res)
        logic_data = _parse(logic_res)

        vision_issues = []
        for v_item in vision_res:
            # v_item is {'raw': ..., 'thinking': ..., 'image_id': ...}
            parsed = _parse(v_item)
            if "issues" in parsed:
                # Inject image metadata if missing
                for issue in parsed["issues"]:
                    if not issue.get("image_id"):
                        issue["image_id"] = v_item["image_id"]
                    if not issue.get("page"):
                        issue["page"] = v_item.get("page")
                vision_issues.extend(parsed["issues"])

        merged = {
            "doc_id": doc_id,
            "normative_thinking": norm_res.get("thinking"),
            "logic_thinking": logic_res.get("thinking"),
            "vision_thinking": vision_thinking_str,
            "normative_issues": norm_data.get("issues", []),
            "logic_issues": logic_data.get("issues", []),
            "vision_issues": vision_issues,
            "issues": (
                norm_data.get("issues", [])
                + logic_data.get("issues", [])
                + vision_issues
            ),
        }

        out_path = Path(args.save_dir) / f"review_{doc_id}.json"
        out_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] saved -> {out_path}")


if __name__ == "__main__":
    main()
