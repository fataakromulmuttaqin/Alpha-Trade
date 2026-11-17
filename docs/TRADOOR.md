# Alpha-Trade × Tradoor Terminal — Analisis Kombinasi

> Repo: https://github.com/tradoor-terminal/tradoor (AGPL-3.0, Rust+WASM, 182★, aktif)
> Diverifikasi 18 Nov 2026: README, docs arsitektur, referensi Frost Script
> (`web/public/frost-script-ai.txt` — disimpan salinannya di `docs/frost-script-ai.txt`).

## Apa itu Tradoor
Terminal trading crypto di browser: chart Rust/WebGL2, DOM+tape, footprint, TPO market
profile, liquidations & net taker flow dari **14 exchange** (Binance, Bybit, OKX,
Hyperliquid, ...), journal, **paper trading** (100k USDT, perp Binance), dan
**strategy tester** di browser: fee/slippage/funding per exchange, fill di open
berikutnya, stop-vs-target di bar yang sama diputuskan dari bar 1m (semantik sama
dengan `backtest.py` kita), laporan dengan **Monte Carlo**, optimizer dengan
held-out bars, forward testing.

## Verdict: BISA dikombinasikan — 3 mode, saling melengkapi

### Mode 1 — Port algoritma ke Frost Script (sudah dibuat)
`docs/frost/alpha-trade-strategy.frost` = port 5-gate confluence engine ke bahasa
Frost Script tradoor. Keuntungan: algoritma Alpha-Trade bisa **di-backtest langsung
di data 14 exchange** dengan biaya riil + Monte Carlo + optimizer held-out — dua hal
yang belum ada di `backtest.py` kita.

Pemetaan gate → Frost:
| Gate Alpha-Trade | Implementasi Frost | Catatan |
|---|---|---|
| TREND (EMA50>EMA200 4h + slope) | `ema(close, 50*mult)` vs `ema(close, 200*mult)` + `change()` | Frost **tidak punya request.security** → HTF diaproksimasi EMA panjang di TF entry (15m + mult 4 ≈ 4h) |
| MOMENTUM (RSI pullback/div + MACD) | `rsi()`, `crossover/crossunder`, `pivothigh/pivotlow` + list `state` untuk divergensi, MACD manual dari `ema` | lengkap |
| VOLUME (POC/VAL/VAH) | proksi rolling VWAP `sum(hlc3*vol)/sum(vol)` + pita `stdev` | Frost tidak punya volume profile bawaan |
| PRICE ACTION (engulf/pin/sweep) | o/h/l/c + `highest/lowest` | persis |
| VOLATILITAS (ATR rank 20–95%) | `percentrank(atr, 500)` | persis, fungsi bawaan ada |
| SL swing±buffer, cap 1–2.5 ATR | `lowest(low,5)`, `max/min` | persis |
| TP1 50%→BE, TP2 2R | `stop()` digeser ke `entryprice` setelah 1.5R, `target()` = 2R | partial TP tidak didukung tester → disederhanakan |

Cara pakai: buka tradoorterminal.com (atau self-host) → indikator → **New Frost
Script** → paste isi `alpha-trade-strategy.frost` → jadikan strategy di tab Strategy.
Editor tradoor otomatis membetulkan paste yang sedikit rusak, dan kalau ada error,
pesan errornya spesifik per baris. Batas bahasa yang sudah dicek: satu statement per
baris, tanpa indexing `[n]` (pakai `change()`), loop maks 10.000 putaran.

### Mode 2 — Alpha-Trade sebagai generator sinyal, Tradoor sebagai terminal
Alpha-Trade tetap otak: feed realtime (Binance/Yahoo GC=F), engine 5 gate, dashboard
GODMODE, notifikasi Telegram. Tradoor dipakai untuk:
- **Eksekusi paper trading** (perp Binance) dari sinyal — catat: tradoor **tidak punya
  webhook/API inbound untuk sinyal eksternal** (Frost Script sengaja di-sandbox tanpa
  jaringan), jadi eksekusinya manual dari sinyal Alpha-Trade/Telegram.
- **Konfirmasi order flow** yang tidak ada di Alpha-Trade: footprint delta, DOM, tape,
  TPO/POC harian, liquidations — cocok untuk filter manual sebelum entry.

### Mode 3 — Self-host berdampingan
Tradoor satu Docker image + PostgreSQL (`docker compose up -d`), Alpha-Trade satu
proses Python. Jalankan keduanya di mesin yang sama; tidak ada konflik port (tradoor
5173/80, Alpha-Trade 8788/8789). Bila self-host, data candle tradoor di-backfill
sendiri (`tradoor-backfill binance-perp:BTCUSDT --from ...`).

## Batasan yang perlu tahu
1. **Simbol**: tradoor hanya pasar crypto (14 exchange). XAUUSD/GC=F tidak ada —
   proxy emas: `binance-perp:PAXGUSDT` atau `bybit:PAXGUSDT`; backtest emas "asli"
   tetap di Alpha-Trade via Yahoo `GC=F`.
2. **Tidak ada auto-trading dari luar**: paper trading tradoor hanya dari UI. Untuk
   otomatis penuh, jalur Alpha-Trade tetap Binance **testnet** (`testnet_executor.py`).
3. Frost Script tidak bisa akses jam/news (tanpa `timenow`) — filter sesi/news tetap
   di sisi Alpha-Trade.
4. Lisensi AGPL-3.0: bebas dipakai/dimodifikasi untuk pemakaian sendiri; kalau
   kodenya dimodifikasi dan dilayani ke publik, wajib open-source-kan modifikasinya.
   Alpha-Trade (kode terpisah, komunikasi via sinyal manual) tidak terpengaruh.

## Ambil data Tradoor untuk Alpha-Trade (sudah dibangun + teruji)

Tradoor menyimpan candle sebagai partisi **TDC1** terdokumentasi penuh
(`docs/format.md` di repo mereka): `data/{venue}/{SYMBOL}/{1m|1h|1d}/*.tdc.gz`
+ `manifest.json` per market. Server tradoor menyajikannya publik di `/data/...`.
`backend/tradoor_feed.py` membaca format ini langsung dari Python:

- `read_tdc1(path|bytes)` — decoder gzip + LEB128/zigzag (round-trip 100% diuji
  dengan encoder internal; 500 bar ≈ 3.4 KB gzip, konsisten klaim 42 MB/5 tahun)
- `load_market(source, market, interval, bars)` — baca manifest, gabung partisi,
  **resample otomatis** (tradoor hanya menyimpan 1m/1h/1d; 5m/15m/30m/4h
  diturunkan dari 1m/1h). `source` = direktori data tradoor ATAU base URL
  `http://host/data`. Di Windows disk lokal, `:` pada nama market diganti `_`.
- `TradoorFeed` — polling feed (interface sama dengan LiveFeed) untuk dashboard.

Jalankan dashboard Alpha-Trade dari data tradoor:

```bash
python -m backend.run --provider tradoor \
  --tradoor-data "C:/tradoor/data" \
  --tradoor-market "binance-perp:BTCUSDT" \
  --entry-tf 5m --htf 4h
# atau dari server tradoor via HTTP:
python -m backend.run --provider tradoor --tradoor-data "http://localhost:8080/data" ...
```

Terverifikasi end-to-end: 55.000 bar 1m sintetis dalam 2 partisi bulanan →
server hidup dengan 6 TF (1m/5m/15m/30m = 1500, 1h = 917, 4h = 230 bar) dan
engine 5-gate berjalan normal di atasnya.

## Modifikasi platform tradoor "sesuai Alpha-Trade"

Tanpa fork (aman AGPL, sudah siap):
1. **Frost Script strategy** `docs/frost/alpha-trade-strategy.frost` — algoritma
   5-gate berjalan native di chart tradoor + strategy tester-nya.
2. **Community script** — strategy Frost boleh dibagikan publik lewat fitur
   Community tradoor (username sekali pilih).

Dengan fork (modifikasi SolidJS/Rust — wajib open-source bila dilayani publik):
3. Tambah panel "ALPHA-TRADE" di `web/` (SolidJS) yang polling
   `http://localhost:8788/api/snapshot` (server Alpha-Trade sudah mengirim header
   CORS `*`) untuk menampilkan sinyal/zone map/heat map GODMODE di dalam tradoor.
   Titik hook: `web/src/` (komponen panel + route workspace), build `deno task build`.
4. Tambah route `POST /api/signals` di `bins/server/src/api.rs` bila ingin sinyal
   Alpha-Trade masuk ke daftar signals internal tradoor (saat ini signals hanya
   dari screener three-drive bawaan).
5. `/api/training/orders` (paper trading) butuh sesi owner (Telegram sign-in) —
   bukan jalur otomatis; untuk otomatis penuh tetap gunakan testnet Alpha-Trade.

## Rekomendasi urutan
1. Paste `alpha-trade-strategy.frost` ke tradoor → jalankan strategy tester di
   `binance-perp:BTCUSDT` 15m (mult 4) → bandingkan hasilnya dengan `backtest.py`
   Alpha-Trade pada data yang sama.
2. Kalau angkanya masuk akal, pakai optimizer tradoor (held-out) untuk mencari
   kandidat parameter, lalu verifikasi ulang di `backtest.py` + `walk_forward()`.
3. Gunakan tradoor footprint/DOM sebagai konfirmasi manual sebelum eksekusi
   testnet/paper.
