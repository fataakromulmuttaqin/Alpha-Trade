# Riset Repo GitHub untuk Kelanjutan Alpha-Trade

> Tanggal riset: 17 Nov 2026 · Environment: Python 3.11.0 (Windows), pandas 2.x
> Semua kandidat **diuji langsung di mesin** (bukan sekadar dibaca). Hasil uji tertulis di bawah.

## Ringkasan verdict

| Repo / Paket | Stars | Lisensi | Status uji di sini | Verdict untuk Alpha-Trade |
|---|---|---|---|---|
| `ccxt/ccxt` | 44.9k | MIT | ✅ jalan (spot via data-api) | **Adopsi bertahap** — layer provider multi-exchange |
| `polakowo/vectorbt` | 9.9k | Apache-2.0 | ⏳ belum diuji (dep berat: numba) | Nanti, jika butuh backtest vektor masif untuk hyperopt |
| `kernc/backtesting.py` | 9.2k | Apache-2.0 | ✅ jalan penuh (11 trade, Sharpe/DD) | **Adopsi** — backtest visual & statistik kaya |
| `twopirllc/pandas-ta` | ~5k | MIT | ❌ ditarik dari PyPI (butuh py≥3.12 utk versi baru) | Ganti ke fork |
| `xgboosted/pandas-ta-classic` | 491 | MIT | ✅ jalan (RSI/MACD/ADX/BBands di pandas 2) | **Adopsi** — sumber indikator (ADX, BB, dsb.) |
| `joshyattridge/smart-money-concepts` | 2.2k | **tanpa lisensi** | ✅ jalan (v2 API) | **Adopsi hati-hati** — dependency pip, jangan copy kodenya |

## Detail per repo + hasil uji

### 1. ccxt/ccxt — MIT
- Uji: `ccxt.binance({"options": {"fetchMarkets": ["spot"]}})` + override
  `urls["api"]["public"] = "https://data-api.binance.vision/api/v3"` → `fetch_ohlcv("PAXG/USDT","5m")` ✅ (5 bar, close real 3852.09).
  Bybit via ccxt gagal di jaringan ini (SSL di-intercept) — diantisipasi; ccxt tetap berguna di jaringan lain.
- Catatan penting yang ditemukan saat uji: (a) tanpa `fetchMarkets: ["spot"]`, ccxt
  memuat market futures dari `fapi.binance.com` yang diblokir di sini → wajib dibatasi spot;
  (b) override URL jangan diberi trailing slash (menyebabkan 403).
- Jalur build: **Fase data-2** — jadikan ccxt provider alternatif di belakang antarmuka
  `fetch_klines` yang sekarang (Binance-native tetap utama karena WS-nya ringan dan sudah
  jatuh-tembak; ccxt dipakai untuk simbol/exchange yang tidak didukung feed custom).

### 2. kernc/backtesting.py — Apache-2.0
- Uji: SMA-cross di data kita → 11 trade, Return %, Max DD %, Sharpe lengkap ✅.
  Dengan sinyal `generate_signals` default di data acak → 0 trade (perilaku selektif engine, normal).
- Syarat yang harus dipatuhi (ditemukan saat uji): kolom **kapital** (`Open/High/Low/Close/Volume`),
  index **DatetimeIndex tanpa timezone**, panjang index ≥ length MA.
- Jalur build: **Fase backtest-2** — tambahkan `backtest_visual.py` yang memakai
  backtesting.py (Output HTML stats + plot) di atas sinyal `generate_signals`,
  sementara `backtest.py` (buatan sendiri, aturan konservatif TP1/BEP) tetap untuk `walk_forward()`.

### 3. xgboosted/pandas-ta-classic — MIT
- Uji: `ta.rsi / ta.macd / ta.atr / ta.adx / ta.bbands` di pandas 2 ✅ (rsi 56.4, macd 3 kolom, adx 3 kolom, bb 5 kolom).
- `pandas-ta` asli sudah **ditarik dari PyPI** dan versi baru mensyaratkan Python ≥3.12 — jadi pakai fork classic ini.
- Jalur build: **Fase indikator** — sudah terintegrasi pertama: `adx_value()` di
  `godmode_engine.market_condition` (ADX ≥25 → [TRENDING], ≤18 → [SIDEWAYS],
  fallback heuristik range-vs-ATR bila lib tidak terpasang — graceful).

### 4. joshyattridge/smart-money-concepts — ⚠️ tanpa lisensi
- Uji (API v2): `smc.fvg` (42 bull / 83 bear + Top/Bottom/MitigatedIndex),
  `smc.swing_highs_lows`, `smc.bos_choch` (BOS 3 / CHoCH 1),
  `smc.ob` (Top/Bottom/OBVolume/Percentage), `smc.liquidity` (flag `Swept`),
  plus `previous_high_low` (PDH/PDL), `sessions`, `retracements` ✅ semua jalan.
- Perbedaan filosofi dengan milik kita: smc menghasilkan **series historis per-bar**
  (enak untuk backtest & menggambar banyak zona), sedangkan `godmode_engine` menghasilkan
  **status aktif terkini** (enak untuk dashboard realtime). Komplementer, bukan pengganti.
- **Peringatan lisensi**: repo tanpa lisensi = semua hak dilindungi. Aman dipakai sebagai
  dependency pip (`pip install smartmoneyconcepts`), **tidak boleh** menyalin kodenya ke repo kita.
- Jalur build: **Fase SMC-2** — (a) `previous_high_low` untuk garis PDH/PDL di chart
  (GODMODE terminal menampilkan Support/Resistance harian); (b) `sessions` untuk
  validasi silang `session_info()` kita; (c) opsional: sumber alternatif FVG/OB untuk
  backtest massal; (d) `retracements` untuk garis fib OTE dari swing (pembanding `fib_state`).

### 5. polakowo/vectorbt — Apache-2.0 (belum diuji)
- Dep berat (numba, llvmlite). Relevan hanya jika nanti melakukan optimasi parameter
  masif (grid hyperopt ribuan kombinasi) — catat untuk Fase tuning, belum dipasang.

## Yang sudah dieksekusi dari riset ini
1. `godmode_engine.adx_value()` + `market_condition` berbasis ADX (pandas-ta-classic), fallback aman.
2. `requirements-research.txt` — daftar dependency riset (opsional, tidak mewajibkan).

## Roadmap integrasi berikutnya (urutan disarankan)
1. **PDH/PDL garis harian** di chart + panel (via smartmoneyconcepts.previous_high_low) — kecil, langsung terlihat.
2. **backtest_visual.py** (backtesting.py) dengan sinyal engine + heatmap hasil per mode.
3. **ccxt provider** opsional untuk simbol non-Binance (EUR/USDT dsb. via exchange lain) di belakang flag `--provider ccxt`.
4. Hyperopt parameter Config (vectorbt atau grid sederhana) — hanya setelah backtest visual stabil.

## Cara reproduksi uji
```bash
pip install smartmoneyconcepts ccxt backtesting pandas-ta-classic
python - <<'EOF'
from smartmoneyconcepts import smc
import ccxt, pandas_ta_classic as ta
from backtesting import Backtest, Strategy
print("smc:", [m for m in dir(smc) if not m.startswith('_')])
EOF
```
