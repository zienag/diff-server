#!/usr/bin/env python3
"""Live diff server — file tree + stacked diff view."""

import json
import os
import re
import subprocess
import urllib.parse
from html import escape
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8777

# --- SVG icons (inline, 16x16) ---

SVG_CHEVRON_DOWN = '<svg class="chev-svg" viewBox="4 7 16 10" width="14" height="9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>'
SVG_CHEVRON_RIGHT = '<svg class="chev-svg" viewBox="0 0 16 16" width="10" height="10"><path d="M6.427 4.427l3.396 3.396a.25.25 0 010 .354l-3.396 3.396A.25.25 0 016 11.396V4.604a.25.25 0 01.427-.177z" fill="currentColor"/></svg>'
SVG_DIR = '<svg class="icon-svg" viewBox="0 0 16 16" width="11" height="11"><path d="M1.75 1A1.75 1.75 0 000 2.75v10.5C0 14.216.784 15 1.75 15h12.5A1.75 1.75 0 0016 13.25v-8.5A1.75 1.75 0 0014.25 3H7.5a.25.25 0 01-.2-.1l-.9-1.2C6.07 1.26 5.55 1 5 1H1.75z" fill="currentColor"/></svg>'
SVG_FILE = '<svg class="icon-svg" viewBox="0 0 16 16" width="11" height="11"><path d="M2 1.75C2 .784 2.784 0 3.75 0h6.586c.464 0 .909.184 1.237.513l2.914 2.914c.329.328.513.773.513 1.237v9.586A1.75 1.75 0 0113.25 16h-9.5A1.75 1.75 0 012 14.25V1.75zm1.75-.25a.25.25 0 00-.25.25v12.5c0 .138.112.25.25.25h9.5a.25.25 0 00.25-.25V6h-2.75A1.75 1.75 0 019 4.25V1.5H3.75zm6.75.062V4.25c0 .138.112.25.25.25h2.688l-.011-.013-2.914-2.914-.013-.011z" fill="currentColor"/></svg>'
SVG_SEARCH = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M10.68 11.74a6 6 0 01-7.922-8.982 6 6 0 018.982 7.922l3.04 3.04a.749.749 0 01-.326 1.275.749.749 0 01-.734-.215l-3.04-3.04zM11.5 7a4.499 4.499 0 10-8.997 0A4.499 4.499 0 0011.5 7z" fill="currentColor"/></svg>'


def detect_vcs(path):
    current = path
    while current != "/":
        if os.path.isdir(os.path.join(current, ".arc")):
            return "arc", current
        if os.path.isdir(os.path.join(current, ".git")):
            return "git", current
        current = os.path.dirname(current)
    return None, None


def get_untracked_files(vcs, path):
    """Get list of untracked file paths (relative to cwd)."""
    try:
        if vcs == "arc":
            result = subprocess.run(
                ["arc", "status", "--short"], cwd=path,
                capture_output=True, text=True, timeout=10,
            )
        else:
            result = subprocess.run(
                ["git", "status", "--porcelain"], cwd=path,
                capture_output=True, text=True, timeout=10,
            )
        files = []
        for line in result.stdout.splitlines():
            if line.startswith("??"):
                fpath = line[3:].strip()
                full = os.path.join(path, fpath)
                if os.path.isfile(full):
                    files.append((fpath, full))
                elif os.path.isdir(full):
                    for dirpath, _, filenames in os.walk(full):
                        for fn in filenames:
                            fp = os.path.join(dirpath, fn)
                            files.append((os.path.relpath(fp, path), fp))
        return files
    except Exception:
        return []


def make_untracked_diff(files, cwd, root):
    """Generate unified diff text for untracked (new) files."""
    diff = ""
    for relpath, fullpath in files:
        try:
            with open(fullpath, "r", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        # Convert cwd-relative path to root-relative for consistency with tracked diffs
        abs_path = os.path.join(cwd, relpath)
        root_rel = os.path.relpath(abs_path, root)
        n = len(lines)
        diff += f"--- /dev/null\n"
        diff += f"+++ {root_rel}\n"
        diff += f"@@ -0,0 +1,{n} @@\n"
        for line in lines:
            diff += "+" + line.rstrip("\n") + "\n"
    return diff


def get_diff(path):
    vcs, root = detect_vcs(path)
    if not vcs:
        return None, None, ""
    try:
        result = subprocess.run(
            [vcs, "diff"], cwd=path, capture_output=True, text=True, timeout=10
        )
        diff_text = result.stdout
        untracked = get_untracked_files(vcs, path)
        if untracked:
            diff_text += make_untracked_diff(untracked, path, root)
        return vcs, root, diff_text
    except Exception:
        return vcs, root, ""


def make_relative(filepath, cwd, root):
    abs_path = os.path.join(root, filepath)
    try:
        return os.path.relpath(abs_path, cwd)
    except ValueError:
        return filepath


def parse_and_render_diff(diff_text, cwd, root):
    files = []
    current = None
    total_add = 0
    total_del = 0

    for line in diff_text.splitlines():
        if line.startswith("diff ") or line.startswith("--- "):
            if current:
                current["html"] += "</table></div>"
                files.append(current)
                current = None
        if line.startswith("+++ "):
            raw = line[4:]
            if raw.startswith("b/"):
                raw = raw[2:]
            raw = raw.split("\t")[0].strip()
            if raw and raw != "/dev/null":
                rel = make_relative(raw, cwd, root)
                current = {"path": rel, "raw_path": raw, "additions": 0, "deletions": 0, "html": ""}
                current["html"] = '<div class="file-diff"><table>'
                current["_first_hunk"] = True
        elif line.startswith("@@") and current:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
            if m:
                old_start = int(m.group(1))
                new_start = int(m.group(2))
                context = escape(m.group(3))
                raw_path_js = current["raw_path"].replace("'", "\\'")

                # Expand-up row: show lines above this hunk
                if current.get("_first_hunk"):
                    # Before first hunk — expand from line 1 to hunk start
                    if new_start > 1:
                        current["html"] += (
                            f'<tr class="expand-row" data-file=\'{raw_path_js}\' data-start="1" data-end="{new_start - 1}">'
                            f'<td class="ln"></td>'
                            f'<td class="expand-cell" onclick="expandLines(this)">'
                            f'\u2191 Show lines 1\u2013{new_start - 1}</td></tr>'
                        )
                    current["_first_hunk"] = False
                else:
                    # Between hunks — expand from last line to this hunk
                    prev_end = current.get("_last_new_line", 0)
                    if new_start - prev_end > 1:
                        current["html"] += (
                            f'<tr class="expand-row" data-file=\'{raw_path_js}\' data-start="{prev_end + 1}" data-end="{new_start - 1}">'
                            f'<td class="ln"></td>'
                            f'<td class="expand-cell" onclick="expandLines(this)">'
                            f'\u2195 Show lines {prev_end + 1}\u2013{new_start - 1}</td></tr>'
                        )

                current["html"] += (
                    f'<tr class="hunk-header">'
                    f'<td class="ln"></td>'
                    f'<td class="hunk-code">'
                    f'<span class="hunk-range">@@ -{m.group(1)} +{m.group(2)} @@</span>'
                    f'<span class="hunk-ctx">{context}</span></td></tr>'
                )
                current["_old"] = old_start
                current["_new"] = new_start
        elif current and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            if line.startswith("+"):
                cls = "add"
                old_ln = ""
                new_ln = str(current.get("_new", ""))
                current["_new"] = current.get("_new", 0) + 1
                current["additions"] += 1
                total_add += 1
            elif line.startswith("-"):
                cls = "del"
                old_ln = str(current.get("_old", ""))
                new_ln = ""
                current["_old"] = current.get("_old", 0) + 1
                current["deletions"] += 1
                total_del += 1
            else:
                cls = "ctx"
                old_ln = str(current.get("_old", ""))
                new_ln = str(current.get("_new", ""))
                current["_old"] = current.get("_old", 0) + 1
                current["_new"] = current.get("_new", 0) + 1

            content = escape(line[1:]) if len(line) > 1 else ""
            ln = new_ln if cls != "del" else old_ln
            current["html"] += (
                f'<tr class="line {cls}">'
                f'<td class="ln">{ln}</td>'
                f'<td class="code">{content}</td>'
                f'</tr>'
            )
            # Track last new-file line for expand-between-hunks
            if cls != "del":
                current["_last_new_line"] = current.get("_new", 1) - 1

    if current:
        # Expand-down after last hunk
        raw_path_js = current["raw_path"].replace("'", "\\'")
        last_ln = current.get("_last_new_line", 0)
        current["html"] += (
            f'<tr class="expand-row" data-file=\'{raw_path_js}\' data-start="{last_ln + 1}" data-end="0">'
            f'<td class="ln"></td>'
            f'<td class="expand-cell" onclick="expandLines(this)">'
            f'\u2193 Show more</td></tr>'
        )
        current["html"] += "</table></div>"
        files.append(current)

    return files, total_add, total_del


def build_file_tree(files):
    tree = {}
    for i, f in enumerate(files):
        parts = f["path"].split("/")
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            node = node[part]
        node[parts[-1]] = {"_idx": i, "_file": f}
    return collapse_single_dirs(tree)


def collapse_single_dirs(tree):
    collapsed = {}
    for key, value in tree.items():
        if "_idx" in value:
            collapsed[key] = value
            continue
        subtree = collapse_single_dirs(value)
        while True:
            child_dirs = [k for k, v in subtree.items() if "_idx" not in v]
            child_files = [k for k, v in subtree.items() if "_idx" in v]
            if len(child_dirs) == 1 and len(child_files) == 0:
                child_name = child_dirs[0]
                key = key + "/" + child_name
                subtree = subtree[child_name]
            else:
                break
        collapsed[key] = subtree
    return collapsed


def diff_bar_html(additions, deletions):
    """Render GitHub-style colored diff bar: 5 small squares."""
    total = additions + deletions
    if total == 0:
        return ""
    blocks = 5
    add_blocks = round(additions / total * blocks) if total else 0
    del_blocks = blocks - add_blocks
    squares = ""
    for _ in range(add_blocks):
        squares += '<span class="bar-block bar-add"></span>'
    for _ in range(del_blocks):
        squares += '<span class="bar-block bar-del"></span>'
    return f'<span class="diff-bar">{squares}</span>'


def collect_indices(tree):
    """Collect all file indices under a tree node."""
    indices = []
    for key, value in tree.items():
        if "_idx" in value:
            indices.append(value["_idx"])
        else:
            indices.extend(collect_indices(value))
    return sorted(indices)


def render_tree_html(tree, depth=0):
    html = ""
    dirs = sorted([k for k, v in tree.items() if "_idx" not in v])
    leaf_files = sorted([k for k, v in tree.items() if "_idx" in v])

    for d in dirs:
        subtree = tree[d]
        child_html = render_tree_html(subtree, depth + 1)
        dir_indices = json.dumps(collect_indices(subtree))
        pad = 4 + depth * 8
        html += (
            f'<div class="tree-dir">'
            f'<div class="tree-dir-name" style="padding-left:{pad}px">'
            f'<span class="tree-chev" onclick="event.stopPropagation();this.closest(\'.tree-dir\').classList.toggle(\'closed\')">{SVG_CHEVRON_DOWN}</span>'
            f'<span class="tree-label" onclick="filterToFiles({dir_indices})">{escape(d)}</span></div>'
            f'<div class="tree-children">{child_html}</div>'
            f'</div>'
        )

    for fname in leaf_files:
        info = tree[fname]
        idx = info["_idx"]
        f = info["_file"]
        stats = ""
        if f["additions"]:
            stats += f'<span class="ts-add">+{f["additions"]}</span>'
        if f["deletions"]:
            stats += f'<span class="ts-del">-{f["deletions"]}</span>'
        pad = 4 + depth * 8
        html += (
            f'<a class="tree-file" style="padding-left:{pad + 12}px" '
            f'href="#file-{idx}" onclick="filterToFiles([{idx}]);return false" data-idx="{idx}">'
            f''
            f'<span class="tree-label">{escape(fname)}</span>'
            f'<span class="tree-file-stats">{stats}</span>'
            f'</a>'
        )
    return html


def make_html(vcs, root, diff_text, path, refresh_seconds):
    files, total_add, total_del = parse_and_render_diff(diff_text, path, root)
    file_count = len(files)
    diff_hash = hash(diff_text)

    file_tree = build_file_tree(files)
    tree_html = render_tree_html(file_tree)

    file_sections = ""
    for i, f in enumerate(files):
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
        file_sections += f'''
        <div class="file-section" id="file-{i}">
            <div class="file-header" onclick="toggleFile({i})">
                <span class="fh-chev" id="chev-{i}">{SVG_CHEVRON_DOWN}</span>
                {dir_part}<span class="fh-name">{escape(fname)}</span>
                <span class="fh-stats">{stat_text} {bar}</span>
            </div>
            <div class="file-body" id="body-{i}">{f["html"]}</div>
        </div>'''

    total_bar = diff_bar_html(total_add, total_del)
    short = os.path.basename(path.rstrip("/")) or path

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>\u0394 {short}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anybody:wght@500;700&family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
    --bg: #111113;
    --bg-raised: #19191d;
    --bg-surface: #1e1e23;
    --bg-hover: #252530;
    --bg-inset: #0c0c0e;
    --border: #2a2a35;
    --border-subtle: #1f1f28;
    --fg: #ececf1;
    --fg-secondary: #a1a1b5;
    --fg-tertiary: #62627a;
    --accent: #c4a0ff;
    --accent-dim: #c4a0ff22;
    --green: #4ae08a;
    --green-fg: #b8f5d0;
    --green-bg: #0f2818;
    --green-ln: #0a3318;
    --green-border: #1a4a28;
    --red: #ff6b6b;
    --red-fg: #ffb3b3;
    --red-bg: #2d1215;
    --red-ln: #3a1418;
    --red-border: #4a1a1e;
    --amber: #f5a623;
    --green-hover: #143520;
    --red-hover: #381518;
    --mono: "IBM Plex Mono", "SF Mono", Menlo, monospace;
    --sans: "DM Sans", -apple-system, sans-serif;
    --display: "Anybody", sans-serif;
    --radius: 8px;
    --radius-sm: 5px;
}}
[data-theme="light"] {{
    --bg: #f8f8fa;
    --bg-raised: #ffffff;
    --bg-surface: #f0f0f4;
    --bg-hover: #e8e8ee;
    --bg-inset: #eeeef2;
    --border: #d4d4dc;
    --border-subtle: #e2e2ea;
    --fg: #1a1a2e;
    --fg-secondary: #555570;
    --fg-tertiary: #8888a0;
    --accent: #7c3aed;
    --accent-dim: #7c3aed18;
    --green: #1a7f37;
    --green-fg: #1a5c2a;
    --green-bg: #dafbe1;
    --green-ln: #ccffd8;
    --green-border: #a7f3bc;
    --red: #cf222e;
    --red-fg: #82071e;
    --red-bg: #ffebe9;
    --red-ln: #ffd7d5;
    --red-border: #ffb8b8;
    --amber: #bf8700;
    --green-hover: #c2eecb;
    --red-hover: #fdd8d5;
}}
@media (prefers-color-scheme: light) {{
    [data-theme="auto"] {{
        --bg: #f8f8fa;
        --bg-raised: #ffffff;
        --bg-surface: #f0f0f4;
        --bg-hover: #e8e8ee;
        --bg-inset: #eeeef2;
        --border: #d4d4dc;
        --border-subtle: #e2e2ea;
        --fg: #1a1a2e;
        --fg-secondary: #555570;
        --fg-tertiary: #8888a0;
        --accent: #7c3aed;
        --accent-dim: #7c3aed18;
        --green: #1a7f37;
        --green-fg: #1a5c2a;
        --green-bg: #dafbe1;
        --green-ln: #ccffd8;
        --green-border: #a7f3bc;
        --red: #cf222e;
        --red-fg: #82071e;
        --red-bg: #ffebe9;
        --red-ln: #ffd7d5;
        --red-border: #ffb8b8;
        --amber: #bf8700;
        --green-hover: #c2eecb;
        --red-hover: #fdd8d5;
    }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{
    height: 100%; background: var(--bg); color: var(--fg);
    font-family: var(--sans); font-size: 13px;
    overflow: hidden; -webkit-font-smoothing: antialiased;
}}

.layout {{ display: flex; height: 100vh; flex-direction: column; }}
.main {{ display: flex; flex: 1; min-height: 0; }}

/* ===== Toolbar ===== */
.toolbar {{
    height: 40px; min-height: 40px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center;
    padding: 0 16px; gap: 12px;
    z-index: 100;
}}
.tb-vcs {{
    font-family: var(--display); font-size: 11px; font-weight: 700;
    padding: 3px 10px; border-radius: 3px;
    background: var(--accent-dim); color: var(--accent);
    letter-spacing: 1px; text-transform: uppercase;
}}
.tb-summary {{
    font-size: 12px; color: var(--fg-secondary);
    display: flex; align-items: center; gap: 8px;
    font-weight: 500;
}}
.tb-summary b {{ color: var(--fg); font-weight: 600; }}
.tb-dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--fg-tertiary);
    transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}}
.tb-dot.active {{
    background: var(--green);
    box-shadow: 0 0 8px var(--green), 0 0 2px var(--green);
}}

.show-all-btn {{
    margin-left: auto;
    padding: 3px 10px;
    background: var(--accent-dim); color: var(--accent);
    border: 1px solid var(--accent); border-radius: var(--radius-sm);
    font-family: var(--sans); font-size: 11px; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease;
}}
.show-all-btn:hover {{ background: var(--accent); color: var(--bg); }}

/* ===== Diff bar ===== */
.diff-bar {{ display: inline-flex; gap: 2px; margin-left: 6px; vertical-align: middle; }}
.bar-block {{ width: 8px; height: 8px; border-radius: 2px; }}
.bar-add {{ background: var(--green); }}
.bar-del {{ background: var(--red); }}

/* ===== Sidebar ===== */
.sidebar {{
    width: 260px; min-width: 160px; max-width: 450px;
    background: var(--bg);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column;
    overflow: hidden; flex-shrink: 0;
}}
.sb-header {{
    padding: 6px 8px 4px;
    display: flex; align-items: center; gap: 6px;
}}
.sb-label {{
    font-family: var(--display); font-size: 10px; font-weight: 700;
    color: var(--fg-tertiary); text-transform: uppercase;
    letter-spacing: 1px;
}}
.sb-count {{
    font-size: 9px; color: var(--accent);
    background: var(--accent-dim);
    padding: 0 5px; border-radius: 3px;
    font-weight: 600; font-family: var(--mono);
    line-height: 16px;
}}
.sb-filter {{
    position: relative; margin: 0 4px 4px;
}}
.sb-filter-icon {{
    position: absolute; left: 6px; top: 50%; transform: translateY(-50%);
    color: var(--fg-tertiary); display: flex;
}}
.sb-filter input {{
    width: 100%;
    padding: 3px 6px 3px 22px;
    background: var(--bg-inset);
    border: 1px solid var(--border-subtle);
    border-radius: 4px;
    color: var(--fg);
    font-size: 11px; font-family: var(--sans); font-weight: 500;
    outline: none;
    transition: all 0.2s ease;
}}
.sb-filter input:focus {{
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-dim);
    background: var(--bg);
}}
.sb-filter input::placeholder {{ color: var(--fg-tertiary); }}

.tree-scroll {{
    flex: 1; overflow-y: auto; padding: 1px 0 8px;
}}
.tree-scroll::-webkit-scrollbar {{ width: 5px; }}
.tree-scroll::-webkit-scrollbar-track {{ background: transparent; }}
.tree-scroll::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
.tree-scroll::-webkit-scrollbar-thumb:hover {{ background: var(--fg-tertiary); }}

.tree-dir-name {{
    display: flex; align-items: center; gap: 2px;
    padding: 1px 4px; height: 20px;
    cursor: pointer; color: var(--fg-secondary);
    font-size: 11px; font-weight: 500; user-select: none;
    border-radius: 3px; margin: 0 3px;
    transition: all 0.1s ease;
}}
.tree-dir-name:hover {{ background: var(--bg-hover); color: var(--fg); }}
.tree-dir-name .tree-label {{ cursor: pointer; }}
.tree-dir-name .tree-label:hover {{ color: var(--accent); }}
.tree-dir-name.active {{ background: var(--accent-dim); color: var(--fg); }}
.tree-chev {{
    width: 16px; height: 16px;
    display: flex; align-items: center; justify-content: center;
    color: var(--fg-tertiary); flex-shrink: 0;
    transition: transform 0.12s cubic-bezier(0.4, 0, 0.2, 1);
}}
.tree-dir.closed .tree-chev {{ transform: rotate(-90deg); }}
.tree-dir.closed .tree-children {{ display: none; }}
.tree-dir-icon {{ display: flex; align-items: center; margin-right: 1px; flex-shrink: 0; opacity: 0.4; }}
.tree-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

.tree-file {{
    display: flex; align-items: center; gap: 2px;
    padding: 1px 4px; height: 20px;
    color: var(--fg-secondary); font-size: 11px; font-weight: 500;
    text-decoration: none; cursor: pointer;
    border-radius: 3px; margin: 0 3px;
    transition: all 0.1s ease;
    position: relative;
}}
.tree-file:hover {{ background: var(--bg-hover); color: var(--fg); }}
.tree-file.active {{
    background: var(--accent-dim); color: var(--fg);
    box-shadow: inset 2px 0 0 var(--accent);
}}
.tree-file-icon {{ display: flex; align-items: center; margin-right: 1px; flex-shrink: 0; opacity: 0.35; }}
.tree-file-stats {{
    margin-left: auto; font-size: 9px;
    display: flex; gap: 4px; flex-shrink: 0;
    font-weight: 500; font-family: var(--mono);
}}
.ts-add {{ color: var(--green); }}
.ts-del {{ color: var(--red); }}

.icon-svg {{ display: block; }}
.chev-svg {{ display: block; }}

/* ===== Resize ===== */
.resize-handle {{
    width: 3px; cursor: col-resize;
    background: transparent; flex-shrink: 0;
    transition: background 0.2s;
    position: relative; z-index: 20;
}}
.resize-handle:hover, .resize-handle.dragging {{ background: var(--accent); }}

/* ===== Diff pane ===== */
.diff-pane {{
    flex: 1; overflow-y: auto; min-width: 0;
    background: var(--bg-inset);
}}
.diff-pane::-webkit-scrollbar {{ width: 6px; }}
.diff-pane::-webkit-scrollbar-track {{ background: transparent; }}
.diff-pane::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
.diff-pane::-webkit-scrollbar-thumb:hover {{ background: var(--fg-tertiary); }}

.file-section {{
    border: 1px solid var(--border);
    border-radius: 4px;
    margin: 4px 6px;
    overflow: hidden;
    background: var(--bg);
}}
.file-header {{
    background: var(--bg-raised);
    padding: 4px 8px;
    font-size: 11px; cursor: pointer;
    display: flex; align-items: center; gap: 4px;
    position: sticky; top: 0; z-index: 10;
    border-bottom: 1px solid var(--border);
    user-select: none;
    transition: background 0.12s;
}}
.file-header:hover {{ background: var(--bg-hover); }}
.fh-chev {{
    width: 14px; height: 14px;
    display: flex; align-items: center; justify-content: center;
    color: var(--fg-tertiary); flex-shrink: 0;
    transition: transform 0.15s cubic-bezier(0.4, 0, 0.2, 1);
}}
.fh-chev.collapsed {{ transform: rotate(-90deg); }}
.fh-dir {{ color: var(--fg-tertiary); font-weight: 400; }}
.fh-name {{ color: var(--fg); font-weight: 600; }}
.fh-stats {{
    margin-left: auto;
    display: flex; align-items: center; gap: 6px;
    font-size: 11px; font-weight: 500; font-family: var(--mono);
}}
.fs-add {{ color: var(--green); }}
.fs-del {{ color: var(--red); }}

.file-body {{ overflow-x: auto; }}
.file-body.collapsed {{ display: none; }}

.file-diff table {{
    width: 100%; border-collapse: collapse;
    font-family: var(--mono); font-size: 12px; line-height: 20px;
}}

.line td {{ padding: 0; }}
.line .ln {{
    width: 1px;
    padding: 0 4px;
    text-align: right; color: var(--fg-tertiary);
    user-select: none; vertical-align: top;
    white-space: nowrap;
    font-size: 10px; opacity: 0.5;
}}
.line .code {{ padding: 0 16px; white-space: pre; }}

.line.ctx .code {{ color: var(--fg-secondary); }}
.line.ctx .ln {{ background: var(--bg); }}
.line.ctx:hover {{ background: var(--bg-raised); }}

.line.add {{ background: var(--green-bg); }}
.line.add .ln {{ background: var(--green-ln); color: var(--green); }}
.line.add .code {{ color: var(--green-fg); }}
.line.add:hover {{ background: var(--green-hover, #143520); }}

.line.del {{ background: var(--red-bg); }}
.line.del .ln {{ background: var(--red-ln); color: var(--red); }}
.line.del .code {{ color: var(--red-fg); }}
.line.del:hover {{ background: var(--red-hover, #381518); }}

.hunk-header td {{
    background: var(--bg-surface); padding: 5px 0;
    border-top: 1px solid var(--border-subtle);
    border-bottom: 1px solid var(--border-subtle);
}}
.hunk-header .ln {{ background: var(--bg-surface); }}
.hunk-header .hunk-code {{
    padding: 0 16px; color: var(--fg-tertiary);
    font-family: var(--mono); font-size: 12px;
}}
.hunk-header .hunk-range {{
    color: var(--accent); opacity: 0.7;
}}
.hunk-header .hunk-ctx {{ color: var(--fg-tertiary); margin-left: 10px; }}

.expand-row td {{ background: var(--bg-surface); }}
.expand-cell {{
    padding: 2px 16px;
    color: var(--accent); opacity: 0.7;
    font-family: var(--mono); font-size: 11px;
    cursor: pointer; user-select: none;
    transition: opacity 0.15s;
}}
.expand-cell:hover {{ opacity: 1; }}
.line.expanded {{ background: var(--bg-surface); }}
.line.expanded .ln {{ opacity: 0.3; }}

.stat-add {{ color: var(--green); font-weight: 600; }}
.stat-del {{ color: var(--red); font-weight: 600; }}

.empty-state {{
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100%; color: var(--fg-tertiary); gap: 12px;
}}
.empty-state svg {{ opacity: 0.2; }}
.empty-state .empty-text {{
    font-family: var(--display); font-size: 13px;
    font-weight: 500; letter-spacing: 1px;
    text-transform: uppercase;
}}

.theme-btn {{
    display: flex; align-items: center; justify-content: center;
    width: 26px; height: 26px;
    background: none; border: 1px solid var(--border);
    border-radius: var(--radius-sm); cursor: pointer;
    color: var(--fg-tertiary); transition: all 0.15s ease;
    flex-shrink: 0;
}}
.theme-btn:hover {{ color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }}
.theme-btn svg {{ display: block; }}

</style>
<script>document.documentElement.setAttribute('data-theme',localStorage.getItem('diff-theme')||'auto')</script>
</head>
<body>
<div class="layout">
    <div class="toolbar">
        <span class="tb-vcs">{vcs or "?"}</span>
        <span class="tb-summary">
            <b>{file_count}</b> file{"s" if file_count != 1 else ""}
            <span class="stat-add">+{total_add}</span>
            <span class="stat-del">&minus;{total_del}</span>
            {total_bar}
        </span>
        <span style="flex:1"></span>
        <button class="show-all-btn" id="show-all-btn" style="display:none" onclick="showAll()">Show all</button>
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
                <span class="sb-count">{file_count}</span>
            </div>
            <div class="sb-filter">
                <span class="sb-filter-icon">{SVG_SEARCH}</span>
                <input id="filter" type="text" placeholder="Filter\u2026" oninput="filterTree(this.value)" spellcheck="false">
            </div>
            <div class="tree-scroll" id="tree">{tree_html}</div>
        </div>
        <div class="resize-handle" id="resize-handle"></div>
        <div class="diff-pane" id="diff-pane">
            {"<div class='empty-state'><svg viewBox='0 0 24 24' width='36' height='36' fill='none' stroke='currentColor' stroke-width='1.5'><polyline points='20 6 9 17 4 12'/></svg><span class='empty-text'>Clean</span></div>" if file_count == 0 else file_sections}
        </div>
    </div>
</div>

<script>
const THEMES = ['auto', 'dark', 'light'];
function applyTheme(t) {{
    document.documentElement.setAttribute('data-theme', t);
    document.getElementById('theme-icon-dark').style.display = t === 'dark' ? '' : 'none';
    document.getElementById('theme-icon-light').style.display = t === 'light' ? '' : 'none';
    document.getElementById('theme-icon-auto').style.display = t === 'auto' ? '' : 'none';
    document.getElementById('theme-btn').title = 'Theme: ' + t;
}}
function cycleTheme() {{
    const cur = localStorage.getItem('diff-theme') || 'dark';
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    localStorage.setItem('diff-theme', next);
    applyTheme(next);
}}
applyTheme(localStorage.getItem('diff-theme') || 'dark');

let activeFilter = null;

function toggleFile(i) {{
    document.getElementById('body-' + i).classList.toggle('collapsed');
    document.getElementById('chev-' + i).classList.toggle('collapsed');
}}

function applyFilter(indices) {{
    const pane = document.getElementById('diff-pane');
    const sections = document.querySelectorAll('.file-section');
    const total = sections.length;
    const valid = indices.filter(i => i < total);

    if (valid.length === 0) {{
        showAll();
        return;
    }}

    const idxSet = new Set(valid);

    sections.forEach((s, i) => {{
        s.style.display = idxSet.has(i) ? '' : 'none';
    }});

    document.querySelectorAll('.tree-file').forEach(f => f.classList.remove('active'));
    valid.forEach(idx => {{
        const link = document.querySelector('.tree-file[data-idx="' + idx + '"]');
        if (link) link.classList.add('active');
    }});

    document.getElementById('show-all-btn').style.display = '';
    pane.scrollTo({{ top: 0 }});
}}

function filterToFiles(indices) {{
    const isSame = activeFilter && JSON.stringify(activeFilter) === JSON.stringify(indices);
    if (isSame) {{ showAll(); return; }}

    activeFilter = indices;
    const url = new URL(window.location);
    url.searchParams.set('focus', indices.join(','));
    history.replaceState(null, '', url);
    applyFilter(indices);
}}

function showAll() {{
    activeFilter = null;
    const url = new URL(window.location);
    url.searchParams.delete('focus');
    history.replaceState(null, '', url);
    document.querySelectorAll('.file-section').forEach(s => s.style.display = '');
    document.querySelectorAll('.tree-file').forEach(f => f.classList.remove('active'));
    document.getElementById('show-all-btn').style.display = 'none';
}}

// Restore filter from URL on load
(function() {{
    const params = new URLSearchParams(window.location.search);
    const focus = params.get('focus');
    if (focus) {{
        const total = document.querySelectorAll('.file-section').length;
        const indices = focus.split(',').map(Number).filter(n => !isNaN(n) && n < total);
        if (indices.length) {{
            activeFilter = indices;
            applyFilter(indices);
        }} else {{
            // focused files no longer in diff — reset
            showAll();
        }}
    }}
}})()

const diffPane = document.getElementById('diff-pane');
let scrollTick = false;
diffPane.addEventListener('scroll', () => {{
    if (scrollTick) return;
    scrollTick = true;
    requestAnimationFrame(() => {{
        const sections = document.querySelectorAll('.file-section');
        let activeIdx = 0;
        for (let i = 0; i < sections.length; i++) {{
            if (sections[i].getBoundingClientRect().top <= 80) activeIdx = i;
        }}
        document.querySelectorAll('.tree-file').forEach(f => f.classList.remove('active'));
        const link = document.querySelector('.tree-file[data-idx="' + activeIdx + '"]');
        if (link) {{
            link.classList.add('active');
            link.scrollIntoView({{ block: 'nearest' }});
        }}
        scrollTick = false;
    }});
}});

function filterTree(query) {{
    const q = query.toLowerCase();
    document.querySelectorAll('.tree-file').forEach(f => {{
        f.style.display = f.textContent.toLowerCase().includes(q) ? '' : 'none';
    }});
    document.querySelectorAll('.tree-dir').forEach(d => {{
        const vis = d.querySelector('.tree-file:not([style*="display: none"])');
        d.style.display = vis ? '' : 'none';
    }});
}}

const handle = document.getElementById('resize-handle');
const sidebar = document.getElementById('sidebar');
let dragging = false;
handle.addEventListener('mousedown', e => {{
    dragging = true; handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
}});
document.addEventListener('mousemove', e => {{
    if (!dragging) return;
    sidebar.style.width = Math.max(140, Math.min(500, e.clientX)) + 'px';
}});
document.addEventListener('mouseup', () => {{
    if (!dragging) return;
    dragging = false; handle.classList.remove('dragging');
    document.body.style.cursor = ''; document.body.style.userSelect = '';
}});

let lastHash = {diff_hash};
async function checkForUpdates() {{
    try {{
        const dot = document.getElementById('refresh-dot');
        dot.classList.add('active');
        const r = await fetch('/hash?path=' + encodeURIComponent('{path}'));
        const d = await r.json();
        if (d.hash !== lastHash) location.reload();
        setTimeout(() => dot.classList.remove('active'), 300);
    }} catch(e) {{}}
    setTimeout(checkForUpdates, {refresh_seconds} * 1000);
}}
setTimeout(checkForUpdates, {refresh_seconds} * 1000);

async function expandLines(cell) {{
    const row = cell.closest('tr');
    const file = row.dataset.file;
    const start = parseInt(row.dataset.start);
    const end = parseInt(row.dataset.end);
    try {{
        const r = await fetch('/context?path=' + encodeURIComponent('{path}') +
            '&file=' + encodeURIComponent(file) +
            '&start=' + start + '&end=' + end);
        const data = await r.json();
        if (!data.lines || !data.lines.length) {{
            row.remove();
            return;
        }}
        const tbody = row.closest('table');
        const frag = document.createDocumentFragment();
        data.lines.forEach(l => {{
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
        }});
        row.replaceWith(frag);
    }} catch(e) {{
        row.remove();
    }}
}}
</script>
</body>
</html>"""


class DiffHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/context":
            repo_path = params.get("path", [None])[0]
            file_path = params.get("file", [None])[0]
            start = int(params.get("start", [1])[0])
            end = int(params.get("end", [0])[0])
            if repo_path and file_path:
                repo_path = os.path.expanduser(repo_path)
                _, root = detect_vcs(repo_path)
                if root:
                    full = os.path.join(root, file_path)
                    lines_out = []
                    try:
                        with open(full, "r", errors="replace") as f:
                            all_lines = f.readlines()
                        if end <= 0:
                            end = min(start + 20, len(all_lines))
                        end = min(end, len(all_lines))
                        for i in range(start - 1, end):
                            lines_out.append({"num": i + 1, "text": all_lines[i].rstrip("\n")})
                    except Exception:
                        pass
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"lines": lines_out}).encode())
                    return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"lines":[]}')
            return

        if parsed.path == "/hash":
            path = params.get("path", [None])[0]
            if path:
                path = os.path.expanduser(path)
                _, _, diff_text = get_diff(path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"hash": hash(diff_text)}).encode())
            return

        path = params.get("path", [None])[0]
        refresh = int(params.get("refresh", [3])[0])

        if not path:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<!DOCTYPE html>
<html><body style="background:#0d1117;color:#e6edf3;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h2 style="font-weight:600">diff-server</h2>
<p style="color:#8b949e;margin-top:8px">Usage: <code style="background:#161b22;padding:2px 6px;border-radius:4px">?path=/your/repo</code></p>
</div></body></html>""")
            return

        path = os.path.expanduser(path)
        if not os.path.isdir(path):
            self.send_error(400, f"Not a directory: {path}")
            return

        vcs, root, diff_text = get_diff(path)
        html = make_html(vcs, root, diff_text, path, refresh)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", PORT), DiffHandler)
    print(f"diff-server on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
