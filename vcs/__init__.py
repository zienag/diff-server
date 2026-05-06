"""VCS detection and diff retrieval — delegates to backend plugins."""

import os

from vcs.base import get_activity
from vcs.mono import MonoBackend
from vcs.git import GitBackend

_BACKENDS = [MonoBackend, GitBackend]  # monorepo VCS preferred over git at the same level
_root_backends = {}  # root -> backend instance (one poller per root)
_path_cache = {}     # path -> backend (fast lookup)


def _get_backend(path):
    if path in _path_cache:
        return _path_cache[path]

    # Walk up directories, checking all backends at each level.
    # Closest root wins; monorepo VCS preferred over git at the same level.
    current = path
    while current != "/":
        for cls in _BACKENDS:
            if cls.has_root_marker(current):
                root = current
                if root not in _root_backends:
                    _root_backends[root] = cls(root)
                _path_cache[path] = _root_backends[root]
                return _root_backends[root]
        current = os.path.dirname(current)

    # Fallback detection (e.g., FUSE mounts without marker directories)
    for cls in _BACKENDS:
        root = cls.detect_fallback(path)
        if root:
            if root not in _root_backends:
                _root_backends[root] = cls(root)
            _path_cache[path] = _root_backends[root]
            return _root_backends[root]

    _path_cache[path] = None
    return None


def detect_vcs(path):
    """Return (vcs_name, root) or (None, None)."""
    backend = _get_backend(path)
    if backend:
        return backend.name, backend.root
    return None, None


def get_diff_fingerprint(path):
    """Return a string fingerprint that changes when VCS state changes."""
    backend = _get_backend(path)
    if not backend:
        return ""
    return backend.fingerprint(path)


def get_retry_after(path):
    """Seconds until backend is ready to run a fresh burst. 0 if ready now."""
    import time
    backend = _get_backend(path)
    if not backend:
        return 0.0
    end = getattr(backend, "_fp_end", 0.0)
    cooldown = getattr(backend, "_fp_cooldown", 0.0)
    ready_at = end + cooldown
    return max(0.0, ready_at - time.monotonic())


def get_diff(path):
    """Return (vcs_name, root, staged_diff, unstaged_diff)."""
    backend = _get_backend(path)
    if not backend:
        return None, None, "", ""
    try:
        staged, unstaged = backend.get_diff(path)
        return backend.name, backend.root, staged, unstaged
    except Exception:
        return backend.name, backend.root, "", ""


def get_blob(repo_path, file, ref):
    """Return file contents (bytes) at the given ref, or None.
    ref ∈ {'head','worktree'}. file is relative to backend root."""
    backend = _get_backend(repo_path)
    if not backend:
        return None
    try:
        return backend.get_blob(file, ref)
    except Exception:
        return None
