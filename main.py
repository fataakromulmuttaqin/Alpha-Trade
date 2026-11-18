"""Entry point untuk hosting Botkeep (dan PaaS lain yang memakai main.py).

Menjalankan server dashboard Alpha-Trade (HTTP + engine + feed).
Port diambil dari env PORT (PaaS) / AT_PORT (default 8788).
Simpan konfigurasi lewat Environment Botkeep, bukan file .env:
  AT_SYMBOL, AT_LABEL, AT_ENTRY_TF, AT_HTF, AT_PROVIDER, AT_TRADOOR_DATA,
  AT_TRADOOR_MARKET, EXECUTION_MODE, NEWS_KEY_*, TG_TOKEN, TG_CHAT_ID, dst.
"""
import asyncio
import os
import sys
import pathlib

BASE = pathlib.Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

os.environ.setdefault("AT_PORT", os.getenv("PORT", "8080"))
os.environ.setdefault("AT_WS_PORT", "8081")

from backend.run import main  # noqa: E402

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("stop")
