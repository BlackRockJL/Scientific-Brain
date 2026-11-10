import json
import subprocess
from datetime import date
from types import SimpleNamespace

import pytest
from helpers import make_vault, sb

from scientific_brain import update
from scientific_brain.vault import SbError, Vault

OLD, NEW = "a" * 40, "b" * 40
URL = "https://example.org/x/Scientific-Brain"


def done(out="", code=0):
    return SimpleNamespace(stdout=out, stderr="", returncode=code)


class Fake:
    """Replaces update._run and the environment probes; records every call."""

    def __init__(self, monkeypatch, info, pipx=True, claude=True, remote=NEW, plugin=None):
        self.calls, self.remote, self.plugin = [], remote, plugin
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
            return done(f"{self.remote}\tHEAD\n" if self.remote else "", 0 if self.remote else 128)
        if args[:3] == ["claude", "plugin", "update"]:
            return done(json.dumps(self.plugin) if self.plugin is not None else "")
        return done()

    def names(self):
        return [" ".join(c[:2]) for c in self.calls]


def vcs(commit=OLD):
    return {"url": URL, "vcs_info": {"vcs": "git", "commit_id": commit}}


def vault(tmp_path):
    return Vault(make_vault(tmp_path))


def test_editable_or_plain_install_does_nothing(tmp_path, monkeypatch):
    v = vault(tmp_path)
    for info in ({"url": "file:///x", "dir_info": {"editable": True}}, None):
        f = Fake(monkeypatch, info, claude=False)
        assert update.update(v) == [] and f.calls == []
    f = Fake(monkeypatch, {"url": "file:///x", "dir_info": {"editable": True}})  # claude on PATH: plugin left alone too
    assert update.update(v) == [] and f.calls == []


def test_switched_off_by_config_and_env(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs())
    root = make_vault(tmp_path)
    (root / "sb.toml").write_text("[update]\nauto = false\n")
    assert update.update(Vault(root)) == [] and f.calls == []
    (root / "sb.toml").write_text("")
    monkeypatch.setenv("SB_NO_UPDATE", "1")
    assert update.update(Vault(root)) == [] and f.calls == []


def test_config_type_checked_and_optional(tmp_path):
    root = make_vault(tmp_path)
    assert Vault(root).cfg["update"] == {"auto": True}
    (root / "sb.toml").write_text('[update]\nauto = "no"\n')
    with pytest.raises(SbError, match="'update'"):
        Vault(root)


def test_same_sha_no_reinstall(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(NEW), claude=False)
    assert update.update(vault(tmp_path)) == [] and f.names() == ["git ls-remote"]


def test_new_sha_with_pipx_reinstalls(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), claude=False)
    assert update.update(vault(tmp_path)) == ["sb updated to bbbbbbb"]
    assert ["pipx", "reinstall", "scientific-brain"] in f.calls


def test_new_sha_without_pipx_hints(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), pipx=False, claude=False)
    out = update.update(vault(tmp_path))
    assert len(out) == 1 and "pipx reinstall" in out[0] and URL in out[0]
    assert f.names() == ["git ls-remote"]


def test_reinstall_failure_is_a_note(tmp_path, monkeypatch):
    f = Fake(monkeypatch, vcs(), claude=False)
    orig = f.run
    f.run = lambda a, t: done("", 1) if a[0] == "pipx" else orig(a, t)
    monkeypatch.setattr(update, "_run", f.run)
    assert "failed" in update.update(vault(tmp_path))[0]


@pytest.mark.parametrize("remote", [subprocess.TimeoutExpired("git", 20), None, OSError("no git")])
def test_ls_remote_trouble_still_sleeps(tmp_path, monkeypatch, remote):
    f = Fake(monkeypatch, vcs(), claude=False, remote=remote)
    root = make_vault(tmp_path)
    out = update.update(Vault(root))
    assert len(out) == 1 and "(the sleep goes on)" in out[0] and ["pipx", "reinstall"] not in f.calls
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
    code, out, _ = sb(make_vault(tmp_path), "sleep", "begin")
    assert code == 0 and "sleep started" in out and "sb updated to bbbbbbb" in out
