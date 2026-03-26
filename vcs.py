"""VCS detection and diff retrieval."""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

SUBPROCESS_TIMEOUT = 30


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
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
            )
        else:
            result = subprocess.run(
                ["git", "status", "--porcelain"], cwd=path,
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
            )
        files = []
        max_files = 200
        for line in result.stdout.splitlines():
            if len(files) >= max_files:
                break
            if line.startswith("??"):
                fpath = line[3:].strip()
                full = os.path.join(path, fpath)
                if os.path.isfile(full):
                    files.append((fpath, full))
                elif os.path.isdir(full):
                    for dirpath, _, filenames in os.walk(full):
                        for fn in filenames:
                            if len(files) >= max_files:
                                break
                            fp = os.path.join(dirpath, fn)
                            files.append((os.path.relpath(fp, path), fp))
                        if len(files) >= max_files:
                            break
        return files
    except Exception:
        return []


def _is_binary(path, chunk_size=8192):
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(chunk_size)
    except Exception:
        return True


MAX_UNTRACKED_FILE_SIZE = 256 * 1024  # skip files larger than 256KB


def make_untracked_diff(files, cwd, root):
    """Generate unified diff text for untracked (new) files."""
    parts = []
    for relpath, fullpath in files:
        if _is_binary(fullpath):
            continue
        try:
            if os.path.getsize(fullpath) > MAX_UNTRACKED_FILE_SIZE:
                continue
            with open(fullpath, "r", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        abs_path = os.path.join(cwd, relpath)
        root_rel = os.path.relpath(abs_path, root)
        n = len(lines)
        parts.append(f"--- /dev/null\n")
        parts.append(f"+++ {root_rel}\n")
        parts.append(f"@@ -0,0 +1,{n} @@\n")
        for line in lines:
            parts.append("+" + line.rstrip("\n") + "\n")
    return "".join(parts)


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT).stdout


def get_diff_fingerprint(path):
    """Return a cheap string fingerprint of current VCS state (for change detection)."""
    vcs, root = detect_vcs(path)
    if not vcs:
        return ""
    try:
        if vcs == "git":
            with ThreadPoolExecutor(max_workers=3) as pool:
                f1 = pool.submit(_run, ["git", "diff", "--cached", "--no-renames", "--stat"], path)
                f2 = pool.submit(_run, ["git", "diff", "--no-renames", "--stat"], path)
                f3 = pool.submit(_run, ["git", "status", "--porcelain"], path)
            return f1.result() + f2.result() + f3.result()
        else:
            return _run([vcs, "diff", "--stat"], path)
    except Exception:
        return ""


def get_diff(path):
    """Return (vcs, root, staged_diff, unstaged_diff).

    For git: staged = git diff --cached, unstaged = git diff + untracked.
    For VCS without staging (e.g. svn-like): staged is empty, everything goes to unstaged.
    """
    vcs, root = detect_vcs(path)
    if not vcs:
        return None, None, "", ""
    try:
        t0 = time.monotonic()
        if vcs == "git":
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_staged = pool.submit(_run, ["git", "diff", "--cached", "--no-renames"], path)
                f_unstaged = pool.submit(_run, ["git", "diff", "--no-renames"], path)
                f_untracked = pool.submit(get_untracked_files, vcs, path)
            staged = f_staged.result()
            unstaged = f_unstaged.result()
            untracked = f_untracked.result()
        else:
            staged = ""
            unstaged = _run([vcs, "diff"], path)
            untracked = get_untracked_files(vcs, path)
        t1 = time.monotonic()
        if untracked:
            unstaged += make_untracked_diff(untracked, path, root)
        t2 = time.monotonic()
        print(f"[perf] git_cmds={t1-t0:.3f}s  untracked_diff={t2-t1:.3f}s  files={len(untracked)}", flush=True)
        return vcs, root, staged, unstaged
    except Exception:
        return vcs, root, "", ""
