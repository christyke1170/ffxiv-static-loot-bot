$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
& .\.venv\Scripts\static-loot-db-upgrade.exe
Write-Host "Setup complete. Edit .env, then run: .\.venv\Scripts\static-loot-bot.exe"