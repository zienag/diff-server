# diff-server

A zero-dependency Python server that renders a live diff viewer in the browser. Shows git/arc changes as a GitHub-style stacked diff with a file tree sidebar.

![Python 3](https://img.shields.io/badge/python-3-blue)

## Usage

```bash
python3 server.py
```

Open in browser:

```
http://localhost:8777?path=/path/to/your/repo
```

### Query parameters

| Param | Description | Default |
|-------|-------------|---------|
| `path` | Path to a git/arc repository (required) | — |
| `refresh` | Auto-refresh interval in seconds | `3` |

## Features

- Stacked unified diff view with syntax-colored additions/deletions
- Collapsible file tree sidebar with search filter
- Auto-refresh — polls for changes and reloads when diff changes
- Untracked files shown as new file diffs
- Expand-in-place context lines (click "Show lines N–M")
- Resizable sidebar
- Supports both **git** and **arc** (Arcanum) repositories
- No dependencies — Python 3 stdlib only
