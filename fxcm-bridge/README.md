# FXCM Bridge

This bridge runs separately from the Render scanner because the official ForexConnect Python SDK uses a legacy/native runtime.

## Requirements

- Windows or a compatible ForexConnect host
- Python 3.7 recommended by the official ForexConnect package
- A rotated FXCM demo password
- `forexconnect` installed in this bridge environment

```powershell
py -3.7 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install forexconnect requests python-dotenv
```

## Environment

Create `.env` in this folder. Never commit it.

```env
FXCM_ENABLED=true
FXCM_USERNAME=
FXCM_PASSWORD=
FXCM_CONNECTION=demo
FXCM_SERVER=
FXCM_POLL_SECONDS=30
SCANNER_RECEIVER_URL=https://battousai-bot.onrender.com/fxcm/market-data
FXCM_BRIDGE_SHARED_SECRET=
```

The bridge sends only normalized market data. It never sends orders and never controls the scanner strategy.
