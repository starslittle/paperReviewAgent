param (
    [string]$DocName
)

if (-not $DocName) {
    Write-Error "Please provide a document folder name (e.g., DocAgent)"
    exit 1
}

$SampleDir = "./data/"
$ExtractDir = "preprocess/extract_output/MinerU"
$ProcessDir = "preprocess/processed_output/MinerU"

# Check if folder exists
if (-not (Test-Path "$SampleDir/$DocName")) {
    Write-Error "Folder $SampleDir/$DocName does not exist."
    exit 1
}

Write-Host "`n=== Step 1: Extracting PDF Content (MinerU API) ==="
python preprocess/1_run_pdf_extract.py `
    --raw-data-dir $SampleDir `
    --result-dir "preprocess/extract_output" `
    --doc-id $DocName

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Step 2: Converting Data to Internal Format ==="
python preprocess/2_process_extracted_data.py `
    --extract-data-dir $ExtractDir `
    --save-dir $ProcessDir

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Step 3: Generating Page Images (Optional) ==="
python preprocess/3_make_page_images.py `
    --raw-data-dir "$SampleDir/$DocName" `
    --save-dir $ProcessDir

if ($LASTEXITCODE -ne 0) { Write-Warning "Image generation failed or skipped." }

Write-Host "`n=== Step 4: Running Agent Review ==="
python review_runner.py --doc-id $DocName

Write-Host "`n=== Step 5: Generating HTML Report ==="
python generate_report.py --doc-id $DocName

Write-Host "`n[Done] All steps completed."


if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }