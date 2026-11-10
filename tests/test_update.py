import json
import subprocess
from datetime import date
from types import SimpleNamespace

import pytest
from helpers import make_vault, sb

from scientific_brain import update
from scientific_brain.vault import SbError, Vault

OLD, NEW = "a" * 40, "b" * 40
TAGS = f"{OLD}\trefs/tags/v0.1.0\n{NEW}\trefs/tags/v0.2.0\n{OLD}\trefs/tags/v0.10.0\n{NEW}\trefs/tags/v0.10.0^{{}}\n" \
       f"{OLD}\trefs/tags/v0.9.0\n{OLD}\trefs/tags/v2.0.0-rc1\n{OLD}\trefs/tags/nightly\n"  # newest release is v0.10.0
URL = "https://example.org/x/Scientific-Brain"


def done(out="", code=0):
    return SimpleNamespace(stdout=out, stderr="", returncode=code)


class Fake:
    """Replaces update._run and the environment probes; records every call."""

    def __init__(self, monkeypatch, info, pipx=True, claude=True, remote=TAGS, plugin=None, have="0.1.0"):
        self.calls, self.remote, self.plugin = [], remote, plugin
        monkeypatch.setattr(update.metadata, "version", lambda n: have)
        monkeypatch.delenv("SB_NO_UPDATE", raising=False)
        monkeypatch.setenv("SB_TODAY", "2026-11-08")
        monkeypatch.setattr(update, "_run", self.run)
        monkeypatch.setattr(update, "_direct_url", lambda: info)
        monkeypatch.setattr(update.shutil, "which", lambda n: "/bin/" + n if {"pipx": pipx, "claude": claude}.get(n) else None)
        monkeypatch.setattr(update.sys, "prefix", "/home/u/.local/pipx/venvs/scientific-brain" if pipx else "/opt/py")

    def run(self, args, timeout):
        self.calls.append(args)
        if isinstance(self.remote, Exception) and args[0] == "git":
            raise self.remote
        if args[0] == "git":
            return done(self.remote or "", 0 if self.remote is not None else 128)
        if args[:3] == ["claude", "plugin", "update"]:
            return done(json.dumps(self.plugin) if self.plugin is not None else "")
        return done()

    def names(self):
        return [" ".join(c[:2]) for c in self.calls]


def vcs(commit=OLD, pin=None):
    return {"url": URL, "vcs_info": {"vcs": "git", "commit_id": commit, **({"requested_revision": pin} if pin else {})}}


def vault(tmp_path, auto=True):
    root = make_vault(tmp_path)
    (root / "sb.toml").write_text(f"[update]\nauto = {str(auto).lower()}\n")
    return Vault(root)


def test_editable_or_plain_install_does_nothing(tmp_path, monkeypatch):
    v = vault(tmp_path)
    for info in ({"url": "file:///x", "dir_info": {"editable": True}}, None):
        f = Fake(monkeypatch, info, claude=False)
        assert update.update(v) == [] and f.calls == []
    f = Fake(monkeypatch, {"url": "file:///x", "dir_info": {"editable": True}})  # claude on PATH: plugin left alone too
    assert update.update(v) == [] and f.calls == []


def test_switched_off_by_config_and_env(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), plugin={"success": True, "oldVersion": "a1", "newVersion": "b2"})
    root = make_vault(tmp_path)
    (root / "sb.toml").write_text("[update]\nauto = false\n")
    assert update.update(Vault(root)) == [] and f.calls == []
    (root / "sb.toml").write_text("[update]\nauto = true\n")
    monkeypatch.setenv("SB_NO_UPDATE", "1")
    assert update.update(Vault(root)) == [] and f.calls == []


def test_config_type_checked_and_optional(tmp_path):
    root = make_vault(tmp_path)
    assert Vault(root).cfg["update"] == {"auto": True}
    (root / "sb.toml").write_text('[update]\nauto = "no"\n')
    with pytest.raises(SbError, match="'update'"):
        Vault(root)


def test_current_release_does_nothing(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), claude=False, have="0.10.0")
    assert update.update(vault(tmp_path)) == [] and f.names() == ["git ls-remote"]
    assert f.calls[0] == ["git", "ls-remote", "--tags", URL]


def test_newer_release_with_pipx_installs_the_highest_tag(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), claude=False)
    assert update.update(vault(tmp_path)) == ["sb updated to v0.10.0"]  # 0.10.0 beats 0.9.0 and 0.2.0; rc and other tags ignored
    assert ["pipx", "install", "--force", f"git+{URL}@v0.10.0"] in f.calls and ["pipx", "reinstall", "scientific-brain"] not in f.calls


def test_new_head_without_new_release_does_nothing(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(OLD), claude=False, have="0.10.0", remote=f"{NEW}\tHEAD\n{OLD}\trefs/tags/v0.10.0\n")
    assert update.update(vault(tmp_path)) == [] and f.names() == ["git ls-remote"]


def test_release_tag_pin_follows_the_releases(tmp_path, monkeypatch):
    v = vault(tmp_path)
    f = Fake(monkeypatch, vcs(pin="v0.1.0"), claude=False)
    assert update.update(v) == ["sb updated to v0.10.0"]
    assert ["pipx", "install", "--force", f"git+{URL}@v0.10.0"] in f.calls
    f = Fake(monkeypatch, vcs(pin="v0.10.0"), claude=False, have="0.10.0")  # current: silent
    assert update.update(v) == [] and f.names() == ["git ls-remote"]


@pytest.mark.parametrize("pin", ["main", OLD, "v1.0", "release-1", "v0.1.0-rc1"])
def test_other_pins_are_never_reinstalled_only_hinted(tmp_path, monkeypatch, pin):
    v = vault(tmp_path)
    f = Fake(monkeypatch, vcs(pin=pin), claude=False)
    out = update.update(v)
    assert len(out) == 1 and f"pinned to {pin}" in out[0] and "v0.10.0" in out[0]
    assert f.names() == ["git ls-remote"]
    f = Fake(monkeypatch, vcs(pin=pin), claude=False, have="0.10.0")  # pinned and current: silent
    assert update.update(v) == [] and f.names() == ["git ls-remote"]


def test_no_release_tag_is_a_hint_not_an_update(tmp_path, monkeypatch):
    v = vault(tmp_path)
    for remote in ("", f"{NEW}\tHEAD\n{OLD}\trefs/tags/v1.0\n{OLD}\trefs/tags/v1.0.0-rc1\n"):
        f = Fake(monkeypatch, vcs(), claude=False, remote=remote)
        out = update.update(v)
        assert len(out) == 1 and "no release tag" in out[0] and f.names() == ["git ls-remote"]


def test_unknown_installed_version_does_nothing(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), claude=False, have="dev")
    assert update.update(vault(tmp_path)) == [] and f.names() == ["git ls-remote"]


def test_newer_release_without_pipx_hints(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), pipx=False, claude=False)
    out = update.update(vault(tmp_path))
    assert len(out) == 1 and "pipx install --force" in out[0] and f"{URL}@v0.10.0" in out[0]
    assert f.names() == ["git ls-remote"]


def test_install_failure_is_a_note(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), claude=False)
    orig = f.run
    f.run = lambda a, t: done("", 1) if a[0] == "pipx" else orig(a, t)
    monkeypatch.setattr(update, "_run", f.run)
    assert "failed" in update.update(vault(tmp_path))[0]


@pytest.mark.parametrize("remote", [subprocess.TimeoutExpired("git", 20), None, OSError("no git")])
def test_ls_remote_trouble_still_sleeps(tmp_path, monkeypatch, remote):
    f = Fake(monkeypatch, vcs(), claude=False, remote=remote)
    root = make_vault(tmp_path)
    (root / "sb.toml").write_text("[update]\nauto = true\n")
    out = update.update(Vault(root))
    assert len(out) == 1 and "(the sleep goes on)" in out[0] and not any(c[0] == "pipx" for c in f.calls)
    code, text, _ = sb(root, "sleep", "begin")
    assert code == 0 and "(the sleep goes on)" in text and (root / ".sb" / "sleeping").read_text().strip() == "2026-11-08"


def test_plugin_update_reported(tmp_path, monkeypatch):
    f = Fake(monkeypatch, None, plugin={"success": True, "oldVersion": "a1", "newVersion": "b2"})
    assert update.update(vault(tmp_path)) == ["plugin updated; restart Claude Code to load it"]
    assert f.names() == ["claude plugin", "claude plugin"]
    assert f.calls[0][:4] == ["claude", "plugin", "marketplace", "update"]


@pytest.mark.parametrize("plugin", [{"success": True, "oldVersion": "a1", "newVersion": "a1"}, {}, [1], None])
def test_plugin_unchanged_or_unclear_is_silent(tmp_path, monkeypatch, plugin):
    Fake(monkeypatch, None, plugin=plugin)
    assert update.update(vault(tmp_path)) == []


def test_plugin_timeout_is_a_note(tmp_path, monkeypatch):
    Fake(monkeypatch, None)

    def boom(args, timeout):
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(update, "_run", boom)
    assert "timed out" in update.update(vault(tmp_path))[0]


def test_begin_prints_update_notes(tmp_path, monkeypatch):
    Fake(monkeypatch, vcs(), claude=False)
    root = make_vault(tmp_path)
    (root / "sb.toml").write_text("[update]\nauto = true\n")
    code, out, _ = sb(root, "sleep", "begin")
    assert code == 0 and "sleep started" in out and "sb updated to v0.10.0" in out
