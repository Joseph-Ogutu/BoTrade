import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan_history.py"
FAKE_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # built at runtime so the test file itself holds no literal


def run(repo):
    env = {**os.environ, "SCAN_REPO": str(repo)}
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, check=True).stdout


def git(repo, *a):
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", *ident, *a], cwd=repo, check=True, capture_output=True)


def test_scan_reports_path_and_hash_but_never_the_secret(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "config.py").write_text(f'TOKEN = "{FAKE_GH}"\n')
    (tmp_path / ".env").write_text("X=1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "c1")
    out = run(tmp_path)
    assert "config.py" in out and "github-token" in out and ".env" in out
    assert FAKE_GH not in out and FAKE_GH[4:] not in out


def test_scan_finds_secret_that_was_deleted_later(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text(f"key {FAKE_GH}\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "add")
    (tmp_path / "a.txt").write_text("clean\n")
    git(tmp_path, "commit", "-q", "-am", "remove")
    out = run(tmp_path)
    assert "a.txt" in out and "github-token" in out and FAKE_GH not in out


def test_clean_repo_reports_zero(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "ok.md").write_text("hello\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "ok")
    out = run(tmp_path)
    assert "[github-token] 0 path(s)" in out and "[private-key-block] 0 path(s)" in out


def _strict(repo):
    env = {**os.environ, "SCAN_REPO": str(repo)}
    return subprocess.run([sys.executable, str(SCRIPT), "--strict"], capture_output=True, text=True, env=env)


def test_strict_mode_fails_on_a_secret_shaped_string_and_never_prints_it(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "config.py").write_text(f'TOKEN = "{FAKE_GH}"\n')
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "c1")
    r = _strict(tmp_path)
    assert r.returncode == 1 and FAKE_GH not in r.stdout + r.stderr


def test_strict_mode_passes_on_a_clean_repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("hello\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "c1")
    assert _strict(tmp_path).returncode == 0
