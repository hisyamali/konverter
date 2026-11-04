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
- **Mesin PDF cepat**: PDF berbasis teks dibaca dengan **PyMuPDF** (±3–5 ms/halaman, **±20× lebih cepat** dari `pdfplumber`); PDF tabel bergaris tetap memakai `pdfplumber` (strategi *lines*) karena lebih akurat
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

## Tailwind CSS (UI)

Antarmuka (Flask & Streamlit) memakai **Tailwind CSS v4**. File hasil build (`app/static/css/tailwind.css` dan `app/static/css/tailwind-streamlit.css`) **di-commit** karena Streamlit Community Cloud tidak menjalankan Node saat deploy.

```bash
npm install          # sekali saja
npm run build:css    # build kedua output sekaligus
npm run watch:css:flask      # rebuild otomatis saat mengembangkan UI Flask
npm run watch:css:streamlit  # rebuild otomatis saat mengembangkan UI Streamlit
```

Sumber Tailwind:

- `app/static/css/tailwind.shared.css` — token desain (`@theme`) dan utility brand, dipakai bersama.
- `app/static/css/tailwind.src.css` — input UI Flask (preflight penuh). `@source` mengarah ke `app/templates` dan `app/static/js`.
- `app/static/css/tailwind.streamlit.src.css` — input UI Streamlit (tanpa preflight, utility `!important`). `@source` mengarah ke `streamlit_app.py`.
- `app/static/css/streamlit.css` — jembatan untuk menata DOM milik Streamlit (widget, container `kv-hero`/`kv-converter`, tombol).

Karena utility dipindai dari sumber, class yang dirangkai dinamis di Python/JS harus muncul utuh di file sumber (tidak boleh digabung dari potongan string). CI `.github/workflows/css-build.yml` menolak PR bila CSS hasil build tidak sinkron dengan sumber.

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

Selain antarmuka Flask, repositori menyertakan varian **Streamlit** (`streamlit_app.py`) yang memakai ulang logika konversi yang sama (`app/services/converter.py`). Alur UX-nya mengikuti gaya **iLovePDF**: beranda berisi pilihan alat → halaman alat (unggah → pratinjau → konversi → unduh) → riwayat. Tampilan memakai utility **Tailwind** (`app/static/css/tailwind-streamlit.css`) untuk markup custom, ditambah `app/static/css/streamlit.css` sebagai jembatan untuk widget bawaan Streamlit.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Buka <http://localhost:8501>. Jalankan dari **root repo** agar `.streamlit/config.toml` dan `.streamlit/secrets.toml` terbaca.

### Tamu vs pengguna login

| | Tanpa login (tamu) | Setelah login |
| --- | --- | --- |
| Konversi | Ya | Ya |
| Batas unggahan | **10 MB** (`KONVERTER_GUEST_MAX_UPLOAD_MB`) | **50 MB** (`KONVERTER_USER_MAX_UPLOAD_MB`) |
| File sumber | Diproses sementara di server, **tidak** disimpan ke database/storage; dihapus saat sesi berakhir atau file diganti | Disimpan di bucket privat `sources` |
| Hasil `.xlsx` | Hanya diunduh (byte di memori sesi) | Disimpan di bucket privat `results` + bisa diunduh ulang |
| Riwayat | Tidak ada | Ada (unduh ulang / hapus) |

Batas ukuran dicek di aplikasi **sebelum** konversi; batas keras server tetap `maxUploadSize = 50` di `.streamlit/config.toml`. Tanpa konfigurasi backend akun (Supabase/Appwrite), aplikasi otomatis berjalan **mode tamu saja** (menu Masuk/Riwayat disembunyikan).

### Deploy ke Streamlit Community Cloud (gratis)

1. Push repo ke GitHub (publik).
2. Buka <https://share.streamlit.io> → **New app**.
3. Pilih repo `hisyamali/konverter`, branch `main`, main file **`streamlit_app.py`**.
4. (Opsional, untuk login + riwayat) **Advanced settings → Secrets**, tempel blok `[supabase]` atau `[appwrite]` (lihat bagian berikut).
5. Pilih subdomain (mis. `all-convert.streamlit.app`) → **Deploy**.

Catatan batas Community Cloud:
- CPU maks 2 core, RAM maks ~2.7 GB → app memakai `KONVERTER_MAX_WORKERS=2` di Linux/Cloud (otomatis `1` di Windows lokal agar stabil).
- Batas unggah **50 MB** (`maxUploadSize` di `.streamlit/config.toml`); tamu dibatasi 10 MB di aplikasi.
- App **tidur setelah 12 jam** tanpa trafik dan **tidak mendukung custom domain**.
- Overlay progress bergantung pada CSS Streamlit (`data-testid`); versi Streamlit dipin `1.66.1` agar tampilan stabil.

### Akun, riwayat & penyimpanan (Supabase **atau** Appwrite)

Login, riwayat, dan penyimpanan hasil bisa memakai **salah satu** backend: **Supabase** (Auth + Postgres + Storage) atau **Appwrite** (Auth + TablesDB + Storage). Isi salah satu saja; tanpa konfigurasi, aplikasi tetap jalan dalam **mode tamu**.

Pilih backend aktif di `.streamlit/secrets.toml`:

```toml
[app]
backend = "auto"   # "auto" (default) | "supabase" | "appwrite"
```

`auto` memilih Supabase bila kredensialnya ada, lalu Appwrite. Bila `backend` diisi eksplisit tetapi backend itu belum dikonfigurasi, aplikasi berjalan sebagai **mode tamu** (tidak ada perpindahan otomatis antar penyedia, agar akun tidak "hilang" tanpa sepengetahuan pengguna). Bisa juga lewat env var `KONVERTER_BACKEND`. Akun Supabase dan Appwrite **terpisah** — pengguna Supabase perlu mendaftar ulang bila pindah ke Appwrite.

#### Opsi A — Supabase

**1. Buat proyek Supabase** (gratis, tanpa kartu kredit) di <https://supabase.com>, lalu:

- **SQL Editor → New query**, tempel isi `supabase/schema.sql`, klik **Run**. Ini membuat tabel `public.jobs`, mengaktifkan **Row Level Security**, serta bucket privat `sources` dan `results`.
- **Project Settings → API**, salin **Project URL** dan **anon public key**.

**2. Isi kredensial**

Lokal — salin `.streamlit/secrets.toml.example` menjadi `.streamlit/secrets.toml` (sudah di-`.gitignore`) lalu isi:

```toml
[supabase]
url = "https://<project-ref>.supabase.co"
anon_key = "<anon-public-key>"
```

Streamlit Community Cloud — **App settings → Secrets**, tempel blok yang sama. Alternatif lain: set env var `SUPABASE_URL` dan `SUPABASE_ANON_KEY`.

> **Keamanan**: hanya **anon key** yang dipakai (aman di klien karena RLS membatasi akses per pengguna). **Jangan** menaruh `service_role` key di aplikasi ini. Bucket `sources`/`results` privat — unduhan memakai *signed URL* berumur 1 jam.

**3. (Opsional) Konfirmasi email**: di **Authentication → Providers → Email**, matikan "Confirm email" bila ingin pengguna langsung aktif saat mendaftar, atau biarkan aktif agar Streamlit menampilkan pesan "cek email".

**Skema data**: tabel `jobs` menyimpan `user_id`, `conversion_type`, `original_name`, `source_path`, `result_path`, ukuran file, `status` (`processing`/`completed`/`failed`), `error`, `page_count`, `row_count`, dan `duration_ms`. Path objek storage mengikuti pola `<user_id>/<job_id>/<nama-file>` sehingga kebijakan RLS hanya mengizinkan pemilik folder masing-masing.

#### Opsi B — Appwrite

**1. Buat proyek Appwrite** (gratis, tanpa kartu kredit) di <https://cloud.appwrite.io>, salin **API endpoint** dan **Project ID** dari *Project Settings*.

**2. Siapkan database/table/bucket otomatis** dengan skrip (butuh API key sementara ber-scope `databases` + `storage`):

```bash
# PowerShell
$env:APPWRITE_API_KEY = "standard_xxxx"
python scripts/appwrite_setup.py --endpoint https://sgp.cloud.appwrite.io/v1 --project-id <PROJECT_ID>
```

```bash
# Linux/macOS
export APPWRITE_API_KEY="standard_xxxx"
python scripts/appwrite_setup.py --endpoint https://sgp.cloud.appwrite.io/v1 --project-id <PROJECT_ID>
```

Skrip ini **idempotent** (aman diulang): membuat database `konverter`, table `jobs` (14 kolom + index `user_created`), dan bucket privat `sources`/`results`, lalu mencetak blok TOML siap tempel. **Hapus API key setelah setup** — jangan simpan di Secrets.

**3. Isi kredensial** (atau tempel hasil cetak skrip):

```toml
[appwrite]
endpoint = "https://sgp.cloud.appwrite.io/v1"
project_id = "<project-id>"
database_id = "konverter"
jobs_table_id = "jobs"
sources_bucket_id = "sources"
results_bucket_id = "results"

[app]
backend = "appwrite"
```

Alternatif Streamlit Cloud: isi **App settings → Secrets** dengan blok di atas. Alternatif env var: `APPWRITE_ENDPOINT`, `APPWRITE_PROJECT_ID`.

**Catatan Appwrite**: file sumber/hasil disimpan tanpa signed URL — tombol unduh di riwayat mengambil byte lewat server lalu memakai tombol unduh Streamlit ("Siapkan unduhan"). Izin baca/ubah/hapus diatur **per baris/file** (`Role.user`), dan table/bucket hanya mengizinkan `Create` untuk pengguna login, sehingga data antar pengguna tetap terisolasi.

> Fitur akun, riwayat, dan batas ukuran bertingkat hanya ada di **varian Streamlit**. UI Flask tanpa login internal — navbar-nya menampilkan tombol **Masuk** yang mengarah ke aplikasi Streamlit bila `KONVERTER_STREAMLIT_URL` diisi (batas tunggal `KONVERTER_MAX_UPLOAD_MB`).

### Dua deployment (production + staging) dengan backend berbeda

Konfigurasi yang didukung: dua app Streamlit terpisah, masing-masing dengan **Secrets sendiri**, sehingga production dan staging bisa memakai backend akun yang berbeda sekaligus (Supabase untuk production, Appwrite untuk staging).

**App production** — branch `main`, URL `https://all-convert.streamlit.app` → **Supabase**
- Secrets (Settings → Secrets):
  ```toml
  [supabase]
  url = "https://<project-ref>.supabase.co"
  anon_key = "<anon-public-key>"

  [app]
  backend = "supabase"
  ```
- Prasyarat: jalankan `supabase/schema.sql` di Supabase (tabel `jobs` + RLS + bucket `sources`/`results`).

**App staging** — branch `staging`, URL `https://stagging-konverter.streamlit.app` → **Appwrite**
- Siapkan Appwrite dulu:
  ```powershell
  $env:APPWRITE_API_KEY = "standard_xxxx"
  python scripts/appwrite_setup.py --endpoint https://sgp.cloud.appwrite.io/v1 --project-id <PROJECT_ID>
  ```
  (hapus API key setelah setup; skrip idempotent)
- Secrets (Settings → Secrets), **tanpa** blok `[supabase]`:
  ```toml
  [appwrite]
  endpoint = "https://sgp.cloud.appwrite.io/v1"
  project_id = "<project-id>"
  database_id = "konverter"
  jobs_table_id = "jobs"
  sources_bucket_id = "sources"
  results_bucket_id = "results"

  [app]
  backend = "appwrite"
  ```

Catatan:
- Secrets Streamlit Cloud bersifat **per app**, bukan per repo — jadi keduanya tidak saling ganggu.
- Set `backend` eksplisit (bukan `auto`) agar pilihan tidak bergeser kalau kredensial lain ikut tersimpan.
- Akun, tabel `jobs`, dan bucket **terpisah** antar backend — akun staging tidak bisa login di production (ini yang diinginkan untuk staging).
- Satu app hanya memakai **satu** backend aktif; tidak ada dual-write. Yang berjalan bersamaan adalah dua deployment dengan Secrets masing-masing.
- Untuk pengujian lokal, file `.streamlit/secrets.toml` hanya memuat satu backend; ganti `[app] backend` untuk berpindah.

### Menjaga app tetap bangun (keep-alive)

Streamlit menghitung aktivitas dari **sesi browser** (JavaScript + WebSocket), bukan HTTP GET. Ping biasa (UptimeRobot, `curl`) tetap menerima `200` walau app sedang tidur, jadi tidak berguna. Karena itu repositori menyertakan workflow `.github/workflows/keep-warm.yml` yang membuka app memakai Chromium headless (Playwright), mengklik tombol **"Yes, get this app back up!"** bila app tidur, lalu menahan sesi ±60 detik.

1. Set URL app di **Settings → Secrets and variables → Actions → Variables**:
   - Name: `STREAMLIT_APP_URL`
   - Value: `https://all-convert.streamlit.app`
2. Jalankan manual sekali dari tab **Actions → Keep Streamlit Awake → Run workflow**.
3. Setelah itu workflow berjalan otomatis tiap 3 jam.

Untuk **staging** (app Streamlit yang di-deploy dari branch `staging`), gunakan workflow `.github/workflows/keep-warm-staging.yml`:
1. Set variabel terpisah di **Settings → Secrets and variables → Actions → Variables**:
   - Name: `STREAMLIT_STAGING_APP_URL`
   - Value: `https://<subdomain>-staging.streamlit.app`
2. Jalankan manual dari tab **Actions → Keep Streamlit Staging Awake → Run workflow**.
3. Workflow ini juga berjalan otomatis tiap 3 jam **setelah file-nya ada di branch default (`main`)**; dari branch `staging`, jadwal belum aktif sehingga perlu dijalankan manual.

Catatan:
- Workflow harus ada di branch default (`main`) agar jadwalnya aktif.
- Repo publik memakai menit GitHub Actions gratis; repo privat memakai kuota 2.000 menit/bulan (≈960 menit di sini).
- Ini bukan fitur resmi Streamlit dan bisa berhenti berfungsi bila kebijakan fair-use berubah. Pengunjung pertama setelah app tidur tetap melihat cold start.
- GitHub menonaktifkan scheduled workflow bila repo tidak ada aktivitas selama 60 hari; jalankan manual untuk mengaktifkan lagi.
- Debug: screenshot kegagalan diunggah sebagai artifact `keep-warm-failure`.

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
| `KONVERTER_GUEST_MAX_UPLOAD_MB` | `10` | Batas unggahan untuk **tamu** (tanpa login) di varian Streamlit |
| `KONVERTER_USER_MAX_UPLOAD_MB` | `50` | Batas unggahan untuk pengguna yang **login** di varian Streamlit |
| `KONVERTER_STREAMLIT_URL` | *(kosong)* | URL aplikasi Streamlit untuk tombol **Masuk** di navbar Flask. Kosong = tombol disembunyikan |
| `SUPABASE_URL` | *(kosong)* | Project URL Supabase (alternatif blok `[supabase]` di `secrets.toml`). Kosong = mode tamu saja |
| `SUPABASE_ANON_KEY` | *(kosong)* | anon public key Supabase. Jangan pakai `service_role` |
| `KONVERTER_BACKEND` | `auto` | Backend akun: `auto`, `supabase`, atau `appwrite` (alternatif `[app] backend`) |
| `APPWRITE_ENDPOINT` | `https://cloud.appwrite.io/v1` | API endpoint Appwrite |
| `APPWRITE_PROJECT_ID` | *(kosong)* | Project ID Appwrite (alternatif blok `[appwrite]` di `secrets.toml`) |
| `KONVERTER_PDF_WORDS` | `1` | Pakai PyMuPDF untuk ekstraksi **teks** PDF (jauh lebih cepat, ±20×). Set `0` untuk kembali sepenuhnya ke `pdfplumber` |
| `KONVERTER_MIN_PARALLEL_PAGES` | `60` | Ambang halaman minimal PDF bergaris sebelum memakai worker paralel; di bawahnya sekuensial lebih cepat karena biaya spawn proses |
| `KONVERTER_TEXT_MIN_PARALLEL_PAGES` | `1000` | Ambang halaman minimal jalur teks sebelum memakai worker paralel (PyMuPDF sangat cepat sehingga dokumen lebih kecil sebaiknya sekuensial). Otomatis mengikuti ambang bergaris bila PyMuPDF tidak tersedia |
| `KONVERTER_MAX_WORKERS` | core fisik | Batas atas worker paralel untuk PDF besar. Default = jumlah core fisik (mis. 6); worker aktual tetap dipilih otomatis dari RAM kosong. Paksakan nilai (1–12) untuk mengendalikan beban |
| `KONVERTER_MAX_WORKERS_CAP` | `8` | Batas keras worker saat default otomatis dipakai |
| `KONVERTER_MAX_CONCURRENT` | `4` | Jumlah maksimum konversi yang berjalan bersamaan; permintaan berikutnya menunggu antrean |
| `KONVERTER_QUEUE_WAIT_SECONDS` | `900` | Batas waktu menunggu slot konversi sebelum server mengembalikan HTTP 429 |
| `KONVERTER_CHUNK_PAGES` | `10` | Halaman per tugas worker. Lebih kecil = progress & ETA lebih halus dan beban memori buffer lebih rendah; terukur tidak memperlambat jalur teks (10 dan 5 halaman sama cepat atau lebih cepat dari 20) |
| `KONVERTER_TASKS_PER_CHILD` | `32` | Jumlah tugas sebelum worker proses di-recycle (menjaga memori worker tetap datar) |
| `KONVERTER_PROGRESS_PAGE_STEP` | `5` | Langkah halaman minimal sebelum pesan progress "Halaman X dari Y" dikirim ke UI (dipakai untuk progress & ETA). Kecilkan untuk update lebih rapat, besarkan untuk lebih hemat |
| `KONVERTER_MEM_GB_PER_WORKER` | `0.15` | Perkiraan RAM per worker (GB) untuk memilih jumlah worker |
| `KONVERTER_MEM_RESERVE_GB` | `0.4` | RAM yang selalu disisakan (GB) untuk aplikasi lain |
| `KONVERTER_MEM_FLOOR_GB` | `0.35` | Ambang RAM kosong (GB): bila turun di bawah ini, konversi beralih ke mode hemat (sekuensial) |
| `KONVERTER_POLITE` | `1` | Mode sopan: turunkan prioritas proses worker (`BELOW_NORMAL`) agar aplikasi lain tetap responsif. Set `0` untuk menonaktifkan |
| `LOG_LEVEL` | `INFO` | Level logging: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_MAX_FILES` | `30` | Retensi file log harian (hari) |
| `LOG_CHANNEL` | `stack` | Channel log: `stack` (console+file), `daily`, `single`, `console` |
| `LOG_VIEWER_AUTH` | *(kosong)* | Basic auth untuk `/logs` (format `user:pass`) |

> **Jumlah worker dinamis**: tiap konversi memilih worker = min(core fisik, `KONVERTER_MAX_WORKERS`, RAM kosong/`KONVERTER_MEM_GB_PER_WORKER`), lalu membagi anggaran worker berdasarkan jumlah konversi aktif. Jika RAM bebas di bawah kebutuhan minimum untuk proses anak, pembacaan PDF berjalan sekuensial untuk menghindari swap.
>
> **Antrean**: maksimum `KONVERTER_MAX_CONCURRENT` konversi aktif per proses aplikasi. Request selanjutnya menunggu slot dan progress menampilkan posisi antrean. Untuk konfigurasi Gunicorn multi-process, limiter ini berlaku per proses; gunakan satu worker Gunicorn dengan beberapa thread agar batasnya global pada instance tunggal.
>
> **Guard memori (mode hemat)**: saat konversi berjalan, bila RAM kosong turun di bawah `KONVERTER_MEM_FLOOR_GB`, aplikasi menghentikan worker paralel dan melanjutkan sisa halaman secara sekuensial (`memori menipis, beralih ke mode hemat`) — mesin tetap responsif dan proses tetap selesai. Perilaku serupa berlaku bila paralel macet >90 detik.

> **Mode sopan** (`KONVERTER_POLITE=1`): proses worker konversi diberi prioritas *Below Normal* sehingga saat mesin berat aplikasi lain (browser/IDE) tetap responsif. Aplikasi **tidak pernah** menutup/menghentikan aplikasi lain — hanya mengatur prioritas worker-nya sendiri.

Pedoman kecepatan (terukur pada i5-11400H 6-core, Windows):

- **PDF teks (tanpa garis)**: ±3–5 ms/halaman dengan PyMuPDF. File 30 halaman selesai **±0,35 detik** (sebelumnya ±3,1 detik / ±103 ms per halaman dengan `pdfplumber`) — **±20× lebih cepat** dengan output baris identik (720/720). Karena itu jalur teks baru memakai worker paralel bila halaman > `KONVERTER_TEXT_MIN_PARALLEL_PAGES` (default 400); di bawah itu sekuensial lebih cepat (overhead spawn proses lebih besar dari waktunya).
- **PDF tabel bergaris**: tetap `pdfplumber` (±110–170 ms/halaman single-thread). Paralel berskala hampir linier: 4 worker ≈ 2,8× lebih cepat. **File 4679 halaman terukur selesai ±897 detik (±15 menit)** dengan worker dinamis 4–6, puncak memori proses utama ±420 MB. Tutup aplikasi berat (browser/IDE) agar RAM kosong naik → worker lebih banyak → lebih cepat (6 worker ±9–11 menit).

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
│   ├── config.toml         # Konfigurasi Streamlit (maxUploadSize 50 MB, tema)
│   └── secrets.toml.example # Template secrets (Supabase/Appwrite + [app] backend) — salin ke secrets.toml
├── supabase/
│   └── schema.sql          # Skema Postgres: tabel jobs + RLS + bucket sources/results
├── scripts/
│   └── appwrite_setup.py   # Setup otomatis database/table/bucket Appwrite (idempotent)
├── requirements.txt
├── .devcontainer/
│   ├── devcontainer.json       # Konfigurasi GitHub Codespaces (port publik)
│   └── local/
│       └── devcontainer.json   # Konfigurasi VS Code lokal (Python native)
├── .github/
│   ├── scripts/
│   │   ├── keep-warm.mjs    # Kunjungan browser via Playwright (keep-alive)
│   │   ├── package.json
│   │   └── package-lock.json
│   └── workflows/
│       ├── docker-build.yml  # GitHub Actions: build & push image ke GHCR
│       └── keep-warm.yml     # GitHub Actions: cegah app Streamlit tidur
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
│   │   ├── css/tailwind.src.css        # Sumber Tailwind UI Flask
│   │   ├── css/tailwind.streamlit.src.css  # Sumber Tailwind UI Streamlit
│   │   ├── css/tailwind.shared.css      # Token desain bersama
│   │   ├── css/tailwind.css             # Hasil build (di-commit)
│   │   ├── css/tailwind-streamlit.css   # Hasil build (di-commit)
│   │   └── css/streamlit.css            # Jembatan CSS untuk widget Streamlit
│   └── services/
│       ├── converter.py    # Logika konversi CSV/PDF → Excel, ekstraksi tabel
│       ├── eta.py          # Perhitungan progress/ETA (dipakai Flask & Streamlit)
│       ├── concurrency.py  # Pembatas/antrean konversi bersamaan
│       ├── store.py        # Pemilih backend akun (Supabase/Appwrite) + fallback mode tamu
│       ├── supabase_store.py # Integrasi Supabase (auth, tabel jobs, storage) — Streamlit
│       ├── appwrite_store.py # Integrasi Appwrite (auth, TablesDB, storage) — Streamlit
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

- Ekstraksi tabel bergaris memakai `pdfplumber` dengan `PDF_TABLE_SETTINGS` (strategi *lines*) untuk hasil yang konsisten pada laporan mutasi bank. (`find_tables` PyMuPDF diukur **lebih lambat** 2–3× dan menambah baris kosong, jadi tidak dipakai untuk tabel.)
- **Format teks (tanpa garis)**: untuk PDF mutasi bank yang datanya berupa teks pada kolom tetap (mis. Bank Sumselbabel/BRI), aplikasi otomatis mendeteksi dan memakai parser berbasis posisi kolom (Tanggal, Keterangan, Debet, Kredit, Saldo, No. Referensi) — deteksi dilakukan dari halaman pertama, tanpa perubahan manual. Parser ini membaca kata via **PyMuPDF (`get_text("words")`)** bila tersedia: ±3–5 ms/halaman (**±20×** lebih cepat dari `pdfplumber`), output baris identik (diuji 720/720). Nonaktifkan dengan `KONVERTER_PDF_WORDS=0`.
- Halaman dibaca per chunk oleh beberapa worker (`PARALLEL_CHUNK_PAGES=20`); jumlah worker dinamis = min(core fisik, `KONVERTER_MAX_WORKERS`, RAM kosong). Default `KONVERTER_MAX_WORKERS` = core fisik (6 di mesin uji). Jalur teks memakai ambang terpisah `KONVERTER_TEXT_MIN_PARALLEL_PAGES` (default 400).
- **Hemat memori**: hasil tiap chunk dialirkan (streaming) sesuai urutan halaman langsung ke baris data, tanpa menahan semua tabel mentah; penggabungan baris dilakukan in-place (`extend`) dan worker membersihkan cache `pdfplumber` tiap halaman (`flush_cache`) sehingga memori worker tetap datar (±0,2 GB) walau file ribuan halaman. Puncak memori proses utama turun drastis (contoh 119 halaman: ~1,1 GB → ~0,1 GB).
- Bila tidak ada kemajuan selama `PARALLEL_DEGRADE_SECONDS=90` detik, proses beralih ke pembacaan sekuensial untuk sisa halaman (menjamin selesai meski mesin sibuk).
- Perkiraan waktu konversi dikalibrasi dengan mengukur kecepatan ekstraksi blok halaman sekuensial pada pratinjau.
- Pada mesin ini kecepatan baca tabel bergaris terukur ±110–170 ms/halaman single-thread; jalur teks ±3–5 ms/halaman. File tabel 4679 halaman ≈ 1900–2100 detik CPU — dengan worker dinamis 4–6 dan mesin cukup bebas selesai **±15 menit** (terukur 897 detik).

> **Lisensi PyMuPDF**: `pymupdf` didistribusikan di bawah **AGPL-3.0** (atau lisensi komersial Artifex). Proyek ini memakainya sebagai dependensi open-source. Jika kamu men-deploy modifikasi aplikasi ini sebagai layanan jaringan, ikuti ketentuan AGPL (sertakan sumber) atau beli lisensi komersial Artifex. Ingin menghindari AGPL? Set `KONVERTER_PDF_WORDS=0` dan hapus `pymupdf` dari `requirements.txt` — aplikasi otomatis kembali ke `pdfplumber` (lebih lambat pada jalur teks).

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
