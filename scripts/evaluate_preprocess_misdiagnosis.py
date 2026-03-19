"""
评估预处理误诊风险（代理指标）

用途：
1) 读取 preprocess/processed_output/MinerU/{doc_id}/quality_report.json
2) 读取 sample_results/review/review_{doc_id}.json
3) 计算“可核验证据在 outline 中不可检索”的比例（代理误诊率）
4) 输出评估结果 JSON，便于回归比较
"""

from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List


def _normalize_text(text: str) -> str:
    s = str(text or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，。！？；：,.!?;:()（）\"“”‘’'`【】\[\]<>]", "", s)
    return s


def _load_outline_text(outline_path: str) -> str:
    if not os.path.exists(outline_path):
        return ""
    root = ET.parse(outline_path).getroot()
    xml = ET.tostring(root, encoding="unicode", method="xml")
    return _normalize_text(xml)


def _is_layout_dependent_issue(issue: Dict[str, Any]) -> bool:
    issue_type = str(issue.get("issue_type", "") or "")
    text = " ".join(
        [
            issue_type,
            str(issue.get("diagnosis", "") or ""),
            str(issue.get("quote", "") or ""),
            str(issue.get("suggestion", "") or ""),
        ]
    ).lower()
    if not any(k in issue_type for k in ["规范", "格式", "编号"]):
        return False
    layout_keywords = [
        "上标",
        "右上角",
        "字体",
        "字号",
        "行距",
        "缩进",
        "对齐",
        "加粗",
        "斜体",
        "版式",
        "排版",
        "superscript",
        "font",
        "line spacing",
        "indent",
        "alignment",
    ]
    return any(k in text for k in layout_keywords)


def evaluate(
    doc_id: str,
    processed_dir: str,
    review_dir: str,
    outline_dir: str,
) -> Dict[str, Any]:
    quality_path = os.path.join(processed_dir, doc_id, "quality_report.json")
    review_path = os.path.join(review_dir, f"review_{doc_id}.json")
    outline_path = os.path.join(outline_dir, f"outline_{doc_id}.xml")

    quality = {}
    if os.path.exists(quality_path):
        with open(quality_path, "r", encoding="utf-8") as f:
            quality = json.load(f)

    review = {}
    if os.path.exists(review_path):
        with open(review_path, "r", encoding="utf-8") as f:
            review = json.load(f)

    outline_norm = _load_outline_text(outline_path)
    all_issues = review.get("all_issues") or (
        (review.get("issues") or []) + (review.get("pending_review_issues") or [])
    )

    verifiable = [
        i
        for i in all_issues
        if str(i.get("evidence_status", "")).lower() == "verifiable"
        and len(str(i.get("evidence_quote", "") or "").strip()) >= 12
    ]
    verifiable_non_layout = [i for i in verifiable if not _is_layout_dependent_issue(i)]

    unmatched = 0
    unmatched_items: List[Dict[str, Any]] = []
    for it in verifiable_non_layout:
        eq = _normalize_text(str(it.get("evidence_quote", "") or ""))
        if not eq:
            continue
        if eq not in outline_norm:
            unmatched += 1
            unmatched_items.append(
                {
                    "issue_type": it.get("issue_type", ""),
                    "severity": it.get("severity", ""),
                    "page": it.get("page"),
                    "section": it.get("section", ""),
                    "evidence_quote": str(it.get("evidence_quote", ""))[:180],
                }
            )

    unmatched_rate = unmatched / max(len(verifiable_non_layout), 1)

    result = {
        "doc_id": doc_id,
        "paths": {
            "quality_report": quality_path,
            "review": review_path,
            "outline": outline_path,
        },
        "preprocess_quality": quality.get("preprocess_misdiagnosis_proxy", {}),
        "evidence_match_proxy": {
            "verifiable_issue_count": len(verifiable),
            "verifiable_non_layout_count": len(verifiable_non_layout),
            "unmatched_count": unmatched,
            "unmatched_rate": round(unmatched_rate, 4),
            "interpretation": "该值越低越好；反映可核验证据在预处理文本中的可检索性",
        },
        "unmatched_samples": unmatched_items[:20],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="评估预处理误诊风险代理指标")
    parser.add_argument("--doc-id", required=True)
    parser.add_argument(
        "--processed-dir",
        default="preprocess/processed_output/MinerU",
    )
    parser.add_argument(
        "--review-dir",
        default="sample_results/review",
    )
    parser.add_argument(
        "--outline-dir",
        default="sample_results/outline",
    )
    parser.add_argument(
        "--output-dir",
        default="sample_results/review",
    )
    args = parser.parse_args()

    result = evaluate(
        doc_id=args.doc_id,
        processed_dir=args.processed_dir,
        review_dir=args.review_dir,
        outline_dir=args.outline_dir,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    out_path = os.path.join(args.output_dir, f"preprocess_eval_{args.doc_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[OK] saved -> {out_path}")
    print(
        "[Summary] preprocess_risk_score=",
        result.get("preprocess_quality", {}).get("risk_score", "N/A"),
        "unmatched_rate=",
        result.get("evidence_match_proxy", {}).get("unmatched_rate", "N/A"),
    )


if __name__ == "__main__":
    main()

