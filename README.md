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

## Menjalankan dengan Docker

```bash
docker compose up --build
```

Buka <http://localhost:5000>. Produksi memakai `gunicorn` (`wsgi.py`) dengan worker `gthread` (`--threads 8`) agar polling `/progress` tetap terlayani saat `/convert` berjalan, dan `--timeout 0` agar konversi besar tidak diputus. Aplikasi menyimpan file sementara di filesystem kontainer (ephemeral) — aman karena file otomatis dibersihkan.

## Akses Online (GitHub Codespaces)

Aplikasi ini adalah **server Flask**, sehingga tidak bisa di-hosting di GitHub Pages. Cara gratis tanpa kartu untuk mengaksesnya online adalah **GitHub Codespaces**:

1. Buka repo di GitHub → tombol **`<> Code`** → tab **Codespaces** → **Create codespace on main**.
2. Terminal muncul otomatis; jalankan:
   ```bash
   docker compose up --build
   ```
3. Buka tab **Ports**, cari port **5000** (label *Konverter Web*), klik kanan → **Port Visibility → Public**.
4. Dapatkan URL publik seperti `https://<nama-codespace>-5000.app.github.dev` dan bagikan.

> URL hanya aktif **selama sesi Codespace berjalan** (bukan permanen; sesi berhenti saat idle/tertutup). Untuk akses permanen dengan resource besar, gunakan VPS atau hosting Docker berbayar.

## Image Docker di GitHub Container Registry (GHCR)

Setiap push ke `main`, GitHub Actions (`.github/workflows/docker-build.yml`) otomatis membangun image dan mengunggahnya ke:

```
ghcr.io/hisyamali/konverter
```

Tag yang dibuat: `latest` dan `sha-<7 huruf>`. Tidak perlu secret — pakai `GITHUB_TOKEN` bawaan.

Menjalankan image dari host mana pun (VPS/mesin lain):

```bash
docker run -d -p 5000:7860 -e PORT=7860 -e KONVERTER_MAX_WORKERS=2 ghcr.io/hisyamali/konverter:latest
```

> Package GHCR defaultnya **private**. Agar bisa di-pull publik, buka GitHub → repo → **Settings → Packages** → set visibility package `konverter` → **public**.

## Dev Container (VS Code / Codespaces)

Repositori memiliki **dua** konfigurasi dev container agar lingkungan lokal dan Codespaces tidak saling mengganggu:

| Konfigurasi | Untuk | Ringkasan |
| --- | --- | --- |
| `.devcontainer/devcontainer.json` | **GitHub Codespaces** (default) | Image universal + port 5000 otomatis publik |
| `.devcontainer/local/devcontainer.json` | **VS Code lokal** | Image Python 3.13, install dependensi, jalankan `python run.py` |

### Codespaces

Otomatis memakai `.devcontainer/devcontainer.json`. Setelah codespace terbuka, jalankan `docker compose up --build` lalu jadikan port 5000 publik (lihat bagian *Akses Online*).

### VS Code lokal

1. Buka repo di VS Code (Docker Desktop harus berjalan).
2. Command Palette (`Ctrl+Shift+P`) → **Dev Containers: Reopen in Container**.
3. Jika muncul pilihan, pilih **Konverter (local - Python native)**. Bila picker tidak muncul, gunakan **Dev Containers: Open Folder in Container...** lalu pilih konfigurasi tersebut.
4. Di terminal dalam container, jalankan `python run.py` lalu buka <http://localhost:5000>.

> Konfigurasi lokal tidak menjalankan Docker di dalam container. Jika ingin memakai Docker, jalankan `docker compose up --build` dari terminal **host** (Docker Desktop), atau pakai config Codespaces.

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
├── Dockerfile              # Image Docker (host-agnostic)
├── docker-compose.yml      # Menjalankan via Docker Compose
├── .dockerignore
├── requirements.txt
├── .devcontainer/
│   ├── devcontainer.json       # Konfigurasi GitHub Codespaces (port publik)
│   └── local/
│       └── devcontainer.json   # Konfigurasi VS Code lokal (Python native)
├── .github/
│   └── workflows/
│       └── docker-build.yml  # GitHub Actions: build & push image ke GHCR
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