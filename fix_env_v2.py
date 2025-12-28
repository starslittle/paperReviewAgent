content = """ADOBE_CLIENT_ID=a7958496554a40489a43aa23226e0f80
ADOBE_CLIENT_SECRET=p8e-nQXgvazaRFCEfQmdY96je61Lze3ignoA
OPENAI_API_KEY=your_openai_api_key_here
DEEPSEEK_API_KEY=sk-e93bee09615e4fc9bcaaa9b770c77ebd
DEEPSEEK_BASE_URL=https://api.deepseek.com
DASHSCOPE_API_KEY=sk-d128331f08b3481399b90ad038f73413
"""
import os
print(f"Current working directory: {os.getcwd()}")
with open('.env', 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated .env file successfully.")

