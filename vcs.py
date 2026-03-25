"""VCS detection and diff retrieval for git and arc."""

import os
import subprocess


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
    """Return (vcs, root, staged_diff, unstaged_diff).

    For git: staged = git diff --cached, unstaged = git diff + untracked.
    For arc: no staging concept, so staged is empty and everything goes to unstaged.
    """
    vcs, root = detect_vcs(path)
    if not vcs:
        return None, None, "", ""
    try:
        if vcs == "git":
            staged = subprocess.run(
                ["git", "diff", "--cached"], cwd=path,
                capture_output=True, text=True, timeout=10,
            ).stdout
            unstaged = subprocess.run(
                ["git", "diff"], cwd=path,
                capture_output=True, text=True, timeout=10,
            ).stdout
        else:
            staged = ""
            unstaged = subprocess.run(
                [vcs, "diff"], cwd=path,
                capture_output=True, text=True, timeout=10,
            ).stdout
        untracked = get_untracked_files(vcs, path)
        if untracked:
            unstaged += make_untracked_diff(untracked, path, root)
        return vcs, root, staged, unstaged
    except Exception:
        return vcs, root, "", ""
