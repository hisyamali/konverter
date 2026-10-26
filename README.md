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
  - Detektor kemacetan/memori: jika pembacaan lambat, pesan `pembacaan melambat (±N detik), tetap berjalan`; bila tidak ada kemajuan selama 90 detik **atau** RAM kosong turun di bawah ambang, proses otomatis **beralih ke mode hemat/sekuensial** dan tetap menyelesaikan file — tidak pernah membeku atau gagal karena mesin sibuk
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

## Aplikasi Streamlit (opsional)

Selain antarmuka Flask, repositori menyertakan varian **Streamlit** (`streamlit_app.py`) yang memakai ulang logika konversi yang sama (`app/services/converter.py`). Tampilannya meniru UI Flask (hero, stepper 2 langkah, kartu pratinjau, overlay progress + ETA, feature grid) melalui `app/static/css/streamlit.css`.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Buka <http://localhost:8501>. Alur sama: unggah → pratinjau → konversi → unduh `.xlsx`. Jalankan dari **root repo** agar `.streamlit/config.toml` terbaca.

### Deploy ke Streamlit Community Cloud (gratis)

1. Push repo ke GitHub (publik).
2. Buka <https://share.streamlit.io> → **New app**.
3. Pilih repo `hisyamali/konverter`, branch `main`, main file **`streamlit_app.py`**.
4. Pilih subdomain (mis. `hisyamali-konverter.streamlit.app`) → **Deploy**.

Catatan batas Community Cloud:
- CPU maks 2 core, RAM maks ~2.7 GB → app memakai `KONVERTER_MAX_WORKERS=2` di Linux/Cloud (otomatis `1` di Windows lokal agar stabil).
- Batas unggah **50 MB** (`maxUploadSize` di `.streamlit/config.toml`).
- App **tidur setelah 12 jam** tanpa trafik dan **tidak mendukung custom domain**.
- Overlay progress bergantung pada CSS Streamlit (`data-testid`); versi Streamlit dipin `1.66.1` agar tampilan stabil.

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
| `KONVERTER_MAX_WORKERS` | core fisik | Batas atas worker paralel untuk PDF besar. Default = jumlah core fisik (mis. 6); worker aktual tetap dipilih otomatis dari RAM kosong. Paksakan nilai (1–12) untuk mengendalikan beban |
| `KONVERTER_MAX_WORKERS_CAP` | `8` | Batas keras worker saat default otomatis dipakai |
| `KONVERTER_CHUNK_PAGES` | `20` | Halaman per tugas worker |
| `KONVERTER_TASKS_PER_CHILD` | `32` | Jumlah tugas sebelum worker proses di-recycle (menjaga memori worker tetap datar) |
| `KONVERTER_MEM_GB_PER_WORKER` | `0.15` | Perkiraan RAM per worker (GB) untuk memilih jumlah worker |
| `KONVERTER_MEM_RESERVE_GB` | `0.4` | RAM yang selalu disisakan (GB) untuk aplikasi lain |
| `KONVERTER_MEM_FLOOR_GB` | `0.35` | Ambang RAM kosong (GB): bila turun di bawah ini, konversi beralih ke mode hemat (sekuensial) |
| `KONVERTER_POLITE` | `1` | Mode sopan: turunkan prioritas proses worker (`BELOW_NORMAL`) agar aplikasi lain tetap responsif. Set `0` untuk menonaktifkan |
| `LOG_LEVEL` | `INFO` | Level logging: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_MAX_FILES` | `30` | Retensi file log harian (hari) |
| `LOG_CHANNEL` | `stack` | Channel log: `stack` (console+file), `daily`, `single`, `console` |
| `LOG_VIEWER_AUTH` | *(kosong)* | Basic auth untuk `/logs` (format `user:pass`) |

> **Jumlah worker dinamis**: tiap konversi memilih worker = min(core fisik, `KONVERTER_MAX_WORKERS`, RAM kosong/`KONVERTER_MEM_GB_PER_WORKER`). Semakin banyak RAM bebas, semakin banyak worker → semakin cepat. Contoh di mesin 6 core: RAM bebas 0,8 GB → 2 worker; 1,0 GB → 4 worker; ≥1,5 GB → 6 worker.
>
> **Guard memori (mode hemat)**: saat konversi berjalan, bila RAM kosong turun di bawah `KONVERTER_MEM_FLOOR_GB`, aplikasi menghentikan worker paralel dan melanjutkan sisa halaman secara sekuensial (`memori menipis, beralih ke mode hemat`) — mesin tetap responsif dan proses tetap selesai. Perilaku serupa berlaku bila paralel macet >90 detik.

> **Mode sopan** (`KONVERTER_POLITE=1`): proses worker konversi diberi prioritas *Below Normal* sehingga saat mesin berat aplikasi lain (browser/IDE) tetap responsif. Aplikasi **tidak pernah** menutup/menghentikan aplikasi lain — hanya mengatur prioritas worker-nya sendiri.

Pedoman kecepatan (terukur pada i5-11400H 6-core, Windows): ±0,40–0,45 dtk/halaman single-thread. Paralel berskala hampir linier: 4 worker ≈ 2,8× lebih cepat. **File 4679 halaman terukur selesai ±897 detik (±15 menit)** dengan worker dinamis 4–6, puncak memori proses utama ±420 MB, tanpa degradasi. Tutup aplikasi berat (browser/IDE) agar RAM kosong naik → worker lebih banyak → lebih cepat (6 worker ±9–11 menit).

## Struktur Proyek

```
konverter/
├── run.py                  # Entry point aplikasi (mode stabil, tanpa reloader) — dev lokal
├── wsgi.py                 # Entry point produksi (gunicorn)
├── streamlit_app.py        # Varian UI Streamlit (deploy ke Streamlit Cloud)
├── Dockerfile              # Image Docker (host-agnostic)
├── docker-compose.yml      # Menjalankan via Docker Compose
├── .dockerignore
├── .streamlit/
│   └── config.toml         # Konfigurasi Streamlit (maxUploadSize 50 MB, tema)
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
│   ├── routes/
│   │   ├── main.py         # Endpoint /, /preview, /convert, /progress
│   │   └── logs.py         # Log viewer
│   ├── templates/
│   │   └── index.html      # UI satu halaman
│   ├── static/
│   │   ├── js/app.js       # Logika klien (upload, polling progress, download)
│   │   ├── js/logs.js      # Log viewer (auto-refresh)
│   │   ├── css/style.css   # Tema UI Flask
│   │   └── css/streamlit.css  # Reskin UI Streamlit (mirip Flask)
│   └── services/
│       ├── converter.py    # Logika konversi CSV/PDF → Excel, ekstraksi tabel
│       ├── eta.py          # Perhitungan progress/ETA (dipakai Flask & Streamlit)
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

- Ekstraksi tabel memakai `pdfplumber` dengan `PDF_TABLE_SETTINGS` (strategi *lines*) untuk hasil yang konsisten pada laporan mutasi bank.
- **Format teks (tanpa garis)**: untuk PDF mutasi bank yang datanya berupa teks pada kolom tetap (mis. Bank Sumselbabel/BRI), aplikasi otomatis mendeteksi dan memakai parser berbasis posisi kolom (Tanggal, Keterangan, Debet, Kredit, Saldo, No. Referensi) — deteksi dilakukan dari halaman pertama, tanpa perubahan manual.
- Halaman dibaca per chunk oleh beberapa worker (`PARALLEL_CHUNK_PAGES=20`); jumlah worker dinamis = min(core fisik, `KONVERTER_MAX_WORKERS`, RAM kosong). Default `KONVERTER_MAX_WORKERS` = core fisik (6 di mesin uji).
- **Hemat memori**: hasil tiap chunk dialirkan (streaming) sesuai urutan halaman langsung ke baris data, tanpa menahan semua tabel mentah; penggabungan baris dilakukan in-place (`extend`) dan worker membersihkan cache `pdfplumber` tiap halaman (`flush_cache`) sehingga memori worker tetap datar (±0,2 GB) walau file ribuan halaman. Puncak memori proses utama turun drastis (contoh 119 halaman: ~1,1 GB → ~0,1 GB).
- Bila tidak ada kemajuan selama `PARALLEL_DEGRADE_SECONDS=90` detik, proses beralih ke pembacaan sekuensial untuk sisa halaman (menjamin selesai meski mesin sibuk).
- Perkiraan waktu konversi dikalibrasi dengan mengukur kecepatan ekstraksi blok halaman sekuensial pada pratinjau.
- Pada mesin ini kecepatan baca terukur ±0,40–0,45 detik/halaman single-thread; file 4679 halaman ≈ 1900–2100 detik CPU — dengan worker dinamis 4–6 dan mesin cukup bebas selesai **±15 menit** (terukur 897 detik).

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

## Logging & Log Viewer

Aplikasi menyertakan sistem logging bergaya Laravel dengan rotasi harian dan **Log Viewer** bawaan (`/logs`).

### Fitur Logging
- **Format Laravel**: `[Y-m-d H:i:s] channel.LEVEL: message {json_context}` (timezone Asia/Jakarta)
- **Rotasi harian**: `TimedRotatingFileHandler` di `storage/logs/app.log`, retensi `LOG_MAX_FILES` hari (default 30)
- **Channel**: `LOG_CHANNEL=stack` (console + file), `daily`, `single`, `console`
- **Context JSON**: request_id, job_id, preview_id, memory_mb, system_memory_percent, duration_ms, dll.
- **Level**: `LOG_LEVEL` (`DEBUG`–`CRITICAL`, default `INFO`)

### Log Viewer (`/logs`)
- **Akses**: `/logs` (daftar file), `/logs/view/<file>` (isi dengan filter), `/logs/download/<file>`
- **Filter**: level (DEBUG/INFO/WARNING/ERROR/CRITICAL), search text, tail lines (50–5000)
- **Auto-refresh**: polling 2 detik via `/logs/api`
- **Autentikasi**: `LOG_VIEWER_AUTH=user:pass` (basic auth), default tanpa auth untuk dev
- **Highlight**: ERROR (merah), WARNING (kuning), INFO (biru), DEBUG (abu)

### Milestone Konversi (di log)
| Milestone | Context Utama |
|---|---|
| `reading` | input_path, total_pages, memory_mb, system_memory_percent |
| `normalize` | rows, columns, memory_mb, memory_delta_mb |
| `write` | rows, columns |
| `done` | output_path, rows, duration_ms, memory_mb, memory_delta_mb, system_memory_percent |
| `error` | error, error_type, job_id, preview_id |

### Request/Job/Preview Logging
- **Request**: request_id, method, path, status, duration_ms, memory_delta_mb, system_memory_percent
- **Job**: create, update (phase), finish (error/success)
- **Preview**: register, get, delete, sweep, purge_expired

### Error & Peringatan
Semua kegagalan kini tercatat (bukan hanya exception konversi):

| Kejadian | Level | Pesan |
|---|---|---|
| Validasi upload / pratinjau kedaluwarsa | `WARNING` | `Request rejected` + `reason`, `job_id`, `preview_id` |
| Gagal membaca file saat pratinjau (ValueError) | `WARNING` | `Preview rejected` |
| Gagal membaca file saat pratinjau (exception lain) | `ERROR` | `Preview failed` + traceback |
| Konversi gagal | `ERROR` | `Conversion failed` + traceback |
| Error di worker paralel (`BrokenProcessPool`/lain) | `ERROR` | `Conversion error` + `stage` + traceback |
| Degradasi ke mode hemat (RAM/macet) | `WARNING` | `Conversion degraded` + `reason` |
| Upload melebihi batas | `WARNING` | `Upload rejected` + `content_length` |
| HTTP 404 | `WARNING` | `HTTP error` + `status`, `path` |
| HTTP 500 / exception tak tertangani | `ERROR` | `HTTP error` / `Request exception` + traceback |

Traceback ditulis pada **baris log yang sama** (newline di-escape menjadi `\n`) agar tetap bisa difilter level dan di-tail. Filter `ERROR` di Log Viewer akan menampilkan seluruh baris error beserta traceback-nya.

### Contoh Entri Log
```
[2026-10-19 16:52:05] app.INFO: Conversion reading {"input_path":".../SULSELBAR.pdf","total_pages":8,"memory_mb":157.8,"system_memory_percent":96.4}
[2026-10-19 16:52:05] app.INFO: Conversion normalize {"rows":454,"memory_mb":122.3,"memory_delta_mb":-35.5}
[2026-10-19 16:52:05] app.INFO: Conversion write {"rows":454,"columns":[0,1,2,3,4,5]}
[2026-10-19 16:52:05] app.INFO: Conversion done {"output_path":".../SULSELBAR-xxx.xlsx","rows":454,"duration_ms":5518,"memory_mb":122.3,"memory_delta_mb":-35.5,"system_memory_percent":96.3}
[2026-10-19 16:52:05] app.INFO: Job finish {"job_id":"e2e3","error":null,"percent":100,"message":"Selesai"}
```

## Pemecahan Masalah

- **Konversi terasa lambat / progress diam lama**: aplikasi otomatis menurunkan worker atau beralih ke mode sekuensial, jadi proses akan tetap berjalan (hanya lebih pelan). Untuk mempercepat, tutup aplikasi berat (browser/VS Code) saat konversi agar RAM kosong naik. Kompetisi CPU atau pemindaian antivirus (mis. Windows Defender) juga bisa menahan; tambahkan folder proyek ke pengecualian real-time antivirus untuk hasil terbaik.
- **Unggah ditolak**: pastikan jenis file sesuai dengan jenis konversi yang dipilih dan ukurannya di bawah `KONVERTER_MAX_UPLOAD_MB`.