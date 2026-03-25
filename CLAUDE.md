# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python HTTP server that renders a live diff viewer in the browser. It shows VCS changes (git or arc) as a GitHub-style stacked diff with a collapsible file tree sidebar. No dependencies beyond Python 3 stdlib.

## Running

```bash
python3 server.py
# Serves on http://localhost:8777
# Open http://localhost:8777?path=/path/to/repo
```

Query params: `path` (required, repo directory), `refresh` (poll interval in seconds, default 3), `focus` (comma-separated file indices to filter).

## Architecture

Everything is in `server.py` (~990 lines). The server generates a complete HTML page per request with inline CSS/JS — no build step, no static files, no templates.

**Request flow:**
- `GET /` — renders diff HTML for the repo at `?path=`
- `GET /hash` — returns JSON hash of current diff (used by client polling for auto-refresh)
- `GET /context` — returns JSON lines for expand-in-place (clicking "Show lines N–M" in the diff)

**Key functions:**
- `detect_vcs(path)` — walks up to find `.arc` or `.git`, returns VCS type and root
- `get_diff(path)` — runs `git diff` or `arc diff`, appends synthetic diffs for untracked files
- `parse_and_render_diff()` — parses unified diff format into per-file HTML tables with line numbers
- `build_file_tree()` / `collapse_single_dirs()` — builds sidebar tree, collapses single-child directories
- `make_html()` — assembles the full HTML page with embedded CSS and JS

**Client-side JS** (embedded in the HTML template inside `make_html()`):
- Polls `/hash` endpoint and reloads on change
- Sidebar file/directory click filtering with URL state (`?focus=`)
- Scroll-spy highlights active file in sidebar
- Resizable sidebar via drag handle
- Expand-in-place fetches context lines from `/context` endpoint
