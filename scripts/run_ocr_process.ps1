# OCR Enhancement Processing Script
# Usage: .\run_ocr_process.ps1

param(
    [string]$ExtractDataDir = "./extract_output/",
    [string]$SaveDir = "./processed_output/",
    [string]$RawDataDir = "../data/",
    [int]$OcrMaxPages = 50,
    [switch]$UseGpu,
    [string]$DocId = ""
)

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "OCR Enhanced Document Processing" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Check if PaddleOCR is installed
Write-Host "[Check] Verifying PaddleOCR installation..." -ForegroundColor Yellow
$paddleInstalled = python -c "import paddleocr; print('ok')" 2>$null
if ($paddleInstalled -ne "ok") {
    Write-Host "[Error] PaddleOCR is not installed" -ForegroundColor Red
    Write-Host "Please run: pip install paddlepaddle paddleocr" -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] PaddleOCR is installed" -ForegroundColor Green
Write-Host ""

# Build command arguments
$pythonArgs = @(
    "step2_process.py",
    "--extract-data-dir", $ExtractDataDir,
    "--save-dir", $SaveDir,
    "--raw-data-dir", $RawDataDir,
    "--use-ocr",
    "--ocr-max-pages", $OcrMaxPages
)

if ($DocId -ne "") {
    $pythonArgs += "--doc-id"
    $pythonArgs += $DocId
}

if ($UseGpu) {
    Write-Host "[Info] GPU usage requires paddlepaddle-gpu" -ForegroundColor Yellow
    Write-Host "       pip install paddlepaddle-gpu paddleocr" -ForegroundColor Yellow
}

# Display configuration
Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Extract Dir: $ExtractDataDir" -ForegroundColor Gray
Write-Host "  Save Dir: $SaveDir" -ForegroundColor Gray
Write-Host "  Raw PDF Dir: $RawDataDir" -ForegroundColor Gray
Write-Host "  OCR Max Pages: $OcrMaxPages" -ForegroundColor Gray
Write-Host "  Use GPU: $UseGpu" -ForegroundColor Gray
Write-Host ""

# Execute processing
Write-Host "[Start] Executing OCR enhancement..." -ForegroundColor Yellow
Write-Host ""

& python @pythonArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Green
    Write-Host "[Success] Processing completed!" -ForegroundColor Green
    Write-Host "======================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Output path: $SaveDir" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next Steps:" -ForegroundColor Yellow
    Write-Host "  1. View results:" -ForegroundColor Gray
    Write-Host "     python view_data.py $SaveDir/bylw/data.pkl" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Run review:" -ForegroundColor Gray
    Write-Host "     cd .." -ForegroundColor Gray
    Write-Host "     python review_runner.py --doc-id bylw" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "[Error] Processing failed with exit code: $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
