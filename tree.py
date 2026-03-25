"""File tree building and HTML rendering for the sidebar."""

import json
from html import escape

SVG_CHEVRON_DOWN = '<svg class="chev-svg" viewBox="4 7 16 10" width="14" height="9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>'


def build_file_tree(files, idx_offset=0):
    tree = {}
    for i, f in enumerate(files):
        parts = f["path"].split("/")
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            node = node[part]
        node[parts[-1]] = {"_idx": idx_offset + i, "_file": f}
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
            f'<span class="tree-label">{escape(fname)}</span>'
            f'<span class="tree-file-stats">{stats}</span>'
            f'</a>'
        )
    return html
