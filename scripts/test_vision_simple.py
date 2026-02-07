"""
简单的图文一致性测试
"""

import os
import sys

# 保证从项目根目录查找路径（在 scripts/ 下运行时）
_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
if os.getcwd() != _root:
    os.chdir(_root)

# 检查环境
print("Python version:", sys.version)
print("Current directory:", os.getcwd())

# 检查关键文件
files_to_check = [
    "preprocess/processed_output/MinerU/bylw-pgy/data.pkl",
    "agent/doc_agent.py",
    "agent/prompts.py",
]

print("\nChecking files:")
for file_path in files_to_check:
    exists = os.path.exists(file_path)
    print(f"  {file_path}: {'✓' if exists else '✗'}")

# 检查环境变量
import os

env_vars = ["DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"]
print("\nChecking environment variables:")
for var in env_vars:
    value = os.getenv(var)
    has_value = bool(value)
    print(
        f"  {var}: {'✓' if has_value else '✗'} {'(set)' if has_value else '(not set)'}"
    )

# 尝试导入模块
print("\nTesting imports:")
try:
    from agent import doc_agent

    print("  ✓ doc_agent imported successfully")
except ImportError as e:
    print(f"  ✗ doc_agent import failed: {e}")

try:
    from agent.doc_reader import OutlineOnlyReader

    print("  ✓ OutlineOnlyReader imported successfully")
except ImportError as e:
    print(f"  ✗ OutlineOnlyReader import failed: {e}")

print("\nTest completed.")
