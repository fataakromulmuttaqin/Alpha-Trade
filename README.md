# ALPHA-TRADE — SMC Confluence Dashboard & Signal Engine

Dashboard trading ala GODMODE untuk XAUUSD (via proxy emas Binance) yang digerakkan
 oleh **confluence engine 5-gate** — arsitektur web-nya mengikuti pola backend
 **Meridian** (server REST + WebSocket broadcast + static dashboard + candlestick
 chart `lightweight-charts`).

![panel](https://img.shields.io/badge/mode-sinyal%20%7C%20paper%20%7C%20testnet-blue) ![python](https://img.shields.io/badge/python-3.11%2B-green)

## Apa yang ada di sini

| File | Peran |
|---|---|
| `backend/confluence_engine.py` | Engine sinyal 5 gate (Trend, Momentum, Volume Profile, Price Action, Volatilitas) + SL/TP ATR + `position_size()`. **Patch kecil:** `generate_signals(df, cfg, htf_df=None)` sekarang bisa menerima candle HTF **native dari feed** (bukan resample). Perilaku lama tidak berubah. |
| `backend/smc_structure.py` | Swing high/low, BOS, CHoCH (tanpa lookahead), `structure_confirms()`. |
| `backend/backtest.py` | Simulasi konservatif: entry open bar berikutnya, SL didahulukan, TP1 50% + breakeven, biaya spread, time stop, `walk_forward()`. |
| `backend/binance_feed.py` | REST histori + WebSocket live per TF. **Baru:** callback `on_candle` dan class **`MultiFeed`** — beberapa timeframe native sekaligus (mis. 15m + 4h, masing-masing stream sendiri dari Binance, bukan hasil resample). |
| `backend/testnet_executor.py` | **Mode eksekusi Binance Testnet** (spot `testnet.binance.vision` / futures `testnet.binancefuture.com`), HMAC signing, sizing dari `position_size()`, mode `paper` untuk simulasi. Tidak ada jalur mainnet. |
| `backend/godmode_engine.py` | **Lapisan semantik GODMODE SMC** (hasil reverse-engineering godmode.my.id — lihat `docs/GODMODE-ALGORITHM.md`): 4 lampu tren EMA, zona S1–S3/D1–D3, kondisi `[TRENDING]/[SIDEWAYS]`, skor confluence dinamis, klasifikasi setup (GOLDEN SETUP / HTF SNIPER ENTRY / SWING REVERSAL / A+ SNIPER CID), alert 2-stage, sesi pasar WIB, AI reasoning SITUASI-KARENA-MAKA, forecast ATR 1×/2×/3× + SL 1.5×. |
| `backend/analytics.py` | Panel dashboard: zona supply/demand + CVD, event struktur (CHOCH/HH/HL/LH/SWEEP/TC), volume flow + absorption, heatmap MTF, AI confluence, fib EQ/GP/OTE — kini menyatu dengan layer GODMODE. |
| `backend/server.py` | Server ala Meridian: static + REST (`/api/snapshot`, `/api/candles`, `/api/health`, `/api/signals`) + broadcast WebSocket. |
| `backend/run.py` | Orkestrator: feed → engine per candle close → dashboard + eksekusi. Ada `--demo` (data sintetis, tanpa internet). |
| `frontend/` | Dashboard GODMODE-style: chart candlestick + overlay zona/fib/SL/TP, kartu Golden/High Risk, AI Confluence, Volume Flow, Zone Map, Market Structure Events, MTF Heatmap, Trade Setup, Signal Log. `lightweight-charts` v5 di-vendor lokal (tanpa CDN). |

## Cara pakai

```bash
pip install -r backend/requirements.txt

# 1) Uji dashboard tanpa internet (data sintetis, candle 5m dipercepat)
python -m backend.run --demo

# 2) Live: XAUUSD proxy via PAXGUSDT spot, entry 5m + tren 4h — 6 TF native (1m..4h)
python -m backend.run --symbol PAXGUSDT --label XAUUSD --entry-tf 5m --htf 4h

# 3) EMAS ASLI (futures COMEX) via Yahoo Finance, polling REST tanpa key:
python -m backend.run --provider yahoo --symbol "GC=F" --label XAUUSD --entry-tf 5m --htf 1h

# 4) Backfill history bertahun-tahun ke format tradoor (arsip resmi Binance), lalu jalankan dari sana:
python -m backend.backfill_tradoor --symbol PAXGUSDT --from 2023-01
python -m backend.run --provider tradoor --tradoor-data tradoor-data/data --tradoor-market binance:PAXGUSDT

# 5) Kripto / simbol lain
python -m backend.run --symbol BTCUSDT --label BTCUSDT --entry-tf 15m --htf 4h
```

**Provider data & fallback otomatis** (`backend/binance_feed.py`):
- Utama: Binance realtime WS — host utama `api.binance.com`, fallback otomatis ke
  `data-api.binance.vision` (endpoint publik resmi, lolos blokir jaringan umum);
  bisa dioverride env `AT_REST_BASE` / `AT_WS_BASE`.
- Cadangan 1: **Bybit v5** public spot (REST + WS, dinormalisasi ke format Binance).
- Cadangan 2: **Yahoo Finance** chart API — punya emas asli `GC=F` (futures COMEX),
  polling REST (tanpa WS), interval 4h di-resample dari 60m.
- Chart: **lightweight-charts v5** (library yang sama dengan dashboard Meridian —
  `frontend/vendor/`), candle + volume histogram + overlay canvas zona/CHoCH/kotak nilai.

Buka **http://localhost:8788** (WS broadcast di **ws://localhost:8789**, keduanya
bisa diubah via `AT_PORT` / `AT_WS_PORT`). Sinyal hanya dievaluasi pada candle
yang **sudah close** (tidak repainting).

### Mode eksekusi

```bash
EXECUTION_MODE=off      # default: hanya sinyal (aman)
EXECUTION_MODE=paper    # simulasi order lokal
EXECUTION_MODE=testnet  # MARKET order ke Binance Testnet
```

Key testnet di-set lewat `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`
(lihat `.env.example`):
- Spot testnet: <https://testnet.binance.vision> (login GitHub → Generate HMAC Key)
- Futures testnet: <https://testnet.binancefuture.com> (mendukung short murni;
  spot testnet hanya bisa menjual saldo yang ada)

### Notifikasi Telegram (opsional)

`binance_feed.telegram_notifier()` otomatis aktif di feed jika env
`TG_TOKEN` + `TG_CHAT_ID` terisi.

## Catatan penting

- **Binance tidak punya XAUUSD spot.** Proxy emas: `PAXGUSDT` (spot) atau
  `XAUUSDT` perpetual (futures). Harga/likuiditas tidak identik dengan XAUUSD
  broker MT4/MT5 — parameter hasil backtest bisa bergeser saat eksekusi di broker.
- **Engine sangat selektif dengan parameter default** (di data sintetis: 0 sinyal
  dari 12.000 bar). Sebelum live: jalankan `backend/backtest.py` + `walk_forward()` dengan
  data historis asli, isi biaya spread riil, tuning `Config` hati-hati (rawan overfit).
- Semua perhitungan HTF memakai bar yang sudah close; feed gap/reconnect otomatis
  re-seed buffer (backoff 1–60 detik, termasuk batas koneksi 24 jam Binance).
- Modul ini mengirim **sinyal dan order testnet saja** — tidak pernah menyentuh
  akun mainnet. Untuk eksekusi uang nyata, uji dulu di testnet sampai konsisten.

## Struktur data snapshot (REST `/api/snapshot`)

`stats` (price/rsi/atr/structure/ai_score/fib) · `volume_flow` (buy/sell/net CVD/
absorption) · `zones` (supply & demand: top/bottom/strength/CVD/status) ·
`events` (CHoCH/HH/LL/SWEEP) · `heatmap` (Trend/Zone/Momentum/CVD Flow/Breakout
per TF) · `setup` (side/entry/sl/tp1/tp2/rr/score) · `confluence` (checklist +
confidence) · `signals_log`.

## Uji cepat

```bash
python -m backend.smoke_test        # analytics + engine dengan data sintetis
python -m backend.confluence_engine # demo engine asli
python -m backend.backtest          # demo backtest + walk_forward
```
