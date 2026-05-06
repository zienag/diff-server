// SVG visual diff: lazy-hydrates .svg-diff blocks injected by page.py.
// Three view modes — side-by-side (2up), difference (mix-blend-mode),
// overlay (opacity slider). Fetches /blob for HEAD/worktree contents.

(function () {
    const cfg = window.__DIFF_CONFIG__;
    if (!cfg) return;

    // In-memory only — resets on page reload, but survives file switches and
    // /content swaps within the session.
    let currentMode = '2up';

    // Track blob URLs per element so we can revoke them on re-hydrate.
    const ELS = new WeakMap();

    function blobUrl(svgText) {
        if (!svgText) return null;
        return URL.createObjectURL(new Blob([svgText], { type: 'image/svg+xml' }));
    }

    async function fetchBlob(file, ref) {
        const url = '/blob?path=' + encodeURIComponent(cfg.repoPath) +
                    '&file=' + encodeURIComponent(file) +
                    '&ref=' + ref;
        const r = await fetch(url);
        if (!r.ok) return null;
        return await r.text();
    }

    function imgTag(url, cls) {
        if (!url) {
            return `<div class="svg-pane-empty ${cls || ''}">∅</div>`;
        }
        return `<img class="${cls || ''}" src="${url}" alt="" draggable="false">`;
    }

    function render(state) {
        const { view, beforeUrl, afterUrl, mode, status } = state;
        if (status === 'added') {
            view.innerHTML = `
                <div class="svg-stage svg-stage-single">
                    <div class="svg-pane">
                        <div class="svg-pane-label svg-pane-label-after">After (added)</div>
                        <div class="svg-canvas">${imgTag(afterUrl, 'svg-img-after')}</div>
                    </div>
                </div>`;
            return;
        }
        if (status === 'deleted') {
            view.innerHTML = `
                <div class="svg-stage svg-stage-single">
                    <div class="svg-pane">
                        <div class="svg-pane-label svg-pane-label-before">Before (deleted)</div>
                        <div class="svg-canvas">${imgTag(beforeUrl, 'svg-img-before')}</div>
                    </div>
                </div>`;
            return;
        }
        if (mode === '2up') {
            view.innerHTML = `
                <div class="svg-stage svg-stage-2up">
                    <div class="svg-pane">
                        <div class="svg-pane-label svg-pane-label-before">Before</div>
                        <div class="svg-canvas">${imgTag(beforeUrl, 'svg-img-before')}</div>
                    </div>
                    <div class="svg-pane">
                        <div class="svg-pane-label svg-pane-label-after">After</div>
                        <div class="svg-canvas">${imgTag(afterUrl, 'svg-img-after')}</div>
                    </div>
                </div>`;
            return;
        }
        if (mode === 'diff') {
            view.innerHTML = `
                <div class="svg-stage svg-stage-overlay svg-stage-diff">
                    <div class="svg-canvas">
                        ${imgTag(beforeUrl, 'svg-layer svg-layer-base')}
                        ${imgTag(afterUrl, 'svg-layer svg-layer-blend')}
                    </div>
                    <div class="svg-hint">Black = unchanged · color = delta</div>
                </div>`;
            return;
        }
        if (mode === 'onion') {
            view.innerHTML = `
                <div class="svg-stage svg-stage-overlay">
                    <div class="svg-canvas">
                        ${imgTag(beforeUrl, 'svg-layer svg-layer-base')}
                        ${imgTag(afterUrl, 'svg-layer svg-layer-top')}
                    </div>
                    <div class="svg-slider-row">
                        <span class="svg-slider-label">Before</span>
                        <input type="range" class="svg-slider" min="0" max="100" value="50">
                        <span class="svg-slider-label">After</span>
                    </div>
                </div>`;
            const top = view.querySelector('.svg-layer-top');
            const slider = view.querySelector('.svg-slider');
            const apply = () => {
                if (top) top.style.opacity = (slider.value / 100).toFixed(2);
            };
            slider.addEventListener('input', apply);
            apply();
        }
    }

    async function hydrate(el) {
        if (el.dataset.hydrated === '1') return;
        el.dataset.hydrated = '1';

        const file = el.dataset.file;
        const status = el.dataset.status || 'modified';
        const view = el.querySelector('.svg-view');
        const tabs = el.querySelectorAll('.svg-tab');
        const sourceToggle = el.querySelector('.svg-source-toggle');
        const sourceBox = el.querySelector('.svg-source');

        // Apply the session's current mode so newly-rendered blocks match.
        tabs.forEach(t => t.classList.toggle('is-active', t.dataset.mode === currentMode));

        // Wire source toggle.
        sourceToggle.addEventListener('click', () => {
            const open = sourceBox.hidden;
            sourceBox.hidden = !open;
            sourceToggle.textContent = open ? 'Hide source' : 'Show source';
            sourceToggle.classList.toggle('is-active', open);
        });

        // Fetch both blobs in parallel. 404 is fine — yields null pane.
        let beforeText = null, afterText = null;
        try {
            [beforeText, afterText] = await Promise.all([
                status === 'added' ? Promise.resolve(null) : fetchBlob(file, 'head'),
                status === 'deleted' ? Promise.resolve(null) : fetchBlob(file, 'worktree'),
            ]);
        } catch (e) {
            view.innerHTML = `<div class="svg-error">Failed to load: ${e}</div>`;
            return;
        }

        const state = {
            view,
            beforeUrl: blobUrl(beforeText),
            afterUrl: blobUrl(afterText),
            mode: el.querySelector('.svg-tab.is-active')?.dataset.mode || '2up',
            status,
        };
        ELS.set(el, state);

        tabs.forEach(t => {
            t.addEventListener('click', () => {
                currentMode = t.dataset.mode;
                // Sync every other svg-diff block on the page so they all switch together.
                document.querySelectorAll('.svg-diff').forEach(other => {
                    other.querySelectorAll('.svg-tab').forEach(o => {
                        o.classList.toggle('is-active', o.dataset.mode === currentMode);
                    });
                    const otherState = ELS.get(other);
                    if (otherState && otherState.mode !== currentMode) {
                        otherState.mode = currentMode;
                        render(otherState);
                    }
                });
            });
        });

        render(state);
    }

    // Hydrate visible blocks; observe the rest. Tied to current diff-pane DOM,
    // so re-runs after every applyContent() swap.
    let observer = null;
    function rescan() {
        if (observer) observer.disconnect();
        observer = new IntersectionObserver(entries => {
            entries.forEach(e => {
                if (e.isIntersecting) {
                    hydrate(e.target);
                    observer.unobserve(e.target);
                }
            });
        }, { rootMargin: '200px' });

        document.querySelectorAll('.svg-diff').forEach(el => {
            // If already in viewport, hydrate immediately.
            const rect = el.getBoundingClientRect();
            if (rect.top < window.innerHeight + 200 && rect.bottom > -200) {
                hydrate(el);
            } else {
                observer.observe(el);
            }
        });
    }

    // Re-scan on each content swap. app.js sets innerHTML on #diff-pane, so
    // the simplest hook is a MutationObserver on that container.
    function startWatching() {
        const pane = document.getElementById('diff-pane');
        if (!pane) { setTimeout(startWatching, 50); return; }
        rescan();
        new MutationObserver(() => rescan())
            .observe(pane, { childList: true, subtree: false });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startWatching);
    } else {
        startWatching();
    }
})();
