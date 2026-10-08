# Konverter

Aplikasi web (Flask) untuk mengonversi file **PDF** dan **CSV** menjadi **Excel (.xlsx)**, dirancang untuk menangani file PDF besar (ribuan halaman, mis. laporan mutasi bank).

## Fitur

- Konversi **PDF → Excel** dan **CSV → Excel**
- Alur **2 langkah**: unggah → pratinjau → konversi
  - PDF: menampilkan jumlah halaman, sampel baris tabel, dan **perkiraan waktu konversi**
  - CSV: menampilkan jumlah baris/kolom, header, dan 10 baris sampel
- **Progress real-time** pada modal konversi:
  - Persentase + pesan tahap (`Halaman X dari halaman Y`, `Menyusun kolom data`, `Menulis file Excel`)
  - **Perkiraan waktu tersisa** (2 baris: `± ... tersisa` / `berjalan ...`) dan total waktu saat selesai
  - Detektor kemacetan: jika pembacaan lambat, pesan `pembacaan melambat (±N detik), tetap berjalan`; bila tidak ada kemajuan selama 3 menit, proses gagal dengan pesan jelas (tidak membeku selamanya)
- **Paralel** untuk PDF besar (baca halaman per chunk dengan beberapa worker)
- **Batas unggah** konfigurasi via variabel lingkungan (default 128 MB)
- **Pembersihan otomatis** file sementara (unggahan/pratinjau/output lama dihapus setelah 24 jam / pekerjaan selesai)

## Persyaratan

- Python 3.10+ (dikembangkan di 3.13)
- Windows/Linux/macOS

## Instalasi

```bash
git clone <url-repo>
cd konverter
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
```

## Menjalankan

```bash
python server.py
```

Buka <http://localhost:5000>.

`server.py` memakai mode stabil (tanpa reloader/debug) sehingga konversi lama tidak terputus; `run.py` tersedia untuk pengembangan (debug). Untuk pengaturan beban, lihat tabel konfigurasi di bawah.

```bash
# contoh: kurangi beban paralel untuk mesin yang sedang sibuk
$env:KONVERTER_MAX_WORKERS=2; python server.py   # PowerShell
KONVERTER_MAX_WORKERS=2 python server.py          # Linux/macOS
```

> **Catatan**: karena mode `debug=True`, gunakan `python run.py` hanya untuk pengembangan.

## Konfigurasi

| Variabel lingkungan | Default | Keterangan |
| --- | --- | --- |
| `KONVERTER_MAX_UPLOAD_MB` | `128` | Batas ukuran file unggahan (MB) |
| `KONVERTER_MAX_WORKERS` | `4` | Jumlah worker paralel untuk PDF besar (1–8). Kurangi jika mesin sibuk, naikkan jika ingin lebih cepat |
| `KONVERTER_CHUNK_PAGES` | `20` | Halaman per tugas worker |

Pedoman kecepatan (terukur pada i5-11400H, Windows): ±0,55–0,65 dtk/halaman single-thread. Dengan `KONVERTER_MAX_WORKERS=4` (default) file 4679 halaman ±**15–26 menit**; tutup aplikasi berat lalu pakai 6–8 worker untuk hasil ±**10–15 menit**.

## Struktur Proyek

```
konverter/
├── run.py                  # Entry point (pengembangan, debug)
├── server.py               # Entry point stabil (tanpa reloader)
├── requirements.txt
├── app/
│   ├── __init__.py         # create_app(), konfigurasi folder
│   ├── routes.py           # Endpoint /, /preview, /convert, /progress
│   ├── templates/
│   │   └── index.html      # UI satu halaman
│   ├── static/
│   │   ├── js/app.js       # Logika klien (upload, polling progress, download)
│   │   └── css/style.css
│   └── services/
│       ├── converter.py    # Logika konversi CSV/PDF → Excel, ekstraksi tabel
│       ├── previews.py     # Registrasi/pratinjau temp, pembersihan TTL
│       └── progress.py     # Status pekerjaan konversi (in-memory)
└── storage/
    ├── uploads/            # File terunggah (sementara)
    └── outputs/            # Hasil xlsx (sementara)
```

## API

### `POST /preview`

Unggah file dan dapatkan pratinjau + `preview_id`.

- Form: `conversion_type` (`pdf-to-excel` | `csv-to-excel`), `file`
- Respons `200`: `preview_id`, `filename`, `size_bytes`, `conversion_type`, dan metadata spesifik tipe (PDF: `page_count`, `sample_rows`, `estimated_seconds`; CSV: `row_count`, `column_count`, `headers`, `sample_rows`)

### `POST /convert`

Konversi file yang sudah dipratinjau.

- Form: `preview_id`, opsional `job_id`
- Respons `200`: file `.xlsx` (attachment)
- `400`: JSON berisi `error` (file kedaluwarsa / gagal konversi)

### `GET /progress?job_id=...`

Status pekerjaan konversi.

- Respons: `percent`, `message`, `done`, `error`, `eta_seconds`, `elapsed_seconds`

`preview_id` bersifat **sekali pakai**: setelah `/convert` dipanggil (berhasil/gagal) atau lewat TTL 24 jam, file terunggah dan pratinjau dihapus.

## Detail Teknis Konversi PDF

- Ekstraksi tabel memakai `pdfplumber` dengan `PDF_TABLE_SETTINGS` (strategi *lines*, `snap_xtolerance`) untuk hasil yang konsisten pada laporan mutasi bank.
- Halaman diproses per proses workers (`PARALLEL_MAX_WORKERS=8`, `PARALLEL_CHUNK_PAGES=20`) untuk file lebih dari `PARALLEL_MIN_PAGES` halaman.
- Perkiraan waktu konversi dikalibrasi dengan mengukur kecepatan ekstraksi blok halaman sekuensial pada pratinjau.
- Pada mesin ini kecepatan baca terukur ±0,55–0,65 detik/halaman single-thread; dengan 4 worker sejajar file 4679 halaman selesai ±15–26 menit, atau ±10–15 menit dengan 6–8 worker bila aplikasi berat ditutup.

## Menghapus Cache / File Sementara

Aplikasi ini tidak menyimpan cache permanen; data sementara otomatis dibersihkan (unggahan/pratinjau kedaluwarsa setelah 24 jam, file terhapus setelah konversi). Jika ingin membersihkan secara manual:

```bash
# 1. File unggahan & hasil sementara (aman dihapus kapan saja)
#    hati-hati: jangan hapus folder-nya, cukup isinya
rm storage/uploads/* storage/outputs/*   # Linux/macOS
del storage\uploads\* storage\outputs\*  # Windows (cmd)
```

File CSV/PDF terunggah dan hasil `.xlsx` akan langsung hilang dari `storage/uploads` dan `storage/outputs`. Sertakan pengecualian: jangan hapus file `.gitkeep` bila ada.

Jika aplikasi sedang berjalan, cukup kosongkan isi kedua folder tersebut lalu segarkan halaman; sistem membuat ulang folder secara otomatis bila diperlukan.

**Cache browser**: jika tampilan/UI terasa tidak diperbarui, muat ulang dengan keras (Ctrl+Shift+R) atau bersihkan cache browser.

**Cache Python (`__pycache__`)**: aman dihapus bila terjadi kebingungan versi kode setelah pembaruan:

```bash
find . -type d -name __pycache__ -exec rm -rf {} +   # Linux/macOS
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse  # PowerShell
```

## Pemecahan Masalah

- **Konversi terasa lambat / progress diam lama**: kemungkinan kompetisi CPU atau pemindaian antivirus (mis. Windows Defender) pada folder proyek. Tambahkan folder proyek ke pengecualian real-time antivirus untuk hasil terbaik.
- **Unggah ditolak**: pastikan jenis file sesuai dengan jenis konversi yang dipilih dan ukurannya di bawah `KONVERTER_MAX_UPLOAD_MB`.