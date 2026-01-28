from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

MODIFY_FIGURE = "MODIFY_FIGURE"
MODIFY_TEXT = "MODIFY_TEXT"
BOTH_LIGHT = "BOTH_LIGHT"


def _clamp_strength(value: Optional[float], default: float = 0.5) -> float:
    if value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num < 0:
        return 0.0
    if num > 1:
        return 1.0
    return num


@dataclass
class ModificationDecision:
    modification_target: str
    reason: str
    llm_instruction: Dict[str, str]


def decide_modification_target(
    figure_role: str,
    expected_stage: Optional[str],
    actual_stage: Optional[str],
    text_claim_strength: Optional[float],
    image_evidence_strength: Optional[float],
    image_type: Optional[str] = None,
    text_is_explanatory: bool = False,
) -> Dict[str, str]:
    """
    Returns:
    {
        "modification_target": "MODIFY_FIGURE" | "MODIFY_TEXT" | "BOTH_LIGHT",
        "reason": str
    }
    """
    t_strength = _clamp_strength(text_claim_strength)
    i_strength = _clamp_strength(image_evidence_strength)

    signals: List[Dict[str, str]] = []

    # Rule 0: 实验指标图 + 文字解释 → 优先改文
    if image_type in ["metric_curve", "quantitative_plot", "evaluation_chart", "bar_chart", "table"]:
        if text_is_explanatory:
            reason = "实验指标图且文本为图像解释，优先修订文字表述"
            return {"modification_target": MODIFY_TEXT, "reason": reason}

    # Rule 1: FigureRole 优先级（最高）
    if figure_role in ["RESULT_CLAIM", "COMPARISON_CLAIM"]:
        if i_strength >= t_strength:
            signals.append(
                {
                    "rule": "rule1",
                    "target": MODIFY_TEXT,
                    "detail": "RESULT/COMPARISON 角色下，图像证据强度不低于文字主张",
                }
            )
        else:
            signals.append(
                {
                    "rule": "rule1",
                    "target": MODIFY_FIGURE,
                    "detail": "RESULT/COMPARISON 角色默认优先改图",
                }
            )
    elif figure_role == "METHOD_REFERENCE":
        signals.append(
            {
                "rule": "rule1",
                "target": MODIFY_TEXT,
                "detail": "METHOD_REFERENCE 角色默认优先改文",
            }
        )
    elif figure_role == "ILLUSTRATIVE":
        signals.append(
            {
                "rule": "rule1",
                "target": "",
                "detail": "ILLUSTRATIVE 角色弱绑定，不单独决定方向",
            }
        )

    # Rule 2: Stage 不一致修正
    if expected_stage == "EVIDENCE" and actual_stage == "METHOD":
        signals.append(
            {
                "rule": "rule2",
                "target": MODIFY_FIGURE,
                "detail": "期望 EVIDENCE 但实际为 METHOD",
            }
        )
    elif expected_stage == "METHOD" and actual_stage == "EVIDENCE":
        signals.append(
            {
                "rule": "rule2",
                "target": MODIFY_TEXT,
                "detail": "期望 METHOD 但实际为 EVIDENCE",
            }
        )

    # Rule 3: 主张强度对比
    if t_strength > i_strength:
        signals.append(
            {
                "rule": "rule3",
                "target": MODIFY_FIGURE,
                "detail": "文字主张强度高于图像证据",
            }
        )
    elif i_strength > t_strength:
        signals.append(
            {
                "rule": "rule3",
                "target": MODIFY_TEXT,
                "detail": "图像证据强度高于文字主张",
            }
        )

    targets = [s["target"] for s in signals if s.get("target")]
    unique_targets = sorted(set(targets))

    if not targets:
        reason = (
            f"信号不足，无法稳定决策。strengths(text={t_strength:.2f}, image={i_strength:.2f})"
        )
        return {"modification_target": BOTH_LIGHT, "reason": reason}

    if len(unique_targets) > 1:
        detail = "; ".join(
            [f"{s['rule']}→{s['target']}({s['detail']})" for s in signals if s.get("target")]
        )
        reason = f"规则存在冲突，采用轻微修改策略。{detail}"
        return {"modification_target": BOTH_LIGHT, "reason": reason}

    decision = unique_targets[0]
    detail = "; ".join(
        [f"{s['rule']}({s['detail']})" for s in signals if s.get("target")]
    )
    reason = (
        f"决策={decision}。{detail}。strengths(text={t_strength:.2f}, image={i_strength:.2f})"
    )
    return {"modification_target": decision, "reason": reason}


def build_llm_instruction(modification_target: str) -> Dict[str, str]:
    if modification_target == MODIFY_TEXT:
        return {
            "focus": "text",
            "constraint": "只提出文本相关的修改建议，不要建议修改图像。",
        }
    if modification_target == MODIFY_FIGURE:
        return {
            "focus": "figure",
            "constraint": "只提出图像/图表相关的修改建议，不要建议修改文本。",
        }
    return {
        "focus": "both",
        "constraint": "只提出轻微一致性调整，避免建议大幅改动。",
    }


def build_suggestion_prompt(
    context: Dict[str, Any], decision: Dict[str, str]
) -> str:
    llm_instruction = build_llm_instruction(decision.get("modification_target", BOTH_LIGHT))
    parts = [
        "你是一名严格的论文图文一致性审稿人。",
        "请基于以下结构化信息生成一条简洁、可执行的修改建议。",
        f"决策方向: {decision.get('modification_target')}",
        f"决策理由: {decision.get('reason')}",
    ]

    if context.get("figure_role"):
        parts.append(f"FigureRole: {context.get('figure_role')}")
    if context.get("expected_stage") or context.get("actual_stage"):
        parts.append(
            f"Stage: expected={context.get('expected_stage')} actual={context.get('actual_stage')}"
        )
    if context.get("text_claim_strength") is not None or context.get(
        "image_evidence_strength"
    ) is not None:
        parts.append(
            "Strengths: "
            f"text={context.get('text_claim_strength')}, "
            f"image={context.get('image_evidence_strength')}"
        )
    if context.get("quote"):
        parts.append(f"问题描述/引用: {context.get('quote')}")

    parts.append(f"约束: {llm_instruction.get('constraint')}")
    parts.append("输出要求：仅返回建议文本，不要输出JSON或其他格式。")
    return "\n".join(parts)

