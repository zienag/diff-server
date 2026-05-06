"""HTML page assembly for the diff viewer."""

import hashlib
import json
import os
from html import escape

from diff_parser import parse_and_render_diff
from tree import build_file_tree, render_tree_html, diff_bar_html, SVG_CHEVRON_DOWN

SVG_SEARCH = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M10.68 11.74a6 6 0 01-7.922-8.982 6 6 0 018.982 7.922l3.04 3.04a.749.749 0 01-.326 1.275.749.749 0 01-.734-.215l-3.04-3.04zM11.5 7a4.499 4.499 0 10-8.997 0A4.499 4.499 0 0011.5 7z" fill="currentColor"/></svg>'


def _svg_diff_shell(file_dict, source_html):
    """Wrap text diff with a lazy-hydrating SVG visual viewer.
    Status: added (no head) / deleted (no worktree) / modified."""
    if file_dict["additions"] > 0 and file_dict["deletions"] == 0:
        status = "added"
    elif file_dict["deletions"] > 0 and file_dict["additions"] == 0:
        status = "deleted"
    else:
        status = "modified"
    raw = escape(file_dict["raw_path"], quote=True)
    return (
        f'<div class="svg-diff" data-file="{raw}" data-status="{status}">'
        f'  <div class="svg-tabs">'
        f'    <button class="svg-tab is-active" data-mode="2up">Side-by-side</button>'
        f'    <button class="svg-tab" data-mode="diff">Difference</button>'
        f'    <button class="svg-tab" data-mode="onion">Overlay</button>'
        f'    <span class="svg-status svg-status-{status}">{status}</span>'
        f'    <span class="svg-spacer"></span>'
        f'    <button class="svg-source-toggle" type="button">Show source</button>'
        f'  </div>'
        f'  <div class="svg-view"><div class="svg-loading">Loading…</div></div>'
        f'  <div class="svg-source" hidden>{source_html}</div>'
        f'</div>'
    )


def _render_file_sections(files, idx_offset=0):
    """Render file diff sections, returning HTML and next index offset."""
    html = ""
    for local_i, f in enumerate(files):
        i = idx_offset + local_i
        fname = os.path.basename(f["path"])
        fdir = os.path.dirname(f["path"])
        adds = f["additions"]
        dels = f["deletions"]
        stat_text = ""
        if adds:
            stat_text += f'<span class="fs-add">+{adds}</span>'
        if dels:
            stat_text += f'<span class="fs-del">-{dels}</span>'
        bar = diff_bar_html(adds, dels)

        dir_part = f'<span class="fh-dir">{escape(fdir)}/</span>' if fdir else ''
        rename_part = ''
        if f.get("renamed_from"):
            rename_part = f'<span class="fh-rename"> \u2190 {escape(f["renamed_from"])}</span>'
        escaped_path = escape(f["path"], quote=True)
        body_html = _svg_diff_shell(f, f["html"]) if f.get("is_svg") else f["html"]
        html += f'''
        <div class="file-section" id="file-{i}">
            <div class="file-header" onclick="toggleFile({i})">
                <span class="fh-chev" id="chev-{i}">{SVG_CHEVRON_DOWN}</span>
                {dir_part}<span class="fh-name">{escape(fname)}</span>{rename_part}
                <button class="fh-copy" data-path="{escaped_path}" onclick="copyPath(this,event)" title="Copy path">
                    <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 010 1.5h-1.5a.25.25 0 00-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 00.25-.25v-1.5a.75.75 0 011.5 0v1.5A1.75 1.75 0 019.25 16h-7.5A1.75 1.75 0 010 14.25zM5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0114.25 11h-7.5A1.75 1.75 0 015 9.25zm1.75-.25a.25.25 0 00-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 00.25-.25v-7.5a.25.25 0 00-.25-.25z"/></svg>
                </button>
                <span class="fh-stats">{stat_text} {bar}</span>
            </div>
            <div class="file-body" id="body-{i}">{body_html}</div>
        </div>'''
    return html


def make_content(vcs, root, staged_diff, unstaged_diff, path):
    """Build diff content and return a dict for JSON response."""
    staged_files, staged_add, staged_del = parse_and_render_diff(staged_diff, path, root) if staged_diff else ([], 0, 0)
    unstaged_files, unstaged_add, unstaged_del = parse_and_render_diff(unstaged_diff, path, root) if unstaged_diff else ([], 0, 0)

    file_count = len(staged_files) + len(unstaged_files)
    total_add = staged_add + unstaged_add
    total_del = staged_del + unstaged_del
    diff_hash = hashlib.md5((staged_diff + unstaged_diff).encode()).hexdigest()

    has_both = bool(staged_files) and bool(unstaged_files)
    tree_html = ""
    if staged_files:
        staged_tree = build_file_tree(staged_files)
        if has_both:
            tree_html += f'<div class="tree-section-label tree-section-staged">Staged <span class="tree-section-count">{len(staged_files)}</span></div>'
        tree_html += render_tree_html(staged_tree)
    if unstaged_files:
        unstaged_tree = build_file_tree(unstaged_files, idx_offset=len(staged_files))
        if has_both:
            tree_html += f'<div class="tree-section-label tree-section-unstaged">Modified <span class="tree-section-count">{len(unstaged_files)}</span></div>'
        tree_html += render_tree_html(unstaged_tree)

    diff_content = ""
    if staged_files:
        diff_content += f'<div class="section-header section-staged">Staged <span class="section-count">{len(staged_files)}</span></div>'
        diff_content += _render_file_sections(staged_files, idx_offset=0)
    if unstaged_files:
        label = "Modified" if staged_files else "Changes"
        diff_content += f'<div class="section-header section-unstaged">{label} <span class="section-count">{len(unstaged_files)}</span></div>'
        diff_content += _render_file_sections(unstaged_files, idx_offset=len(staged_files))

    total_bar = diff_bar_html(total_add, total_del)

    if file_count == 0:
        diff_content = (
            "<div class='empty-state'>"
            "<svg viewBox='0 0 24 24' width='36' height='36' fill='none' stroke='currentColor' stroke-width='1.5'>"
            "<polyline points='20 6 9 17 4 12'/></svg>"
            "<span class='empty-text'>Clean</span></div>"
        )

    summary_html = (
        f'<b>{file_count}</b> file{"s" if file_count != 1 else ""}'
        f' <span class="stat-add">+{total_add}</span>'
        f' <span class="stat-del">&minus;{total_del}</span>'
        f' {total_bar}'
    )

    return {
        "diffHash": diff_hash,
        "fileCount": file_count,
        "summaryHtml": summary_html,
        "treeHtml": tree_html,
        "diffHtml": diff_content,
    }


def make_shell_html(vcs, path, refresh_seconds):
    short = os.path.basename(path.rstrip("/")) or path

    config_json = json.dumps({
        "diffHash": None,
        "repoPath": path,
        "refreshSeconds": refresh_seconds,
    })

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>\u0394 {short}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anybody:wght@500;700&family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
<script>document.documentElement.setAttribute('data-theme',localStorage.getItem('diff-theme')||'auto')</script>
</head>
<body>
<div class="layout">
    <div class="toolbar">
        <span class="tb-vcs">{vcs or "?"}</span>
        <span class="tb-summary" id="tb-summary">
            <span class="loading-text">Loading\u2026</span>
        </span>
        <span style="flex:1"></span>
        <button class="show-all-btn" id="show-all-btn" style="display:none" onclick="showAll()">Show all</button>
        <button class="auto-btn" id="auto-btn" onclick="toggleAuto()" title="Toggle auto-refresh">
            <svg id="auto-icon-on" viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M4 3h3v10H4zM9 3h3v10H9z"/></svg>
            <svg id="auto-icon-off" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M4 2.75v10.5a.75.75 0 001.126.648l9-5.25a.75.75 0 000-1.296l-9-5.25A.75.75 0 004 2.75z"/></svg>
        </button>
        <button class="refresh-btn" id="refresh-btn" onclick="manualRefresh()" title="Refresh now" style="display:none">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M8 2.5a5.487 5.487 0 00-4.131 1.869l1.204 1.204A.25.25 0 014.896 6H1.25A.25.25 0 011 5.75V2.104a.25.25 0 01.427-.177l1.38 1.38A7.001 7.001 0 0114.95 7.16a.75.75 0 11-1.49.178A5.501 5.501 0 008 2.5zm-6.47 6.15a.75.75 0 01.84.66 5.501 5.501 0 009.76 2.424l-1.204-1.204a.25.25 0 01.177-.427h3.647a.25.25 0 01.25.25v3.646a.25.25 0 01-.427.177l-1.38-1.38A7.001 7.001 0 011.05 9.49a.75.75 0 01.66-.84z"/></svg>
        </button>
        <button class="wrap-btn" id="wrap-btn" onclick="toggleWrap()" title="Toggle word wrap">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M1.75 3h12.5a.75.75 0 010 1.5H1.75a.75.75 0 010-1.5zm0 8h4.5a.75.75 0 010 1.5h-4.5a.75.75 0 010-1.5zm0-4h9.862a2.39 2.39 0 01-.262 4.77h-1.1l.56-.56a.75.75 0 10-1.06-1.06l-1.82 1.82a.75.75 0 000 1.06l1.82 1.82a.75.75 0 001.06-1.06l-.56-.56H11.35a3.89 3.89 0 00.412-7.77H1.75a.75.75 0 010-1.5z"/></svg>
        </button>
        <button class="theme-btn" id="theme-btn" onclick="cycleTheme()" title="Toggle theme">
            <svg id="theme-icon-dark" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M6.2 1.2a.75.75 0 00-1.06.04 7 7 0 109.58 1.34.75.75 0 00-1.04-.22 5.5 5.5 0 01-7.52-1.2z"/></svg>
            <svg id="theme-icon-light" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M8 1.5a.75.75 0 01.75.75v1a.75.75 0 01-1.5 0v-1A.75.75 0 018 1.5zm0 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm5.66-5.16a.75.75 0 010 1.06l-.7.71a.75.75 0 11-1.07-1.06l.71-.71a.75.75 0 011.06 0zM14.5 8a.75.75 0 01-.75.75h-1a.75.75 0 010-1.5h1A.75.75 0 0114.5 8zm-2.84 5.66a.75.75 0 01-1.06 0l-.71-.7a.75.75 0 111.06-1.07l.71.71a.75.75 0 010 1.06zM8 14.5a.75.75 0 01-.75-.75v-1a.75.75 0 011.5 0v1A.75.75 0 018 14.5zm-5.66-2.84a.75.75 0 010-1.06l.7-.71a.75.75 0 111.07 1.06l-.71.71a.75.75 0 01-1.06 0zM1.5 8a.75.75 0 01.75-.75h1a.75.75 0 010 1.5h-1A.75.75 0 011.5 8zm2.84-5.66a.75.75 0 011.06 0l.71.7a.75.75 0 11-1.06 1.07l-.71-.71a.75.75 0 010-1.06z"/></svg>
            <svg id="theme-icon-auto" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M8 1a7 7 0 100 14A7 7 0 008 1zM3 8a5 5 0 015-5v10a5 5 0 01-5-5z"/></svg>
        </button>
        <span class="tb-activity-label" id="activity-label"></span>
        <span class="tb-dot" id="refresh-dot" title="Idle"></span>
    </div>
    <div class="main">
        <div class="sidebar" id="sidebar">
            <div class="sb-header">
                <span class="sb-label">Changes</span>
                <span class="sb-count" id="sb-count">&hellip;</span>
            </div>
            <div class="sb-filter">
                <span class="sb-filter-icon">{SVG_SEARCH}</span>
                <input id="filter" type="text" placeholder="Filter\u2026" oninput="filterTree(this.value)" spellcheck="false">
            </div>
            <div class="tree-scroll" id="tree"></div>
        </div>
        <div class="resize-handle" id="resize-handle"></div>
        <div class="diff-pane" id="diff-pane">
            <div class="empty-state"><span class="loading-text">Loading\u2026</span></div>
        </div>
    </div>
</div>

<script>window.__DIFF_CONFIG__ = {config_json};</script>
<script src="/static/app.js"></script>
<script src="/static/svg-diff.js"></script>
</body>
</html>"""
