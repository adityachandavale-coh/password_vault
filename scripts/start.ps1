# Start HomeHub on Windows. Host, port, and certificate directory come from
# .env; anything passed here is forwarded to `python -m homehub`.
param([string]$Bind = '0.0.0.0', [Parameter(ValueFromRemainingArguments = $true)] $Extra)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    python -m venv .venv
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
}
& $python -m homehub --host $Bind @Extra
exit $LASTEXITCODE
