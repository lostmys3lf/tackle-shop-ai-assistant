# Log Keputusan Teknis

Catatan keputusan teknis per tanggal dalam format satu baris: `YYYY-MM-DD | keputusan | alasan`.

- 2026-11-13 | Menu berjenjang Telegram memakai pola stateless hybrid (callback `menu:*` + `GET /products`) di sub-workflow 06 terpisah dari workflow 02 | Memisahkan callback navigasi dari konfirmasi transaksi (`confirm:`/`reject:`), menghindari tabel state menu di DB, dan tetap mengizinkan chat bebas kapan saja.
- 2026-11-15 | Menambahkan `NODE_OPTIONS=--dns-result-order=ipv4first` pada service `n8n` di Docker Compose | Menghindari timeout IPv6 Node/Undici saat container menghubungi `api.telegram.org`, sekaligus memastikan registrasi webhook Telegram memakai jalur IPv4 yang stabil.
