# DocAgent 环境设置指南

## 📋 环境要求

- **Python 版本**: 3.11.14 (与 paddlepaddle 兼容)
- **包管理器**: uv 0.9.26
- **操作系统**: Windows 10/11

## 🔧 已安装的依赖包

### 核心依赖
- **AI/LLM SDKs**: `openai>=2.14.0`, `anthropic>=0.75.0`
- **数据处理**: `pandas>=2.3.3`, `openpyxl>=3.1.5`
- **图像处理**: `pillow>=12.1.0`, `PyMuPDF>=1.26.7`
- **PDF服务**: `pdfservices-sdk>=4.2.0`
- **工具**: `python-dotenv>=1.0.0`, `tqdm>=4.67.1`

### OCR 相关
- **PaddlePaddle**: `3.0.0b1` (CPU版本)
- **PaddleOCR**: `>=2.7.0`
- **额外**: `curl-cffi`, `requests`

## 🚀 使用方法

### 激活环境
```powershell
# 方法1: 使用便捷脚本
.\activate_env.ps1

# 方法2: 手动激活
.\.venv\Scripts\activate
```

### 验证环境
```powershell
python --version  # 应显示 Python 3.11.14
python -c "import pandas, pymupdf; print('环境正常')"
```

## 📝 预处理流程

### 为 bylw-pgy 文档运行完整预处理

#### 方法1: 一键脚本
```powershell
.\run_pipeline.ps1 -DocName "bylw-pgy"
```

#### 方法2: 分步执行
```powershell
# 1. PDF内容提取 (MinerU API)
python preprocess/1_run_pdf_extract.py --raw-data-dir ./data --result-dir preprocess/extract_output --doc-id bylw-pgy

# 2. 数据转换处理
python preprocess/2_process_extracted_data.py --extract-data-dir preprocess/extract_output/MinerU --save-dir preprocess/processed_output/MinerU --doc-id bylw-pgy

# 3. 生成页面图片（可选）
python preprocess/3_make_page_images.py --raw-data-dir ./data/bylw-pgy --save-dir preprocess/processed_output/MinerU --resolution 144

# 4. 构建XML树结构
python preprocess/4_build_xml_tree.py --processed-dir preprocess/processed_output/MinerU --doc-id bylw-pgy --output-dir sample_results
```

## ⚠️ 注意事项

1. **API Token**: 需要在 `.env` 文件中配置 `MINERU_API_TOKEN`
2. **网络连接**: 步骤1需要网络连接调用 MinerU API
3. **路径**: 确保所有路径都相对于项目根目录
4. **虚拟环境**: 必须先激活虚拟环境才能运行脚本

## 🔍 故障排除

### 如果遇到模块导入错误
```powershell
# 重新安装依赖
uv pip install -r requirements.txt
```

### 如果遇到编码问题
确保 PowerShell 使用 UTF-8 编码：
```powershell
$OutputEncoding = [System.Text.Encoding]::UTF8
```

### 如果需要更新环境
```powershell
# 删除旧环境
Remove-Item .venv -Recurse -Force

# 重新创建环境
uv venv --python 3.11
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

## 📞 技术支持

如遇到问题，请检查：
1. Python 版本是否为 3.11.x
2. 虚拟环境是否已激活
3. 所有依赖是否正确安装
4. API Token 是否正确配置