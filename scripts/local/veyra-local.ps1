# Thin wrapper: .\scripts\local\veyra-local.ps1 release --install
# See docs/LOCAL_RELEASE.md. Uses the Python on PATH (3.11+ with Pillow).
$ErrorActionPreference = 'Stop'
& python -X utf8 -B (Join-Path $PSScriptRoot 'veyra_local.py') @args
exit $LASTEXITCODE
