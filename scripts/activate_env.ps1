# 激活 DocAgent 虚拟环境
# 使用方法: .\activate_env.ps1

# 设置 UTF-8 编码以正确显示中文
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

Write-Host "激活 DocAgent 虚拟环境..." -ForegroundColor Green
& ".\.venv\Scripts\activate.ps1"

Write-Host "环境已激活! 现在可以运行预处理命令了。" -ForegroundColor Green
Write-Host ""
Write-Host "常用命令:" -ForegroundColor Yellow
Write-Host "  # 为 bylw-pgy 运行完整预处理流程" -ForegroundColor Cyan
Write-Host "  .\run_pipeline.ps1 -DocName 'bylw-pgy'" -ForegroundColor White
Write-Host ""
Write-Host "  # 或分别运行:" -ForegroundColor Cyan
Write-Host "  python preprocess/1_run_pdf_extract.py --raw-data-dir ./data --result-dir preprocess/extract_output --doc-id bylw-pgy" -ForegroundColor White
Write-Host "  python preprocess/2_process_extracted_data.py --extract-data-dir preprocess/extract_output/MinerU --save-dir preprocess/processed_output/MinerU --doc-id bylw-pgy" -ForegroundColor White
Write-Host "  python preprocess/3_make_page_images.py --raw-data-dir ./data/bylw-pgy --save-dir preprocess/processed_output/MinerU --resolution 144" -ForegroundColor White
Write-Host "  python preprocess/4_build_xml_tree.py --processed-dir preprocess/processed_output/MinerU --doc-id bylw-pgy --output-dir sample_results" -ForegroundColor White