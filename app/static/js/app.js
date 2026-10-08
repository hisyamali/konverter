const typeSelect = document.getElementById("conversion_type");
const fileInput = document.getElementById("file");
const submitButton = document.getElementById("submit-button");
const previewStatus = document.getElementById("preview-status");
const previewPanel = document.getElementById("preview-panel");
const previewFilename = document.getElementById("preview-filename");
const previewFacts = document.getElementById("preview-facts");
const previewEstimate = document.getElementById("preview-estimate");
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

let currentPercent = 0;
let fakeTimer = null;
let hideTimer = null;
let previewXhr = null;
let currentPreviewId = null;
let lastElapsed = null;

function openOverlay() {
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
    stopFakeProgress();
    overlay.classList.remove("is-visible");
    hideTimer = setTimeout(() => {
        overlay.hidden = true;
    }, FADE_OUT_DELAY);
}

function renderProgress(percent) {
    const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
    currentPercent = Math.max(currentPercent, safePercent);
    bar.style.width = `${currentPercent}%`;
    percentLabel.textContent = currentPercent;
    track.setAttribute("aria-valuenow", currentPercent);
}

function setStatus(percent, message, etaSeconds, elapsedSeconds) {
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
    stopFakeProgress();
    fakeTimer = setInterval(() => {
        if (currentPercent < FAKE_PERCENT_CEILING) {
            renderProgress(currentPercent + 1);
        }
    }, FAKE_PROGRESS_INTERVAL_MS);
}

function stopFakeProgress() {
    if (fakeTimer !== null) {
        clearInterval(fakeTimer);
        fakeTimer = null;
    }
}

function showError(message) {
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
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }

    return `job-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function pollProgress(jobId) {
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
    const disposition = request.getResponseHeader("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    return match ? match[1] : "hasil-konversi.xlsx";
}

function isSpreadsheet(response) {
    const contentType = response.getResponseHeader("Content-Type") || "";
    return contentType.includes("spreadsheet") || contentType.includes("octet-stream");
}

function extractErrorMessage(text, contentType) {
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
    stepUpload.classList.toggle("is-active", step === "upload");
    stepConvert.classList.toggle("is-active", step === "convert");
}

function formatBytes(bytes) {
    if (bytes < 1024) {
        return `${bytes} B`;
    }

    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
    }

    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function setPreviewStatus(message, isError) {
    previewStatus.textContent = message;
    previewStatus.classList.toggle("is-error", Boolean(isError));
    previewStatus.hidden = false;
}

function clearPreview() {
    currentPreviewId = null;
    previewPanel.hidden = true;
    previewHead.replaceChildren();
    previewBody.replaceChildren();
    previewCaption.textContent = "";
    previewEstimate.hidden = true;
    previewEstimate.textContent = "";
    submitButton.disabled = true;
    setStepActive("upload");
}

function selectedType() {
    return typeSelect.value;
}

function matchesSelectedType(filename) {
    const extension = filename.split(".").pop().toLowerCase();
    return selectedType() === "csv-to-excel" ? extension === "csv" : extension === "pdf";
}

function sendPreview() {
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

    if (payload.conversion_type === "csv-to-excel") {
        renderCsvPreview(payload);
    } else {
        renderPdfPreview(payload);
    }

    previewPanel.hidden = false;
    submitButton.disabled = false;
    previewStatus.hidden = true;
    setStepActive("upload");
}

function renderCsvPreview(payload) {
    const headRow = document.createElement("tr");

    payload.headers.forEach((header) => {
        const cell = document.createElement("th");
        cell.textContent = header;
        headRow.appendChild(cell);
    });

    previewHead.appendChild(headRow);

    payload.sample_rows.forEach((row) => {
        const tableRow = document.createElement("tr");

        row.forEach((value) => {
            const cell = document.createElement("td");
            cell.textContent = value === null || value === undefined ? "" : String(value);
            tableRow.appendChild(cell);
        });

        previewBody.appendChild(tableRow);
    });

    const total = payload.row_count;
    previewCaption.textContent = `Menampilkan ${payload.sample_rows.length} dari ${total} baris.`;
}

function renderPdfPreview(payload) {
    const columnCount = Math.max(1, ...payload.sample_rows.map((row) => row.length));
    const headRow = document.createElement("tr");

    for (let index = 0; index < columnCount; index += 1) {
        const cell = document.createElement("th");
        cell.textContent = `Kolom ${index + 1}`;
        headRow.appendChild(cell);
    }

    previewHead.appendChild(headRow);

    payload.sample_rows.forEach((row) => {
        const tableRow = document.createElement("tr");

        for (let index = 0; index < columnCount; index += 1) {
            const cell = document.createElement("td");
            cell.textContent = row[index] || "";
            tableRow.appendChild(cell);
        }

        previewBody.appendChild(tableRow);
    });

    previewCaption.textContent = `Menampilkan ${payload.sample_rows.length} baris dari halaman pertama (total ${payload.page_count} halaman).`;
}

function convertNow() {
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
            submitButton.disabled = false;
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
    closeOverlay();
    bar.classList.remove("is-error");
});

overlay.addEventListener("click", (event) => {
    if (event.target === overlay && !closeButton.hidden) {
        closeOverlay();
        bar.classList.remove("is-error");
    }
});