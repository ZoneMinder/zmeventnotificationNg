#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

GH_REPO="ZoneMinder/zmeventnotificationNg"

# --- Read version ---
if [ ! -f ./VERSION ]; then
    echo "ERROR: VERSION file not found"
    exit 1
fi
VER=$(cat ./VERSION | tr -d '[:space:]')

# Keep hook package version in sync with VERSION file
INIT_PY="hook/zmes_hook_helpers/__init__.py"
SETUP_PY="hook/setup.py"
sed -i "s/^__version__ = \".*\"/__version__ = \"${VER}\"/" "$INIT_PY"

echo "=== Release v${VER} ==="
echo

# --- Preflight checks ---
if ! command -v git-cliff &>/dev/null; then
    echo "ERROR: git-cliff not found. Install it from https://git-cliff.org"
    exit 1
fi
if ! command -v gh &>/dev/null; then
    echo "ERROR: gh CLI not found. Install it from https://cli.github.com"
    exit 1
fi
export GITHUB_TOKEN=$(gh auth token)

# --- Keep pyzm dependency pin in sync with latest PyPI release ---
if command -v curl &>/dev/null && command -v python3 &>/dev/null; then
    CURRENT_PYZM=$(grep -oP "pyzm>=\K[0-9][0-9.]*" "$SETUP_PY" || true)
    LATEST_PYZM=$(curl -fsSL https://pypi.org/pypi/pyzm/json 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])" 2>/dev/null || true)
    if [ -z "$CURRENT_PYZM" ]; then
        echo "WARNING: could not find a 'pyzm>=' pin in $SETUP_PY; skipping pyzm sync."
        echo
    elif [ -z "$LATEST_PYZM" ]; then
        echo "WARNING: could not fetch latest pyzm version from PyPI; skipping pyzm sync."
        echo
    elif [ "$CURRENT_PYZM" != "$LATEST_PYZM" ]; then
        echo "pyzm pin in $SETUP_PY is 'pyzm>=${CURRENT_PYZM}', latest on PyPI is ${LATEST_PYZM}."
        read -p "Bump pin to 'pyzm>=${LATEST_PYZM}'? [y/N] " bump_pyzm
        if [[ "$bump_pyzm" =~ ^[Yy]$ ]]; then
            sed -i "s/pyzm>=${CURRENT_PYZM}/pyzm>=${LATEST_PYZM}/" "$SETUP_PY"
            echo "  Updated $SETUP_PY: pyzm>=${LATEST_PYZM}"
        else
            echo "  Leaving pyzm pin unchanged."
        fi
        echo
    fi
else
    echo "WARNING: curl/python3 not available; skipping pyzm version sync."
    echo
fi

# --- Step 1: Check if tag already exists ---
if git rev-parse "v${VER}" &>/dev/null; then
    # Compute bumped patch version
    MAJOR=$(echo "$VER" | cut -d. -f1)
    MINOR=$(echo "$VER" | cut -d. -f2)
    PATCH=$(echo "$VER" | cut -d. -f3)
    BUMPED="${MAJOR}.${MINOR}.$((PATCH + 1))"

    echo "Tag v${VER} already exists."
    echo "  1) Overwrite existing release (v${VER})"
    echo "  2) Bump version: v${VER} -> v${BUMPED}"
    read -p "Choose [1/2] or anything else to abort: " choice
    case "$choice" in
        1)
            echo "  Deleting old release and tag v${VER} ..."
            gh release delete "v${VER}" --repo "$GH_REPO" --yes 2>/dev/null || true
            git tag -d "v${VER}"
            git push origin --delete "v${VER}" 2>/dev/null || true
            ;;
        2)
            echo "  Bumping version: v${VER} -> v${BUMPED}"
            VER="$BUMPED"
            echo "$VER" > VERSION
            sed -i "s/^__version__ = \".*\"/__version__ = \"${VER}\"/" "$INIT_PY"
            git add VERSION "$INIT_PY" "$SETUP_PY"
            git commit -m "chore: bump version to v${VER}"
            git push origin master
            echo "  Done."
            ;;
        *)
            echo "Aborted."
            exit 0
            ;;
    esac
    echo
fi

# --- Step 2: Check for uncommitted files ---
DIRTY_FILES=$(git status --porcelain)
if [ -n "$DIRTY_FILES" ]; then
    # Check if the only dirty files are VERSION, __init__.py and setup.py
    NON_VERSION=$(echo "$DIRTY_FILES" | grep -v ' VERSION$' | grep -v "$INIT_PY" | grep -v "$SETUP_PY" || true)
    if [ -n "$NON_VERSION" ]; then
        echo "ERROR: Uncommitted files besides VERSION, $INIT_PY and $SETUP_PY:"
        echo "$NON_VERSION"
        exit 1
    fi
    echo "Committing version files ..."
    git add VERSION "$INIT_PY" "$SETUP_PY"
    git commit -m "chore: bump version to v${VER}"
    git push origin master
    echo "  Done."
    echo
fi

# --- Confirm before proceeding ---
BRANCH=$(git rev-parse --abbrev-ref HEAD)
REMOTE_URL=$(git remote get-url origin)
echo "--- Release summary ---"
echo "  Version:      v${VER}"
echo "  Branch:       ${BRANCH}"
echo "  Remote:       ${REMOTE_URL}"
echo "  GitHub repo:  ${GH_REPO}"
echo
echo "This will: generate CHANGELOG, commit, tag, push, and create GitHub release."
read -p "Proceed? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi
echo

# --- Step 3: Generate and commit changelog ---
echo "Generating CHANGELOG.md ..."
git-cliff --tag "v${VER}" -o CHANGELOG.md
echo "  Done."

echo "Committing CHANGELOG.md ..."
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG for v${VER}"
git push origin master
echo "  Done."
echo

# --- Step 4: Tag ---
echo "Creating tag v${VER} ..."
git tag -a "v${VER}" -m "v${VER}"
git push origin --tags
echo "  Done."
echo

# --- Step 5: Create GitHub Release ---
echo "Creating GitHub Release v${VER} ..."
NOTES_FILE=$(mktemp)
git-cliff --latest --strip header > "$NOTES_FILE" 2>/dev/null
gh release create "v${VER}" --repo "$GH_REPO" --title "v${VER}" --notes-file "$NOTES_FILE"
rm -f "$NOTES_FILE"
echo "  Done."

echo
echo "=== Release v${VER} complete ==="
