"""Preflight tests for scripts/make_release.sh, run hermetically.

The script runs from a throwaway git repo (with a local bare repo as
origin) and stub gh / git-cliff / curl on PATH, so nothing reaches GitHub,
PyPI or the real checkout. stdin is closed, so the run always stops at the
first interactive prompt ("Proceed?") at the latest.
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUMMARY = "--- Release summary ---"


def _stub(path, body):
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


@pytest.fixture
def release(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _stub(stubs / "git-cliff", "exit 0")
    _stub(stubs / "curl", "exit 1")
    _stub(stubs / "gh", 'if [[ "$1 $2" == "auth token" ]]; then echo tok; exit 0; fi\n'
                        'echo "unexpected gh $*" >&2; exit 1')

    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(os.path.join(REPO, "scripts", "make_release.sh"), work / "scripts")
    (work / "hook" / "zmes_hook_helpers").mkdir(parents=True)
    (work / "hook" / "zmes_hook_helpers" / "__init__.py").write_text(
        '__version__ = "1.2.3"\nVERSION=__version__\n')
    (work / "hook" / "setup.py").write_text("install_requires=['pyzm>=2.0.0']\n")
    (work / "VERSION").write_text("1.2.3\n")

    env = {
        "PATH": "{}:/usr/bin:/bin".format(stubs),
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "SKIP_E2E": "1",
    }

    def git(*args, cwd=work):
        subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                       capture_output=True)

    git("init", "-q", "--bare", str(tmp_path / "origin.git"), cwd=tmp_path)
    git("init", "-q", "-b", "master")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("remote", "add", "origin", str(tmp_path / "origin.git"))

    def run(answers=None):
        kw = {"input": answers} if answers is not None else {"stdin": subprocess.DEVNULL}
        return subprocess.run(["bash", "scripts/make_release.sh"], cwd=work, env=env,
                              capture_output=True, text=True, timeout=60, **kw)

    return {"stubs": stubs, "git": git, "run": run, "origin": tmp_path / "origin.git",
            "work": work}


def _origin_refs(release):
    out = subprocess.run(["git", "--git-dir", str(release["origin"]), "show-ref"],
                         capture_output=True, text=True)
    return out.stdout


def test_missing_git_cliff_aborts(release):
    os.remove(release["stubs"] / "git-cliff")
    r = release["run"]()
    assert r.returncode == 1
    assert "ERROR: git-cliff not found" in r.stdout


def test_clean_master_reaches_confirmation(release):
    r = release["run"]()
    assert SUMMARY in r.stdout, r.stdout + r.stderr
    assert "Branch:       master" in r.stdout
    assert "ERROR" not in r.stdout
    assert _origin_refs(release) == ""  # stopped at the prompt, nothing pushed


def test_gh_auth_failure_aborts_before_any_step(release):
    _stub(release["stubs"] / "gh", "exit 1")
    r = release["run"]()
    assert r.returncode != 0
    assert "gh auth login" in r.stdout
    assert SUMMARY not in r.stdout


def test_non_master_branch_aborts_before_any_push(release):
    release["git"]("checkout", "-q", "-b", "feature")
    r = release["run"]()
    assert r.returncode != 0
    assert "must be run on master" in r.stdout
    assert SUMMARY not in r.stdout
    assert _origin_refs(release) == ""


def _pyzm_on_pypi(release, version):
    _stub(release["stubs"] / "curl",
          'echo \'{"info": {"version": "%s"}}\'' % version)


def _real_setup_pin(release, pin):
    # same form as the real hook/setup.py: pyzm with the [ml] extra
    (release["work"] / "hook" / "setup.py").write_text(
        "install_requires=[\n    'numpy', 'requests', 'imutils',\n"
        "    '%s', 'scikit-learn',\n]\n" % pin)
    release["git"]("commit", "-q", "-am", "real pin")


def test_pyzm_pin_with_extras_is_found(release):
    _real_setup_pin(release, "pyzm[ml]>=2.5.0")
    _pyzm_on_pypi(release, "2.5.4")
    r = release["run"]()
    assert "could not find a 'pyzm>=' pin" not in r.stdout
    assert "is 'pyzm[ml]>=2.5.0', latest on PyPI is 2.5.4" in r.stdout


def test_pyzm_pin_bump_keeps_extras(release):
    _real_setup_pin(release, "pyzm[ml]>=2.5.0")
    _pyzm_on_pypi(release, "2.5.4")
    release["run"](answers="y\n")
    setup = (release["work"] / "hook" / "setup.py").read_text()
    assert "'pyzm[ml]>=2.5.4'" in setup
    assert "2.5.0" not in setup


def test_pyzm_pin_without_extras_still_bumps(release):
    _pyzm_on_pypi(release, "2.5.4")
    release["run"](answers="y\n")
    assert "pyzm>=2.5.4" in (release["work"] / "hook" / "setup.py").read_text()


def test_pyzm_pin_already_current_no_prompt(release):
    _real_setup_pin(release, "pyzm[ml]>=2.5.4")
    _pyzm_on_pypi(release, "2.5.4")
    r = release["run"]()
    assert "latest on PyPI" not in r.stdout
    assert "could not find" not in r.stdout
