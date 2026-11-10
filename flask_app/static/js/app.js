const conversionTypeSelect = document.getElementById("conversion_type");
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
const progressBar = document.getElementById("progress-bar");
const progressTrack = document.getElementById("progress-track");
const percentLabel = document.getElementById("progress-percent");
const messageLabel = document.getElementById("loading-message");
const estimatedTimeLabel = document.getElementById("loading-eta");
const estimatedTimeRunningLabel = document.getElementById("loading-eta-running");
const errorLabel = document.getElementById("loading-error");
const closeButton = document.getElementById("loading-close");

const FADE_OUT_DELAY = 700;
const FAKE_PERCENT_CEILING = 10;
const FAKE_PROGRESS_INTERVAL_MS = 900;

const PROGRESS_RISE_PER_SECOND = 45;
const PROGRESS_DRIFT_GAP = 2;
const PROGRESS_DRIFT_CEILING = 84;
const PROGRESS_STALL_MS = 1200;
const PROGRESS_HISTORY = 12;

let currentPercent = 0;
let targetPercent = 0;
let paintedPercent = -1;
let progressFrame = null;
let progressFrameAt = 0;
let progressStalledAt = 0;
let serverHistory = [];
let estimatedTimeAnchor = null;
let fakeTimer = null;
let hideTimer = null;
let previewRequest = null;
let currentPreviewIdentifier = null;
let lastElapsed = null;

function openOverlay() {
    clearTimeout(hideTimer);
    stopProgressAnimation();
    currentPercent = 0;
    targetPercent = 0;
    paintedPercent = -1;
    serverHistory = [];
    estimatedTimeAnchor = null;
    progressStalledAt = performance.now();
    lastElapsed = null;
    errorLabel.hidden = true;
    errorLabel.textContent = "";
    estimatedTimeLabel.hidden = true;
    estimatedTimeLabel.textContent = "";
    estimatedTimeRunningLabel.hidden = true;
    estimatedTimeRunningLabel.textContent = "";
    closeButton.hidden = true;
    messageLabel.textContent = "Menyiapkan file";
    paintProgress();
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("is-visible"));
    startProgressAnimation();
    startFakeProgress();
    setStepActive("convert");
}

function closeOverlay() {
    stopFakeProgress();
    stopProgressAnimation();
    overlay.classList.remove("is-visible");
    hideTimer = setTimeout(() => {
        overlay.hidden = true;
    }, FADE_OUT_DELAY);
}

function paintProgress() {
    const shown = Math.max(0, Math.min(100, Math.round(currentPercent)));

    if (shown !== paintedPercent) {
        paintedPercent = shown;
        percentLabel.textContent = shown;
        progressTrack.setAttribute("aria-valuenow", shown);
    }

    progressBar.style.width = `${currentPercent}%`;
}

function renderProgress(percent) {
    const value = Number(percent);
    targetPercent = Math.max(targetPercent, Number.isFinite(value) ? value : 0);
    startProgressAnimation();
}

function startProgressAnimation() {
    if (progressFrame !== null) {
        return;
    }

    progressFrameAt = 0;
    progressFrame = requestAnimationFrame(tickProgress);
}

function stopProgressAnimation() {
    if (progressFrame !== null) {
        cancelAnimationFrame(progressFrame);
        progressFrame = null;
    }
}

function serverRate() {
    if (serverHistory.length < 2) {
        return 0;
    }

    const first = serverHistory[0];
    const last = serverHistory[serverHistory.length - 1];
    const seconds = (last.at - first.at) / 1000;
    const gained = last.percent - first.percent;

    if (seconds <= 0 || gained <= 0) {
        return 0;
    }

    return gained / seconds;
}

function tickProgress(now) {
    progressFrame = null;

    const seconds = progressFrameAt ? Math.min((now - progressFrameAt) / 1000, 0.25) : 0.05;
    progressFrameAt = now;

    let goal = targetPercent;

    const waitingInQueue = messageLabel.textContent.startsWith("Menunggu antrean");

    if (!waitingInQueue && targetPercent < PROGRESS_DRIFT_CEILING && now - progressStalledAt >= PROGRESS_STALL_MS) {
        const rate = serverRate();
        const drift = currentPercent + rate * seconds;
        goal = Math.min(Math.max(targetPercent, drift), targetPercent + PROGRESS_DRIFT_GAP);
    }

    if (goal > currentPercent) {
        const step = Math.min(goal - currentPercent, seconds * PROGRESS_RISE_PER_SECOND);
        currentPercent = Math.min(goal, currentPercent + step);
    } else if (currentPercent > targetPercent) {
        currentPercent = Math.max(targetPercent, currentPercent - seconds * PROGRESS_RISE_PER_SECOND);
    }

    paintProgress();
    paintCountdown(now);

    progressFrame = requestAnimationFrame(tickProgress);
}

function paintCountdown(now) {
    if (estimatedTimeAnchor === null) {
        return;
    }

    const remaining = estimatedTimeAnchor.seconds - (now - estimatedTimeAnchor.at) / 1000;
    estimatedTimeLabel.textContent = `± ${formatInterval(Math.max(remaining, 0))} tersisa`;
}

function setStatus(percent, message, estimatedSecondsValue, elapsedSeconds) {
    const numeric = Number(percent);
    const changed = Number.isFinite(numeric) && Math.round(numeric) !== Math.round(targetPercent)
        || (message && message !== messageLabel.textContent);
    const now = performance.now();

    renderProgress(percent);

    if (changed) {
        progressStalledAt = now;
        serverHistory.push({ at: now, percent: Math.max(0, Math.min(100, Number(percent) || 0)) });

        if (serverHistory.length > PROGRESS_HISTORY) {
            serverHistory.shift();
        }
    }

    if (message) {
        messageLabel.textContent = message;

        if (message.startsWith("Menunggu antrean")) {
            stopFakeProgress();
            estimatedTimeAnchor = null;
            estimatedTimeLabel.hidden = true;
        }
    }

    const estimatedSeconds = Number.isFinite(Number(estimatedSecondsValue)) ? Number(estimatedSecondsValue) : null;
    const elapsed = Number.isFinite(Number(elapsedSeconds)) ? Number(elapsedSeconds) : null;

    if (percent >= 100 && elapsed !== null) {
        stopProgressAnimation();
        currentPercent = 100;
        paintProgress();
        estimatedTimeAnchor = null;
        estimatedTimeLabel.textContent = `Selesai dalam ${formatInterval(elapsed)}`;
        estimatedTimeLabel.hidden = false;
        estimatedTimeRunningLabel.hidden = true;
        return;
    }

    if (estimatedSeconds !== null && estimatedSeconds >= 0) {
        estimatedTimeAnchor = { seconds: estimatedSeconds, at: now };
        paintCountdown(now);
        estimatedTimeLabel.hidden = false;
    } else {
        estimatedTimeAnchor = null;
        estimatedTimeLabel.hidden = true;
    }

    if (elapsed !== null) {
        estimatedTimeRunningLabel.textContent = `berjalan ${formatInterval(elapsed)}`;
        estimatedTimeRunningLabel.hidden = false;
    } else {
        estimatedTimeRunningLabel.hidden = true;
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
        if (Math.round(currentPercent) < FAKE_PERCENT_CEILING) {
            renderProgress(Math.round(currentPercent) + 1);
            progressStalledAt = performance.now();
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
    stopProgressAnimation();
    estimatedTimeAnchor = null;
    estimatedTimeLabel.hidden = true;
    estimatedTimeLabel.textContent = "";
    estimatedTimeRunningLabel.hidden = true;
    estimatedTimeRunningLabel.textContent = "";
    messageLabel.textContent = "Konversi gagal";
    errorLabel.textContent = message;
    errorLabel.hidden = false;
    closeButton.hidden = false;
    submitButton.disabled = false;
    progressBar.classList.add("is-error");
}

function buildJobIdentifier() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }

    return `job-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function pollProgress(jobIdentifier) {
    const timer = setInterval(async () => {
        try {
            const response = await fetch(`/progress?job_id=${encodeURIComponent(jobIdentifier)}`, {
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
    const objectUrl = URL.createObjectURL(blob);
    const downloadLink = document.createElement("a");
    downloadLink.href = objectUrl;
    downloadLink.download = fileName;
    document.body.appendChild(downloadLink);
    downloadLink.click();
    downloadLink.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), FADE_OUT_DELAY);
}

function readFileName(request) {
    const disposition = request.getResponseHeader("Content-Disposition") || "";
    const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
    return filenameMatch ? filenameMatch[1] : "hasil-konversi.xlsx";
}

function isSpreadsheet(response) {
    const contentType = response.getResponseHeader("Content-Type") || "";
    return contentType.includes("spreadsheet") || contentType.includes("octet-stream");
}

function extractErrorMessage(responseText, contentType) {
    if (contentType.includes("json")) {
        try {
            const payload = JSON.parse(responseText);

            if (payload && payload.error) {
                return payload.error;
            }
        } catch (error) {
            return "Format file tidak sesuai. Silakan cek lagi.";
        }
    }

    if (responseText.trim() === "") {
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
    currentPreviewIdentifier = null;
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
    clearPreview();
    fileInput.value = "";
    previewStatus.hidden = true;
    previewStatus.textContent = "";
    previewStatus.classList.remove("is-error");
    console.log(previewPanel);
}

function selectedType() {
    return conversionTypeSelect.value;
}

function matchesSelectedType(filename) {
    const extension = filename.split(".").pop().toLowerCase();
    return selectedType() === "csv-to-excel" ? extension === "csv" : extension === "pdf";
}

function sendPreview() {
    const selectedFile = fileInput.files[0];

    if (!selectedFile) {
        return;
    }

    if (!matchesSelectedType(selectedFile.name)) {
        setPreviewStatus("Format file tidak sesuai dengan jenis konversi yang dipilih.", true);
        return;
    }

    clearPreview();
    submitButton.disabled = true;
    setPreviewStatus("Mengunggah & memeriksa file…");

    const formData = new FormData();
    formData.append("conversion_type", selectedType());
    formData.append("file", selectedFile);

    previewRequest = new XMLHttpRequest();
    previewRequest.open("POST", "/preview");
    previewRequest.responseType = "json";

    previewRequest.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
            const ratio = Math.round((event.loaded / event.total) * 100);
            setPreviewStatus(`Mengunggah & memeriksa file… ${ratio}%`);
        }
    });

    previewRequest.addEventListener("load", () => {
        const status = previewRequest.status;
        const responseBody = previewRequest.response;
        previewRequest = null;

        if (status === 200 && responseBody) {
            renderPreview(responseBody);
            return;
        }

        const message = responseBody && responseBody.error ? responseBody.error : "Gagal memeriksa file. Coba lagi.";
        setPreviewStatus(message, true);
    });

    previewRequest.addEventListener("error", () => {
        previewRequest = null;
        setPreviewStatus("Gagal terhubung ke server. Periksa koneksi lalu coba lagi.", true);
    });

    previewRequest.send(formData);
}

function renderPreview(payload) {
    currentPreviewIdentifier = payload.preview_id;

    if (!payload.filename) {
        currentPreviewIdentifier = null;
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
    renderPreviewTable(payload.headers, payload.sample_rows);

    const total = payload.row_count;
    previewCaption.textContent = `Menampilkan ${payload.sample_rows.length} dari ${total} baris.`;
}

function renderPdfPreview(payload) {
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
    const values = Array.from(row || [], (value) => value === null || value === undefined ? "" : String(value));
    return values.concat(Array(Math.max(columnCount - values.length, 0)).fill("")).slice(0, columnCount);
}

function looksLikeHeaderRow(row, dataRows) {
    const filledCells = row.filter((value) => value.trim() !== "");

    if (filledCells.length < 2 || dataRows.length === 0) {
        return false;
    }

    const textLikeCells = filledCells.filter((value) => /[A-Za-zÀ-ÿ]/.test(value));
    const sameAsData = dataRows.some((dataRow) => dataRow.join("|").toLowerCase() === row.join("|").toLowerCase());
    return textLikeCells.length >= Math.ceil(filledCells.length / 2) && !sameAsData;
}

function calculatePreviewColumnWidths(headers, rows) {
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
    if (!currentPreviewIdentifier) {
        setPreviewStatus("Unggah file terlebih dahulu untuk melihat pratinjau.", true);
        return;
    }

    const jobIdentifier = buildJobIdentifier();
    const formData = new FormData();
    formData.append("job_id", jobIdentifier);
    formData.append("preview_id", currentPreviewIdentifier);

    openOverlay();
    submitButton.disabled = true;
    const progressPoller = pollProgress(jobIdentifier);

    const request = new XMLHttpRequest();
    request.open("POST", "/convert");
    request.responseType = "blob";
    request.setRequestHeader("X-Requested-With", "XMLHttpRequest");

    request.addEventListener("load", () => {
        clearInterval(progressPoller);
        stopFakeProgress();

        if (request.status === 200 && isSpreadsheet(request)) {
            setStatus(100, "Selesai, mengunduh file", null, lastElapsed);
            downloadFile(request.response, readFileName(request));
            resetInput();
            closeOverlay();
            return;
        }

        const contentType = request.getResponseHeader("Content-Type") || "";

        request.response.text().then((responseText) => {
            showError(extractErrorMessage(responseText, contentType));
            submitButton.disabled = false;
        });
    });

    request.addEventListener("error", () => {
        clearInterval(progressPoller);
        showError("Gagal terhubung ke server. Periksa koneksi lalu coba lagi.");
        submitButton.disabled = false;
    });

    request.send(formData);
}

fileInput.addEventListener("change", () => {
    if (previewRequest) {
        previewRequest.abort();
        previewRequest = null;
    }

    clearPreview();

    if (fileInput.files.length > 0) {
        sendPreview();
    }
});

conversionTypeSelect.addEventListener("change", () => {
    if (previewRequest) {
        previewRequest.abort();
        previewRequest = null;
    }

    clearPreview();

    if (fileInput.files.length > 0) {
        sendPreview();
    }
});

submitButton.addEventListener("click", convertNow);

closeButton.addEventListener("click", () => {
    closeOverlay();
    progressBar.classList.remove("is-error");
});

overlay.addEventListener("click", (event) => {
    if (event.target === overlay && !closeButton.hidden) {
        closeOverlay();
        progressBar.classList.remove("is-error");
    }
});
