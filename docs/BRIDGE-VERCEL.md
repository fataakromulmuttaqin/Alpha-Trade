# Bridge Vercel → Backend Lokal

Frontend di-host di Vercel (statis), backend tetap jalan di PC lokal — data
mengalir lewat **tunnel** publik: `browser (Vercel) → tunnel → backend lokal`.

## Prinsip

- Vercel **hanya** menyajikan file statis `frontend/` (vercel.json sudah ada).
- Browser tidak bicara ke Vercel API sama sekali: semua `fetch` diarahkan ke
  **base API** (URL tunnel backend lokal).
- Dengan base API ter-set, dashboard otomatis masuk **mode polling**
  (snapshot tiap 4 detik + refresh candle incremental) — WS broadcast lokal
  tidak dikenakan, tapi cukup responsif karena data hanya berubah per candle
  close. WS tetap dipakai bila frontend dilayani backend yang sama origin.
- Backend sudah mengirim header CORS `*` + handler OPTIONS preflight.

## Mengisi base API (3 cara, prioritas berurutan)

1. **Parameter URL** — `https://<projek>.vercel.app/?api=https://xxxx.trycloudflare.com`
   (tidak disimpan; hanya sesi tab itu)
2. **Klik tulisan status koneksi** di footer dashboard → masukkan URL tunnel →
   tersimpan di localStorage browser → berlaku sampai dihapus.
3. **`frontend/config.js`** — `window.AT_API_BASE = "https://..."` (tetap,
   ikut deploy; cocok bila pakai tunnel dengan domain permanen).

Menghapus nilai tersimpan: klik status koneksi → kosongkan input.

## Menjalankan tunnel (backend lokal)

### Opsi A — Cloudflare quick tunnel (gratis, tanpa akun, URL acak per run)

```bash
# sekali saja: unduh cloudflared dari https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:8080
# -> tampil URL https://xxxx-yyyy.trycloudflare.com
```

Lalu isi base API di dashboard (cara 1/2 di atas) dengan URL itu.

### Opsi B — ngrok (butuh akun, bisa domain statis)

```bash
ngrok http 8080
```

### Opsi C — URL permanen (rekomendasi jangka panjang)

`cloudflared tunnel` dengan **named tunnel** + domain sendiri → URL tidak
berubah, cukup di-set sekali di `config.js`. Lihat dokumentasi Cloudflare
(Zero Trust → Networks → Tunnels).

## Deployment Vercel

```bash
cd frontend
vercel --prod        # atau hubungkan repo di dashboard Vercel (root: frontend)
```

## Keamanan

- Tunnel membuat backend-mu dapat diakses publik. API hanya membaca data
  pasar/simulasi + `POST /api/settings` (ubah mode). Tidak ada kredensial di
  dalamnya, tapi jangan bagikan URL tunnel ke orang lain.
- Saat backend berhenti, tunnel mati — dashboard Vercel akan menampilkan
  "mode polling" tanpa data. Restart backend + tunnel, URL quick tunnel
  berubah → perbarui base API di dashboard.
