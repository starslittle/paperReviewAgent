"""
简化的图文一致性检查脚本
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

def main():
    print("=" * 80)
    print("图文一致性检查 - Vision Consistency Check")
    print("=" * 80)

    doc_id = "bylw-pgy"
    data_path = os.path.join("preprocess/processed_output/MinerU", doc_id)

    # 检查数据文件
    data_pkl = os.path.join(data_path, "data.pkl")
    if not os.path.exists(data_pkl):
        print(f"[ERROR] Data file not found: {data_pkl}")
        return

    print(f"\n[1/3] Loading document: {doc_id}")
    from agent import doc_agent
    from agent.vision_agent import VisionAgent
    from preprocess.doc_reader import DocReader
    reader = DocReader(data_path=data_path)
    print(f"  - Total pages: {reader.num_page}")
    print(f"  - Images: {reader.image_count}")
    print(f"  - Tables: {reader.table_count}")

    # 初始化 Agent
    api_key = os.getenv("DEEPSEEK_API_KEY")
    vision_api_key = os.getenv("DASHSCOPE_API_KEY")

    if not vision_api_key:
        print("[ERROR] DASHSCOPE_API_KEY not found in environment variables")
        print("Please set your DashScope API key for vision checking")
        return

    agent = doc_agent.DocAgent(
        reader,
        model_id="deepseek-chat",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    print("\n[2/3] Running Vision Review (图文一致性检查)...")
    print("  - This may take several minutes...")

    try:
        vision_res = VisionAgent(agent).run_vision_review(
            vision_model_id="qwen3-vl-flash",
            vision_api_key=vision_api_key,
            vision_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            include_page_image=True,
            max_images=5,  # 限制图片数量以加快测试
        )

        print(f"  - Completed. Reviewed {len(vision_res)} images")

        print("\n[3/3] Results:")

        # 统计问题
        total_issues = 0
        for i, v in enumerate(vision_res):
            print(f"\n--- Image {i+1} ---")
            issues = v.get("issues", [])
            total_issues += len(issues)
            if issues:
                print(f"Found {len(issues)} issues:")
                for issue in issues:
                    print(f"  - {issue.get('quote', 'Unknown issue')}")
            else:
                print("No issues found.")

        print("\n" + "=" * 80)
        print("图文一致性检查完成!")
        print(f"总共发现 {total_issues} 个问题")
        print("=" * 80)

    except Exception as e:
        print(f"[ERROR] Vision review failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()