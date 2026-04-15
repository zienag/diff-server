# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local-only Python HTTP server that renders a live diff viewer in the browser, designed to be used side-by-side with Claude Code agents in a cmux terminal. It shows VCS changes as a GitHub-style stacked diff with a collapsible file tree sidebar. No dependencies beyond Python 3 stdlib.

**Primary use case:** Open this in a cmux browser pane next to your terminal so you can see the agent's changes in real-time. Use `cmux browser open` to open the diff viewer in the integrated browser if running inside cmux.

**Trust model:** This is a personal dev tool that binds to `127.0.0.1`. The user has full filesystem access — there is no security boundary. Endpoints intentionally read arbitrary paths the user provides via `?path=`.

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
vcs/             — VCS backend package
  __init__.py    — Facade: detect_vcs(), get_diff(), get_diff_fingerprint()
  base.py        — Shared utilities: run_cmd, collect_diff, parse_untracked_files
  git.py         — Git backend (index mtime optimization)
  mono.py        — Monorepo VCS backend (info hash, xattr counter, adaptive penalty)
diff_parser.py   — Unified diff text → per-file HTML table fragments
tree.py          — File tree building, collapsing single-child dirs, sidebar HTML
page.py          — Full HTML page assembly, SVG icons, template
static/style.css — All CSS (themes, layout, diff colors)
static/app.js    — All client JS (theme toggle, filtering, scroll-spy, polling, expand)
```

**VCS backends:** Each backend is a standalone class (no inheritance) that implements `has_root_marker()`, `detect_fallback()`, `fingerprint()`, and `get_diff()`. Shared logic lives as free functions in `vcs/base.py` — backends opt in via composition. Detection walks up from the given path, checking all backends at each directory level (closest root wins). Backends are cached per path.

- **Git backend** uses `.git/index` mtime to skip expensive status scans between polls.
- **Mono backend** has three optimization levels: xattr mount counter (O(1), VFS only), VCS info hash (cheap subprocess), and adaptive penalty (scales poll interval based on how long status takes).

**Request flow:**
- `GET /` — renders diff HTML for the repo at `?path=`
- `GET /hash` — returns JSON hash of current diff (client polls this for auto-refresh)
- `GET /context` — returns JSON lines for expand-in-place
- `GET /static/*` — serves CSS/JS files

**How dynamic data reaches JS:** The HTML page includes a `<script>window.__DIFF_CONFIG__ = {...}</script>` blob with `diffHash`, `repoPath`, and `refreshSeconds`. The JS in `static/app.js` reads from `window.__DIFF_CONFIG__`.

**Theming:** CSS variables in `static/style.css` define dark (`:root`), light (`[data-theme="light"]`), and auto (`@media prefers-color-scheme` + `[data-theme="auto"]`). Theme state persisted in `localStorage` as `diff-theme`.

**Threading:** The server uses `ThreadingMixIn` (real OS threads, one per request). This is fine for a local tool with a handful of tabs, but doesn't scale. Python stdlib has no async HTTP server — switching to async would require `aiohttp` (external dep) or a raw `asyncio.Protocol`, plus rewriting VCS subprocess calls to use `asyncio.create_subprocess_exec`.
