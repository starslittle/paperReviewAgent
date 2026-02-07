"""
简化的审查运行脚本
"""

import os
import sys
from dotenv import load_dotenv

# 设置 UTF-8 编码
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

load_dotenv(override=True, encoding="utf-8")

# 添加路径
sys.path.insert(0, os.getcwd())

from agent import doc_agent
from agent import doc_reader
from agent.logic_agent import LogicAgent
from agent.normative_agent import NormativeAgent
from agent.vision_agent import VisionAgent
import pandas as pd


def main():
    print("=" * 80)
    print("Paper Review Agent - Starting")
    print("=" * 80)

    doc_id = "bylw-pgy"
    data_path = os.path.join("preprocess/processed_output/MinerU", doc_id)
    save_dir = "sample_results"

    os.makedirs(save_dir, exist_ok=True)

    # 使用预处理生成的 outline XML（避免重复构建）
    outline_path = os.path.join(save_dir, f"outline_{doc_id}.xml")
    if not os.path.exists(outline_path):
        print(f"[ERROR] Outline XML not found: {outline_path}")
        print(
            f"[HINT] Please run preprocessing first: ./scripts/run_pipeline.ps1 -DocName {doc_id}"
        )
        return

    print(f"\n[1/5] Loading document: {doc_id}")
    reader = doc_reader.OutlineOnlyReader(
        outline_path=outline_path,
        data_path=data_path,
    )
    print(f"  - Total pages: {reader.num_page}")
    print(f"  - Images: {len(reader.image_path_dict)}")
    print(f"  - Tables: {len(reader.table_image_path_dict)}")

    # 初始化 Agent
    api_key = os.getenv("DEEPSEEK_API_KEY")
    vision_api_key = os.getenv("DASHSCOPE_API_KEY")

    agent = doc_agent.DocAgent(
        reader,
        model_id="deepseek-chat",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    print(f"\n[2/5] Running Normative Review...")
    norm_res = NormativeAgent(agent).run()
    print(f"  - Completed. Issues: {len(norm_res.get('thinking', ''))} chars")

    print(f"\n[3/5] Running Hierarchical Logic Review...")
    logic_res = LogicAgent(agent).run()
    print(f"  - Completed. Thinking: {len(logic_res.get('thinking', ''))} chars")

    print(f"\n[4/5] Running Vision Review...")
    print(f"  - This may take several minutes...")
    vision_res = VisionAgent(agent).run_vision_review(
        vision_model_id="qwen3-vl-flash",
        vision_api_key=vision_api_key,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        include_page_image=True,
        max_images=10,  # 限制图片数量以加快测试
    )
    print(f"  - Reviewed {len(vision_res)} images")

    print(f"\n[5/5] Saving results...")

    # 解析结果 - 使用 agent 的解析方法
    def _parse_json_response(raw_content):
        """Helper to safely extract and parse JSON from LLM response."""
        import json
        import re

        if not raw_content:
            return {"issues": []}

        # 提取 <json> 标签内容
        json_block = re.search(r"<json>(.*?)</json>", raw_content, re.DOTALL)
        if json_block:
            raw_content = json_block.group(1).strip()

        # 移除 <thinking> 标签
        cleaned = re.sub(
            r"<thinking>.*?</thinking>", "", raw_content, flags=re.DOTALL
        ).strip()

        # 移除 markdown 代码块标记
        cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()

        if not cleaned:
            return {"issues": []}

        # 尝试修复截断的JSON
        if cleaned and cleaned[-1] not in "}]]":
            print(f"[Warning] JSON appears to be truncated, attempting to fix...")
            if cleaned.count('"') % 2 != 0:
                cleaned += '"'
            open_braces = cleaned.count("{") - cleaned.count("}")
            open_brackets = cleaned.count("[") - cleaned.count("]")
            for _ in range(open_brackets):
                cleaned += "]"
            for _ in range(open_braces):
                cleaned += "}"
            print(
                f"[Fix] Attempted to close {open_brackets} brackets and {open_braces} braces"
            )

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"[Error] JSON parse failed: {e}")
            print(f"[Error] Raw content preview: {raw_content[:200]}...")
            # 尝试提取第一个完整的JSON对象
            try:
                start = cleaned.find("{")
                if start == -1:
                    return {"issues": []}
                # 找到匹配的闭合括号
                brace_count = 0
                in_string = False
                escape_next = False
                for i in range(start, len(cleaned)):
                    char = cleaned[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if char == "\\":
                        escape_next = True
                        continue
                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if not in_string:
                        if char == "{":
                            brace_count += 1
                        elif char == "}":
                            brace_count -= 1
                            if brace_count == 0:
                                json_str = cleaned[start : i + 1]
                                return json.loads(json_str)
            except:
                pass
            return {"issues": []}

    norm_data = _parse_json_response(norm_res.get("raw", ""))
    logic_data = _parse_json_response(logic_res.get("raw", ""))

    # 合并结果
    final_result = {
        "doc_id": doc_id,
        "normative_thinking": norm_res.get("thinking", ""),
        "logic_thinking": logic_res.get("thinking", ""),
        "vision_thinking": "",
        "issues": [],
    }

    # 收集所有问题
    final_result["issues"].extend(norm_data.get("issues", []))
    final_result["issues"].extend(logic_data.get("issues", []))

    # 添加视觉问题（ARG流程直接返回结构化 issues）
    for v in vision_res:
        issues = v.get("issues", [])
        for issue in issues:
            if not issue.get("image_id"):
                issue["image_id"] = v.get("figure_id", "")
            if not issue.get("page"):
                issue["page"] = v.get("page", None)
        final_result["issues"].extend(issues)

    # 保存结果
    import json

    result_file = os.path.join(save_dir, f"review_{doc_id}.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)

    print(f"  - Saved to: {result_file}")
    print(f"  - Total issues: {len(final_result['issues'])}")

    print(f"\n" + "=" * 80)
    print("Review Complete!")
    print("=" * 80)

    # 统计
    high_count = len([i for i in final_result["issues"] if i.get("severity") == "High"])
    medium_count = len(
        [i for i in final_result["issues"] if i.get("severity") == "Medium"]
    )
    low_count = len([i for i in final_result["issues"] if i.get("severity") == "Low"])

    print(f"\nSeverity Distribution:")
    print(f"  High:   {high_count}")
    print(f"  Medium: {medium_count}")
    print(f"  Low:    {low_count}")
    print(f"  Total:  {len(final_result['issues'])}")

    # 生成 HTML 报告
    print(f"\nGenerating HTML report...")
    from generate_report import generate_html

    html_file = os.path.join(save_dir, f"report_{doc_id}.html")
    generate_html(result_file, html_file)
    print(f"  - HTML report: {html_file}")


if __name__ == "__main__":
    main()
