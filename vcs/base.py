"""Shared utilities for VCS backends. No base classes — just functions."""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

SUBPROCESS_TIMEOUT = 30
FULL_SCAN_INTERVAL = 10
MAX_UNTRACKED_FILE_SIZE = 256 * 1024
MAX_UNTRACKED_FILES = 200


def run_cmd(cmd, cwd):
    """Run a command and return stdout, with timeout."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
    ).stdout


def is_binary(path, chunk_size=8192):
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(chunk_size)
    except Exception:
        return True


def make_untracked_diff(files, cwd, root):
    """Generate unified diff text for untracked (new) files."""
    parts = []
    for relpath, fullpath in files:
        if is_binary(fullpath):
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


def parse_untracked_files(status_cmd, path):
    """Run status command and parse untracked (??) file entries."""
    try:
        result = subprocess.run(
            status_cmd, cwd=path,
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
        )
        files = []
        for line in result.stdout.splitlines():
            if len(files) >= MAX_UNTRACKED_FILES:
                break
            if not line.startswith("??"):
                continue
            fpath = line[3:].strip()
            full = os.path.join(path, fpath)
            if os.path.isfile(full):
                files.append((fpath, full))
            elif os.path.isdir(full):
                for dirpath, _, filenames in os.walk(full):
                    for fn in filenames:
                        if len(files) >= MAX_UNTRACKED_FILES:
                            break
                        fp = os.path.join(dirpath, fn)
                        files.append((os.path.relpath(fp, path), fp))
                    if len(files) >= MAX_UNTRACKED_FILES:
                        break
        return files
    except Exception:
        return []


def collect_diff(staged_cmd, unstaged_cmd, status_cmd, path, root):
    """Run staged/unstaged diff + untracked scan in parallel. Return (staged, unstaged)."""
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_staged = pool.submit(run_cmd, staged_cmd, path)
        f_unstaged = pool.submit(run_cmd, unstaged_cmd, path)
        f_untracked = pool.submit(parse_untracked_files, status_cmd, path)
    staged = f_staged.result(timeout=SUBPROCESS_TIMEOUT + 5)
    unstaged = f_unstaged.result(timeout=SUBPROCESS_TIMEOUT + 5)
    untracked = f_untracked.result(timeout=SUBPROCESS_TIMEOUT + 5)
    t1 = time.monotonic()
    if untracked:
        unstaged += make_untracked_diff(untracked, path, root)
    t2 = time.monotonic()
    print(f"[perf] vcs_cmds={t1-t0:.3f}s  untracked_diff={t2-t1:.3f}s  files={len(untracked)}", flush=True)
    return staged, unstaged
