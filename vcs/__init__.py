"""VCS detection and diff retrieval — delegates to backend plugins."""

import os

from vcs.mono import MonoBackend
from vcs.git import GitBackend

_BACKENDS = [MonoBackend, GitBackend]  # monorepo VCS preferred over git at the same level
_backend_cache = {}  # path -> VCSBackend | None


def _get_backend(path):
    if path in _backend_cache:
        return _backend_cache[path]

    # Walk up directories, checking all backends at each level.
    # Closest root wins; monorepo VCS preferred over git at the same level.
    current = path
    while current != "/":
        for cls in _BACKENDS:
            if cls.has_root_marker(current):
                backend = cls(current)
                _backend_cache[path] = backend
                return backend
        current = os.path.dirname(current)

    # Fallback detection (e.g., FUSE mounts without marker directories)
    for cls in _BACKENDS:
        root = cls.detect_fallback(path)
        if root:
            backend = cls(root)
            _backend_cache[path] = backend
            return backend

    _backend_cache[path] = None
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
