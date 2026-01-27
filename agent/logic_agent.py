from __future__ import annotations

from typing import Any, Dict


class LogicAgent:
    def __init__(self, doc_agent: Any):
        self.doc_agent = doc_agent

    def run(self) -> Dict[str, Any]:
        res = self.doc_agent.run_hierarchical_logic_review()
        raw = res.get("raw", "")
        thinking = res.get("thinking", "")
        parsed = self.doc_agent._parse_json(raw) if raw else {"issues": []}
        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            parsed["issues"] = []
        return {
            "raw": raw,
            "parsed": parsed,
            "thinking": thinking,
            "errors": [],
        }
