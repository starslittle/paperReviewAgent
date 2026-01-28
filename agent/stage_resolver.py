from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class StageResolution:
    final_stage: str
    votes: Dict[str, Any]
    image_type: Optional[str] = None


METRIC_IMAGE_TYPES = {
    "metric_curve",
    "quantitative_plot",
    "evaluation_chart",
    "bar_chart",
    "table",
}
METHOD_IMAGE_TYPES = {
    "method_diagram",
    "architecture_diagram",
    "flowchart",
    "framework_diagram",
}


def is_metric_chart(image_type: Optional[str]) -> bool:
    return image_type in METRIC_IMAGE_TYPES


def is_method_diagram(image_type: Optional[str]) -> bool:
    return image_type in METHOD_IMAGE_TYPES


def normalize_image_type(raw_type: Optional[str], context_text: str = "") -> Optional[str]:
    raw = (raw_type or "").strip().lower()
    text = (context_text or "").lower()

    def has_any(keywords):
        return any(k in raw or k in text for k in keywords)

    if has_any(["precision", "recall", "f1", "f-score", "roc", "auc", "loss", "mAP", "iou", "pr curve"]):
        return "metric_curve"
    if has_any(["curve", "曲线", "折线", "折线图", "line chart"]):
        return "metric_curve"
    if has_any(["bar", "histogram", "柱状", "条形", "bar chart"]):
        return "bar_chart"
    if has_any(["table", "表格", "数据表"]):
        return "table"
    if has_any(["plot", "scatter", "scatter plot", "quantitative", "定量", "对比图", "对比"]):
        return "quantitative_plot"
    if has_any(["evaluation", "assessment", "performance", "评估", "评测", "性能", "实验结果"]):
        return "evaluation_chart"
    if has_any(["flowchart", "流程图", "流程", "步骤"]):
        return "flowchart"
    if has_any(["architecture", "架构", "network", "网络结构", "框架", "framework"]):
        return "architecture_diagram"
    if has_any(["pipeline", "method", "algorithm", "方法", "算法", "模型结构", "结构图"]):
        return "method_diagram"
    return None


class StageResolver:
    heading_weight = 0.2
    image_weight = 0.45
    text_role_weight = 0.65

    def _infer_heading_stage(self, section_title: str) -> Optional[str]:
        title = (section_title or "").lower()
        if any(k in title for k in ["训练", "构建", "算法", "training", "build", "algorithm"]):
            return "METHOD"
        if any(
            k in title
            for k in ["评估", "实验", "结果", "性能", "evaluation", "experiment", "result", "performance"]
        ):
            return "EVIDENCE"
        return None

    def _infer_image_stage(self, image_type: Optional[str]) -> Optional[str]:
        if is_metric_chart(image_type):
            return "EVIDENCE"
        if is_method_diagram(image_type):
            return "METHOD"
        return None

    def _infer_text_role_stage(self, text_role: str) -> Optional[str]:
        role = (text_role or "").strip().upper()
        if role in ["RESULT_CLAIM", "COMPARISON_CLAIM"]:
            return "EVIDENCE"
        if role == "METHOD_REFERENCE":
            return "METHOD"
        return None

    def _weighted_vote(
        self,
        heading_stage: Optional[str],
        image_stage: Optional[str],
        text_role_stage: Optional[str],
    ) -> Tuple[str, Dict[str, Any]]:
        scores = {"METHOD": 0.0, "EVIDENCE": 0.0}

        def add(stage: Optional[str], weight: float):
            if stage in scores:
                scores[stage] += weight

        add(heading_stage, self.heading_weight)
        add(image_stage, self.image_weight)
        add(text_role_stage, self.text_role_weight)

        max_score = max(scores.values()) if scores else 0.0
        if max_score == 0.0:
            final_stage = "UNKNOWN"
        else:
            final_stage = "EVIDENCE" if scores["EVIDENCE"] >= scores["METHOD"] else "METHOD"

        votes = {
            "heading": {"stage": heading_stage, "weight": self.heading_weight},
            "image": {"stage": image_stage, "weight": self.image_weight},
            "text_role": {"stage": text_role_stage, "weight": self.text_role_weight},
            "scores": scores,
        }
        return final_stage, votes

    def resolve(
        self,
        section_title: str,
        image_type: Optional[str],
        text_role: str,
    ) -> StageResolution:
        heading_stage = self._infer_heading_stage(section_title)
        image_stage = self._infer_image_stage(image_type)
        text_role_stage = self._infer_text_role_stage(text_role)

        # 标题仅为弱信号：若无图像与文本强信号，则不判定
        if image_stage is None and text_role_stage is None:
            final_stage = "UNKNOWN"
            votes = {
                "heading": {"stage": heading_stage, "weight": self.heading_weight},
                "image": {"stage": image_stage, "weight": self.image_weight},
                "text_role": {"stage": text_role_stage, "weight": self.text_role_weight},
                "scores": {"METHOD": 0.0, "EVIDENCE": 0.0},
            }
            return StageResolution(final_stage=final_stage, votes=votes, image_type=image_type)

        final_stage, votes = self._weighted_vote(heading_stage, image_stage, text_role_stage)
        return StageResolution(final_stage=final_stage, votes=votes, image_type=image_type)
