param([string]$Bind = '0.0.0.0', [int]$Port = 8443)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path 'data\certs\server.crt') -or -not (Test-Path 'data\certs\server.key')) {
    throw 'Generate data/certs/server.crt and server.key with scripts/make_cert.py first.'
}
& .\.venv\Scripts\python.exe -m uvicorn homehub.main:app --host $Bind --port $Port --ssl-certfile data/certs/server.crt --ssl-keyfile data/certs/server.key

