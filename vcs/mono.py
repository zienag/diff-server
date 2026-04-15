"""Monorepo VCS backend.

Fingerprint strategy:
  1. FUSE xattr counter — O(1), no subprocess. When the VFS counter is
     readable, it IS the fingerprint. A mutation to any watched path
     increments it, so no status scan is needed to detect change.
  2. Fallback (nativefs / no xattr) — `info --json` for cheap commit-hash
     check + periodic cached status scan within adaptive cooldown.

Full diff (`get_diff`) runs subprocess bursts only on cache miss. Cache key
is the xattr counter snapshot captured BEFORE the burst runs (so changes
that happen mid-burst drive a refresh next call). When xattr is unavailable
we fall back to a time-based cooldown window so N tabs polling at once do
not multiply VCS load.

Adaptive cooldown: penalty = max(base, duration * stretch). Slow repos rest
longer between bursts — VCS subprocesses stay <1/3 of wall-clock.
"""

import json
import os
import threading
import time

from vcs.base import run_cmd, run_subprocess, parse_untracked_files, make_untracked_diff

LOCK_GAP = 0.3
COOLDOWN_S = 3.0
COOLDOWN_STRETCH = 2.0
# Nativefs checkouts can't use xattr and status is slow; rest longer between bursts.
NATIVEFS_COOLDOWN_S = 15.0

_VCS_DIR = "\x2e\x61\x72\x63"
_VCS_CMD = _VCS_DIR[1:]
_XATTR_COUNTER = f"user.{_VCS_CMD}.get.counter"


class MonoBackend:
    name = _VCS_CMD

    def __init__(self, root):
        self.root = root
        self._mutex = threading.Lock()

        # Full diff cache. Key: xattr counter snapshot at compute time
        # (or None when falling back to time cooldown).
        self._diff_cache = None
        self._diff_cache_counter = None
        self._diff_end = 0.0
        self._diff_cooldown = COOLDOWN_S

        # Fingerprint fallback cache (no xattr path only).
        self._fp_cache = None
        self._fp_end = 0.0
        self._fp_cooldown = COOLDOWN_S

        # Platform detection — probed lazily once.
        self._xattr_available = True
        self._mount_probed = False
        self._is_nativefs = False

    def _xattr_counter(self):
        """O(1) VFS counter. Returns str or None if unavailable."""
        if not self._xattr_available:
            return None
        try:
            raw = os.getxattr(self.root, _XATTR_COUNTER)
            return raw.decode().strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
        except OSError:
            self._xattr_available = False
            print(f"[mono] xattr {_XATTR_COUNTER} unavailable — falling back to info+status", flush=True)
            return None
        except AttributeError:
            self._xattr_available = False
            return None

    def _probe_mount(self):
        """One-shot: detect nativefs vs FUSE mount. Sets nativefs cooldown."""
        if self._mount_probed:
            return
        self._mount_probed = True
        try:
            result = run_subprocess(
                [_VCS_CMD, "info", "--json", "--mount"], self.root,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                info = json.loads(result.stdout)
                if info.get("repository_type") == "nativefs":
                    self._is_nativefs = True
                    self._fp_cooldown = max(self._fp_cooldown, NATIVEFS_COOLDOWN_S)
                    self._diff_cooldown = max(self._diff_cooldown, NATIVEFS_COOLDOWN_S)
                    print(f"[mono] nativefs checkout — stretched cooldown to {NATIVEFS_COOLDOWN_S}s", flush=True)
        except Exception:
            pass

    def _info_hash(self):
        """Cheap commit-hash query (~10ms). '' on failure."""
        try:
            result = run_subprocess(
                [_VCS_CMD, "info", "--json"], self.root,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout).get("hash", "") or ""
        except Exception:
            pass
        return ""

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
        # --ignored: arc otherwise reports globally-ignored dirs as ??
        # --no-ahead-behind / --no-sync-status: skip remote tracking roundtrips
        return [_VCS_CMD, "status", "--short", "--ignored",
                "--no-ahead-behind", "--no-sync-status"]

    def _diff_cmd(self, cached=False, stat=False):
        cmd = [_VCS_CMD, "diff"]
        if cached:
            cmd.append("--cached")
        if stat:
            cmd.append("--stat")
        return cmd

    def fingerprint(self, path):
        with self._mutex:
            # Fast path: xattr counter IS the fingerprint on FUSE mounts.
            counter = self._xattr_counter()
            if counter is not None:
                return f"x:{counter}"

            # One-shot probe to learn if we're on nativefs (affects cooldown).
            if not self._mount_probed:
                self._probe_mount()
                # xattr may succeed after the probe if the first attempt was flaky.
                counter = self._xattr_counter()
                if counter is not None:
                    return f"x:{counter}"

            # Fallback: arc info hash + periodic cached status scan.
            age = time.monotonic() - self._fp_end
            if self._fp_cache is not None and age < self._fp_cooldown:
                print(f"[mono] fingerprint CACHED age={age*1000:.0f}ms cooldown={self._fp_cooldown:.1f}s", flush=True)
                return self._fp_cache

            t0 = time.monotonic()
            try:
                hash_ = self._info_hash()
                time.sleep(LOCK_GAP)
                status = run_cmd(self._status_cmd(), path)
                result = f"h:{hash_}|{status}"
            except Exception:
                result = ""
            duration = time.monotonic() - t0
            self._fp_cache = result
            self._fp_end = time.monotonic()
            base = NATIVEFS_COOLDOWN_S if self._is_nativefs else COOLDOWN_S
            self._fp_cooldown = max(base, duration * COOLDOWN_STRETCH)
            print(f"[mono] fingerprint FRESH dt={duration:.2f}s next-cooldown={self._fp_cooldown:.1f}s", flush=True)
            return result

    def get_diff(self, path):
        with self._mutex:
            counter = self._xattr_counter()

            # Cache hit: xattr counter matches the snapshot saved on last success.
            if counter is not None and self._diff_cache is not None and counter == self._diff_cache_counter:
                print(f"[mono] get_diff XATTR-CACHED counter={counter}", flush=True)
                return self._diff_cache

            # Fallback cache hit: within time cooldown window.
            age = time.monotonic() - self._diff_end
            if self._diff_cache is not None and age < self._diff_cooldown:
                print(f"[mono] get_diff CACHED age={age*1000:.0f}ms cooldown={self._diff_cooldown:.1f}s", flush=True)
                return self._diff_cache

            # Snapshot counter BEFORE running so concurrent changes during the
            # subprocess burst trigger a refresh on the next call.
            snapshot = counter
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
                print(f"[perf] vcs_cmds={t1-t0:.3f}s untracked_diff={t2-t1:.3f}s files={len(untracked)}", flush=True)
                result = (staged, unstaged)
                duration = t2 - t0
            except Exception:
                result = ("", "")
                duration = 0.0

            self._diff_cache = result
            self._diff_cache_counter = snapshot
            self._diff_end = time.monotonic()
            base = NATIVEFS_COOLDOWN_S if self._is_nativefs else COOLDOWN_S
            self._diff_cooldown = max(base, duration * COOLDOWN_STRETCH)
            return result
