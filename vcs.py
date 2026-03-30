"""VCS detection and diff retrieval."""

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SUBPROCESS_TIMEOUT = 30
FULL_SCAN_INTERVAL = 10  # seconds between expensive git status scans


def detect_vcs(path):
    current = path
    while current != "/":
        if os.path.isdir(os.path.join(current, ".arc")):
            return "arc", current
        if os.path.isdir(os.path.join(current, ".git")):
            return "git", current
        current = os.path.dirname(current)
    # Mounted arc repos (FUSE) don't have .arc directory — ask arc directly
    try:
        result = subprocess.run(
            ["arc", "root"], cwd=path,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "arc", result.stdout.strip()
    except Exception:
        pass
    return None, None


def _status_cmd(vcs):
    if vcs == "git":
        return [vcs, "status", "--porcelain"]
    return [vcs, "status", "--short"]


def _diff_stat_cmd(vcs, cached=False):
    cmd = [vcs, "diff"]
    if cached:
        cmd.append("--cached")
    if vcs == "git":
        cmd.append("--no-renames")
    cmd.append("--stat")
    return cmd


def _diff_cmd(vcs, cached=False):
    cmd = [vcs, "diff"]
    if cached:
        cmd.append("--cached")
    if vcs == "git":
        cmd.append("--no-renames")
    return cmd


def get_untracked_files(vcs, path):
    """Get list of untracked file paths (relative to cwd)."""
    try:
        result = subprocess.run(
            _status_cmd(vcs), cwd=path,
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


_fp_cache = {}  # path -> {fingerprint, index_mtime, staged, untracked, last_full}
_fp_lock = threading.Lock()


def _git_index_mtime(root):
    try:
        return os.stat(os.path.join(root, ".git", "index")).st_mtime
    except OSError:
        return 0


def get_diff_fingerprint(path):
    """Return a cheap string fingerprint of current VCS state (for change detection).

    Optimized for large repos: caches results and only runs the expensive
    status scan every FULL_SCAN_INTERVAL seconds. Between full scans, only
    diff --stat is run (cheap for tracked files). Works for both git and arc.
    """
    vcs, root = detect_vcs(path)
    if not vcs:
        return ""
    try:
        now = time.monotonic()
        index_mtime = _git_index_mtime(root) if vcs == "git" else 0

        with _fp_lock:
            entry = _fp_cache.get(path)

        if entry:
            index_changed = vcs == "git" and (index_mtime != entry['index_mtime'])
            need_full = index_changed or (now - entry['last_full'] >= FULL_SCAN_INTERVAL)
        else:
            need_full = True

        if need_full:
            with ThreadPoolExecutor(max_workers=3) as pool:
                f1 = pool.submit(_run, _diff_stat_cmd(vcs, cached=True), path)
                f2 = pool.submit(_run, _diff_stat_cmd(vcs), path)
                f3 = pool.submit(_run, _status_cmd(vcs), path)
            staged = f1.result()
            unstaged = f2.result()
            untracked = f3.result()
            fp = staged + unstaged + untracked
            with _fp_lock:
                _fp_cache[path] = {
                    'fingerprint': fp,
                    'index_mtime': index_mtime,
                    'staged': staged,
                    'untracked': untracked,
                    'last_full': now,
                }
            return fp
        else:
            unstaged = _run(_diff_stat_cmd(vcs), path)
            return entry['staged'] + unstaged + entry['untracked']
    except Exception:
        return ""


def get_diff(path):
    """Return (vcs, root, staged_diff, unstaged_diff) for both git and arc."""
    vcs, root = detect_vcs(path)
    if not vcs:
        return None, None, "", ""
    try:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_staged = pool.submit(_run, _diff_cmd(vcs, cached=True), path)
            f_unstaged = pool.submit(_run, _diff_cmd(vcs), path)
            f_untracked = pool.submit(get_untracked_files, vcs, path)
        staged = f_staged.result()
        unstaged = f_unstaged.result()
        untracked = f_untracked.result()
        t1 = time.monotonic()
        if untracked:
            unstaged += make_untracked_diff(untracked, path, root)
        t2 = time.monotonic()
        print(f"[perf] vcs_cmds={t1-t0:.3f}s  untracked_diff={t2-t1:.3f}s  files={len(untracked)}", flush=True)
        return vcs, root, staged, unstaged
    except Exception:
        return vcs, root, "", ""
