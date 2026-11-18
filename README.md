# ALPHA-TRADE — SMC Confluence Dashboard & Signal Engine

Dashboard trading ala GODMODE untuk XAUUSD (via proxy emas Binance / emas asli
Yahoo `GC=F`) yang digerakkan oleh **confluence engine 5-gate** — arsitektur
web-nya mengikuti pola backend **Meridian** (server REST + WebSocket broadcast +
static dashboard + chart `lightweight-charts` v5).

![panel](https://img.shields.io/badge/mode-sinyal%20%7C%20paper%20%7C%20testnet-blue)
![news](https://img.shields.io/badge/news-newsdata%20·%20finnhub%20·%20FMP-teal)
![layout](https://img.shields.io/badge/layout-resizable-gold)
![python](https://img.shields.io/badge/python-3.11%2B-green)

## Isi repo

| File | Peran |
|---|---|
| `backend/confluence_engine.py` | Engine sinyal 5 gate (Trend, Momentum, Volume Profile, Price Action, Volatilitas) dengan `min_confluence` (0–4), `gate_trend` (untuk mode sideways), SL/TP berbasis ATR, `position_size()`. `generate_signals(df, cfg, htf_df=None)` menerima candle HTF native dari feed. |
| `backend/smc_structure.py` | Swing high/low, BOS, CHoCH (tanpa lookahead), `structure_confirms()`. |
| `backend/backtest.py` | Backtest konservatif (entry open berikutnya, SL didahulukan, TP1 50% + BE, biaya, time stop) + `walk_forward()`. |
| `backend/paper_sim.py` | **Simulator eksekusi internal**: setiap sinyal lolos filter dieksekusi otomatis (entry next-open, TP1 50% → BE → TP2, SL-first konservatif, time stop 200 bar), equity compounding 1%/trade, persist `sim_trades.json`. |
| `backend/binance_feed.py` | Feed realtime 6 TF native (1m–4h) + fallback host (`data-api.binance.vision`) + fallback provider **Bybit** dan **Yahoo Finance** (`GC=F` = emas asli COMEX). Callback `on_candle`, class `MultiFeed`. |
| `backend/tradoor_feed.py` | **Jembatan data Tradoor**: decoder partisi TDC1 (gzip + LEB128), loader manifest, resample TF, polling feed — pakai history multi-exchange multi-tahun dari tradoor self-host. |
| `backend/backfill_tradoor.py` | Backfill sendiri dari arsip resmi Binance (`data.binance.vision`) ke partisi TDC1 — history bertahun-tahun tanpa install tradoor. |
| `backend/news_feed.py` | **Berita 3 provider** (newsdata.io → finnhub → FMP) dengan fallback otomatis, cache TTL 10 menit, skor relevansi 50+ kata kunci, verifikasi silang lintas provider. |
| `backend/godmode_engine.py` | **Lapisan semantik GODMODE SMC** (lihat `docs/GODMODE-ALGORITHM.md`): 4 lampu tren EMA, zona S1–S3/D1–D3, `[TRENDING]/[SIDEWAYS]` berbasis ADX, checklist + skor confluence, klasifikasi setup (GOLDEN SETUP / HTF SNIPER ENTRY / SWING REVERSAL / A+ SNIPER CID), alert 2-stage, sesi pasar WIB, AI reasoning, forecast ATR, preset mode sensitivitas + exit protocol. |
| `backend/analytics.py` | Semua panel dashboard: header stats, volume flow + absorption, zona + CVD, event struktur, heatmap MTF, confluence, fib, forecast. |
| `backend/ml_gate.py` `backend/montecarlo.py` `backend/significance.py` `backend/research_report.py` `backend/risk.py` `backend/tune.py` | Modul riset: ML gate, uji Monte Carlo, signifikansi statistik, laporan riset, kalkulasi risiko, tuning parameter. |
| `backend/testnet_executor.py` | **Eksekusi Binance Testnet** (spot/futures), HMAC signing, sizing dari `position_size()`, mode `paper`. Tidak ada jalur mainnet. |
| `backend/server.py` `backend/run.py` | Server ala Meridian (REST + WebSocket broadcast + static) dan orkestrator satu proses (`--demo` untuk offline). |
| `frontend/` | Dashboard GODMODE-style **resizable**: chart lightweight-charts v5 + volume histogram + overlay (zona band, CHoCH, FVG, OB, fib, SL/TP, zona trade simulasi, marker berita), kartu Golden/High Risk, AI Confluence, Volume Flow, Zone Map, Events, MARKET NEWS, MTF Heatmap, Trade Setup, SIMULASI TRADING, Journal, Signal Log, Risk Calculator, panel Research. |

## Cara pakai

```bash
pip install -r backend/requirements.txt

# 1) Uji dashboard tanpa internet (data sintetis, candle dipercepat)
python -m backend.run --demo

# 2) Live: XAUUSD proxy via PAXGUSDT, entry 5m + tren 4h — 6 TF native (1m..4h)
#    + history dalam dari data format tradoor (satu server, satu port)
python -m backend.backfill_tradoor --symbol PAXGUSDT --from 2023-01
python -m backend.run --symbol PAXGUSDT --label XAUUSD --entry-tf 5m --htf 4h \
       --tradoor-data tradoor-data/data --tradoor-market binance:PAXGUSDT

# 3) EMAS ASLI (futures COMEX) via Yahoo Finance, polling REST tanpa key
python -m backend.run --provider yahoo --symbol "GC=F" --label XAUUSD --entry-tf 5m --htf 1h

# 4) Data dari tradoor self-host (lokal atau HTTP /data/...)
python -m backend.run --provider tradoor --tradoor-data "http://host/data" \
       --tradoor-market binance-perp:BTCUSDT

# 5) Kripto / simbol lain
python -m backend.run --symbol BTCUSDT --label BTCUSDT --entry-tf 15m --htf 4h
```

Dashboard: **http://localhost:8788** · WS broadcast: **ws://localhost:8789**
(`AT_PORT` / `AT_WS_PORT`). Sinyal hanya dievaluasi pada candle yang **sudah
close** (tidak repainting). Layout **bisa di-resize**: seret pemisah antar kolom
dan antar baris — tersimpan otomatis di browser.

## Fitur utama

- **Engine 5 gate + mode analisis**: selector GOLDEN (Elite, 4/4 gate) ·
  AGGRESSIVE (Momentum Pulse — cukup 1 gate, sinyal sangat sering) · STANDARD
  (Balanced 3/4) · PRECISION (Sniper 4/4, zona ketat) · PING PONG (Sideways —
  tanpa syarat tren, main di zona). **Exit protocol**: NITRO / QUICK /
  INSTITUTIONAL HOLD mengubah multipliier SL/TP. Mengganti mode langsung
  **menghitung ulang sinyal historis** sehingga marker di chart berubah seketika.
- **Filter sinyal fungsional**: chip SIGNAL GOLDEN (skor ≥3) dan SIGNAL HIGH RISK
  (≤2) menentukan sinyal mana yang dieksekusi; yang disaring tetap tercatat untuk
  analisis.
- **Simulasi trading internal**: setiap sinyal lolos filter dieksekusi otomatis
  (TP1 50% → BE → TP2, SL-first konservatif), hasilnya jadi **history + statistik**
  (equity, winrate, profit factor, avg R) di kartu SIMULASI TRADING — ukuran
  efektivitas algoritma secara langsung, tersimpan antar restart.
- **Visualisasi trade di chart**: zona hijau ke TP2 / merah ke SL dengan label
  `TP 2 ($…) | RR 1:x`, zona supply/demand band, garis Minor/Major CHoCH, FVG,
  Order Blocks, fib EQ/GP/OTE, PDH/PDL, marker berita 📰 (maks 3).
- **Berita 3 provider** (newsdata.io → finnhub → FMP) dengan fallback, cache,
  verifikasi silang, dan filter relevansi trading — toggle NEWS: ON/OFF di chart.
- **Panel analisis**: AI Confluence + gauge, 4 lampu tren EMA, Volume Flow +
  absorption, Zone Map multi-level, Market Structure Events, MTF Heatmap 6 TF,
  Risk Calculator (max loss + lot saran), Institutional Journal, AI reasoning
  SITUASI-KARENA-MAKA, Forecast ATR, sesi pasar WIB, panel Research
  (signifikansi / Monte Carlo / ML gate).
- **Layout resizable** dengan splitter yang tersimpan di browser.

## Mode eksekusi

```bash
EXECUTION_MODE=off      # default: hanya sinyal (aman)
EXECUTION_MODE=paper    # simulasi order lokal
EXECUTION_MODE=testnet  # MARKET order ke Binance Testnet
```

Key testnet lewat `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`
(lihat `.env.example`): spot <https://testnet.binance.vision> · futures
<https://testnet.binancefuture.com>. Tidak ada jalur mainnet di repo ini.

## Deployment cloud

Arsitektur: **frontend statik di Vercel + backend Python di Botkeep** (bisa juga
satu origin di mana pun backend hidup — dashboard memakai polling 4 detik).

| Bagian | Layanan | Alamat |
|---|---|---|
| Frontend | Vercel (project `alpha-trade`) | `https://alpha-trade-fataakromulmuttaqins-projects.vercel.app` |
| Backend | Botkeep (server `alpha-trade`) | domain dari panel/API — di-set ke `frontend/config.js` |

### Backend di Botkeep (via Developer API)

`botkeep_setup.py` membuat server langsung lewat Developer API
(key: env `BOTKEEP_API_KEY` atau file `botkeep.key` — keduanya tidak di-commit):

```bash
python botkeep_setup.py             # create + env + domain alias + start
python botkeep_setup.py --status    # cek status server
```

Konfigurasi server: Python 3.11 · start `python main.py` · repo `main` branch ·
RAM 1 GB / CPU 50% / storage 1 GB. Env yang dipasang otomatis:
`AT_SYMBOL=PAXGUSDT`, `AT_LABEL=XAUUSD`, `AT_ENTRY_TF=5m`, `AT_HTF=4h`,
`EXECUTION_MODE=off` (paper simulation — tidak menyentuh mainnet).

Catatan: kapasitas free pool Botkeep bersifat best-effort; saat penuh, API
mengembalikan 503 dan script akan terus mencoba (tiap 90 detik).

### Menghubungkan frontend

Setelah backend hidup, isi `frontend/config.js` dengan URL backend (tanpa
garis miring di akhir) lalu redeploy Vercel. Bila frontend dilayani backend
yang sama (satu origin), biarkan `window.AT_API_BASE = ""`.

### Alternatif yang sudah dievaluasi

- **Hugging Face Spaces** — Docker/Gradio Spaces kini butuh PRO ($9/bln);
  hanya Static Spaces yang gratis (tidak bisa menjalankan Python).
- **Render free** — 750 jam/bln, tidur setelah 15 menit tanpa trafik;
  24/7 hanya dengan keep-alive ping eksternal (habiskan kuota tepat).

## Berita (opsional, key di `.env`)

- `NEWS_KEY_NEWSDATA` — newsdata.io (utama; free plan: size maks 10)
- `NEWS_KEY_FINNHUB` — finnhub.io (fallback; daftar gratis untuk token)
- `NEWS_KEY_FMP` — financialmodelingprep.com (cadangan; `economy-latest`)

## Catatan penting

- **Binance tidak punya XAUUSD spot.** Proxy: `PAXGUSDT` (spot) / `XAUUSDT`
  perpetual / emas asli via Yahoo `GC=F`. Harga PAXG ≠ XAUUSD broker MT5 —
  gunakan kalibrasi offset (konsep GODMODE) sebelum eksekusi di broker.
- **Engine selektif pada mode default** — itu disengaja (GODMODE: "lebih baik
  tidak ada sinyal daripada sinyal salah"). Gunakan mode AGGRESSIVE untuk uji
  frekuensi, dan selalu validasi dengan `walk_forward()` sebelum dipercaya.
- Semua perhitungan memakai bar yang sudah close (tanpa lookahead); feed
  gap/reconnect otomatis re-seed; sim equity di-reset benar saat replay.
- Eksekusi hanya ke **testnet/paper** — tidak pernah menyentuh mainnet.

## Validasi edge (jujur, data asli)

`python -m backend.validate_edge` — 2 juta bar 1m PAXGUSDT (2023–2026), entry 5m:
**semua konfigurasi default PF < 1** (kalah). Kesimpulan awal: konfigurasi lama
tidak punya edge.

Tuning optuna (train 2023–2025) + verifikasi **out-of-sample 2026** yang tidak
pernah disentuh tuning (`python -m backend.tune_edge`):

| TF | OOS 2026 | Catatan |
|---|---|---|
| **1h** | **n=67 · WR 38.8% · PF 1.63 · +28.4% · maxDD −10.6%** | robust: kenaikan biaya & tetangga parameter tetap profit |
| 15m | n=15 · WR 53.3% · PF 2.27 · +9.1% | sampel kecil |
| 5m | n=131 · PF 0.97 | breakeven — noise dominan |

Preset **EDGE-1H** (dropdown SIGNAL MODE + exit `ENGINE`) memuat konfigurasi
tersebut — jalankan backend dengan `--entry-tf 1h`. Bukti edge bukan jaminan
masa depan: paruh kedua 2026 datar, satu aset, dan hasil live akan berbeda
dari backtest. Pakai risiko kecil dan pantau PaperSim.

## Riset & dokumen

- `docs/GODMODE-ALGORITHM.md` — reverse-engineering algoritma godmode.my.id
- `docs/TRADOOR.md` — analisis kombinasi + jembatan data Tradoor + Frost Script port
- `docs/RISET-REPO.md` — riset repo GitHub pendukung (ccxt, backtesting.py,
  smartmoneyconcepts, pandas-ta-classic) lengkap dengan hasil uji
- `backend/requirements-research.txt` — dependency riset opsional

## Uji cepat

```bash
python -m backend.smoke_test        # analytics + engine + layer GODMODE
python -m backend.tradoor_feed      # round-trip decoder TDC1
python -m backend.news_feed         # tarik berita dari provider
python -m backend.confluence_engine # demo engine asli
python -m backend.backtest          # demo backtest + walk_forward
```
