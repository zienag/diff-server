"""Monorepo VCS backend.

Mutex serializes all VCS work. Commands run sequentially with LOCK_GAP
pauses — one read lock at a time, write locks get through.

COOLDOWN cache: after a command-burst completes, subsequent calls for the
same operation within COOLDOWN_S seconds return the cached result instead
of re-running subprocesses. This enforces "gap from END of previous run
to START of next" regardless of how many clients (tabs) are polling, so
N tabs do not multiply VCS load.

TODO: optimization strategies (not implemented):
  - xattr mount counter: os.getxattr(root, "user.<vcs>.get.counter")
    O(1) check on VFS mounts, skip commands if counter unchanged.
  - <vcs> info --json: cheap commit hash check, skip full status when
    only working-tree changes matter and commit hasn't changed.
  - Background poller: move VCS work to a single thread, HTTP handlers
    read cached results. Tradeoff: up to POLL_INTERVAL staleness.
  - Adaptive penalty: scale poll interval based on command duration
    (penalty = penalty * 0.8 + duration * 10).
"""

import os
import threading
import time

from vcs.base import run_cmd, run_subprocess, parse_untracked_files, make_untracked_diff

LOCK_GAP = 0.3
# Minimum gap from END of previous burst to START of next. Adaptive: if
# subprocesses took >COOLDOWN_S, stretch to their actual duration so we
# don't churn on slow repos. N tabs never multiply load.
COOLDOWN_S = 3.0
# Multiplier on measured duration — slow repo gets longer rest.
# 2.0 = spend at most 1/3 of wall-clock on VCS subprocesses.
COOLDOWN_STRETCH = 2.0

_VCS_DIR = "\x2e\x61\x72\x63"
_VCS_CMD = _VCS_DIR[1:]
_XATTR_COUNTER = f"user.{_VCS_CMD}.get.counter"


class MonoBackend:
    name = _VCS_CMD

    def __init__(self, root):
        self.root = root
        self._mutex = threading.Lock()
        self._fp_cache = None
        self._fp_end = 0.0
        self._fp_cooldown = COOLDOWN_S
        self._diff_cache = None   # (staged, unstaged)
        self._diff_end = 0.0
        self._diff_cooldown = COOLDOWN_S
        self._last_xattr = None
        self._xattr_available = True  # flips to False on first ENOTSUP

    def _xattr_counter(self):
        """O(1) mount counter from arc VFS. None if unavailable."""
        if not self._xattr_available:
            return None
        try:
            return os.getxattr(self.root, _XATTR_COUNTER)
        except OSError:
            self._xattr_available = False
            print(f"[mono] xattr {_XATTR_COUNTER} unavailable — falling back to subprocess polling", flush=True)
            return None
        except AttributeError:
            self._xattr_available = False
            return None

    @staticmethod
    def has_root_marker(path):
        return os.path.isdir(os.path.join(path, _VCS_DIR))

    @staticmethod
    def detect_fallback(path):
        try:
            result = run_subprocess(
                [_VCS_CMD, "root"], path,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _status_cmd(self):
        # --ignored: arc otherwise reports globally-ignored dirs as ??,
        # and we'd walk into them. With --ignored they're correctly !!.
        return [_VCS_CMD, "status", "--short", "--ignored"]

    def _diff_cmd(self, cached=False, stat=False):
        cmd = [_VCS_CMD, "diff"]
        if cached:
            cmd.append("--cached")
        if stat:
            cmd.append("--stat")
        return cmd

    def fingerprint(self, path):
        with self._mutex:
            # Fast path 1: xattr mount counter. O(1), no subprocess.
            counter = self._xattr_counter()
            if counter is not None and self._fp_cache is not None and counter == self._last_xattr:
                print(f"[mono] fingerprint XATTR-CACHED (counter unchanged)", flush=True)
                return self._fp_cache

            # Fast path 2: adaptive cooldown window since last burst.
            age = time.monotonic() - self._fp_end
            if self._fp_cache is not None and age < self._fp_cooldown:
                print(f"[mono] fingerprint CACHED age={age*1000:.0f}ms  cooldown={self._fp_cooldown:.1f}s", flush=True)
                return self._fp_cache

            t0 = time.monotonic()
            try:
                staged = run_cmd(self._diff_cmd(cached=True, stat=True), path)
                time.sleep(LOCK_GAP)
                unstaged = run_cmd(self._diff_cmd(stat=True), path)
                time.sleep(LOCK_GAP)
                status = run_cmd(self._status_cmd(), path)
                result = staged + unstaged + status
            except Exception:
                result = ""
            duration = time.monotonic() - t0
            self._fp_cache = result
            self._fp_end = time.monotonic()
            self._fp_cooldown = max(COOLDOWN_S, duration * COOLDOWN_STRETCH)
            if counter is not None:
                self._last_xattr = counter
            print(f"[mono] fingerprint FRESH dt={duration:.2f}s  next-cooldown={self._fp_cooldown:.1f}s", flush=True)
            return result

    def get_diff(self, path):
        with self._mutex:
            counter = self._xattr_counter()
            # Return cached full diff if mount counter didn't change.
            if counter is not None and self._diff_cache is not None and counter == self._last_xattr:
                print(f"[mono] get_diff XATTR-CACHED", flush=True)
                return self._diff_cache

            age = time.monotonic() - self._diff_end
            if self._diff_cache is not None and age < self._diff_cooldown:
                print(f"[mono] get_diff CACHED age={age*1000:.0f}ms  cooldown={self._diff_cooldown:.1f}s", flush=True)
                return self._diff_cache

            try:
                t0 = time.monotonic()
                staged = run_cmd(self._diff_cmd(cached=True), path)
                time.sleep(LOCK_GAP)
                unstaged = run_cmd(self._diff_cmd(), path)
                time.sleep(LOCK_GAP)
                untracked = parse_untracked_files(self._status_cmd(), path)
                t1 = time.monotonic()
                if untracked:
                    unstaged += make_untracked_diff(untracked, path, self.root)
                t2 = time.monotonic()
                print(f"[perf] vcs_cmds={t1-t0:.3f}s  untracked_diff={t2-t1:.3f}s  files={len(untracked)}", flush=True)
                result = (staged, unstaged)
                duration = t2 - t0
            except Exception:
                result = ("", "")
                duration = 0.0
            self._diff_cache = result
            self._diff_end = time.monotonic()
            self._diff_cooldown = max(COOLDOWN_S, duration * COOLDOWN_STRETCH)
            if counter is not None:
                self._last_xattr = counter
            return result
