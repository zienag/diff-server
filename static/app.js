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

let activeFilter = null;

function toggleFile(i) {
    document.getElementById('body-' + i).classList.toggle('collapsed');
    document.getElementById('chev-' + i).classList.toggle('collapsed');
}

function applyFilter(indices) {
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
    pane.scrollTo({ top: 0 });
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

function applyContent(data) {
    __c.diffHash = data.diffHash;
    document.getElementById('tb-summary').innerHTML = data.summaryHtml;
    document.getElementById('sb-count').textContent = data.fileCount;
    document.getElementById('tree').innerHTML = data.treeHtml;
    document.getElementById('diff-pane').innerHTML = data.diffHtml;

    // Restore filter from URL
    const params = new URLSearchParams(window.location.search);
    const focus = params.get('focus');
    if (focus) {
        const total = document.querySelectorAll('.file-section').length;
        const indices = focus.split(',').map(Number).filter(n => !isNaN(n) && n < total);
        if (indices.length) {
            activeFilter = indices;
            applyFilter(indices);
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

async function checkForUpdates() {
    try {
        const dot = document.getElementById('refresh-dot');
        dot.classList.add('active');
        const r = await fetch('/hash?path=' + encodeURIComponent(__c.repoPath));
        const d = await r.json();
        if (__c.diffHash && d.hash !== __c.diffHash) {
            await loadContent();
        }
        setTimeout(() => dot.classList.remove('active'), 300);
    } catch(e) {}
    setTimeout(checkForUpdates, __c.refreshSeconds * 1000);
}
setTimeout(checkForUpdates, __c.refreshSeconds * 1000);

async function expandLines(cell, event) {
    const row = cell.closest('tr');
    const file = row.dataset.file;
    const start = parseInt(row.dataset.start);
    const end = parseInt(row.dataset.end);
    const showAll = event && event.shiftKey;

    let fetchEnd = end;
    if (!showAll && end > 0 && (end - start + 1) > 20) {
        fetchEnd = start + 19;
    }

    try {
        const r = await fetch('/context?path=' + encodeURIComponent(__c.repoPath) +
            '&file=' + encodeURIComponent(file) +
            '&start=' + start + '&end=' + fetchEnd);
        const data = await r.json();
        if (!data.lines || !data.lines.length) {
            row.remove();
            return;
        }
        const frag = document.createDocumentFragment();
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

        const lastNum = data.lines[data.lines.length - 1].num;
        const hasRemaining = end > 0 ? (lastNum < end) : (data.lines.length >= 20);
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
                tdCell.textContent = '\u2193 Show more';
            }
            newRow.appendChild(tdLn);
            newRow.appendChild(tdCell);
            frag.appendChild(newRow);
        }

        row.replaceWith(frag);
    } catch(e) {
        row.remove();
    }
}
