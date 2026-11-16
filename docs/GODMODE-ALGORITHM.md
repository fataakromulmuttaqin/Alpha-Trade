# GODMODE SMC Engine — Catatan Reverse-Engineering Algoritma

> Sumber: observasi langsung https://godmode.my.id (dashboard.php, smc_detector.php,
> scanner.php, cara-pakai.php, track_record.php, candle-guide.php, modal PANDUAN
> SIGNAL, modal Release Intelligence V4.5/V5.0) pada 17 Nov 2026.
> Ini catatan engineering untuk adaptasi ke Alpha-Trade, bukan dokumentasi resmi.

## 1. Arsitektur sistem (2 generasi engine)

| Generasi | Halaman | Isi |
|---|---|---|
| v4.2 "INST. TERMINAL" | `scanner.php` (Trading Lab) | Terminal sinyal lama: multi-aset, mode sensitivitas, exit protocol, DXY/ADX/BB, AI reasoning SITUASI-KARENA-MAKA, pressure meter, kalkulator risiko, jurnal |
| v5.0 "SMC ENGINE" | `smc_detector.php` | Detector SMC penuh: chart institusional M1–H4, zone map S1–S3/D1–D3, structure events, volume flow, MTF heatmap, trade setup, AI score |

Feed harga: Binance (BTC/USDT, PAXG/USDT, ETH, SOL, BNB, EUR/USDT, GBP/USDT,
AUD/USDT). Khusus XAU/USD premium: **feed Swissquote** ("Real MT5"), dengan
**MT5 Offset per instrumen** (kalibrasi harga broker: offset = harga MT5 − harga
GodMode; semua Entry/SL/TP otomatis digeser).

## 2. Taksonomi sinyal (nama setup di track record)

| Setup | Total | TP Hit | BEP+ | SL | Pure TP% | Effective WR* |
|---|---|---|---|---|---|---|
| SWING REVERSAL | 72 | 30 | 17 | 25 | 41.7% | 65.3% |
| A+ SNIPER CID | 6 | 1 | 3 | 2 | 16.7% | 66.7% |
| HTF SNIPER ENTRY | 67 | 27 | 26 | 14 | 40.3% | 79.1% |
| GOLDEN SETUP | 72 | 24 | 32 | 16 | 33.3% | 77.8% |

*Effective Winrate = (TP + BEP+) / Total — BEP+ (breakeven-plus, +5.9…+10.1 pips)
dihitung menang. TP1 standar = +25.0 pips (2.5 pt di XAUUSD).

Klasifikasi sinyal di PANDUAN SIGNAL:
- **GOLDEN SIGNAL** — momentum besar selaras dengan struktur harga kuat (probabilitas tertinggi)
- **MTF ALIGNED** — entry terkonfirmasi searah di banyak TF sekaligus (otomatis)
- **SMC SIGNAL ENTRY** — label seperti `BUY_FLOOR`, `BREAKOUT_DETECTED`
- Label setup di track record: SWING REVERSAL / HTF SNIPER ENTRY / GOLDEN SETUP / A+ SNIPER CID (CID = Change In Delivery, langka)

## 3. Aturan inti algoritma (dari dokumen resmi mereka)

1. **Sangat selektif**: menunggu **breakout valid** + **2 Candle Rejection**
   sebagai "konfirmasi mutlak" sebelum sinyal terbit (alasan sinyal jarang).
2. **Golden Filter**: sinyal wajib searah tren utama **M15** dan **wajib ada
   liquidity sweep** sebelumnya ("Sweep Requirement").
3. **Zone Shield (Anti-Collision)**: blok paksa BUY bila harga tertahan di
   Supply institusi kuat; blok SELL di Demand kuat.
4. **Waterfall Filter (Crash Protection)**: matikan sinyal saat crash/rally parah.
5. **2-Stage Alert**: Stage 1 "GET READY" saat harga menyentuh zona institusi
   (jangan entry dulu — tunggu rejection); Stage 2 sinyal valid dengan
   Entry/SL/TP1/TP2 hanya setelah ada konfirmasi candlestick (CHOCH/BOS) di zona.
6. **No Trade Zone**: harga di tengah range = STANDBY; tunggu harga ke zona
   institusional + sweep ("ping-pong": beli di Demand, jual di Supply).
7. **4 lapis tren (lampu konfluensi)**: Micro EMA9, Mini EMA20, Macro EMA50,
   Daily Trend. Aturan entry per mode: Precision = 4/4 hijau, Standard ≥3/4,
   Aggressive ≥2/4.
8. **MTF zone alignment**: zona Demand M15 yang juga Demand kuat H1 menaikkan
   winrate (klaim 60–70%).

## 4. Mode sensitivitas (ALGORITHM SENSITIVITY)

| Mode | Nama internal | Logika |
|---|---|---|
| Aggressive | MOMENTUM PULSE 2.0 | Momentum murni **EMA 9/20 + RSI pulse**, tanpa nunggu pola candle; sinyal ~2 menit sekali; micro-scalping |
| Standard | BALANCED FLOW | Tren wajib searah; ≥3/4 lampu |
| Precision | HIGH CONFLUENCE | Sniper: semua selaras (4/4) |
| Golden | GOLDEN SIGNAL (Elite) | Extra ketat: searah M15 + sweep wajib |
| Ping Pong | SIDEWAYS | Khusus pasar ranging: main di tepi range |

## 5. Exit protocol

| Mode | Nama | Perilaku |
|---|---|---|
| Quick Exit | DYNAMIC TRAILING | Keluar cepat, SL ketat (scalping) |
| Trend Follow | POSITION HOLD | Jarak jauh, tahan swing/intraday |
| (terminal) | NITRO SCALP | Paling cepat |

Sinyal exit otomatis 4 kondisi: `TP TERCAPAI` / `CLOSE BUY NOW` (reversal bearish
saat long) / `CLOSE SELL NOW` / `HOLD POSITION`. Plus **Smart Context (Hedging)**:
bila sinyal berlawanan muncul saat posisi aktif → opsi amankan profit / cut-loss
parsial / hedging.

## 6. SL/TP (observasi angka nyata)

Dua sampel SELL di XAUUSD M5 (ATR 2.02–2.07):
- Entry 3856.77 → SL 3861.27 (jarak **4.50 pt**), TP1 3854.27 (**2.50 pt**), TP2 3847.77 (**9.00 pt**), RR tampil **1:2**
- Entry 3858.74 → SL 3863.24 (**4.50 pt**), TP1 3856.24 (**2.50 pt**), TP2 3849.74 (**9.00 pt**), RR **1:2**

Konsistensi: SL = 4.5 pt, TP1 = 2.5 pt (≈0.556R), TP2 = 9.0 pt (2.0R) →
**TP2 = 2R, TP1 ≈ 25 pips tetap**. 4.5 pt ≈ 2.2×ATR — kemungkinan SL = 2×ATR
dibulatkan/di-cap. Dokumen Forecast menyebut proyeksi pakai **ATR(14):
TP1/TP2/TP3 = 1×/2×/3× ATR, SL = 1.5× ATR**. Kesimpulan: keluarga SL/TP berbasis
ATR multipliernya bergantung mode/exit-protocol.

## 7. AI Score 0–100 (observasi checklist)

Contoh teramati (98/100, ACTION: EXECUTE):
- HTF M15 Trend RANGING → **+20**
- Zone AT SUPPLY → **+30** (di sampel lain AT SUPPLY = **+20** → bobot zona dinamis)
- Sweep BEARISH SWEEP → **+20**
- 2-Candle WAITING → **0** (saat CONFIRMED → **+20**)
- Sisa poin hingga 100 berasal dari faktor yang tidak ditampilkan
  (struktur, volume flow/CVD, bias heatmap MTF) → estimasi bonus ±18–28.

Interpretasi: skor = checklist terlihat (20–30/komponen) + bonus konteks.
Panel terkait: Market Structure `[TRENDING]/[SIDEWAYS]`, RSI 32.4 "OVERSOLD"
(muncul di kartu contoh EXECUTE BUY), CHOCH BULLISH DETECTED sebagai
"Structure Break".

## 8. Komponen panel (semantik persis)

- **Stat header**: Price (feed+offset), RSI(14) + label Neutral/Bullish/Bearish
  (dan OVERSOLD/OVERBOUGHT di kartu sinyal), ATR(14) "Volatility H1", Structure
  **BULLISH / HH + HL STRUCTURE** (bukan EXPANDING), AI Score x/100 + HIGH/MED/LOW,
  Fibonacci DISCOUNT/PREMIUM + "Equilibrium: <harga>".
- **Zone Map**: multi-zona `S1/S2/S3` (supply) dan `D1/D2/D3` (demand) berformat
  "3855.92–3858.83"; zona aktif menampilkan TOP—Resistance / BOTTOM—Entry Sell,
  **Strength dalam poin** (3.95 pts), status `PRICE IN ZONE` / `AT SUPPLY`, demand
  punya `(DOWN) 1.96 pts` (jarak ke bawah?). MT5 OFFSET input + SYNC/RESET +
  "= Binance + 10.00".
- **Market Structure Events** (baris: tag, harga, deskripsi, arah):
  - `CHOCH` — "Bearish-Bullish flip — LH broken upward" UP
  - `HH` — "Higher High — Last bullish swing high" UP
  - `HL` — "Higher Low — Bullish support level" UP (Alpha-Trade belum punya HL/LH!)
  - `SWEEP` — "Liquidity grabbed below 3854.01 — bullish reversal signal" UP
  - `TC` — "Bullish trend change — LL to HL structure flip" UP; "Bearish trend
    change — HH to LH structure flip" DOWN (TC = trend change, bisa muncul ganda)
- **MTF Confluence Heatmap** (M5 M15 H1 H4): Trend BULL/RANG/BEAR/EXP; Zone
  SUPPLY/DEMAND/(DEM|SUP) dengan jarak poin, mis. `DEM (27.6p)`; Momentum
  NEUT/BULL/BEAR; CVD Flow MIXED/BULL/BEAR; Breakout CH-BULL/CH-BEAR/WAIT. Header
  chip: "MONITORING - BULL BIAS".
- **Volume Flow**: 20 candle close terakhir, BUY VOL/SELL VOL, NET CVD, buyers%
  /sellers%, chip `⚡ ABSORPTION (BUY/SELL)`.
- **Golden Mode / High Risk Mode**: harga + `STANDBY`/`EXECUTE`/`WAIT` +
  "GET READY (GOLDEN)".
- **Trade Setup**: side, Entry, SL, TP1, TP2, RR `1:2` (= TP2/R).

## 9. Candlestick patterns yang dipakai (BOOK)

Bullish/Bearish Engulfing, Hammer, Shooting Star, Doji, Morning/Evening Star,
Three White Soldiers/Three Black Crows — dengan konteks "valid di Order Block /
demand level". Ini dasar "2 Candle Rejection".

## 10. Sesi pasar (WIB) & winrate per sesi

| Sesi | Jam | Winrate | Karakter |
|---|---|---|---|
| Asia | 06:00–14:00 | 73.0% | Akumulasi & ranging |
| London | 14:00–19:00 | 74.4% | Ekspansi tren & breakout |
| New York | 19:00–02:59 | 75.9% | Likuiditas & momentum terbesar |
| Rollover | 03:00–06:00 | 66.7% | Spread lebar, volatilitas rendah |

Best practice mereka: hindari ±15 menit berita USD high-impact (kalender di
dashboard), mulai lot terkecil.

## 11. Fitur chart terminal v4.2

Toolbar: SIGNAL, EMA (9/20/50), ST, BO (breakout), POLA (pattern), RR PROJ,
ZONE, BPE, VWAP, EVENTS, NEWS, AI INFO, H1, MTF, FORECAST, FIT, FS, SMC toggle.
FORECAST: arah dari **slope EMA20 + posisi vs EMA50**; garis Entry/TP1/TP2/TP3/SL
dari ATR(14) 1×/2×/3×/SL 1.5×; zigzag proyeksi ~11 candle. Lock View LIVE/LOCKED.

## 12. Release notes yang tertangkap

- **V4.5**: Aggressive 2.0 (EMA9/20 + RSI Pulse); Market Structure State
  [TRENDING]/[SIDEWAYS] di checklist; Sentiment Meter; setting pindah ke header.
- **V5.0**: SMC Fibonacci Detector (multi-aset XAUUSD & BTC/USD); Dynamic Zone
  Toggle (OB & FVG real-time); Telegram alert baru; dukungan BTC/USD.

## 13. Gap Alpha-Trade vs GODMODE (yang perlu dibangun)

1. Event `HL`/`LH` (Higher Low / Lower High) — belum ada di analytics.py.
2. Struktur: tampil `BULLISH — HH + HL STRUCTURE` (bukan EXPANDING/CONTRACTING).
3. Zone Map multi-level S1–S3 / D1–D3 + strength dalam **poin** (bukan jumlah candle).
4. Skor confluence: bobot dinamis zona (20–30) + bonus konteks hingga 98–100.
5. Setup taxonomy: label GOLDEN SETUP / HTF SNIPER / SWING REVERSAL / A+ SNIPER.
6. 2-Stage alert: "GET READY" saat harga masuk zona; "VALID" setelah rejection
   (2-candle/CHOCH) di dalam zona.
7. TP1 tetap ~25 pips (atau 0.55R), TP2 = 2R; SL ≈ 2×ATR (cap 1–2.5×ATR sudah ada).
8. Market Condition [TRENDING]/[SIDEWAYS] (ADX-like atau dari struktur).
9. AI reasoning 3 baris SITUASI / KARENA / MAKA.
10. Sesi pasar WIB + rekomendasi (Asia/London/NY/Rollover).
