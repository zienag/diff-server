"""Git VCS backend."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from vcs.base import run_cmd, collect_diff, SUBPROCESS_TIMEOUT, FULL_SCAN_INTERVAL


class GitBackend:
    name = "git"

    def __init__(self, root):
        self.root = root
        self._fp_cache = {}
        self._fp_lock = threading.Lock()

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

    def get_diff(self, path):
        return collect_diff(
            self._diff_cmd(cached=True),
            self._diff_cmd(),
            self._status_cmd(),
            path, self.root,
        )
