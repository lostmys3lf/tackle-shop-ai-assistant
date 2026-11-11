# Clean code: ringkasan untuk project ini

Disarikan dengan kata-kata sendiri dari materi "Rangkuman Python Clean Code" kelas MLOps Dicoding (https://www.dicoding.com/academies/443/tutorials/25800), yang merujuk ke buku Clean Code karya Robert C. Martin. Bagian "Aturan yang dipakai" nanti masuk ke AGENTS.md.

## Kenapa penting
Kode lebih sering dibaca daripada ditulis. Sistem yang dikembangkan bertahap dan berulang (seperti sistem ML atau sistem ini) butuh kode yang efisien, gampang dibaca, andal, dan gampang dirawat.

## Enam prinsip dasar
1. Nama variable deskriptif, bermakna, jelas bedanya, dan konsisten (misalnya `age` untuk satu nilai, `age_list` untuk kumpulannya).
2. Ikuti konvensi standar penulisan kode.
3. Keep it simple: kurangi kompleksitas sebisa mungkin.
4. Kode disertai dokumentasi yang baik.
5. DRY (Don't Repeat Yourself): jangan duplikasi logika.
6. Satu function hanya mengerjakan satu tugas.

## Cara menerapkannya
- **Style guide**: pakai PEP 8, yang mengatur penamaan, format baris, whitespace, dan comment. Manfaatnya kode lebih gampang dibaca, dikolaborasikan, dan di-debug.
- **Modular code**: pecah sistem jadi function dan module kecil yang bisa dites sendiri-sendiri dan dipakai ulang lewat import. Batasnya: satu function satu tugas, hindari argumen terlalu banyak, dan jangan memecah berlebihan sampai malah susah diikuti.
- **Refactoring**: setelah kode jalan, rapikan struktur internalnya tanpa mengubah perilakunya. Yang dipertimbangkan: efisiensi dan keterbacaan.
- **Dokumentasi**: ada tiga jenis, yaitu inline comment, docstring, dan dokumentasi project (README). Comment secukupnya saja, tapi docstring selalu ada.
- **Linter dan formatter**: linter (misalnya pylint) mengecek error dan kepatuhan PEP 8 tanpa menjalankan kode. Formatter (misalnya Black atau autopep8) merapikan format secara otomatis.

## Aturan yang dipakai di project ini
- Ikuti PEP 8. Format dengan Black, cek dengan pylint, dan dua-duanya jalan di CI.
- Nama function, variable, dan constant pakai bahasa Inggris yang deskriptif. Comment dan docstring pakai bahasa Indonesia yang natural.
- Satu function satu tugas. Kalau butuh lebih dari 4 argumen, pertimbangkan pakai Pydantic model atau dataclass.
- Jangan duplikasi logika. Perhitungan akuntansi (jurnal, stok, HPP) cuma ada di satu module.
- Pilih solusi paling sederhana yang memenuhi spec. Jangan bikin abstraksi buat kebutuhan yang belum ada.
- Setiap function dan class publik punya docstring. Inline comment hanya untuk menjelaskan alasan, bukan mengulang isi kode.
- Refactor hanya kalau test-nya ada dan tetap lulus setelah refactor.
