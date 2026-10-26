param([ValidateRange(1024,65535)][int]$Port = 8510)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonCandidates = @(
    (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
    (Join-Path $ProjectRoot '..\..\.venv\Scripts\python.exe')
)
$WebsitePython = $PythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $WebsitePython) {
    throw 'Python environment not found. Create .venv and install requirements.txt first.'
}
$PreviousPythonPath = $env:PYTHONPATH
Push-Location -LiteralPath $ProjectRoot
try {
    # An editable installation can point at a different checkout. Always run
    # the source beside this launcher, including from an isolated worktree.
    $env:PYTHONPATH = Join-Path $ProjectRoot 'src'
    & $WebsitePython -m streamlit run src/web_task_agent/streamlit_app.py `
        --server.address 127.0.0.1 --server.port $Port --server.headless true `
        --browser.gatherUsageStats false
} finally {
    $env:PYTHONPATH = $PreviousPythonPath
    Pop-Location
}
