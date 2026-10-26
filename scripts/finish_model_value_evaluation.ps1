param(
    [Parameter(Mandatory=$true)][ValidateSet('deepseek', 'qwen')][string]$Provider,
    [Parameter(Mandatory=$true)][string]$Model,
    [string]$Python = 'python',
    [string]$Dataset = 'evaluations/model-value-v4',
    [string]$OutputRoot = ('portfolio-artifacts/formal-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if ([string]::IsNullOrWhiteSpace($Model)) { throw 'Model must be explicit.' }
if (Test-Path -LiteralPath $OutputRoot) { throw 'Output directory already exists; preserve previous evidence.' }
& $Python scripts/run_model_value_benchmark.py --suite planner --dataset $Dataset --split test --validate-only
if ($LASTEXITCODE -ne 0) { throw 'Complete the genuine human review receipt before formal evaluation.' }
foreach ($suite in @('planner', 'reader')) {
    $maxCalls = if ($suite -eq 'planner') { 1000 } else { 240 }
    & $Python scripts/run_model_value_benchmark.py --suite $suite --dataset $Dataset --split test `
        --strategies rules,always,adaptive --runs 3 --seed 20261023 --provider $Provider --model $Model `
        --max-calls $maxCalls --max-planner-calls-per-case 4 --max-observed-tokens 2000000 `
        --case-deadline-seconds 60 --output-dir (Join-Path $OutputRoot $suite)
    if ($LASTEXITCODE -ne 0) { throw "$suite did not complete; inspect the preserved manifest and failures before resuming." }
    $manifestPath = Join-Path (Join-Path $OutputRoot $suite) 'manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.halted_reason -or @($manifest.missing_planned_episodes).Count -gt 0) {
        throw "$suite is partial; inspect the preserved manifest before resuming."
    }
}
& $Python scripts/run_external_validation.py --dataset $Dataset --output-dir (Join-Path $OutputRoot 'external')
if ($LASTEXITCODE -ne 0) { throw 'External collection did not complete; inspect preserved evidence.' }
