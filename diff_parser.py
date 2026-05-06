"""Parse unified diff text into per-file HTML table fragments."""

import os
import re
from dataclasses import dataclass, field
from html import escape

_HUNK_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)")

_FILE_OPEN = '<div class="file-diff"><table>'
_FILE_CLOSE = '</table></div>'
_RENAMED_BODY = (
    f'{_FILE_OPEN}'
    '<tr class="line ctx"><td class="ln"></td>'
    '<td class="code"><em>Renamed, no content changes</em></td>'
    f'</tr>{_FILE_CLOSE}'
)


def _make_relative(filepath, cwd, root):
    try:
        return os.path.relpath(os.path.join(root, filepath), cwd)
    except ValueError:
        return filepath


def _expand_row(raw_path, start, end, direction=""):
    """Render an expand-context row. direction in {'', 'up'}. end=0 => 'show more'."""
    path_attr = raw_path.replace("'", "\\'")
    if direction == "up":
        label = f"\u2191 Show lines {start}\u2013{end}"
        dir_attr = ' data-dir="up"'
    elif end > 0:
        label = f"\u2195 Show lines {start}\u2013{end}"
        dir_attr = ""
    else:
        label = '\u2193 Show more <span class="expand-hint">\u2318 all</span>'
        dir_attr = ""
    return (
        f'<tr class="expand-row" data-file=\'{path_attr}\' '
        f'data-start="{start}" data-end="{end}"{dir_attr}>'
        f'<td class="ln"></td>'
        f'<td class="expand-cell" onclick="expandLines(this,event)">{label}</td></tr>'
    )


def _hunk_header_row(old_start, new_start, context):
    return (
        f'<tr class="hunk-header">'
        f'<td class="ln"></td>'
        f'<td class="hunk-code">'
        f'<span class="hunk-range">@@ -{old_start} +{new_start} @@</span>'
        f'<span class="hunk-ctx">{escape(context)}</span></td></tr>'
    )


def _line_row(cls, ln, content):
    return (
        f'<tr class="line {cls}">'
        f'<td class="ln">{ln}</td>'
        f'<td class="code">{content}</td>'
        f'</tr>'
    )


@dataclass
class _FileState:
    path: str
    raw_path: str
    additions: int = 0
    deletions: int = 0
    renamed_from: str = None
    html: str = ""
    parts: list = field(default_factory=lambda: [_FILE_OPEN])
    old_line: int = 0
    new_line: int = 0
    last_new_line: int = 0
    first_hunk: bool = True

    def finalize(self):
        if self.last_new_line > 0:
            self.parts.append(_expand_row(self.raw_path, self.last_new_line + 1, 0))
        self.parts.append(_FILE_CLOSE)
        self.html = "".join(self.parts)

    def to_dict(self):
        d = {
            "path": self.path,
            "raw_path": self.raw_path,
            "additions": self.additions,
            "deletions": self.deletions,
            "html": self.html,
            "is_svg": self.raw_path.lower().endswith(".svg"),
        }
        if self.renamed_from is not None:
            d["renamed_from"] = self.renamed_from
        return d


def _merge_renames(files, total_add, total_del):
    """Collapse add+delete pairs with same basename and matching line counts into renames."""
    adds, dels = {}, {}
    for i, f in enumerate(files):
        base = os.path.basename(f["path"])
        if f["additions"] > 0 and f["deletions"] == 0:
            adds.setdefault(base, []).append((i, f))
        elif f["deletions"] > 0 and f["additions"] == 0:
            dels.setdefault(base, []).append((i, f))

    remove = set()
    for base, add_list in adds.items():
        del_list = dels.get(base)
        if not del_list:
            continue
        used = set()
        for _, af in add_list:
            for di, df in del_list:
                if di in used or af["additions"] != df["deletions"]:
                    continue
                af["renamed_from"] = df["path"]
                total_add -= af["additions"]
                total_del -= df["deletions"]
                af["additions"] = 0
                af["deletions"] = 0
                af["html"] = _RENAMED_BODY
                remove.add(di)
                used.add(di)
                break

    if remove:
        files = [f for i, f in enumerate(files) if i not in remove]
    return files, total_add, total_del


def _parse_plus_path(line):
    raw = line[4:].split("\t")[0].strip()
    if raw.startswith("b/"):
        raw = raw[2:]
    if not raw or raw == "/dev/null":
        return None
    return raw


def _emit_hunk_gap(state, new_start):
    """Emit an expand-row spanning the gap between last rendered line and this hunk."""
    if state.first_hunk:
        if new_start > 1:
            state.parts.append(_expand_row(state.raw_path, 1, new_start - 1, "up"))
        state.first_hunk = False
    elif new_start - state.last_new_line > 1:
        state.parts.append(_expand_row(state.raw_path, state.last_new_line + 1, new_start - 1))


def parse_and_render_diff(diff_text, cwd, root):
    files = []
    state = None
    total_add = 0
    total_del = 0

    for line in diff_text.splitlines():
        if state and (line.startswith("diff ") or line.startswith("--- ")):
            state.finalize()
            files.append(state.to_dict())
            state = None

        if line.startswith("+++ "):
            raw = _parse_plus_path(line)
            if raw:
                state = _FileState(path=_make_relative(raw, cwd, root), raw_path=raw)
            continue

        if state is None:
            continue

        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                continue
            old_start, new_start = int(m.group(1)), int(m.group(2))
            _emit_hunk_gap(state, new_start)
            state.parts.append(_hunk_header_row(old_start, new_start, m.group(3)))
            state.old_line = old_start
            state.new_line = new_start
            continue

        if not line:
            continue
        c = line[0]
        if c == "+":
            cls, ln = "add", state.new_line
            state.new_line += 1
            state.additions += 1
            total_add += 1
            state.last_new_line = ln
        elif c == "-":
            cls, ln = "del", state.old_line
            state.old_line += 1
            state.deletions += 1
            total_del += 1
        elif c == " ":
            cls, ln = "ctx", state.new_line
            state.old_line += 1
            state.new_line += 1
            state.last_new_line = ln
        else:
            continue

        content = escape(line[1:]) if len(line) > 1 else ""
        state.parts.append(_line_row(cls, ln, content))

    if state:
        state.finalize()
        files.append(state.to_dict())

    return _merge_renames(files, total_add, total_del)
