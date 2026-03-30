#!/bin/bash
# Verify all VCS commands used by diff-server work correctly.
# Usage: ./check_vcs.sh /path/to/repo

set -e

REPO="${1:?Usage: $0 /path/to/repo}"
cd "$REPO"

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    printf "%-40s" "$desc"
    if output=$("$@" 2>&1); then
        echo "OK (${#output} bytes)"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        echo "  cmd: $*"
        echo "  output: $(echo "$output" | head -3)"
        FAIL=$((FAIL + 1))
    fi
}

# Detect VCS
if arc root >/dev/null 2>&1; then
    VCS="arc"
elif git rev-parse --git-dir >/dev/null 2>&1; then
    VCS="git"
else
    echo "FAIL: $REPO is not a git or arc repository"
    exit 1
fi

echo "VCS: $VCS"
echo "Repo: $REPO"
echo ""

if [ "$VCS" = "arc" ]; then
    # _status_cmd(vcs="arc")
    check "arc status --short"              arc status --short
    # _diff_stat_cmd(vcs="arc")
    check "arc diff --stat"                 arc diff --stat
    # _diff_stat_cmd(vcs="arc", cached=True)
    check "arc diff --cached --stat"        arc diff --cached --stat
    # _diff_cmd(vcs="arc")
    check "arc diff"                        arc diff
    # _diff_cmd(vcs="arc", cached=True)
    check "arc diff --cached"               arc diff --cached
else
    # _status_cmd(vcs="git")
    check "git status --porcelain"          git status --porcelain
    # _diff_stat_cmd(vcs="git")
    check "git diff --no-renames --stat"    git diff --no-renames --stat
    # _diff_stat_cmd(vcs="git", cached=True)
    check "git diff --cached --no-renames --stat"  git diff --cached --no-renames --stat
    # _diff_cmd(vcs="git")
    check "git diff --no-renames"           git diff --no-renames
    # _diff_cmd(vcs="git", cached=True)
    check "git diff --cached --no-renames"  git diff --cached --no-renames
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
