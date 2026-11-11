# Tasks

Kerjakan berurutan, satu task per sesi. Cara pakai di Antigravity:
"Kerjakan Task N di docs/tasks.md. Baca AGENTS.md dan docs/spec.md dulu. Bikin rencananya, tunggu persetujuan saya."

Status: `[ ]` belum, `[x]` selesai.

## Task 1: Docker Compose lengkap `[ ]`
Tambahkan service `n8n` dan `api` ke `docker-compose.yml` yang sudah ada (Postgres sudah ada, jangan diubah selain yang perlu). `api` berisi FastAPI minimal dengan `GET /health`.
Selesai kalau:
- `docker compose up -d` menjalankan `postgres`, `n8n`, dan `api` tanpa error, dan healthcheck semuanya sehat.
- `GET http://localhost:8000/health` mengembalikan status OK dan status koneksi database.
- UI n8n terbuka di `http://localhost:5678`, dan data n8n tersimpan di volume (nggak hilang setelah restart).
- `.env.example` diperbarui dengan variable baru.

## Task 2: Setup MCP (dikerjakan Jef) `[ ]`
Sambungkan MCP Postgres dan MCP n8n di Antigravity ke service dari Task 1.
Selesai kalau: agent bisa membaca daftar tabel Postgres dan daftar workflow n8n lewat MCP.

## Task 3: Schema, migration, dan seed `[ ]`
Bikin tabel sesuai spec bagian 7, seed bagan akun, dan seed produk dari `db/seed/products.csv` beserta jurnal stok awal.
Selesai kalau:
- Migration jalan dari database kosong.
- Seed produk membuat transaksi `opening`, stock movement, dan jurnal stok awal yang seimbang.
- Kalau `products.csv` masih kosong, seed berhenti dengan pesan yang jelas, bukan error acak.

## Task 4: Logika akuntansi `[ ]`
Implementasi aturan spec bagian 8 di `app/accounting/` sebagai kode murni (tanpa LLM).
Selesai kalau `uv run pytest` lulus dengan test minimal:
- Jurnal pembelian dan penjualan seimbang.
- Contoh HPP rata-rata di spec menghasilkan angka yang sama persis.
- Penjualan melebihi stok ditolak.
- Transaksi `pending` nggak mengubah stok dan jurnal.

## Task 5: Client LLM `[ ]`
Client tunggal di `app/llm/` untuk endpoint kompatibel OpenAI: model utama dan fallback dari env, timeout, retry dengan jeda saat 429, validasi output Pydantic, dan tracing Langfuse (aktif hanya kalau env Langfuse diisi).
Selesai kalau:
- Test dengan mock membuktikan: retry saat 429, pindah ke fallback saat model utama gagal, dan error yang jelas saat semuanya gagal.
- Satu panggilan LLM sungguhan muncul sebagai trace di Langfuse.

## Task 6: Router jenis pesan `[ ]`
Klasifikasi pesan jadi `sale`, `purchase`, `question`, `other`. Provider dipilih lewat `ROUTER_PROVIDER`: Jev (Cloudflare Workers AI) atau LLM.
Selesai kalau kedua provider bisa dipanggil lewat interface yang sama, dan provider Jev otomatis jatuh ke LLM kalau gagal.

## Task 7: Ekstraksi dan endpoint transaksi `[ ]`
`POST /messages` untuk penjualan dan pembelian, `POST /transactions/{id}/confirm`, dan `POST /transactions/{id}/reject`, sesuai spec bagian 9 dan 10. Prompt di `prompts/` dan didaftarkan ke prompt management Langfuse.
Selesai kalau:
- Chat penjualan menghasilkan transaksi `pending` dan teks konfirmasi yang benar.
- Confirm menjalankan logika akuntansi Task 4 dan mengembalikan sisa stok.
- Produk yang nggak dikenali menghasilkan pertanyaan balik, bukan transaksi.

## Task 8: Eval dan CI `[ ]`
Dataset minimal 30 contoh di `evals/datasets/`, runner `evals/run_eval.py`, dan workflow GitHub Actions sesuai spec bagian 13 dan 14.
Selesai kalau eval jalan sampai selesai tanpa kena limit Groq, menampilkan akurasi per field, tercatat sebagai dataset run di Langfuse, dan CI lulus di GitHub. Ini memenuhi MLOps maturity Level 1 di spec bagian 17.

## Task 9: Workflow n8n pesan dan konfirmasi `[ ]`
Channel: Telegram. Workflow 1 dan 2 di spec bagian 12. Butuh bot Telegram dari BotFather (dikerjakan Jef) dan webhook lewat Cloudflare Tunnel.
Selesai kalau pemilik bisa mencatat penjualan dari HP, menekan Benar, dan menerima balasan sisa stok.

## Task 10: Agent `[ ]`
Intent `question` dijawab agent dengan tools di spec bagian 11.
Selesai kalau pertanyaan stok, penjualan per periode, dan barang terlaris dijawab dengan angka yang sama dengan hasil query langsung.

## Task 11: Laporan dan jadwal `[ ]`
Endpoint laporan di spec bagian 9 dan workflow n8n 3 sampai 5. Jam pengiriman masih `TBD`, jadi pakai jam sementara yang mudah diubah.
Selesai kalau ketiga laporan bisa dipicu manual dari n8n dan isinya cocok dengan data.

## Task 12: Monitoring `[ ]`
`GET /metrics`, service Prometheus dan Grafana di Docker Compose, dan satu dashboard sesuai spec bagian 13.
Selesai kalau dashboard menampilkan data setelah beberapa pesan dikirim.

## Task 13: Foto nota `[ ]`
Provider OCR lewat `OCR_PROVIDER`: PaddleOCR-VL (lokal) dan Qwen vision di Groq.
Selesai kalau foto nota asli menghasilkan transaksi `pending` lewat ekstraktor yang sama, dan hasil tes nota asli masuk ke dataset eval.

## Task 14: Deploy ke GCP `[ ]`
Satu VM Compute Engine menjalankan Docker Compose, plus README bahasa Inggris (arsitektur, cara jalan, keputusan desain, roadmap).
Selesai kalau sistem bisa dipakai dari HP saat laptop mati, dan langkah backup sebelum 26 November 2026 tertulis di README.

## Task 15: Deploy otomatis (MLOps Level 2, opsional) `[ ]`
GitHub Actions sesuai spec bagian 14 dan 17: setelah test dan eval lulus di `main`, build image bertag commit SHA, push ke registry, lalu deploy ke VM GCP.
Selesai kalau satu perubahan prompt yang lulus eval otomatis sampai ke VM tanpa langkah manual, dan rollback ke tag sebelumnya sudah dicoba sekali. Kerjakan hanya kalau masih sempat sebelum 26 November 2026.

## Task 16: Promo barang nggak laku `[ ]`
`POST /promo/slow-moving` dan workflow n8n mingguan.
Selesai kalau caption dan saran bundling terkirim, dan semua angka di dalamnya berasal dari data.
