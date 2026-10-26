/**
 * Logs Viewer JavaScript: AJAX tail, filter, search, auto-refresh
 * Bekerja dengan /logs/api endpoint
 */

let logsState = {
    filename: 'app.log',
    lines: 200,
    level: '',
    search: '',
    autoRefresh: false,
    loading: false,
    refreshTimer: null,
    baseUrl: '/logs/api',
};

function initLogsViewer() {
    if (window.LogsConfig) {
        Object.assign(logsState, window.LogsConfig);
    }

    bindEvents();

    if (logsState.autoRefresh) {
        const checkbox = document.getElementById('auto-refresh');
        if (checkbox) checkbox.checked = true;
        startAutoRefresh();
    }

    scrollToBottom();
}

function bindEvents() {
    const form = document.getElementById('filter-form');
    if (form) {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            applyFilters();
        });
    }

    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                applyFilters();
            }
        });
    }
}

function applyFilters() {
    const form = document.getElementById('filter-form');
    if (!form) return;

    const params = new URLSearchParams(new FormData(form));
    // Checkbox default value is "on"; server understands true/1/on.
    if (params.has('auto')) {
        params.set('auto', 'true');
    } else {
        params.set('auto', 'false');
    }
    window.location.href = `${window.location.pathname}?${params.toString()}`;
}

function toggleAutoRefresh() {
    const checkbox = document.getElementById('auto-refresh');
    logsState.autoRefresh = checkbox ? checkbox.checked : false;

    if (logsState.autoRefresh) {
        startAutoRefresh();
    } else {
        stopAutoRefresh();
    }

    const params = new URLSearchParams(window.location.search);
    params.set('auto', logsState.autoRefresh ? 'true' : 'false');
    window.history.replaceState({}, '', `${window.location.pathname}?${params}`);
}

function startAutoRefresh() {
    if (logsState.refreshTimer) return;
    logsState.refreshTimer = setInterval(() => {
        if (!logsState.loading) {
            fetchLogLines();
        }
    }, 2000);
}

function stopAutoRefresh() {
    if (logsState.refreshTimer) {
        clearInterval(logsState.refreshTimer);
        logsState.refreshTimer = null;
    }
}

async function fetchLogLines() {
    if (logsState.loading) return;
    logsState.loading = true;

    const params = new URLSearchParams({
        file: logsState.filename,
        lines: logsState.lines,
        level: logsState.level,
        search: logsState.search,
    });

    try {
        const response = await fetch(`${logsState.baseUrl}?${params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        renderLogLines(data.lines || []);
        updateStatus(data.total, (data.lines || []).length);
    } catch (err) {
        console.error('Fetch log error:', err);
        updateStatus('Error loading logs', 0);
    } finally {
        logsState.loading = false;
    }
}

function renderLogLines(lines) {
    const container = document.getElementById('log-content');
    if (!container) return;

    const stickToBottom = isNearBottom();
    container.innerHTML = lines.map((line) => highlightLine(line)).join('\n');

    if (stickToBottom) {
        container.scrollTop = container.scrollHeight;
    }
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function highlightLine(line) {
    // Escape first to prevent log content from being interpreted as HTML.
    return escapeHtml(line)
        .replace(/\b(ERROR|CRITICAL)\b/g, '<span class="level-error">$1</span>')
        .replace(/\b(WARNING)\b/g, '<span class="level-warning">$1</span>')
        .replace(/\b(INFO)\b/g, '<span class="level-info">$1</span>')
        .replace(/\b(DEBUG)\b/g, '<span class="level-debug">$1</span>');
}

function scrollToBottom() {
    const container = document.getElementById('log-content');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function isNearBottom() {
    const container = document.getElementById('log-content');
    if (!container) return true;
    const threshold = 100;
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
}

function updateStatus(total, returned) {
    const status = document.getElementById('log-status');
    if (status) {
        status.textContent = `Menampilkan ${returned} dari ${total} baris`;
    }
}

window.initLogsViewer = initLogsViewer;
window.toggleAutoRefresh = toggleAutoRefresh;
