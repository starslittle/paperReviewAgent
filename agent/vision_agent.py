from __future__ import annotations

from typing import Any, Dict, List, Optional


class VisionAgent:
    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent

    def run(
        self,
        vision_model_id: str,
        vision_api_key: Optional[str],
        vision_base_url: Optional[str],
        include_page_image: bool,
    ) -> Dict[str, Any]:
        res_list = self.doc_agent.run_vision_review(
            vision_model_id=vision_model_id,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            include_page_image=include_page_image,
        )

        issues: List[Dict[str, Any]] = []
        thinking_list: List[str] = []

        for res in res_list:
            if not isinstance(res, dict) or "error" in res:
                continue
            raw = res.get("raw", "")
            parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}
            if isinstance(parsed, list):
                parsed_issues = parsed
            elif isinstance(parsed, dict):
                parsed_issues = parsed.get("issues", [])
            else:
                parsed_issues = []
            if isinstance(parsed_issues, list):
                for iss in parsed_issues:
                    if isinstance(iss, dict) and not iss.get("page"):
                        iss["page"] = res.get("page")
                issues.extend([iss for iss in parsed_issues if isinstance(iss, dict)])
            if res.get("thinking"):
                header = (
                    f"### 🖼️ 图片分析: {res.get('image_id', 'unknown')} "
                    f"(第 {res.get('page', '?')} 页)"
                )
                thinking_list.append(f"{header}\n\n{res.get('thinking')}")

        thinking = "\n\n".join(thinking_list)
        return {
            "raw": res_list,
            "parsed": {"issues": issues},
            "thinking": thinking,
            "errors": [],
        }
