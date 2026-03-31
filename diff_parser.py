"""Parse unified diff text into per-file HTML table fragments."""

import os
import re
from html import escape


def make_relative(filepath, cwd, root):
    abs_path = os.path.join(root, filepath)
    try:
        return os.path.relpath(abs_path, cwd)
    except ValueError:
        return filepath


def parse_and_render_diff(diff_text, cwd, root):
    files = []
    current = None
    parts = None
    total_add = 0
    total_del = 0

    for line in diff_text.splitlines():
        if line.startswith("diff ") or line.startswith("--- "):
            if current:
                parts.append("</table></div>")
                current["html"] = "".join(parts)
                files.append(current)
                current = None
                parts = None
        if line.startswith("+++ "):
            raw = line[4:]
            if raw.startswith("b/"):
                raw = raw[2:]
            raw = raw.split("\t")[0].strip()
            if raw and raw != "/dev/null":
                rel = make_relative(raw, cwd, root)
                current = {"path": rel, "raw_path": raw, "additions": 0, "deletions": 0}
                parts = ['<div class="file-diff"><table>']
                current["_first_hunk"] = True
        elif line.startswith("@@") and current:
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
            if m:
                old_start = int(m.group(1))
                new_start = int(m.group(2))
                context = escape(m.group(3))
                raw_path_js = current["raw_path"].replace("'", "\\'")

                if current.get("_first_hunk"):
                    if new_start > 1:
                        parts.append(
                            f'<tr class="expand-row" data-file=\'{raw_path_js}\' data-start="1" data-end="{new_start - 1}" data-dir="up">'
                            f'<td class="ln"></td>'
                            f'<td class="expand-cell" onclick="expandLines(this,event)">'
                            f'\u2191 Show lines 1\u2013{new_start - 1}</td></tr>'
                        )
                    current["_first_hunk"] = False
                else:
                    prev_end = current.get("_last_new_line", 0)
                    if new_start - prev_end > 1:
                        parts.append(
                            f'<tr class="expand-row" data-file=\'{raw_path_js}\' data-start="{prev_end + 1}" data-end="{new_start - 1}">'
                            f'<td class="ln"></td>'
                            f'<td class="expand-cell" onclick="expandLines(this,event)">'
                            f'\u2195 Show lines {prev_end + 1}\u2013{new_start - 1}</td></tr>'
                        )

                parts.append(
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
            parts.append(
                f'<tr class="line {cls}">'
                f'<td class="ln">{ln}</td>'
                f'<td class="code">{content}</td>'
                f'</tr>'
            )
            if cls != "del":
                current["_last_new_line"] = current.get("_new", 1) - 1

    if current:
        raw_path_js = current["raw_path"].replace("'", "\\'")
        last_ln = current.get("_last_new_line", 0)
        parts.append(
            f'<tr class="expand-row" data-file=\'{raw_path_js}\' data-start="{last_ln + 1}" data-end="0">'
            f'<td class="ln"></td>'
            f'<td class="expand-cell" onclick="expandLines(this,event)">'
            f'\u2193 Show more <span class="expand-hint">\u2318 all</span></td></tr>'
        )
        parts.append("</table></div>")
        current["html"] = "".join(parts)
        files.append(current)

    return files, total_add, total_del
