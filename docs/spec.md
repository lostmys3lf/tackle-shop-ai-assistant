# Spec: Asisten Toko Pancing Papa

Bagian bertanda `TBD` belum diputuskan. Jangan diisi dengan asumsi.
Bagian bertanda `(usulan)` adalah pilihan teknis yang belum dikonfirmasi Jef. Boleh dipakai, tapi sebut di rencana task supaya bisa dicek.

## 1. Tujuan dan pengguna
- Pengguna: pemilik toko perlengkapan mancing (gaptek, pakai HP).
- Masalah: pencatatan penjualan dan stok masih manual atau nggak ada.
- Solusi: pemilik cukup chat biasa atau kirim foto nota. Sistem mencatat transaksi setelah dikonfirmasi, update stok, bikin jurnal, menjawab pertanyaan soal data toko, dan mengirim laporan otomatis.

## 2. Scope versi pertama
1. Catat penjualan dan pembelian stok lewat chat. Foto nota jadi jalur tambahan.
2. Konfirmasi ke pemilik (tombol Benar / Salah) sebelum transaksi disimpan.
3. Jurnal double-entry otomatis, stok terupdate, HPP metode rata-rata.
4. Tanya data lewat chat (agent + tools read-only).
5. Otomatis lewat n8n: alert stok menipis (harian), laporan penjualan (mingguan), laba rugi sederhana (bulanan).
6. Tahap terakhir: caption promo dan saran bundling untuk barang yang nggak laku.

Di luar scope versi pertama: neraca saldo, jurnal penyesuaian, neraca, laporan perubahan modal, beban operasional. Masuk roadmap README.

## 3. Hal yang belum diputuskan
- Channel chat: Telegram (diputuskan, karena gratis dan setup-nya mudah). FastAPI tetap dibuat netral terhadap channel dan semua urusan channel ada di n8n, supaya nanti bisa pindah ke WhatsApp tanpa mengubah FastAPI.
- Data produk awal: 5 produk di `db/seed/products.csv`. Harga jual asli dari toko. Harga beli masih dummy, dihitung dari markup 20% di atas harga pokok (`harga beli = harga jual / 1,2`). Stok dan stok minimal juga dummy. Kategori: `peralatan` dan `bahan_umpan`.
- Jam pengiriman laporan otomatis. `TBD`.
- Metode pembayaran selain tunai. `TBD`. Versi pertama mengasumsikan semua transaksi tunai.
- Barang yang dibeli per kemasan tapi dijual per biji (misalnya kail isi 13 per bungkus), dan apakah varian ukuran (kail no. 1-15, senar dengan beberapa ukuran) dicatat terpisah atau digabung. `TBD`, ditunda setelah alur inti jalan. Versi pertama hanya menangani produk yang satuan beli dan jualnya sama.

## 4. Arsitektur
- Telegram ke n8n, n8n ke FastAPI, FastAPI ke PostgreSQL dan LLM.
- n8n: menerima pesan, cek allowlist pemilik, meneruskan ke FastAPI, mengirim tombol konfirmasi, menjalankan jadwal laporan.
- FastAPI: routing jenis pesan, ekstraksi, agent, logika akuntansi, laporan.
- Semua service di Docker Compose: `postgres`, `n8n`, `api`. Prometheus dan Grafana ditambahkan di task monitoring.

## 5. Model
| Fungsi | Model | Lewat |
|---|---|---|
| Ekstraksi transaksi, agent, caption | `openai/gpt-oss-120b` | Groq (kompatibel OpenAI) |
| Fallback LLM | `openai/gpt-oss-20b` | Groq |
| Routing jenis pesan, kategori produk | Jev (TypeSafe AI) | Cloudflare Workers AI; kalau nggak tersedia, fallback ke `gpt-oss-20b` |
| Baca foto nota (lokal) | PaddleOCR-VL-1.6 | GPU laptop (GTX 1650 4GB, perlu dites) |
| Baca foto nota (GCP) | `qwen/qwen3.8-27b` (vision) | Groq |

Detail pemanggilan Jev di Cloudflare Workers AI: model ID `typesafe/jev`, lewat REST `POST https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run` dengan header `Authorization: Bearer {CLOUDFLARE_API_TOKEN}` dan body berisi `model` serta `input` (`state` dan `questions`). Jev bukan model chat: input-nya satu `state` dan pertanyaan bertipe `noul`, `choice`, atau `score`, dan output-nya jawaban dengan probabilitas.

Provider OCR dan router dipilih lewat environment variable, kodenya sama.

Batas Groq free tier yang relevan untuk `gpt-oss-120b`: 30 request/menit, 1.000 request/hari, 8.000 token/menit. Client LLM wajib menangani error 429 dengan jeda dan retry.

## 6. Environment variable
```
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_FALLBACK_MODEL
ROUTER_PROVIDER (jev | llm), ROUTER_JEV_MODEL (default `typesafe/jev`), CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN
OCR_PROVIDER (paddle | groq_vision), OCR_VISION_MODEL
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, DATABASE_URL
API_SECRET (dipakai n8n saat memanggil FastAPI, lewat header X-API-Key)
TELEGRAM_OWNER_CHAT_ID (dipakai n8n untuk allowlist pemilik; token bot disimpan sebagai credential di n8n)
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
```

## 7. Database
Semua uang `NUMERIC(14,2)`. Semua tabel punya `created_at`.

- `accounts`: `code` (PK), `name`, `type` (asset, equity, revenue, expense).
- `products`: `id`, `name`, `category`, `unit`, `sell_price`, `avg_cost`, `stock_qty`, `min_stock`.
- `transactions`: `id`, `type` (sale, purchase, opening), `status` (pending, confirmed, rejected), `source` (chat, photo, seed), `raw_text`, `confirmed_at`.
- `transaction_items`: `id`, `transaction_id`, `product_id`, `qty`, `unit_price`.
- `stock_movements`: `id`, `product_id`, `transaction_id`, `qty_change` (positif masuk, negatif keluar), `unit_cost`.
- `journal_entries`: `id`, `transaction_id`, `entry_date`, `description`.
- `journal_lines`: `id`, `journal_entry_id`, `account_code`, `debit`, `credit`.
- `messages`: `id`, `channel`, `chat_id`, `text`, `intent`, `intent_confidence`, `reply_text`.

Bagan akun awal:
| Kode | Nama | Tipe |
|---|---|---|
| 1-101 | Kas | asset |
| 1-301 | Persediaan Barang Dagang | asset |
| 3-101 | Modal Pemilik | equity |
| 4-101 | Penjualan | revenue |
| 5-101 | Harga Pokok Penjualan | expense |

Migration (usulan): SQLAlchemy 2 + Alembic.

## 8. Aturan akuntansi
Semua dihitung di `app/domain/accounting/`, dan semua jurnal wajib seimbang.

- Stok awal (seed): Debit Persediaan, Kredit Modal Pemilik, sebesar `stok x harga beli`. `avg_cost` awal = harga beli.
- Pembelian: Debit Persediaan, Kredit Kas, sebesar `qty x harga beli`.
  `avg_cost baru = (stok lama x avg_cost lama + qty x harga beli) / (stok lama + qty)`.
- Penjualan, dua jurnal dalam satu entry:
  - Debit Kas, Kredit Penjualan, sebesar `qty x harga jual`.
  - Debit HPP, Kredit Persediaan, sebesar `qty x avg_cost` saat transaksi.
- Harga jual di chat kosong: pakai `sell_price` produk. Harga beli di chat kosong: tanya balik ke pemilik.
- Penjualan melebihi stok: ditolak saat confirm dengan pesan yang jelas. Saat transaksi `pending` dibuat, teks konfirmasi juga sudah memberi peringatan kalau stok nggak cukup, supaya pemilik tahu lebih awal.
- Transaksi baru masuk ke stok dan jurnal setelah statusnya `confirmed`.

Contoh HPP rata-rata: stok 10 dengan avg 50.000, beli 10 seharga 60.000, maka avg baru 55.000. Jual 4, HPP = 220.000.

## 9. Endpoint FastAPI
Semua endpoint selain `/health` dan `/metrics` wajib header `X-API-Key`.

- `GET /health`: status service dan koneksi database.
- `POST /messages`: input `{channel, chat_id, text?, image_base64?}`. Alur: simpan pesan, routing, lalu
  - penjualan atau pembelian: ekstrak, bikin transaksi `pending`, kembalikan teks konfirmasi;
  - pertanyaan: jalankan agent, kembalikan jawaban;
  - lainnya: balasan singkat bahwa bot cuma mengurus pencatatan toko.
  Output: `{intent, reply_text, pending_transaction_id?}`.
- `POST /transactions/{id}/confirm`: jalankan aturan akuntansi, kembalikan ringkasan (termasuk sisa stok).
- `POST /transactions/{id}/reject`: ubah status jadi `rejected`.
- `GET /reports/low-stock`: produk dengan `stock_qty <= min_stock`.
- `GET /reports/sales?start=&end=`: total penjualan, jumlah transaksi, barang terlaris.
- `GET /reports/profit-loss?month=YYYY-MM`: penjualan, HPP, laba kotor.
- `POST /promo/slow-moving`: caption promo dan saran bundling (tahap terakhir).
- `GET /metrics`: metrik Prometheus.

Teks konfirmasi dan balasan transaksi disusun penuh dari template di kode, tanpa LLM, supaya angka pasti benar dan hemat kuota. Jawaban agent (intent `question`) boleh disusun LLM, tapi semua angkanya harus berasal dari hasil tools.

## 10. Ekstraksi
- Prompt menyertakan daftar produk (id, nama, kategori) supaya LLM mengembalikan `product_id`.
- Skema output:
  ```
  {"type": "sale" | "purchase", "items": [{"product_id": int | null, "product_name_raw": str, "qty": int, "unit_price": number | null}]}
  ```
- `product_id` null: bot menanyakan barang mana yang dimaksud. Menambah produk baru lewat chat belum masuk versi pertama.
- Foto nota: OCR jadi teks dulu, lalu lewat ekstraktor yang sama dengan jalur chat.

## 11. Agent
Tools read-only, semua angka dihitung SQL atau Python:
- `get_stock(product_query?)`
- `sales_summary(start, end)`
- `top_products(start, end, limit)`
- `profit_loss(month)`

## 12. Workflow n8n
Workflow diekspor ke `n8n/workflows/*.json` dan ikut di-commit.
1. Pesan masuk: Telegram Trigger, cek allowlist pemilik, `POST /messages`, kirim balasan atau tombol konfirmasi (inline keyboard Telegram).
2. Konfirmasi: callback dari tombol Benar / Salah, panggil confirm atau reject, kirim hasilnya.
3. Alert stok menipis (harian, jam `TBD`).
4. Laporan penjualan (mingguan, jam `TBD`).
5. Laba rugi sederhana (bulanan, jam `TBD`).

## 13. LLMOps
- Eval: `evals/datasets/*.jsonl` berisi minimal 30 contoh chat transaksi + JSON yang benar, plus contoh routing intent. Contoh pertanyaan agent ditambahkan di Task 10. Runner `evals/run_eval.py` menghitung akurasi per field dan gagal kalau di bawah threshold (awal 0,85, usulan). Pakai jeda antar panggilan karena limit token per menit.
- Langfuse Cloud (Hobby, gratis):
  - Tracing setiap panggilan LLM (input, output, token, latency, model).
  - Prompt management: prompt di `prompts/` didaftarkan dan diberi versi; aplikasi mencatat versi prompt yang dipakai di setiap trace.
  - Setiap eval dicatat sebagai dataset run: versi prompt, model, akurasi per field.
- Metrik Prometheus: jumlah panggilan LLM, latency, token, rate gagal parsing, jumlah fallback, jumlah transaksi per status.
- Grafana: satu dashboard dari metrik di atas.
- Guardrail: validasi Pydantic, retry, fallback model, konfirmasi pemilik.

## 14. CI (GitHub Actions)
- Setiap push: `black --check`, `pylint`, `pytest`.
- Eval: manual (`workflow_dispatch`) dan saat ada perubahan di `prompts/` atau `app/infrastructure/llm/`. Butuh secret `LLM_API_KEY` (opsional secret `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`). `LLM_BASE_URL`, `LLM_MODEL`, dan `LLM_FALLBACK_MODEL` disimpan sebagai repository variable, bukan secret, karena nggak rahasia. Hasil eval disimpan sebagai artifact GitHub Actions, dan ikut dicatat ke Langfuse kalau env Langfuse diisi lewat secret.
- Deploy otomatis (Level 2, lihat bagian 17): push ke `main`, test dan eval lulus, build image, push ke registry, lalu VM GCP menarik image baru dan menjalankan `docker compose up -d`.

## 15. Struktur folder dan arsitektur
Pakai Clean Architecture versi ringan dengan empat lapisan.

```
.github/workflows/
app/
  main.py               # bikin app FastAPI dan mendaftarkan router
  config.py             # membaca environment variable
  api/                  # router FastAPI dan schema request/response
  services/             # alur use case: proses pesan, konfirmasi transaksi, laporan, agent
  domain/
    models.py           # model domain (transaksi, item, jurnal) tanpa ketergantungan framework
    accounting/         # aturan jurnal, stok, dan HPP (kode murni)
  infrastructure/
    db/                 # model SQLAlchemy, session, repository, migration
    llm/                # client LLM, router (Jev/LLM), ekstraktor
    ocr/                # provider OCR
prompts/
scripts/              # skrip pendukung, misalnya register_prompts.py untuk mendaftarkan prompt ke Langfuse
evals/
  datasets/
  run_eval.py
db/seed/products.csv
n8n/workflows/
monitoring/       # konfigurasi Prometheus dan Grafana
tests/
docs/
```

Aturan lapisan:
- Arah ketergantungan: `api` ke `services`, `services` ke `domain` dan `infrastructure`. `domain` nggak boleh import apa pun dari `api`, `services`, `infrastructure`, FastAPI, SQLAlchemy, atau client LLM.
- `api` hanya memvalidasi input, memanggil service, dan mengembalikan output. Nggak ada logika bisnis atau query database di sini.
- Query database hanya ada di repository di `infrastructure/db/`.
- Agent: loop agent ada di `services/`, tools-nya memanggil fungsi service atau repository, dan panggilan model lewat client di `infrastructure/llm/`.
- Abstraksi (interface atau base class) hanya dibuat di tempat yang memang punya lebih dari satu implementasi: provider LLM, provider router, dan provider OCR. Bagian lain cukup pakai function atau class biasa.

## 16. Deploy
- Lokal: Docker Compose di laptop, webhook Telegram lewat Cloudflare Tunnel.
- Cloud: satu VM Compute Engine GCP menjalankan Docker Compose yang sama. Kredit berakhir 26 November 2026; backup (`pg_dump`, export workflow n8n) dan matikan VM sebelum tanggal itu.

## 17. Target MLOps maturity
Mengacu ke framework MLOps maturity Google (Level 0 sampai 2). Karena project ini nggak melatih model, padanan "continuous training" adalah evaluasi otomatis setiap kali prompt, model, atau kode LLM berubah.

Level 1 (wajib):
- Pipeline eval otomatis di CI setiap ada perubahan di `prompts/` atau `app/infrastructure/llm/`, dengan threshold yang bikin CI gagal.
- Prompt dan model punya versi (prompt management Langfuse dan environment variable).
- Hasil eval dan trace tercatat di Langfuse, jadi setiap versi bisa dibandingkan.
- Monitoring produksi lewat Prometheus dan Grafana.

Level 2 (target kalau sempat, sebelum kredit GCP habis 26 November 2026):
- CI/CD penuh: perubahan yang lulus test dan eval otomatis di-build jadi image, di-push ke registry (usulan: Docker Hub), dan di-deploy ke VM GCP lewat GitHub Actions.
- Image diberi tag commit SHA, supaya rollback cukup dengan menjalankan image tag sebelumnya.
- Setelah 26 November 2026, workflow deploy dimatikan bersama VM-nya.

