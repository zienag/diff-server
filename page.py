"""HTML page assembly for the diff viewer."""

import hashlib
import json
import os
from html import escape

from diff_parser import parse_and_render_diff
from tree import build_file_tree, render_tree_html, diff_bar_html, SVG_CHEVRON_DOWN

SVG_SEARCH = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M10.68 11.74a6 6 0 01-7.922-8.982 6 6 0 018.982 7.922l3.04 3.04a.749.749 0 01-.326 1.275.749.749 0 01-.734-.215l-3.04-3.04zM11.5 7a4.499 4.499 0 10-8.997 0A4.499 4.499 0 0011.5 7z" fill="currentColor"/></svg>'


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
        html += f'''
        <div class="file-section" id="file-{i}">
            <div class="file-header" onclick="toggleFile({i})">
                <span class="fh-chev" id="chev-{i}">{SVG_CHEVRON_DOWN}</span>
                {dir_part}<span class="fh-name">{escape(fname)}</span>
                <span class="fh-stats">{stat_text} {bar}</span>
            </div>
            <div class="file-body" id="body-{i}">{f["html"]}</div>
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
        <button class="wrap-btn" id="wrap-btn" onclick="toggleWrap()" title="Toggle word wrap">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M1.75 3h12.5a.75.75 0 010 1.5H1.75a.75.75 0 010-1.5zm0 8h4.5a.75.75 0 010 1.5h-4.5a.75.75 0 010-1.5zm0-4h9.862a2.39 2.39 0 01-.262 4.77h-1.1l.56-.56a.75.75 0 10-1.06-1.06l-1.82 1.82a.75.75 0 000 1.06l1.82 1.82a.75.75 0 001.06-1.06l-.56-.56H11.35a3.89 3.89 0 00.412-7.77H1.75a.75.75 0 010-1.5z"/></svg>
        </button>
        <button class="theme-btn" id="theme-btn" onclick="cycleTheme()" title="Toggle theme">
            <svg id="theme-icon-dark" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M6.2 1.2a.75.75 0 00-1.06.04 7 7 0 109.58 1.34.75.75 0 00-1.04-.22 5.5 5.5 0 01-7.52-1.2z"/></svg>
            <svg id="theme-icon-light" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M8 1.5a.75.75 0 01.75.75v1a.75.75 0 01-1.5 0v-1A.75.75 0 018 1.5zm0 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm5.66-5.16a.75.75 0 010 1.06l-.7.71a.75.75 0 11-1.07-1.06l.71-.71a.75.75 0 011.06 0zM14.5 8a.75.75 0 01-.75.75h-1a.75.75 0 010-1.5h1A.75.75 0 0114.5 8zm-2.84 5.66a.75.75 0 01-1.06 0l-.71-.7a.75.75 0 111.06-1.07l.71.71a.75.75 0 010 1.06zM8 14.5a.75.75 0 01-.75-.75v-1a.75.75 0 011.5 0v1A.75.75 0 018 14.5zm-5.66-2.84a.75.75 0 010-1.06l.7-.71a.75.75 0 111.07 1.06l-.71.71a.75.75 0 01-1.06 0zM1.5 8a.75.75 0 01.75-.75h1a.75.75 0 010 1.5h-1A.75.75 0 011.5 8zm2.84-5.66a.75.75 0 011.06 0l.71.7a.75.75 0 11-1.06 1.07l-.71-.71a.75.75 0 010-1.06z"/></svg>
            <svg id="theme-icon-auto" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" style="display:none"><path d="M8 1a7 7 0 100 14A7 7 0 008 1zM3 8a5 5 0 015-5v10a5 5 0 01-5-5z"/></svg>
        </button>
        <span class="tb-dot" id="refresh-dot"></span>
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
</body>
</html>"""
