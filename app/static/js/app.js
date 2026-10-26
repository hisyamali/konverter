// Menghubungkan elemen form, pratinjau, dan modal progress dari HTML ke logika JavaScript.
const typeSelect = document.getElementById("conversion_type");
const fileInput = document.getElementById("file");
const submitButton = document.getElementById("submit-button");
const converterCard = document.querySelector(".converter-card");
const previewStatus = document.getElementById("preview-status");
const previewPanel = document.getElementById("preview-panel");
const previewFilename = document.getElementById("preview-filename");
const previewFacts = document.getElementById("preview-facts");
const previewEstimate = document.getElementById("preview-estimate");
const previewTable = document.querySelector(".preview-table");
const previewTableScroll = document.querySelector(".preview-table-scroll");
const previewHead = document.getElementById("preview-head");
const previewBody = document.getElementById("preview-body");
const previewCaption = document.getElementById("preview-caption");
const stepUpload = document.getElementById("step-upload");
const stepConvert = document.getElementById("step-convert");

const overlay = document.getElementById("loading-overlay");
const bar = document.getElementById("progress-bar");
const track = document.getElementById("progress-track");
const percentLabel = document.getElementById("progress-percent");
const messageLabel = document.getElementById("loading-message");
const etaLabel = document.getElementById("loading-eta");
const etaRunningLabel = document.getElementById("loading-eta-running");
const errorLabel = document.getElementById("loading-error");
const closeButton = document.getElementById("loading-close");

const FADE_OUT_DELAY = 700;
const FAKE_PERCENT_CEILING = 10;
const FAKE_PROGRESS_INTERVAL_MS = 900;

// Menyimpan state sementara selama upload, pratinjau, dan konversi berjalan.
let currentPercent = 0;
let fakeTimer = null;
let hideTimer = null;
let previewXhr = null;
let currentPreviewId = null;
let lastElapsed = null;

function openOverlay() {
    // Menampilkan modal progress dan mengatur ulang status proses konversi.
    clearTimeout(hideTimer);
    currentPercent = 0;
    lastElapsed = null;
    errorLabel.hidden = true;
    errorLabel.textContent = "";
    etaLabel.hidden = true;
    etaLabel.textContent = "";
    etaRunningLabel.hidden = true;
    etaRunningLabel.textContent = "";
    closeButton.hidden = true;
    messageLabel.textContent = "Menyiapkan file";
    renderProgress(0);
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("is-visible"));
    startFakeProgress();
    setStepActive("convert");
}

function closeOverlay() {
    // Menutup modal progress dengan transisi fade-out.
    stopFakeProgress();
    overlay.classList.remove("is-visible");
    hideTimer = setTimeout(() => {
        overlay.hidden = true;
    }, FADE_OUT_DELAY);
}

function renderProgress(percent) {
    // Menggambar progress bar tanpa pernah menurunkan persentase yang sudah tampil.
    const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
    currentPercent = Math.max(currentPercent, safePercent);
    bar.style.width = `${currentPercent}%`;
    percentLabel.textContent = currentPercent;
    track.setAttribute("aria-valuenow", currentPercent);
}

function setStatus(percent, message, etaSeconds, elapsedSeconds) {
    // Memperbarui pesan proses, estimasi sisa waktu, dan durasi berjalan.
    renderProgress(percent);

    if (message) {
        messageLabel.textContent = message;
    }

    const eta = Number.isFinite(Number(etaSeconds)) ? Number(etaSeconds) : null;
    const elapsed = Number.isFinite(Number(elapsedSeconds)) ? Number(elapsedSeconds) : null;

    if (percent >= 100 && elapsed !== null) {
        etaLabel.textContent = `Selesai dalam ${formatInterval(elapsed)}`;
        etaLabel.hidden = false;
        etaRunningLabel.hidden = true;
        return;
    }

    if (eta !== null && eta >= 0) {
        etaLabel.textContent = `± ${formatInterval(eta)} tersisa`;
        etaLabel.hidden = false;
    } else {
        etaLabel.hidden = true;
    }

    if (elapsed !== null) {
        etaRunningLabel.textContent = `berjalan ${formatInterval(elapsed)}`;
        etaRunningLabel.hidden = false;
    } else {
        etaRunningLabel.hidden = true;
    }
}

function formatInterval(seconds) {
    // Mengubah detik menjadi teks Indonesia yang ringkas untuk ETA/durasi.
    const totalSeconds = Math.max(1, Math.round(seconds));

    if (totalSeconds < 60) {
        return `${totalSeconds} detik`;
    }

    const minutes = Math.floor(totalSeconds / 60);
    const restSeconds = (totalSeconds % 60).toString().padStart(2, "0");

    if (minutes < 60) {
        return `${minutes} menit ${restSeconds} detik`;
    }

    const hours = Math.floor(minutes / 60);
    const restMinutes = (minutes % 60).toString().padStart(2, "0");
    return `${hours} jam ${restMinutes} menit ${restSeconds} detik`;
}

function startFakeProgress() {
    // Memberi umpan balik awal saat server belum mengirim progress sebenarnya.
    stopFakeProgress();
    fakeTimer = setInterval(() => {
        if (currentPercent < FAKE_PERCENT_CEILING) {
            renderProgress(currentPercent + 1);
        }
    }, FAKE_PROGRESS_INTERVAL_MS);
}

function stopFakeProgress() {
    // Menghentikan progress semu agar tidak bentrok dengan progress server.
    if (fakeTimer !== null) {
        clearInterval(fakeTimer);
        fakeTimer = null;
    }
}

function showError(message) {
    // Menampilkan error konversi dan membuka tombol tutup modal.
    stopFakeProgress();
    etaLabel.hidden = true;
    etaLabel.textContent = "";
    etaRunningLabel.hidden = true;
    etaRunningLabel.textContent = "";
    messageLabel.textContent = "Konversi gagal";
    errorLabel.textContent = message;
    errorLabel.hidden = false;
    closeButton.hidden = false;
    submitButton.disabled = false;
    bar.classList.add("is-error");
}

function buildJobId() {
    // Membuat ID unik untuk menghubungkan request konversi dengan polling progress.
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }

    return `job-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function pollProgress(jobId) {
    // Meminta status job berkala dari server selama konversi berlangsung.
    const timer = setInterval(async () => {
        try {
            const response = await fetch(`/progress?job_id=${encodeURIComponent(jobId)}`, {
                cache: "no-store"
            });
            const job = await response.json();

            lastElapsed = job.elapsed_seconds;
            setStatus(job.percent, job.message, job.eta_seconds, job.elapsed_seconds);

            if (job.percent > 0) {
                stopFakeProgress();
            }

            if (job.error) {
                showError(job.error);
                clearInterval(timer);
            }
        } catch (error) {
            setStatus(currentPercent, "Memproses file");
        }
    }, 400);

    return timer;
}

function downloadFile(blob, fileName) {
    // Memicu download hasil Excel dari blob response tanpa reload halaman.
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), FADE_OUT_DELAY);
}

function readFileName(request) {
    // Mengambil nama file hasil dari header Content-Disposition server.
    const disposition = request.getResponseHeader("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    return match ? match[1] : "hasil-konversi.xlsx";
}

function isSpreadsheet(response) {
    // Memastikan response sukses benar-benar file Excel atau stream download.
    const contentType = response.getResponseHeader("Content-Type") || "";
    return contentType.includes("spreadsheet") || contentType.includes("octet-stream");
}

function extractErrorMessage(text, contentType) {
    // Mengubah response error server menjadi pesan yang aman ditampilkan ke user.
    if (contentType.includes("json")) {
        try {
            const payload = JSON.parse(text);

            if (payload && payload.error) {
                return payload.error;
            }
        } catch (error) {
            return "Format file tidak sesuai. Silakan cek lagi.";
        }
    }

    if (text.trim() === "") {
        return "Terjadi kesalahan saat memproses file.";
    }

    return "Format file tidak sesuai. Silakan cek lagi.";
}

function setStepActive(step) {
    // Menandai langkah aktif pada indikator unggah/pratinjau dan konversi.
    stepUpload.classList.toggle("is-active", step === "upload");
    stepConvert.classList.toggle("is-active", step === "convert");
}

function formatBytes(bytes) {
    // Mengubah ukuran byte menjadi B, KB, atau MB untuk metadata pratinjau.
    if (bytes < 1024) {
        return `${bytes} B`;
    }

    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
    }

    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function setPreviewStatus(message, isError) {
    // Menampilkan status upload/pratinjau, termasuk gaya error bila perlu.
    previewStatus.textContent = message;
    previewStatus.classList.toggle("is-error", Boolean(isError));
    previewStatus.hidden = false;
}

function clearPreview() {
    // Mengosongkan pratinjau lama saat file atau jenis konversi berubah.
    currentPreviewId = null;
    previewPanel.hidden = true;
    previewTable.querySelector("colgroup")?.remove();
    previewTable.style.minWidth = "";
    previewHead.replaceChildren();
    previewBody.replaceChildren();
    previewCaption.textContent = "";
    previewEstimate.hidden = true;
    previewEstimate.textContent = "";
    submitButton.disabled = true;
    setStepActive("upload");
}

function resetInput() {
    // Mengosongkan input file dan seluruh pratinjau setelah konversi berhasil.
    clearPreview();
    fileInput.value = "";
    previewStatus.hidden = true;
    previewStatus.textContent = "";
    previewStatus.classList.remove("is-error");
    console.log(previewPanel);
}

function selectedType() {
    // Membaca jenis konversi yang sedang dipilih user.
    return typeSelect.value;
}

function matchesSelectedType(filename) {
    // Memvalidasi ekstensi file agar sesuai dengan pilihan PDF/CSV.
    const extension = filename.split(".").pop().toLowerCase();
    return selectedType() === "csv-to-excel" ? extension === "csv" : extension === "pdf";
}

function sendPreview() {
    // Mengunggah file untuk validasi dan sampel pratinjau sebelum konversi penuh.
    const file = fileInput.files[0];

    if (!file) {
        return;
    }

    if (!matchesSelectedType(file.name)) {
        setPreviewStatus("Format file tidak sesuai dengan jenis konversi yang dipilih.", true);
        return;
    }

    clearPreview();
    submitButton.disabled = true;
    setPreviewStatus("Mengunggah & memeriksa file…");

    const data = new FormData();
    data.append("conversion_type", selectedType());
    data.append("file", file);

    previewXhr = new XMLHttpRequest();
    previewXhr.open("POST", "/preview");
    previewXhr.responseType = "json";

    previewXhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
            const ratio = Math.round((event.loaded / event.total) * 100);
            setPreviewStatus(`Mengunggah & memeriksa file… ${ratio}%`);
        }
    });

    previewXhr.addEventListener("load", () => {
        const status = previewXhr.status;
        const body = previewXhr.response;
        previewXhr = null;

        if (status === 200 && body) {
            renderPreview(body);
            return;
        }

        const message = body && body.error ? body.error : "Gagal memeriksa file. Coba lagi.";
        setPreviewStatus(message, true);
    });

    previewXhr.addEventListener("error", () => {
        previewXhr = null;
        setPreviewStatus("Gagal terhubung ke server. Periksa koneksi lalu coba lagi.", true);
    });

    previewXhr.send(data);
}

function renderPreview(payload) {
    // Menggambar metadata dan tabel sampel hasil pratinjau dari server.
    currentPreviewId = payload.preview_id;

    if (!payload.filename) {
        currentPreviewId = null;
        setPreviewStatus("Nama file tidak valid. Pilih file lain.", true);
        return;
    }

    previewFilename.textContent = payload.filename;
    previewFilename.hidden = false;

    const facts = [
        formatBytes(payload.size_bytes),
        payload.conversion_type === "csv-to-excel"
            ? `${payload.row_count} baris`
            : `${payload.page_count} halaman`
    ];
    previewFacts.textContent = facts.join(" · ");

    if (payload.conversion_type === "pdf-to-excel" && Number.isFinite(Number(payload.estimated_seconds))) {
        const seconds = Number(payload.estimated_seconds);

        if (seconds > 0) {
            previewEstimate.textContent = `Perkiraan waktu konversi: ± ${formatInterval(seconds)}`;
            previewEstimate.hidden = false;
        } else {
            previewEstimate.hidden = true;
        }
    } else {
        previewEstimate.hidden = true;
    }

    previewPanel.hidden = false;

    if (payload.conversion_type === "csv-to-excel") {
        renderCsvPreview(payload);
    } else {
        renderPdfPreview(payload);
    }

    submitButton.disabled = false;
    previewStatus.hidden = true;
    setStepActive("upload");
}

function renderCsvPreview(payload) {
    // Membuat tabel pratinjau CSV dari header asli dan sample_rows.
    renderPreviewTable(payload.headers, payload.sample_rows);

    const total = payload.row_count;
    previewCaption.textContent = `Menampilkan ${payload.sample_rows.length} dari ${total} baris.`;
}

function renderPdfPreview(payload) {
    // Membuat tabel pratinjau PDF dengan header dari isi file jika baris pertama tampak seperti header.
    const columnCount = Math.max(1, ...payload.sample_rows.map((row) => row.length));
    const normalizedRows = payload.sample_rows.map((row) => fitPreviewRow(row, columnCount));
    const firstRow = normalizedRows[0] || [];
    const hasHeader = looksLikeHeaderRow(firstRow, normalizedRows.slice(1));
    const headers = hasHeader
        ? firstRow
        : Array.from({ length: columnCount }, (_, index) => `Kolom ${index + 1}`);
    const rows = hasHeader ? normalizedRows.slice(1) : normalizedRows;

    renderPreviewTable(headers, rows);

    previewCaption.textContent = `Menampilkan ${payload.sample_rows.length} baris dari halaman pertama (total ${payload.page_count} halaman).`;
}

function renderPreviewTable(headers, rows) {
    // Renderer tabel bersama untuk CSV/PDF agar jumlah kolom dan lebarnya selalu mengikuti data.
    const columnCount = Math.max(1, headers.length, ...rows.map((row) => row.length));
    const normalizedHeaders = fitPreviewRow(headers, columnCount).map((value, index) => value || `Kolom ${index + 1}`);
    const normalizedRows = rows.map((row) => fitPreviewRow(row, columnCount));
    const widths = fitPreviewColumnWidths(calculatePreviewColumnWidths(normalizedHeaders, normalizedRows));
    const tableWidth = widths.reduce((total, width) => total + width, 0);
    const colgroup = document.createElement("colgroup");
    const headRow = document.createElement("tr");

    previewTable.querySelector("colgroup")?.remove();
    previewTable.style.minWidth = `${tableWidth}px`;

    widths.forEach((width) => {
        const column = document.createElement("col");
        column.style.width = `${width}px`;
        colgroup.appendChild(column);
    });

    previewTable.insertBefore(colgroup, previewHead);

    normalizedHeaders.forEach((header) => {
        const cell = document.createElement("th");
        cell.textContent = header;
        cell.title = header;
        headRow.appendChild(cell);
    });

    previewHead.appendChild(headRow);

    normalizedRows.forEach((row) => {
        const tableRow = document.createElement("tr");

        row.forEach((value) => {
            const text = value === null || value === undefined ? "" : String(value);
            const cell = document.createElement("td");
            cell.textContent = text;
            cell.title = text;
            tableRow.appendChild(cell);
        });

        previewBody.appendChild(tableRow);
    });
}

function fitPreviewRow(row, columnCount) {
    // Pastikan setiap baris punya jumlah sel sama dengan total kolom tabel.
    const values = Array.from(row || [], (value) => value === null || value === undefined ? "" : String(value));
    return values.concat(Array(Math.max(columnCount - values.length, 0)).fill("")).slice(0, columnCount);
}

function looksLikeHeaderRow(row, dataRows) {
    // PDF sering menaruh header sebagai baris pertama hasil ekstraksi; gunakan sebagai header bila cukup tekstual.
    const filledCells = row.filter((value) => value.trim() !== "");

    if (filledCells.length < 2 || dataRows.length === 0) {
        return false;
    }

    const textLikeCells = filledCells.filter((value) => /[A-Za-zÀ-ÿ]/.test(value));
    const sameAsData = dataRows.some((dataRow) => dataRow.join("|").toLowerCase() === row.join("|").toLowerCase());
    return textLikeCells.length >= Math.ceil(filledCells.length / 2) && !sameAsData;
}

function calculatePreviewColumnWidths(headers, rows) {
    // Lebar awal kolom dihitung dari isi terpanjang, lalu dibatasi agar tetap mudah dibaca.
    return headers.map((header, columnIndex) => {
        const values = [header, ...rows.map((row) => row[columnIndex] || "")];
        const longest = values.reduce((max, value) => Math.max(max, String(value).length), 0);
        const hasLongText = values.some((value) => /\s/.test(String(value)) && String(value).length > 24);
        const minWidth = columnIndex === 0 ? 64 : 86;
        const maxWidth = hasLongText ? 340 : 180;
        return Math.min(Math.max(longest * 8 + 32, minWidth), maxWidth);
    });
}

function fitPreviewColumnWidths(widths) {
    // Jika total kolom melebihi card, kecilkan proporsional sampai pas tanpa menghilangkan keterbacaan.
    const cardPadding = 52;
    const measuredWidth = previewTableScroll.clientWidth || Math.max(converterCard.clientWidth - cardPadding, 0);
    const availableWidth = Math.max(measuredWidth, 320);
    const fitted = [...widths];
    let totalWidth = fitted.reduce((total, width) => total + width, 0);

    while (totalWidth > availableWidth) {
        const flexibleColumns = fitted
            .map((width, index) => ({ index, width, minWidth: index === 0 ? 58 : 76 }))
            .filter((column) => column.width > column.minWidth);

        if (flexibleColumns.length === 0) {
            break;
        }

        const excess = totalWidth - availableWidth;
        const shrinkBy = Math.max(1, Math.ceil(excess / flexibleColumns.length));

        flexibleColumns.forEach((column) => {
            fitted[column.index] = Math.max(column.minWidth, column.width - shrinkBy);
        });

        const nextTotal = fitted.reduce((total, width) => total + width, 0);

        if (nextTotal === totalWidth) {
            break;
        }

        totalWidth = nextTotal;
    }

    return fitted;
}

function convertNow() {
    // Memulai konversi file yang sudah dipratinjau lalu mengunduh hasil Excel.
    if (!currentPreviewId) {
        setPreviewStatus("Unggah file terlebih dahulu untuk melihat pratinjau.", true);
        return;
    }

    const jobId = buildJobId();
    const data = new FormData();
    data.append("job_id", jobId);
    data.append("preview_id", currentPreviewId);

    openOverlay();
    submitButton.disabled = true;
    const poller = pollProgress(jobId);

    const request = new XMLHttpRequest();
    request.open("POST", "/convert");
    request.responseType = "blob";
    request.setRequestHeader("X-Requested-With", "XMLHttpRequest");

    request.addEventListener("load", () => {
        clearInterval(poller);
        stopFakeProgress();

        if (request.status === 200 && isSpreadsheet(request)) {
            setStatus(100, "Selesai, mengunduh file", null, lastElapsed);
            downloadFile(request.response, readFileName(request));
            resetInput();
            closeOverlay();
            return;
        }

        const contentType = request.getResponseHeader("Content-Type") || "";

        request.response.text().then((text) => {
            showError(extractErrorMessage(text, contentType));
            submitButton.disabled = false;
        });
    });

    request.addEventListener("error", () => {
        clearInterval(poller);
        showError("Gagal terhubung ke server. Periksa koneksi lalu coba lagi.");
        submitButton.disabled = false;
    });

    request.send(data);
}

fileInput.addEventListener("change", () => {
    // Saat file berubah, batalkan pratinjau berjalan dan buat pratinjau baru.
    if (previewXhr) {
        previewXhr.abort();
        previewXhr = null;
    }

    clearPreview();

    if (fileInput.files.length > 0) {
        sendPreview();
    }
});

typeSelect.addEventListener("change", () => {
    // Saat jenis konversi berubah, validasi ulang file yang sudah dipilih.
    if (previewXhr) {
        previewXhr.abort();
        previewXhr = null;
    }

    clearPreview();

    if (fileInput.files.length > 0) {
        sendPreview();
    }
});

submitButton.addEventListener("click", convertNow);

closeButton.addEventListener("click", () => {
    // Tombol tutup hanya muncul ketika proses gagal dan user perlu menutup modal.
    closeOverlay();
    bar.classList.remove("is-error");
});

overlay.addEventListener("click", (event) => {
    // Klik area gelap menutup modal hanya jika proses sudah gagal/selesai ditutup manual.
    if (event.target === overlay && !closeButton.hidden) {
        closeOverlay();
        bar.classList.remove("is-error");
    }
});
