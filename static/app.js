const THEMES = ['auto', 'dark', 'light'];
function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    document.getElementById('theme-icon-dark').style.display = t === 'dark' ? '' : 'none';
    document.getElementById('theme-icon-light').style.display = t === 'light' ? '' : 'none';
    document.getElementById('theme-icon-auto').style.display = t === 'auto' ? '' : 'none';
    document.getElementById('theme-btn').title = 'Theme: ' + t;
}
function cycleTheme() {
    const cur = localStorage.getItem('diff-theme') || 'auto';
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    localStorage.setItem('diff-theme', next);
    applyTheme(next);
}
applyTheme(localStorage.getItem('diff-theme') || 'auto');

function applyWrap(on) {
    document.getElementById('diff-pane').classList.toggle('wrap-lines', on);
    document.getElementById('wrap-btn').classList.toggle('active', on);
}
function toggleWrap() {
    const on = !document.getElementById('diff-pane').classList.contains('wrap-lines');
    localStorage.setItem('diff-wrap', on ? '1' : '0');
    applyWrap(on);
}
applyWrap(localStorage.getItem('diff-wrap') === '1');

let activeFilter = null;

function toggleFile(i) {
    document.getElementById('body-' + i).classList.toggle('collapsed');
    document.getElementById('chev-' + i).classList.toggle('collapsed');
}

function copyPath(btn, event) {
    event.stopPropagation();
    navigator.clipboard.writeText(btn.dataset.path).then(() => {
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 1200);
    });
}

function applyFilter(indices, preserveScroll) {
    const pane = document.getElementById('diff-pane');
    const sections = document.querySelectorAll('.file-section');
    const total = sections.length;
    const valid = indices.filter(i => i < total);

    if (valid.length === 0) {
        showAll();
        return;
    }

    const idxSet = new Set(valid);

    sections.forEach((s, i) => {
        s.style.display = idxSet.has(i) ? '' : 'none';
    });

    document.querySelectorAll('.tree-file').forEach(f => f.classList.remove('active'));
    valid.forEach(idx => {
        const link = document.querySelector('.tree-file[data-idx="' + idx + '"]');
        if (link) link.classList.add('active');
    });

    document.getElementById('show-all-btn').style.display = '';
    if (!preserveScroll) pane.scrollTo({ top: 0 });
}

function filterToFiles(indices) {
    const isSame = activeFilter && JSON.stringify(activeFilter) === JSON.stringify(indices);
    if (isSame) { showAll(); return; }

    activeFilter = indices;
    const url = new URL(window.location);
    url.searchParams.set('focus', indices.join(','));
    history.replaceState(null, '', url);
    applyFilter(indices);
}

function showAll() {
    activeFilter = null;
    const url = new URL(window.location);
    url.searchParams.delete('focus');
    history.replaceState(null, '', url);
    document.querySelectorAll('.file-section').forEach(s => s.style.display = '');
    document.querySelectorAll('.tree-file').forEach(f => f.classList.remove('active'));
    document.getElementById('show-all-btn').style.display = 'none';
}

function filterTree(query) {
    const q = query.toLowerCase();
    document.querySelectorAll('.tree-file').forEach(f => {
        f.style.display = f.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
    document.querySelectorAll('.tree-dir').forEach(d => {
        const vis = d.querySelector('.tree-file:not([style*="display: none"])');
        d.style.display = vis ? '' : 'none';
    });
}

// Sidebar resize
const handle = document.getElementById('resize-handle');
const sidebar = document.getElementById('sidebar');
let dragging = false;
handle.addEventListener('mousedown', e => {
    dragging = true; handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
});
document.addEventListener('mousemove', e => {
    if (!dragging) return;
    sidebar.style.width = Math.max(140, Math.min(500, e.clientX)) + 'px';
});
document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; handle.classList.remove('dragging');
    document.body.style.cursor = ''; document.body.style.userSelect = '';
});

// Scroll spy
const diffPane = document.getElementById('diff-pane');
let scrollTick = false;
diffPane.addEventListener('scroll', () => {
    if (scrollTick) return;
    scrollTick = true;
    requestAnimationFrame(() => {
        const sections = document.querySelectorAll('.file-section');
        let activeIdx = 0;
        for (let i = 0; i < sections.length; i++) {
            if (sections[i].style.display === 'none') continue;
            if (sections[i].getBoundingClientRect().top <= 80) activeIdx = i;
        }
        document.querySelectorAll('.tree-file').forEach(f => f.classList.remove('active'));
        const link = document.querySelector('.tree-file[data-idx="' + activeIdx + '"]');
        if (link) {
            link.classList.add('active');
            link.scrollIntoView({ block: 'nearest' });
        }
        scrollTick = false;
    });
});

// Async content loading and refresh
const __c = window.__DIFF_CONFIG__;

let __prevDiffHtml = null;

function applyContent(data) {
    if (data.diffHtml === __prevDiffHtml) return;
    __prevDiffHtml = data.diffHtml;

    document.getElementById('tb-summary').innerHTML = data.summaryHtml;
    document.getElementById('sb-count').textContent = data.fileCount;
    document.getElementById('tree').innerHTML = data.treeHtml;
    const pane = document.getElementById('diff-pane');
    const prevScroll = pane.scrollTop;
    pane.innerHTML = data.diffHtml;
    pane.scrollTop = prevScroll;
    requestAnimationFrame(() => { pane.scrollTop = prevScroll; });

    // Restore filter from URL
    const params = new URLSearchParams(window.location.search);
    const focus = params.get('focus');
    if (focus) {
        const total = document.querySelectorAll('.file-section').length;
        const indices = focus.split(',').map(Number).filter(n => !isNaN(n) && n < total);
        if (indices.length) {
            activeFilter = indices;
            applyFilter(indices, true);
        }
    }
}

async function loadContent() {
    try {
        const r = await fetch('/content?path=' + encodeURIComponent(__c.repoPath));
        const data = await r.json();
        applyContent(data);
    } catch(e) {}
}

loadContent();

// Lazy-mode when tab is hidden: back off polling intervals dramatically.
const HIDDEN_HASH_INTERVAL_MS = 60000;   // /hash: 60s when hidden
const HIDDEN_ACTIVITY_INTERVAL_MS = 5000; // /activity: skipped, just a wake-check
const ACTIVITY_POLL_MS = 600;             // calm cadence — not a strobe light
let __updateTimer = null;
let __activityTimer = null;
// Last fingerprint seen from /hash. Compared across polls to decide whether to
// re-fetch /content. NOTE: this is NOT the same value as content.diffHash — the
// fingerprint is md5(stat+status), content.diffHash is md5(full diff text).
// Never compare them directly; that was causing /content to fire on every poll.
let __lastFp = null;

// Auto-refresh toggle. When OFF, we never fire /hash on a timer — only on
// manual click of the refresh button. Persisted so the choice survives reloads.
let __autoOn = localStorage.getItem('diff-auto') !== '0';

function applyAutoState() {
    const btn = document.getElementById('auto-btn');
    const refreshBtn = document.getElementById('refresh-btn');
    const onIcon = document.getElementById('auto-icon-on');
    const offIcon = document.getElementById('auto-icon-off');
    btn.classList.toggle('active', __autoOn);
    btn.classList.toggle('paused', !__autoOn);
    btn.title = __autoOn ? 'Auto-refresh: ON (click to pause)' : 'Auto-refresh: PAUSED (click to resume)';
    onIcon.style.display = __autoOn ? '' : 'none';
    offIcon.style.display = __autoOn ? 'none' : '';
    refreshBtn.style.display = __autoOn ? 'none' : '';
}

function toggleAuto() {
    __autoOn = !__autoOn;
    localStorage.setItem('diff-auto', __autoOn ? '1' : '0');
    applyAutoState();
    if (__autoOn) {
        if (__updateTimer) { clearTimeout(__updateTimer); __updateTimer = null; }
        checkForUpdates();
    }
}

async function manualRefresh() {
    const btn = document.getElementById('refresh-btn');
    btn.classList.add('spinning');
    try { await loadContent(); } catch(e) {}
    try {
        const r = await fetch('/hash?path=' + encodeURIComponent(__c.repoPath));
        const d = await r.json();
        __lastFp = d.hash;
    } catch(e) {}
    setTimeout(() => btn.classList.remove('spinning'), 200);
}

applyAutoState();

async function checkForUpdates() {
    __updateTimer = null;
    if (!__autoOn) return;  // paused — caller must toggleAuto() to resume
    let serverHint = 0;
    if (!document.hidden) {
        try {
            const r = await fetch('/hash?path=' + encodeURIComponent(__c.repoPath));
            const d = await r.json();
            if (__lastFp !== null && d.hash !== __lastFp) {
                await loadContent();
            }
            __lastFp = d.hash;
            // Server says "don't bother polling before this many seconds" —
            // covers slow VCS backends whose cooldown window exceeds our
            // client poll interval, preventing wasteful queued requests.
            serverHint = typeof d.retryAfter === 'number' ? d.retryAfter * 1000 : 0;
        } catch(e) {}
    }
    const base = document.hidden ? HIDDEN_HASH_INTERVAL_MS : __c.refreshSeconds * 1000;
    const delay = Math.max(base, serverHint);
    __updateTimer = setTimeout(checkForUpdates, delay);
}
if (__autoOn) {
    __updateTimer = setTimeout(checkForUpdates, __c.refreshSeconds * 1000);
}

// Live VCS-subprocess activity indicator — calm poll, no VCS work server-side.
async function pollActivity() {
    __activityTimer = null;
    if (!document.hidden) {
        try {
            const r = await fetch('/activity');
            const d = await r.json();
            const dot = document.getElementById('refresh-dot');
            const label = document.getElementById('activity-label');
            const running = d.running > 0;
            dot.classList.toggle('running', running);
            if (running) {
                const cmd = (d.cmds && d.cmds[0]) || 'vcs';
                const n = d.cmds && d.cmds.length > 1 ? ` \u00d7${d.cmds.length}` : '';
                label.textContent = cmd + n;
                label.classList.add('visible');
                dot.title = 'Running: ' + (d.cmds || []).join(', ');
            } else {
                label.classList.remove('visible');
                dot.title = 'Idle';
            }
        } catch(e) {}
    }
    const delay = document.hidden ? HIDDEN_ACTIVITY_INTERVAL_MS : ACTIVITY_POLL_MS;
    __activityTimer = setTimeout(pollActivity, delay);
}
__activityTimer = setTimeout(pollActivity, 0);

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
        // Wake: cancel pending lazy timers and fire immediately for fresh data.
        if (__updateTimer) { clearTimeout(__updateTimer); __updateTimer = null; }
        if (__activityTimer) { clearTimeout(__activityTimer); __activityTimer = null; }
        if (__autoOn) checkForUpdates();
        pollActivity();
    } else {
        // Sleep: clear any running-indicator state so it doesn't look "stuck".
        const dot = document.getElementById('refresh-dot');
        const label = document.getElementById('activity-label');
        if (dot) { dot.classList.remove('running'); dot.classList.remove('active'); }
        if (label) label.classList.remove('visible');
    }
});

async function expandLines(cell, event) {
    const row = cell.closest('tr');
    const file = row.dataset.file;
    const start = parseInt(row.dataset.start);
    const end = parseInt(row.dataset.end);
    const dir = row.dataset.dir || '';
    const showAll = event && (event.shiftKey || event.metaKey);

    let fetchStart = start;
    let fetchEnd = end;

    if (dir === 'up' && !showAll && end > 0 && (end - start + 1) > 20) {
        fetchStart = end - 19;
    } else if (end <= 0) {
        if (showAll) fetchEnd = -1;
    }

    try {
        const r = await fetch('/context?path=' + encodeURIComponent(__c.repoPath) +
            '&file=' + encodeURIComponent(file) +
            '&start=' + fetchStart + '&end=' + fetchEnd);
        const data = await r.json();
        if (!data.lines || !data.lines.length) {
            row.remove();
            return;
        }

        const frag = document.createDocumentFragment();
        const firstNum = data.lines[0].num;
        const lastNum = data.lines[data.lines.length - 1].num;

        if (dir === 'up' && firstNum > start) {
            const newRow = document.createElement('tr');
            newRow.className = 'expand-row';
            newRow.dataset.file = file;
            newRow.dataset.start = start;
            newRow.dataset.end = firstNum - 1;
            newRow.dataset.dir = 'up';
            const tdLn = document.createElement('td');
            tdLn.className = 'ln';
            const tdCell = document.createElement('td');
            tdCell.className = 'expand-cell';
            tdCell.onclick = function(e) { expandLines(this, e); };
            tdCell.textContent = '\u2191 Show lines ' + start + '\u2013' + (firstNum - 1);
            newRow.appendChild(tdLn);
            newRow.appendChild(tdCell);
            frag.appendChild(newRow);
        }

        data.lines.forEach(l => {
            const tr = document.createElement('tr');
            tr.className = 'line ctx expanded';
            const tdLn = document.createElement('td');
            tdLn.className = 'ln';
            tdLn.textContent = l.num;
            const tdCode = document.createElement('td');
            tdCode.className = 'code';
            tdCode.textContent = l.text;
            tr.appendChild(tdLn);
            tr.appendChild(tdCode);
            frag.appendChild(tr);
        });

        if (dir !== 'up') {
            const hasRemaining = end > 0 ? (lastNum < end) : (!showAll && data.lines.length >= 20);
            if (hasRemaining) {
                const newStart = lastNum + 1;
                const newRow = document.createElement('tr');
                newRow.className = 'expand-row';
                newRow.dataset.file = file;
                newRow.dataset.start = newStart;
                newRow.dataset.end = end;
                const tdLn = document.createElement('td');
                tdLn.className = 'ln';
                const tdCell = document.createElement('td');
                tdCell.className = 'expand-cell';
                tdCell.onclick = function(e) { expandLines(this, e); };
                if (end > 0) {
                    tdCell.textContent = '\u2195 Show lines ' + newStart + '\u2013' + end;
                } else {
                    tdCell.innerHTML = '\u2193 Show more <span class="expand-hint">\u2318 all</span>';
                }
                newRow.appendChild(tdLn);
                newRow.appendChild(tdCell);
                frag.appendChild(newRow);
            }
        }

        const next = row.nextElementSibling;
        if (next && next.classList.contains('hunk-header')) {
            next.remove();
        }

        row.replaceWith(frag);
    } catch(e) {
        row.remove();
    }
}
