# Catatan Struktur Pasar — kerangka FzX (dari materi user, 2026-11-23)

Materi user mengajarkan SMC bertingkat (LTF→MTF→HTF). Ringkasan konsep yang
relevan untuk engine Alpha-Trade:

## Hierarki struktur
- LTF: M1/M2/M3 · MTF: M5/M15/M30 · HTF: H1/H4/D1
- Struktur selesai di LTF → disempurnakan MTF → ditutup reaksi HTF (bias sebenarnya).

## Tingkatan BOS / IDM / Likuiditas
- **INTERNAL** (skala kecil): BOS internal, IDM internal, internal liquidity.
- **MIDDLE**: struktur perantara; IDM internal bisa "berubah fungsi" menjadi
  MIDDLE-IDM saat struktur naik skala (perpindahan fungsi inducement).
- **MAJOR**: BOS/likuiditas terbesar (external range), KEY LEVEL, protected high/low.
- **IDM (inducement)**: pullback minor sebelum impuls — harga kerap menyapu IDM
  dulu baru melanjutkan. Entry model FzX menunggu IDM tersapu.
- **Turtle Soup / SBR**: sweep level lalu balik arah (setara sweep kita).
- **Trap**: break palsu sebelum pergerakan sebenarnya.

## Terjemahan ke Alpha-Trade (kandidat eksperimen)
1. **Multi-scale swings**: pivot 3 skala (internal≈5 bar, middle≈15, major≈40)
   → BOS per skala; gate: arah MAJOR harus searah dengan sinyal.
2. **IDM-sweep entry**: setelah BOS searah, tandai IDM (pullback pertama);
   sinyal valid hanya saat harga menyapu IDM (entry lebih dalam, RR lebih baik).
3. **Sweep skala besar**: sweep ekstrem 40-80 bar (middle/major liquidity)
   sebagai skor bonus daripada sweep 20 bar (internal).

Status: kandidat diuji via harness TRAIN→OOS (research_idm.py) — diterima
hanya kalau memperbaiki angka; selain itu didokumentasikan dan tidak aktif.
