# Test environment setup (PowerShell)
# Usage:
#   . .\setup_test_env.ps1
#   .\setup_test_env.ps1 -CheckOnly
#   .\setup_test_env.ps1 -Run test\objects\test_flange_llm.py

param(
    [switch]$CheckOnly,
    [string]$Run
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "venv\Scripts\python.exe not found. Create venv and pip install -r requirements.txt first."
}

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host 'Created .env from .env.example. Fill OPENAI_API_KEY and run again.'
        notepad ".env"
    }
    exit 1
}

# Load .env into current PowerShell session
Get-Content ".env" -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim()
    if ($val.Length -ge 2 -and $val[0] -eq $val[-1] -and $val[0] -in '"', "'") {
        $val = $val.Substring(1, $val.Length - 2)
    }
    if ($key -in @("OPENAI_API_KEY", "OPENAI_BASE_URL", "SW_API_URL")) {
        Set-Item -Path "Env:$key" -Value $val
    }
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

& $python setup_test_env.py --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($CheckOnly) { exit 0 }

if ($Run) {
    $scriptPath = $Run
    if (-not (Test-Path $scriptPath)) {
        $scriptPath = Join-Path $PSScriptRoot $Run
    }
    & $python -X utf8 $scriptPath
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Done. .env loaded into this PowerShell session."
Write-Host 'Example: .\venv\Scripts\python.exe -X utf8 test\objects\test_flange_llm.py'
