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

Write-Host "`n=== Running Preprocessing Pipeline ==="
python preprocess/run_pipeline.py `
    --doc-id $DocName `
    --raw-data-dir $SampleDir `
    --extract-dir "preprocess/extract_output" `
    --processed-dir $ProcessDir `
    --with-page-images

if ($LASTEXITCODE -ne 0) { 
    Write-Error "Preprocessing pipeline failed."
    exit $LASTEXITCODE 
}

Write-Host "`n=== Step 4: Running Agent Review ==="
python review_runner.py --doc-id $DocName

Write-Host "`n=== Step 5: Generating HTML Report ==="
python generate_report.py --doc-id $DocName

Write-Host "`n[Done] All steps completed."


if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }