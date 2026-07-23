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

# --- Cross-repo (ES + pyzm) e2e: must pass before releasing ---
# Runs THIS ES hook chain against the pyzm checkout on real models, plus the
# pyzm<->ES contract test. Aborts the release on any failure.
# Overrides: PYZM_DIR=/path (pyzm checkout), SKIP_E2E=1 (emergency bypass).
if [ "${SKIP_E2E:-}" = "1" ]; then
    echo "WARNING: SKIP_E2E=1 -- skipping cross-repo e2e validation"
else
    PYZM_DIR="${PYZM_DIR:-$(cd "$REPO_DIR/../pyzmNg" 2>/dev/null && pwd || echo "$REPO_DIR/../pyzmNg")}"
    if [ ! -d "$PYZM_DIR" ]; then
        echo "ERROR: pyzm repo not found at $PYZM_DIR (set PYZM_DIR=... or SKIP_E2E=1)"
        exit 1
    fi
    echo "Running cross-repo e2e (this ES hook chain vs pyzm) ..."
    if ! ( cd "$REPO_DIR" && make release-gate PYZM_SRC="$PYZM_DIR" ); then
        echo "ERROR: cross-repo e2e FAILED -- aborting release"
        exit 1
    fi
    echo "Cross-repo e2e passed."
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

# Verify the running system actually reflects this release. install.sh is
# lenient (prints ERROR but keeps going), so we check artifacts, versions and
# run the entry points as the web user — the uid that matters at runtime.
# Hard failures (missing files, version mismatch, broken imports) flip the
# gate to FAIL; config-dependent issues (Perl deps, Version.pm) are warnings.
validate_install() {
    local venv="${ZM_VENV:-/opt/zoneminder/venv}"
    local web_owner="${WEB_OWNER:-www-data}"
    local bin_es="${TARGET_BIN_ES:-/usr/bin}"
    local bin_hook="${TARGET_BIN_HOOK:-/var/lib/zmeventnotification/bin}"
    local perl_lib="${TARGET_PERL_LIB:-/usr/share/perl5}"
    local py="${venv}/bin/python"
    local pm="${perl_lib}/ZmEventNotification/Version.pm"
    local fail=0

    echo
    echo "──── Validating install (v${VER}) ────"

    # 1. Required artifacts present
    for f in \
        "${bin_es}/zmeventnotification.pl" \
        "${bin_hook}/zm_detect.py" "${bin_hook}/zm_train_faces.py" \
        "${bin_hook}/zm_event_start.sh" "${bin_hook}/zm_event_end.sh" \
        "$pm"; do
        if [ -f "$f" ]; then echo "  OK   present: $f"
        else echo "  FAIL missing: $f"; fail=1; fi
    done

    if [ -x "$py" ]; then
        # 2. Hook package version matches the release
        local hookver
        hookver=$(sudo -u "$web_owner" "$py" -c "import zmes_hook_helpers as z; print(z.__version__)" 2>/dev/null || true)
        if [ "$hookver" == "$VER" ]; then echo "  OK   hook package version: ${hookver}"
        else echo "  FAIL hook package version: got '${hookver}', expected '${VER}'"; fail=1; fi

        # 3. pyzm importable and satisfies the setup.py pin
        local pyzmver pin lowest
        pyzmver=$(sudo -u "$web_owner" "$py" -c "from importlib.metadata import version; print(version('pyzm'))" 2>/dev/null || true)
        pin=$(grep -oP "pyzm>=\K[0-9][0-9.]*" "$SETUP_PY" || true)
        if [ -z "$pyzmver" ]; then
            echo "  FAIL pyzm not importable"; fail=1
        elif [ -n "$pin" ]; then
            lowest=$(printf '%s\n%s\n' "$pin" "$pyzmver" | sort -V | head -1)
            if [ "$lowest" == "$pin" ]; then echo "  OK   pyzm ${pyzmver} satisfies pin >=${pin}"
            else echo "  FAIL pyzm ${pyzmver} is older than pin >=${pin}"; fail=1; fi
        else
            echo "  OK   pyzm version: ${pyzmver}"
        fi

        # 3b. pyzm[full] extras present (this maintainer box runs the full pyzm)
        if sudo -u "$web_owner" "$py" -c "import fastapi, uvicorn, ultralytics, streamlit" >/dev/null 2>&1; then
            echo "  OK   pyzm[full] extras import (fastapi/uvicorn/ultralytics/streamlit)"
        else
            echo "  FAIL pyzm[full] extras missing — run: ${py%/python}/pip install 'pyzm[full]'"; fail=1
        fi

        # 4. OpenCV still importable and >= 4.13 (default ONNX models need it)
        local cvver
        cvver=$(sudo -u "$web_owner" "$py" -c "import cv2; print(cv2.__version__)" 2>/dev/null || true)
        if [ -z "$cvver" ]; then echo "  FAIL cv2 not importable"; fail=1
        else echo "  OK   cv2 version: ${cvver}"; fi

        # 5. End-to-end: zm_detect.py runs via its patched venv shebang
        if sudo -u "$web_owner" "${bin_hook}/zm_detect.py" --bareversion >/dev/null 2>&1; then
            echo "  OK   zm_detect.py runs (--bareversion)"
        else
            echo "  FAIL zm_detect.py failed to run"; fail=1
        fi
    else
        echo "  FAIL venv python not found at ${py}"; fail=1
    fi

    # 6. Version.pm fallback matches (advisory)
    if [ -f "$pm" ]; then
        local pmver
        pmver=$(grep -oP "FALLBACK_VERSION = '\K[^']+" "$pm" || true)
        if [ "$pmver" == "$VER" ]; then echo "  OK   Version.pm fallback: ${pmver}"
        else echo "  WARN Version.pm fallback: got '${pmver}', expected '${VER}'"; fi
    fi

    # 7. ES Perl script compiles against installed modules (advisory)
    if perl -c "${bin_es}/zmeventnotification.pl" >/dev/null 2>&1; then
        echo "  OK   zmeventnotification.pl compiles"
    else
        echo "  WARN zmeventnotification.pl did not compile (check Perl deps)"
    fi

    echo
    if [ "$fail" -eq 0 ]; then
        echo "Validation PASSED for v${VER}."
    else
        echo "Validation FAILED — see FAIL lines above."
        return 1
    fi
}

# --- Step 6: Optionally update this local system ---
# Same path a user follows: run install.sh with all components enabled,
# non-interactively. We deliberately do NOT pass --install-opencv: cv2 is
# typically a source/system build here, and apt's python3-opencv would
# downgrade it and break the ONNX (YOLOv11/26) models installed by default.
echo
INSTALL_CMD="sudo ./install.sh --install-es --install-hook --install-config --no-interactive"
read -p "Update this local system to v${VER} now (runs: ${INSTALL_CMD})? [y/N] " do_install
if [[ "$do_install" =~ ^[Yy]$ ]]; then
    echo
    eval "$INSTALL_CMD"

    # install.sh installs only core pyzm (all most users need). On this
    # maintainer box we want the full pyzm — remote ML server + training UI
    # extras — so pull pyzm[full] at the pinned version into the same venv.
    PYZM_VENV="${ZM_VENV:-/opt/zoneminder/venv}"
    PYZM_PIN=$(grep -oP "pyzm>=\K[0-9][0-9.]*" "$SETUP_PY" || true)
    if [ -x "${PYZM_VENV}/bin/pip" ]; then
        echo
        echo "Installing pyzm[full]${PYZM_PIN:+>=$PYZM_PIN} into ${PYZM_VENV} ..."
        sudo -H "${PYZM_VENV}/bin/pip" install "pyzm[full]${PYZM_PIN:+>=$PYZM_PIN}"
    else
        echo "WARNING: ${PYZM_VENV}/bin/pip not found; skipping pyzm[full] install."
    fi

    # install.sh runs tools/install_doctor.py as its final step (config-level
    # diagnostics); validate_install confirms the release itself landed.
    validate_install || true
else
    echo "Skipped. To update later, run from the repo root:"
    echo "  ${INSTALL_CMD}"
fi
