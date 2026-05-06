"""Monorepo VCS backend.

Fingerprint strategy:
  1. FUSE xattr counter — O(1), no subprocess. When the VFS counter is
     readable, it IS the fingerprint. Counter increments on any tracked
     mutation so the xattr alone is a complete dirty signal.
  2. Fallback (nativefs / no xattr) — layered:
     • base = `info --json` hash + `status --short` output (throttled by
       adaptive cooldown — subprocess is expensive on big nativefs)
     • live overlay = lstat(mtime, size) of every file that was modified
       per the last status run. Detects content edits to already-modified
       files cheaply (~0.01ms per stat) without re-running status.

Full diff (`get_diff`) is keyed on the fingerprint string. Whenever the
fingerprint changes, the cache invalidates and fresh `diff` subprocesses
run. The mutex serializes concurrent callers, so N tabs polling at once
share one subprocess burst per fingerprint.

Adaptive cooldown: penalty = max(base, duration * stretch). Slow repos rest
longer between status runs — VCS subprocesses stay <1/3 of wall-clock.
"""

import json
import os
import threading
import time

from vcs.base import run_cmd, run_subprocess, parse_untracked_files, make_untracked_diff, SUBPROCESS_TIMEOUT

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

        # Full diff cache. Keyed by fingerprint string so it's invalidated
        # as soon as the fingerprint changes (xattr counter or status text).
        self._diff_cache = None
        self._diff_cache_key = None

        # Fingerprint fallback state (no xattr path only):
        #   _fp_base   — cached subprocess portion (hash + status output)
        #   _fp_files  — list of files from last status, for mtime polling
        #   _fp_last   — last fingerprint returned (for get_diff cache key)
        self._fp_base = None
        self._fp_files = []
        self._fp_last = None
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
                    print(f"[mono] nativefs checkout — stretched fp cooldown to {NATIVEFS_COOLDOWN_S}s", flush=True)
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

    @staticmethod
    def _parse_status_files(status):
        """Extract file paths from short-format status output."""
        files = []
        for line in status.splitlines():
            if len(line) < 4:
                continue
            p = line[3:].strip()
            if " -> " in p:
                p = p.split(" -> ", 1)[1]
            if p.startswith('"') and p.endswith('"'):
                p = p[1:-1]
            files.append(p)
        return files

    def _mtime_overlay(self, path, files):
        """Cheap content-change signal: lstat(mtime,size) of every modified file.
        Runs in microseconds per file — safe to call on every fingerprint poll."""
        parts = []
        for f in files:
            try:
                st = os.lstat(os.path.join(path, f))
                parts.append(f"{f}:{int(st.st_mtime_ns)}:{st.st_size}")
            except OSError:
                parts.append(f"{f}:x")
        return "|".join(parts)

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

            # Fallback: layered fingerprint. Base (hash + status) is refreshed
            # at most once per cooldown window; mtime overlay is refreshed every
            # call so content edits to already-modified files are seen instantly.
            age = time.monotonic() - self._fp_end
            if self._fp_base is not None and age < self._fp_cooldown:
                overlay = self._mtime_overlay(path, self._fp_files)
                result = f"{self._fp_base}|mt:{overlay}"
                self._fp_last = result
                return result

            t0 = time.monotonic()
            try:
                hash_ = self._info_hash()
                time.sleep(LOCK_GAP)
                status = run_cmd(self._status_cmd(), path)
                base_fp = f"h:{hash_}|{status}"
                files = self._parse_status_files(status)
            except Exception:
                base_fp = ""
                files = []
            duration = time.monotonic() - t0
            overlay = self._mtime_overlay(path, files)
            result = f"{base_fp}|mt:{overlay}"
            self._fp_base = base_fp
            self._fp_files = files
            self._fp_last = result
            self._fp_end = time.monotonic()
            base = NATIVEFS_COOLDOWN_S if self._is_nativefs else COOLDOWN_S
            self._fp_cooldown = max(base, duration * COOLDOWN_STRETCH)
            print(f"[mono] fingerprint FRESH dt={duration:.2f}s files={len(files)} next-cooldown={self._fp_cooldown:.1f}s", flush=True)
            return result

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
                [_VCS_CMD, "show", f"HEAD:{file}"], self.root,
                capture_output=True, timeout=SUBPROCESS_TIMEOUT,
            )
            return result.stdout if result.returncode == 0 else None
        return None

    def get_diff(self, path):
        with self._mutex:
            # Compute cache key from CURRENT state — don't rely on fp_last
            # which might be stale (e.g. server ran overnight, first /content
            # arrives before any /hash).
            counter = self._xattr_counter()
            if counter is not None:
                key = f"x:{counter}"
            elif self._fp_base is not None:
                overlay = self._mtime_overlay(path, self._fp_files)
                key = f"{self._fp_base}|mt:{overlay}"
            else:
                key = None

            if self._diff_cache is not None and key is not None and key == self._diff_cache_key:
                print(f"[mono] get_diff CACHED key={key[:40]!r}", flush=True)
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
                print(f"[perf] vcs_cmds={t1-t0:.3f}s untracked_diff={t2-t1:.3f}s files={len(untracked)}", flush=True)
                result = (staged, unstaged)
            except Exception:
                result = ("", "")

            self._diff_cache = result
            # Re-read key AFTER subprocess (mtimes might have shifted mid-run).
            # Concurrent changes → key differs on next call → cache miss.
            if counter is not None:
                self._diff_cache_key = f"x:{counter}"
            elif self._fp_base is not None:
                self._diff_cache_key = f"{self._fp_base}|mt:{self._mtime_overlay(path, self._fp_files)}"
            else:
                self._diff_cache_key = key
            return result
