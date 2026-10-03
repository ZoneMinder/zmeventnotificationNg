"""Tests for install.sh functions, run hermetically.

install.sh is sourced (its main section is skipped when sourced) inside a
throwaway copy of the repo, with every TARGET_* path, the web user and the
package tools pointed at a sandbox. Nothing touches system paths, the
network, or the real repo checkout.
"""

import getpass
import grp
import os
import shutil
import stat
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

FAKE_PYTHON = """#!/bin/bash
# Stand-in for `python3 -m venv DIR`: lays out a minimal venv.
if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    mkdir -p "$3/bin"
    printf '#!/bin/sh\\nexit 1\\n' > "$3/bin/python"
    printf '#!/bin/sh\\nexit 0\\n' > "$3/bin/pip"
    chmod +x "$3/bin/python" "$3/bin/pip"
    echo "include-system-site-packages = false" > "$3/pyvenv.cfg"
    exit 0
fi
exec python3 "$@"
"""


@pytest.fixture
def sandbox(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(
        ".git", ".claude", "__pycache__", ".pytest_cache"))
    fake_py = tmp_path / "fakepython"
    fake_py.write_text(FAKE_PYTHON)
    fake_py.chmod(0o755)
    data = tmp_path / "data"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHON": "python3",
        "PIP": "true",
        "INSTALLER": "true",
        "WGET": "true",
        "USE_VENV": "no",
        "TARGET_CONFIG": str(tmp_path / "cfg"),
        "TARGET_DATA": str(data),
        "TARGET_BIN_HOOK": str(data / "bin"),
        "TARGET_BIN_ES": str(tmp_path / "bin_es"),
        "TARGET_PERL_LIB": str(tmp_path / "perl"),
        "ZM_VENV": str(tmp_path / "venv"),
        "WEB_OWNER": getpass.getuser(),
        "WEB_GROUP": grp.getgrgid(os.getgid()).gr_name,
    }
    os.makedirs(env["TARGET_CONFIG"])
    return {"tmp": tmp_path, "repo": repo, "env": env, "fake_python": str(fake_py)}


def run(sandbox, script, **env):
    full_env = dict(sandbox["env"], **env)
    return subprocess.run(
        ["bash", "-c", "source ./install.sh\n" + script],
        cwd=sandbox["repo"], env=full_env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=120)


def cfg(sandbox, name):
    return os.path.join(sandbox["env"]["TARGET_CONFIG"], name)


def write_es_configs(sandbox, target):
    for name, text in (
        ("zmeventnotification.yml", "general:\n  secrets: /etc/zm/secrets.yml\n"),
        ("secrets.yml", "secrets:\n  ZM_USER: me\n"),
        ("es_rules.yml", "notifications:\n  monitors: {}\n"),
    ):
        with open(os.path.join(target, name), "w") as f:
            f.write(text)


def test_direct_run_still_reaches_main(sandbox):
    # The source guard must not stop a normal run: --help comes from main.
    r = subprocess.run(["bash", "./install.sh", "--help"], cwd=sandbox["repo"],
                       env=sandbox["env"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "--install-es" in r.stdout


# ── check_args ──────────────────────────────────────────────────────────

ARGS_SCRIPT = ('cmd_args=({args}); check_args; '
               'echo "$INSTALL_HOOK_CONFIG $INSTALL_ES_CONFIG $HOOK_CONFIG_UPGRADE"')


@pytest.mark.parametrize("args,expected", [
    ("", "prompt prompt yes"),
    ("--install-hook --install-config", "yes prompt yes"),
    ("--no-hook-config-upgrade", "prompt prompt no"),
    ("--no-install-hook", "no prompt yes"),
    ("--no-install-hook --install-hook-config", "yes prompt yes"),
    ("--no-install-es", "prompt no yes"),
])
def test_check_args_defaults(sandbox, args, expected):
    r = run(sandbox, ARGS_SCRIPT.format(args=args))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[-3:] == expected.split()


# ── ensure_venv ─────────────────────────────────────────────────────────

def _venv_script():
    return "ensure_venv_bootstrap_pkgs() { :; }\nensure_venv\n"


def test_ensure_venv_keeps_working_venv(sandbox):
    venv = sandbox["tmp"] / "venv"
    (venv / "bin").mkdir(parents=True)
    for exe in ("python", "pip"):
        p = venv / "bin" / exe
        p.write_text("#!/bin/sh\nexit 1\n" if exe == "python" else "#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    (venv / "marker").write_text("keep")
    r = run(sandbox, _venv_script(), USE_VENV="yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Venv already exists" in r.stdout
    assert (venv / "marker").read_text() == "keep"


def test_ensure_venv_recreates_venv_without_pip(sandbox):
    venv = sandbox["tmp"] / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "stale").write_text("x")
    r = run(sandbox, _venv_script(), USE_VENV="yes", PYTHON=sandbox["fake_python"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (venv / "stale").exists()
    assert os.access(venv / "bin" / "pip", os.X_OK)
    assert "include-system-site-packages = true" in (venv / "pyvenv.cfg").read_text()


def test_ensure_venv_creates_missing_venv(sandbox):
    venv = sandbox["tmp"] / "venv"
    r = run(sandbox, _venv_script(), USE_VENV="yes", PYTHON=sandbox["fake_python"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.access(venv / "bin" / "pip", os.X_OK)


def test_ensure_venv_refuses_populated_non_venv_dir(sandbox):
    # e.g. --venv-path pointed at /opt by mistake: never rm -rf it.
    venv = sandbox["tmp"] / "venv"
    venv.mkdir()
    (venv / "important.txt").write_text("data")
    r = run(sandbox, _venv_script() + "echo reached", USE_VENV="yes",
            PYTHON=sandbox["fake_python"])
    assert r.returncode == 1
    assert "reached" not in r.stdout
    assert (venv / "important.txt").read_text() == "data"
    assert not (venv / "bin").exists()


def test_ensure_venv_uses_existing_empty_dir(sandbox):
    venv = sandbox["tmp"] / "venv"
    venv.mkdir()
    r = run(sandbox, _venv_script(), USE_VENV="yes", PYTHON=sandbox["fake_python"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.access(venv / "bin" / "pip", os.X_OK)


# ── config install / upgrade ────────────────────────────────────────────

def test_install_hook_config_rewrites_etc_zm_paths(sandbox):
    target = sandbox["env"]["TARGET_CONFIG"]
    with open(cfg(sandbox, "objectconfig.yml"), "w") as f:
        f.write("general:\n  secrets: /etc/zm/secrets.yml\n")
    r = run(sandbox, "install_hook_config", DOWNLOAD_MODELS="no")
    assert r.returncode == 0, r.stdout + r.stderr
    text = open(cfg(sandbox, "objectconfig.yml")).read()
    assert "secrets: {}/secrets.yml".format(target) in text
    assert "/etc/zm/" not in text


def test_install_es_config_rewrites_etc_zm_paths(sandbox):
    target = sandbox["env"]["TARGET_CONFIG"]
    write_es_configs(sandbox, target)
    r = run(sandbox, "PY_SUDO=''; install_es_config")
    assert r.returncode == 0, r.stdout + r.stderr
    text = open(cfg(sandbox, "zmeventnotification.yml")).read()
    assert "secrets: {}/secrets.yml".format(target) in text


def test_path_rewrite_idempotent_when_target_under_etc_zm(sandbox):
    # TARGET_CONFIG=/etc/zm/es: a second run used to give /etc/zm/es/es/...
    target = str(sandbox["tmp"] / "etc" / "zm" / "es")
    os.makedirs(target)
    write_es_configs(sandbox, target)
    with open(os.path.join(target, "objectconfig.yml"), "w") as f:
        f.write("general:\n  secrets: /etc/zm/secrets.yml\n")
    script = "PY_SUDO=''; install_es_config; install_hook_config\n"
    for _ in range(2):
        r = run(sandbox, script, TARGET_CONFIG=target, DOWNLOAD_MODELS="no")
        assert r.returncode == 0, r.stdout + r.stderr
    for name in ("zmeventnotification.yml", "objectconfig.yml"):
        text = open(os.path.join(target, name)).read()
        assert "secrets: {}/secrets.yml\n".format(target) in text, name


def test_install_es_config_installs_example_secrets(sandbox):
    target = sandbox["env"]["TARGET_CONFIG"]
    write_es_configs(sandbox, target)
    os.remove(cfg(sandbox, "secrets.yml"))
    r = run(sandbox, "PY_SUDO=''; install_es_config")
    assert r.returncode == 0, r.stdout + r.stderr
    with open(cfg(sandbox, "secrets.yml")) as f:
        assert f.read() == open(os.path.join(REPO, "secrets.example.yml")).read()
    assert os.stat(cfg(sandbox, "secrets.yml")).st_mode & stat.S_IRUSR


def test_fresh_secrets_not_world_readable(sandbox):
    write_es_configs(sandbox, sandbox["env"]["TARGET_CONFIG"])
    os.remove(cfg(sandbox, "secrets.yml"))
    r = run(sandbox, "umask 022; PY_SUDO=''; install_es_config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat.S_IMODE(os.stat(cfg(sandbox, "secrets.yml")).st_mode) == 0o640


def test_migrated_secrets_not_world_readable(sandbox):
    write_es_configs(sandbox, sandbox["env"]["TARGET_CONFIG"])
    os.remove(cfg(sandbox, "secrets.yml"))
    with open(cfg(sandbox, "secrets.ini"), "w") as f:
        f.write("[secrets]\nzm_user=me\n")
    r = run(sandbox, "umask 022; PY_SUDO=''; install_es_config")
    assert r.returncode == 0, r.stdout + r.stderr
    st = os.stat(cfg(sandbox, "secrets.yml"))
    assert stat.S_IMODE(st.st_mode) == 0o640
    assert st.st_uid == os.getuid()  # WEB_OWNER in the sandbox
    assert "ZM_USER: me" in open(cfg(sandbox, "secrets.yml")).read()


# ── install_hook ────────────────────────────────────────────────────────

HOOK_ENV = dict(INSTALL_OPENCV="no", DOWNLOAD_MODELS="no")
HOOK_FILES = ("zm_detect.py", "zm_train_faces.py", "zm_event_start.sh",
              "zm_event_end.sh", "pushapi_pushover.py")


def test_install_hook_default_layout(sandbox):
    r = run(sandbox, "PY_SUDO=''; install_hook", **HOOK_ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    bin_hook = sandbox["env"]["TARGET_BIN_HOOK"]
    for name in HOOK_FILES:
        assert os.access(os.path.join(bin_hook, name), os.X_OK), name
    start = open(os.path.join(bin_hook, "zm_event_start.sh")).read()
    assert 'CONFIG_FILE="{}/objectconfig.yml"'.format(sandbox["env"]["TARGET_CONFIG"]) in start


# ── check_deps ──────────────────────────────────────────────────────────

def test_check_deps_requires_pip_without_venv(sandbox):
    r = run(sandbox, "INSTALL_ES=no; INSTALL_HOOK=yes; check_deps; echo reached",
            USE_VENV="no", PIP="/nonexistent/pip3")
    assert r.returncode == 1
    assert "reached" not in r.stdout
    assert "/nonexistent/pip3 is not installed" in r.stdout
