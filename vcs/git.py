"""Git VCS backend."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from vcs.base import run_cmd, run_subprocess, collect_diff, SUBPROCESS_TIMEOUT, FULL_SCAN_INTERVAL

# Gap between END of a diff burst and START of the next one. Applies across
# clients (multiple tabs). Short here because git is fast, so staleness is
# capped at ~1s anyway.
DIFF_COOLDOWN_S = 1.0


class GitBackend:
    name = "git"

    def __init__(self, root):
        self.root = root
        self._fp_cache = {}
        self._fp_lock = threading.Lock()
        self._diff_cache = None   # (staged, unstaged)
        self._diff_end = 0.0
        self._diff_lock = threading.Lock()

    @staticmethod
    def has_root_marker(path):
        return os.path.isdir(os.path.join(path, ".git"))

    @staticmethod
    def detect_fallback(path):
        return None

    def _status_cmd(self):
        return ["git", "status", "--porcelain"]

    def _diff_cmd(self, cached=False, stat=False):
        cmd = ["git", "diff"]
        if cached:
            cmd.append("--cached")
        cmd.append("--no-renames")
        if stat:
            cmd.append("--stat")
        return cmd

    def _index_mtime(self):
        try:
            return os.stat(os.path.join(self.root, ".git", "index")).st_mtime
        except OSError:
            return 0

    def fingerprint(self, path):
        """Git-optimized: skips full scan if .git/index mtime unchanged."""
        try:
            now = time.monotonic()
            index_mtime = self._index_mtime()

            with self._fp_lock:
                entry = self._fp_cache.get(path)

            if entry:
                index_changed = index_mtime != entry["index_mtime"]
                need_full = index_changed or (now - entry["last_full"] >= FULL_SCAN_INTERVAL)
            else:
                need_full = True

            if need_full:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    f1 = pool.submit(run_cmd, self._diff_cmd(cached=True, stat=True), path)
                    f2 = pool.submit(run_cmd, self._diff_cmd(stat=True), path)
                    f3 = pool.submit(run_cmd, self._status_cmd(), path)
                staged = f1.result(timeout=SUBPROCESS_TIMEOUT + 5)
                unstaged = f2.result(timeout=SUBPROCESS_TIMEOUT + 5)
                untracked = f3.result(timeout=SUBPROCESS_TIMEOUT + 5)
                fp = staged + unstaged + untracked
                with self._fp_lock:
                    self._fp_cache[path] = {
                        "fingerprint": fp,
                        "index_mtime": index_mtime,
                        "staged": staged,
                        "untracked": untracked,
                        "last_full": now,
                    }
                return fp
            else:
                unstaged = run_cmd(self._diff_cmd(stat=True), path)
                return entry["staged"] + unstaged + entry["untracked"]
        except Exception:
            return ""

    def get_blob(self, file, ref):
        """ref ∈ {'head','worktree'}. Return bytes or None."""
        if ref == "worktree":
            try:
                with open(os.path.join(self.root, file), "rb") as f:
                    return f.read()
            except OSError:
                return None
        if ref == "head":
            result = run_subprocess(
                ["git", "show", f"HEAD:{file}"], self.root,
                capture_output=True, timeout=SUBPROCESS_TIMEOUT,
            )
            return result.stdout if result.returncode == 0 else None
        return None

    def get_diff(self, path):
        with self._diff_lock:
            age = time.monotonic() - self._diff_end
            if self._diff_cache is not None and age < DIFF_COOLDOWN_S:
                print(f"[git]  get_diff CACHED age={age*1000:.0f}ms (cooldown {DIFF_COOLDOWN_S}s)", flush=True)
                return self._diff_cache
            result = collect_diff(
                self._diff_cmd(cached=True),
                self._diff_cmd(),
                self._status_cmd(),
                path, self.root,
            )
            self._diff_cache = result
            self._diff_end = time.monotonic()
            return result
