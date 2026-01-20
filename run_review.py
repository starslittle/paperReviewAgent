"""
简化的审查运行脚本
"""
import os
import sys
from dotenv import load_dotenv

# 设置 UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv(override=True, encoding='utf-8')

# 添加路径
sys.path.insert(0, os.getcwd())

from agent import doc_agent
from preprocess.doc_reader import DocReader
import pandas as pd

def main():
    print("=" * 80)
    print("Paper Review Agent - Starting")
    print("=" * 80)

    doc_id = "bylw-pgy"
    data_path = os.path.join("preprocess/processed_output/MinerU", doc_id)
    save_dir = "sample_results"

    os.makedirs(save_dir, exist_ok=True)

    # 检查数据文件
    data_pkl = os.path.join(data_path, "data.pkl")
    if not os.path.exists(data_pkl):
        print(f"[ERROR] Data file not found: {data_pkl}")
        return

    print(f"\n[1/5] Loading document: {doc_id}")
    reader = DocReader(data_path=data_path)
    print(f"  - Total pages: {reader.num_page}")
    print(f"  - Images: {reader.image_count}")
    print(f"  - Tables: {reader.table_count}")
    print(f"  - Paragraphs: {reader.para_count}")

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
    norm_res = agent.run_normative_review()
    print(f"  - Completed. Issues: {len(norm_res.get('thinking', ''))} chars")

    print(f"\n[3/5] Running Hierarchical Logic Review...")
    logic_res = agent.run_hierarchical_logic_review()
    print(f"  - Completed. Thinking: {len(logic_res.get('thinking', ''))} chars")

    print(f"\n[4/5] Running Vision Review...")
    print(f"  - This may take several minutes...")
    vision_res = agent.run_vision_review(
        vision_model_id="qwen3-vl-flash",
        vision_api_key=vision_api_key,
        vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        include_page_image=True,
        max_images=10,  # 限制图片数量以加快测试
    )
    print(f"  - Reviewed {len(vision_res)} images")

    print(f"\n[5/5] Saving results...")

    # 解析结果
    def _parse_res(res):
        import json
        import re
        raw = res.get("raw", "")
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            return json.loads(raw[start:end+1])
        return {"issues": []}

    norm_data = _parse_res(norm_res)
    logic_data = _parse_res(logic_res)

    # 合并结果
    final_result = {
        "doc_id": doc_id,
        "normative_thinking": norm_res.get("thinking", ""),
        "logic_thinking": logic_res.get("thinking", ""),
        "vision_thinking": "\n".join([v.get("thinking", "") for v in vision_res]),
        "issues": []
    }

    # 收集所有问题
    final_result["issues"].extend(norm_data.get("issues", []))
    final_result["issues"].extend(logic_data.get("issues", []))

    # 添加视觉问题
    for v in vision_res:
        raw = v.get("raw", "")
        if raw:
            try:
                import json
                import re
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1:
                    data = json.loads(raw[start:end+1])
                    final_result["issues"].extend(data.get("issues", []))
            except:
                pass

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
    high_count = len([i for i in final_result['issues'] if i.get('severity') == 'High'])
    medium_count = len([i for i in final_result['issues'] if i.get('severity') == 'Medium'])
    low_count = len([i for i in final_result['issues'] if i.get('severity') == 'Low'])

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
