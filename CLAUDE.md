# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python HTTP server that renders a live diff viewer in the browser. It shows VCS changes (git or arc) as a GitHub-style stacked diff with a collapsible file tree sidebar. No dependencies beyond Python 3 stdlib.

## Running

```bash
python3 server.py
# Serves on http://localhost:8777
# Open http://localhost:8777?path=/path/to/repo
```

Query params: `path` (required, repo directory), `refresh` (poll interval in seconds, default 3), `focus` (comma-separated file indices to filter).

## Architecture

```
server.py        — HTTP handler, static file serving, entry point
vcs.py           — VCS detection (git/arc), diff retrieval, untracked files
diff_parser.py   — Unified diff text → per-file HTML table fragments
tree.py          — File tree building, collapsing single-child dirs, sidebar HTML
page.py          — Full HTML page assembly, SVG icons, template
static/style.css — All CSS (themes, layout, diff colors)
static/app.js    — All client JS (theme toggle, filtering, scroll-spy, polling, expand)
```

**Request flow:**
- `GET /` — renders diff HTML for the repo at `?path=`
- `GET /hash` — returns JSON hash of current diff (client polls this for auto-refresh)
- `GET /context` — returns JSON lines for expand-in-place
- `GET /static/*` — serves CSS/JS files

**How dynamic data reaches JS:** The HTML page includes a `<script>window.__DIFF_CONFIG__ = {...}</script>` blob with `diffHash`, `repoPath`, and `refreshSeconds`. The JS in `static/app.js` reads from `window.__DIFF_CONFIG__`.

**Theming:** CSS variables in `static/style.css` define dark (`:root`), light (`[data-theme="light"]`), and auto (`@media prefers-color-scheme` + `[data-theme="auto"]`). Theme state persisted in `localStorage` as `diff-theme`.
