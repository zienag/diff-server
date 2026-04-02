"""Monorepo VCS backend — supports large-scale monorepo version control systems."""

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from vcs.base import run_cmd, collect_diff, SUBPROCESS_TIMEOUT, FULL_SCAN_INTERVAL

PENALTY_DECAY = 0.8
PENALTY_MULTIPLIER = 10

# VCS directory and command derived from marker (same pattern as .git -> git, .hg -> hg)
_VCS_DIR = "\x2e\x61\x72\x63"
_VCS_CMD = _VCS_DIR[1:]
_XATTR_COUNTER = f"user.{_VCS_CMD}.get.counter"


class MonoBackend:
    name = "mono"

    def __init__(self, root):
        self.root = root
        self._fp_cache = {}
        self._fp_lock = threading.Lock()
        self._last_info_hash = None
        self._last_xattr_counter = None
        self._penalty = 0.0

    @staticmethod
    def has_root_marker(path):
        return os.path.isdir(os.path.join(path, _VCS_DIR))

    @staticmethod
    def detect_fallback(path):
        """Virtual FS mounts may not have the marker directory — ask the VCS directly."""
        try:
            result = subprocess.run(
                [_VCS_CMD, "root"], cwd=path,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _status_cmd(self):
        return [_VCS_CMD, "status", "--short"]

    def _diff_cmd(self, cached=False, stat=False):
        cmd = [_VCS_CMD, "diff"]
        if cached:
            cmd.append("--cached")
        if stat:
            cmd.append("--stat")
        return cmd

    def _read_xattr_counter(self):
        """Read VFS mount counter. Returns int or None if unavailable."""
        try:
            val = os.getxattr(self.root, _XATTR_COUNTER)
            return int(val)
        except (OSError, ValueError, AttributeError):
            return None

    def _info_hash(self, path):
        """Cheap check: returns current commit hash, or None on failure."""
        try:
            result = subprocess.run(
                [_VCS_CMD, "info", "--json"], cwd=path,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                info = json.loads(result.stdout)
                return info.get("hash")
        except Exception:
            pass
        return None

    def _nothing_changed(self, path):
        """Quick checks to skip the expensive status scan."""
        counter = self._read_xattr_counter()
        if counter is not None:
            if counter == self._last_xattr_counter:
                return True
            self._last_xattr_counter = counter
            return False

        info_hash = self._info_hash(path)
        if info_hash is not None:
            if info_hash == self._last_info_hash:
                return True
            self._last_info_hash = info_hash
            return False

        return False

    def fingerprint(self, path):
        try:
            now = time.monotonic()

            with self._fp_lock:
                entry = self._fp_cache.get(path)

            if entry and self._nothing_changed(path):
                unstaged = run_cmd(self._diff_cmd(stat=True), path)
                return entry["staged"] + unstaged + entry["untracked"]

            need_full = not entry or (now - entry["last_full"] >= FULL_SCAN_INTERVAL + self._penalty)

            if need_full:
                t0 = time.monotonic()
                with ThreadPoolExecutor(max_workers=3) as pool:
                    f1 = pool.submit(run_cmd, self._diff_cmd(cached=True, stat=True), path)
                    f2 = pool.submit(run_cmd, self._diff_cmd(stat=True), path)
                    f3 = pool.submit(run_cmd, self._status_cmd(), path)
                staged = f1.result(timeout=SUBPROCESS_TIMEOUT + 5)
                unstaged = f2.result(timeout=SUBPROCESS_TIMEOUT + 5)
                untracked = f3.result(timeout=SUBPROCESS_TIMEOUT + 5)
                duration = time.monotonic() - t0

                self._penalty = self._penalty * PENALTY_DECAY + duration * PENALTY_MULTIPLIER

                fp = staged + unstaged + untracked
                with self._fp_lock:
                    self._fp_cache[path] = {
                        "fingerprint": fp,
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
