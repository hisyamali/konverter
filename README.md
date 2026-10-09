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
  - Detektor kemacetan: jika pembacaan lambat, pesan `pembacaan melambat (±N detik), tetap berjalan`; bila tidak ada kemajuan selama 90 detik, proses otomatis **beralih ke mode satu-per-satu (sekuensial)** dan tetap menyelesaikan file — tidak pernah membeku atau gagal karena mesin sibuk
- **Paralel adaptif** untuk PDF besar (baca halaman per chunk): jumlah worker dipilih otomatis berdasarkan RAM kosong dan CPU (bisa dipaksa via env)
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
python run.py
```

Buka <http://localhost:5000>.

`run.py` berjalan dalam mode stabil (tanpa reloader/debug, threaded) sehingga konversi lama tidak terputus. Untuk pengaturan beban, lihat tabel konfigurasi di bawah.

```bash
# contoh: paksa beban paralel lebih rendah saat mesin sibuk
$env:KONVERTER_MAX_WORKERS=2; python run.py   # PowerShell
KONVERTER_MAX_WORKERS=2 python run.py          # Linux/macOS
```

## Deploy Online (Render + GitHub Actions)

Aplikasi ini adalah server Flask, sehingga tidak bisa di-hosting di GitHub Pages. Yang dipakai adalah **GitHub Actions** sebagai otomasi deploy, dan **Render** sebagai tempat menjalankan aplikasinya.

### 1. Hubungkan repo ke Render

1. Buat akun di <https://render.com> lalu **New → Blueprint**.
2. Pilih repo `hisyamali/konverter`. Render membaca `render.yaml` dan menyiapkan web service.
3. Buka service → **Settings → Deploy Hook**, salin URL-nya.

### 2. Simpan Deploy Hook sebagai secret GitHub

1. Repo GitHub → **Settings → Secrets and variables → Actions → New repository secret**.
2. Nama: `RENDER_DEPLOY_HOOK`, nilai: URL Deploy Hook dari langkah sebelumnya.

### 3. Deploy

Push ke branch `main` (atau jalankan workflow **Deploy to Render** secara manual dari tab **Actions**). Alur workflow (`.github/workflows/deploy.yml`):

1. **Cek kode** — install dependency, `compileall`, dan import check `create_app()`.
2. **Deploy** — bila cek lolos, memanggil Deploy Hook Render.

Aplikasi akan tersedia di `https://<nama-service>.onrender.com`.

### Catatan

- Produksi memakai `gunicorn` (`wsgi.py`) dengan worker `gthread` (`--threads 8`) agar polling `/progress` tetap terlayani saat `/convert` berjalan, dan `--timeout 0` agar konversi besar tidak diputus.
- Free tier Render tidur saat idle; request pertama setelah tidur terasa lambat. RAM/CPU terbatas, sehingga konversi PDF ribuan halaman bisa lambat — atur `KONVERTER_MAX_WORKERS=1` (default di `render.yaml`).
- Filesystem Render bersifat sementara (ephemeral); aman karena aplikasi hanya menyimpan file sementara.

## Konfigurasi

| Variabel lingkungan | Default | Keterangan |
| --- | --- | --- |
| `KONVERTER_MAX_UPLOAD_MB` | `128` | Batas ukuran file unggahan (MB) |
| `KONVERTER_MAX_WORKERS` | `4` | Batas atas worker paralel untuk PDF besar (1–8). Worker aktual dipilih otomatis (CPU + RAM kosong); paksakan dengan nilai ini untuk mengendalikan beban |
| `KONVERTER_CHUNK_PAGES` | `20` | Halaman per tugas worker |

> **Worker adaptif**: jumlah worker dipilih otomatis setiap konversi = min(`KONVERTER_MAX_WORKERS`, CPU, RAM kosong menyesuaikan). Bila mesin sibuk (RAM menipis), aplikasi memakai lebih sedikit worker sehingga tidak thrash; bila paralel tetap macet >90 detik, otomatis lanjut sekuensial dan **tetap selesai**.

Pedoman kecepatan (terukur pada i5-11400H, Windows): ±0,55–0,65 dtk/halaman single-thread. Dengan worker adaptif (3–4) pada mesin dengan RAM cukup, file 4679 halaman ±**15–26 menit**; tutup aplikasi berat (browser/IDE) agar RAM kosong naik → worker lebih banyak → mendekati **±12–15 menit**.

## Struktur Proyek

```
konverter/
├── run.py                  # Entry point aplikasi (mode stabil, tanpa reloader) — dev lokal
├── wsgi.py                 # Entry point produksi (gunicorn)
├── render.yaml             # Blueprint deploy Render
├── requirements.txt
├── .github/
│   └── workflows/
│       └── deploy.yml      # GitHub Actions: cek kode → deploy ke Render
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
- Halaman dibaca per chunk oleh beberapa worker (`PARALLEL_CHUNK_PAGES=20`, batas `PARALLEL_MAX_WORKERS=4` via `KONVERTER_MAX_WORKERS`) untuk file lebih dari `PARALLEL_MIN_PAGES` halaman; jumlah worker aktual disesuaikan otomatis dengan RAM kosong mesin.
- Bila tidak ada kemajuan selama `PARALLEL_DEGRADE_SECONDS=90` detik, proses beralih ke pembacaan sekuensial untuk sisa halaman (menjamin selesai meski mesin sibuk).
- Perkiraan waktu konversi dikalibrasi dengan mengukur kecepatan ekstraksi blok halaman sekuensial pada pratinjau.
- Pada mesin ini kecepatan baca terukur ±0,55–0,65 detik/halaman single-thread; file 4679 halaman ≈ 2800 detik CPU — dengan worker adaptif 3–4 dan mesin cukup bebas selesai ±15–26 menit.

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

- **Konversi terasa lambat / progress diam lama**: aplikasi otomatis menurunkan worker atau beralih ke mode sekuensial, jadi proses akan tetap berjalan (hanya lebih pelan). Untuk mempercepat, tutup aplikasi berat (browser/VS Code) saat konversi agar RAM kosong naik. Kompetisi CPU atau pemindaian antivirus (mis. Windows Defender) juga bisa menahan; tambahkan folder proyek ke pengecualian real-time antivirus untuk hasil terbaik.
- **Unggah ditolak**: pastikan jenis file sesuai dengan jenis konversi yang dipilih dan ukurannya di bawah `KONVERTER_MAX_UPLOAD_MB`.