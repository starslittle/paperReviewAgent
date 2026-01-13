import os
import requests
import urllib3
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
if not os.getenv("MINERU_API_TOKEN"):
    load_dotenv("../.env")

def test_mineru_connectivity():
    api_token = os.getenv("MINERU_API_TOKEN")
    base_url = os.getenv("MINERU_API_BASE_URL", "https://mineru.net/api/v4").rstrip("/")
    
    print("=== MinerU API 连通性测试 ===")
    print(f"Base URL: {base_url}")
    print(f"Token (前5位): {api_token[:5] if api_token else '未找到'}...")
    
    if not api_token:
        print("[错误] 未在 .env 文件中找到 MINERU_API_TOKEN")
        return

    # 测试 1: 直接请求（带代理检查）
    print("\n[测试 1] 标准请求测试...")
    try:
        response = requests.get(base_url, timeout=10)
        print(f"状态码: {response.status_code}")
        print("成功连接到服务器！")
    except Exception as e:
        print(f"请求失败: {e}")

    # 测试 2: 禁用 SSL 验证和系统代理
    print("\n[测试 2] 禁用 SSL 验证 + 禁用系统代理 (trust_env=False)...")
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(base_url, timeout=10, verify=False)
        print(f"状态码: {response.status_code}")
        print("成功连接 (安全绕过模式)！")
    except Exception as e:
        print(f"请求失败: {e}")

    # 测试 3: 检查系统代理环境变量
    print("\n[测试 3] 检查系统代理环境变量...")
    proxies = ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']
    found_any = False
    for p in proxies:
        val = os.environ.get(p)
        if val:
            print(f"发现代理环境变量: {p} = {val}")
            found_any = True
    if not found_any:
        print("未发现任何代理环境变量。")

    # 测试 4: 具体任务提交接口测试 (GET 请求探测)
    print("\n[测试 4] 具体接口探测 (GET /extract/task)...")
    url_task = f"{base_url}/extract/task"
    try:
        # 虽然该接口通常只接受 POST，但 GET 探测可以验证握手和权限
        headers = {"Authorization": f"Bearer {api_token}"}
        response = session.get(url_task, headers=headers, timeout=10, verify=False)
        print(f"状态码: {response.status_code}")
        print(f"响应内容: {response.text[:100]}...")
        if response.status_code in [200, 401, 405]:
            print("接口物理连通！(405 说明接口存在但需用 POST, 401 说明 Token 校验正常)")
    except Exception as e:
        print(f"接口探测失败: {e}")

if __name__ == "__main__":
    test_mineru_connectivity()
