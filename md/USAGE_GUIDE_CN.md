# DocAgent 项目运行指南

本指南详细介绍了如何从零开始运行 DocAgent 项目，包括 PDF 预处理、OCR 增强以及最终的智能审查流程。

## 📋 环境准备

### 1. 基础依赖安装

在项目根目录下执行：

```bash
pip install pdfservices-sdk openpyxl pandas PyMuPDF openai pillow python-dotenv
```

### 2. OCR 依赖安装（可选，推荐）

如果需要使用 OCR 标题修正功能，请安装 PaddlePaddle 和 PaddleOCR：

- **CPU 版本**:
  ```bash
  pip install paddlepaddle paddleocr
  ```
- **GPU 版本** (推荐有显卡的用户):
  ```bash
  pip install paddlepaddle-gpu paddleocr
  ```

### 3. 配置 API 密钥

在项目根目录下创建 `.env` 文件，并填写以下配置：

```env
# Adobe PDF Services (用于初步提取)
ADOBE_CLIENT_ID=你的_adobe_id
ADOBE_CLIENT_SECRET=你的_adobe_secret

# LLM API (DeepSeek 或其他兼容 OpenAI 格式的 API)
DEEPSEEK_API_KEY=你的_api_key
```

---

## 🏗️ 第一阶段：文档预处理 (Preprocessing)

预处理分为三个核心步骤，请依次进入 `preprocess` 目录执行。

```bash
cd preprocess
```

### 步骤 1：Adobe PDF 结构化提取

将原始 PDF 转换为中间 XML 格式：

```bash
python 1_run_file_extract.py --raw-data-dir ../data/ --result-dir ./extract_output/
```

### 步骤 2：标题增强与修正 (OCR 或 Vision)

你可以选择使用 PaddleOCR 或 视觉大模型 (VLM) 来修正标题：

#### 选项 A：使用 PaddleOCR (传统方案)

进入 `paddleocr` 目录运行：

```powershell
cd paddleocr
.\run_ocr_process.ps1 -DocId bylw -OcrMaxPages 50
```

#### 选项 B：使用视觉大模型 (VLM) 校验 (推荐新方案)

在 `preprocess` 目录下运行，使用 Qwen-VL 等多模态模型进行视觉布局分析：

```powershell
cd preprocess
python 2_process_extracted_data.py --use-vision --doc-id bylw --ocr-max-pages 10
```

_注：此方案会自动生成页面图像并调用 API 进行校验，效果更精准，能有效防止正文被误判为标题。_

### 步骤 3：生成页面图像

为视觉审查代理（Vision Agent）准备图像数据：

```bash
python 3_make_page_images.py --raw-data-dir ../data/ --save-dir ./processed_output/
```

---

## 🤖 第二阶段：运行 DocAgent 审查 (Review)

预处理完成后，返回根目录运行 Agent 协作系统。

```bash
cd ..
```

### 1. 运行单个文档审查

```bash
# 替换 bylw 为你的文档文件夹名称
python review_runner.py --doc-id bylw
```

### 2. 批量运行实验

```bash
python run_experiment.py --preprocessed-data-dir ./preprocess/processed_output/ --save-dir ./sample_results/
```

---

## 🔍 第三阶段：查看结果

### 1. 查看审查报告

审查完成后，结果将保存在 `sample_results/` 目录下：

- `review_[doc_id].json`: 详细的问题列表和页码。
- `report_[doc_id].html`: 可视化的 HTML 报告。
- `outline_[doc_id].xml`: 提取出的文档大纲。

### 2. 验证预处理数据 (Debug)

如果你想检查 OCR 修正后的中间数据，可以运行：

```bash
cd preprocess
python view_data.py ./processed_output/bylw/data.pkl
```

---

## 🛠️ 常见问题

1. **Adobe API 报错**: 请确保 Adobe 凭证有效，且每月免费额度（1000页）未用完。
2. **PaddleOCR 模型下载慢**: 第一次运行时会自动下载模型，请保持网络畅通。
3. **内存不足**: 如果 PDF 极大，请通过 `-OcrMaxPages` 限制 OCR 处理的页数。
