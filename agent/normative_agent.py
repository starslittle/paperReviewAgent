from __future__ import annotations

import re
from typing import Any, Dict


class NormativeAgent:
    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent

    def run(self) -> Dict[str, Any]:
        print("[NormativeAgent] start")
        res = self.doc_agent.run_normative_review()
        raw = res.get("raw", "")
        raw_original = res.get("raw_original", "")
        thinking = res.get("thinking", "")
        print(
            f"[NormativeAgent] raw_length={len(raw) if isinstance(raw, str) else 'n/a'}"
        )
        if isinstance(raw_original, str) and raw_original:
            json_match = re.search(r"<json>(.*?)</json>", raw_original, flags=re.DOTALL)
            if json_match:
                print("[NormativeAgent] json_block_begin")
                print(f"<json>{json_match.group(1).strip()}</json>")
                print("[NormativeAgent] json_block_end")
        elif isinstance(raw, str):
            json_match = re.search(r"<json>(.*?)</json>", raw, flags=re.DOTALL)
            if json_match:
                print("[NormativeAgent] json_block_begin")
                print(f"<json>{json_match.group(1).strip()}</json>")
                print("[NormativeAgent] json_block_end")
        parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}
        if isinstance(parsed, dict):
            issues = parsed.get("issues", [])
            if not isinstance(issues, list):
                print(f"[NormativeAgent] issues_not_list type={type(issues).__name__}")
                parsed["issues"] = []
            else:
                for issue in issues:
                    if isinstance(issue, dict):
                        issue["issue_type"] = "规范性"
            print(f"[NormativeAgent] issues_count={len(parsed.get('issues', []))}")
        else:
            print(f"[NormativeAgent] parsed_not_dict type={type(parsed).__name__}")
            parsed = {"issues": []}
        return {
            "raw": raw,
            "parsed": parsed,
            "thinking": thinking,
            "errors": [],
        }
