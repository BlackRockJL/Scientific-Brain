"""Self-update at the start of a sleep: the CLI (git install) and the Claude Code plugin; never raises."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import metadata

from .vault import Vault

REPO = "scientific-brain"  # distribution, marketplace and plugin name


def _run(args: list[str], timeout: int):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _direct_url() -> dict | None:
    """direct_url.json of the installed distribution; None if absent (e.g. a plain index install)."""
    try:
        raw = metadata.distribution(REPO).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    return json.loads(raw) if raw else None


def _cli(note: list[str]):
    info = _direct_url()
    if not info or info.get("dir_info", {}).get("editable") or "vcs_info" not in info:
        return  # developer install or not from git: nothing to do
    url, have = info["url"], info["vcs_info"].get("commit_id", "")
    try:
        r = _run(["git", "ls-remote", url, "HEAD"], 20)
    except subprocess.TimeoutExpired:
        note.append("sb update check timed out (the sleep goes on)")
        return
    parts = r.stdout.split()
    if r.returncode or not parts:
        note.append("sb update check failed: remote not reachable (the sleep goes on)")
        return
    new = parts[0]
    if new == have:
        return
    pipx = shutil.which("pipx") and "pipx" in sys.prefix and "venvs" in sys.prefix
    if not pipx:
        note.append(f"a newer sb is available; update with 'pipx reinstall {REPO}' "
                    f"or 'pip install --upgrade --force-reinstall {url}'")
        return
    try:
        r = _run(["pipx", "reinstall", REPO], 180)
    except subprocess.TimeoutExpired:
        note.append("sb update timed out (the sleep goes on)")
        return
    note.append(f"sb updated to {new[:7]}" if r.returncode == 0
                else f"sb update failed: pipx reinstall exited {r.returncode} (the sleep goes on)")


def _changed(out: str) -> bool:
    """Whether the --json result of `claude plugin update` reports a new version; unclear counts as no."""
    lines = out.strip().splitlines()
    try:
        d = json.loads(lines[-1]) if lines else None
    except ValueError:
        return False
    if not isinstance(d, dict):
        return False
    if d.get("success") is False or d.get("error"):
        return False
    old, new = d.get("oldVersion"), d.get("newVersion")
    if old and new:
        return old != new
    return d.get("updated") is True or d.get("status") == "updated"


def _plugin(note: list[str]):
    if not shutil.which("claude"):
        return
    try:
        _run(["claude", "plugin", "marketplace", "update", REPO], 60)
        r = _run(["claude", "plugin", "update", f"{REPO}@{REPO}", "--json"], 60)
    except subprocess.TimeoutExpired:
        note.append("plugin update timed out (the sleep goes on)")
        return
    if r.returncode == 0 and _changed(r.stdout):
        note.append("plugin updated; restart Claude Code to load it")


def update(v: Vault) -> list[str]:
    """Message lines; empty means nothing to say."""
    if os.environ.get("SB_NO_UPDATE") or not v.cfg["update"]["auto"]:
        return []
    if ((_direct_url() or {}).get("dir_info") or {}).get("editable"):
        return []  # a developer install leaves both the CLI and the plugin alone
    note: list[str] = []
    for step in (_cli, _plugin):
        try:
            step(note)
        except Exception as e:  # noqa: BLE001 - an update must never stop a sleep
            note.append(f"update step {step.__name__.strip('_')} failed: {type(e).__name__} (the sleep goes on)")
    return note
