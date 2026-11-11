"""Self-update at the start of a sleep, on by default (off: [update] auto = false or SB_NO_UPDATE=1): the CLI (git install, newest release tag) and the Claude Code plugin; never raises."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from importlib import metadata

from . import __version__
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


TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)(?:\^\{\})?\Z")  # release tags vX.Y.Z; ^{} marks an annotated tag's target


RELEASE = re.compile(r"v\d+\.\d+\.\d+\Z")  # a pin of this form follows the releases


def _installed() -> tuple[int, ...] | None:
    """Installed version as a tuple; None if unknown."""
    try:
        raw = metadata.version(REPO)
    except metadata.PackageNotFoundError:
        raw = __version__
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", raw)
    return tuple(map(int, m.groups())) if m else None


def _latest(ls_remote: str) -> tuple[int, ...] | None:
    """Highest vX.Y.Z tag in `git ls-remote --tags` output; None if there is none."""
    found = [tuple(map(int, m.groups())) for ln in ls_remote.splitlines()
             if len(f := ln.split()) == 2 and (m := TAG.match(f[1]))]
    return max(found, default=None)


def _cli(note: list[str]):
    info = _direct_url()
    if not info or info.get("dir_info", {}).get("editable") or "vcs_info" not in info:
        return  # developer install or not from git: nothing to do
    url, pinned = info["url"], info["vcs_info"].get("requested_revision")
    try:
        r = _run(["git", "ls-remote", "--tags", url], 20)
    except subprocess.TimeoutExpired:
        note.append("sb update check timed out (the sleep goes on)")
        return
    if r.returncode:
        note.append("sb update check failed: remote not reachable (the sleep goes on)")
        return
    latest, have = _latest(r.stdout), _installed()
    if latest is None:
        note.append("sb not updated: no release tag vX.Y.Z found on the remote")
        return
    if have is None or latest <= have:
        return
    tag = "v" + ".".join(map(str, latest))
    spec = f"git+{url}@{tag}"
    if pinned and not RELEASE.match(pinned):  # a pin to a branch, commit or other tag is never reinstalled
        note.append(f"sb is pinned to {pinned}; release {tag} is available: pipx install --force {spec}")
        return
    if not (shutil.which("pipx") and "pipx" in sys.prefix and "venvs" in sys.prefix):
        note.append(f"sb release {tag} is available; update with 'pipx install --force {spec}' "
                    f"or 'pip install --upgrade --force-reinstall {spec}'")
        return
    try:
        r = _run(["pipx", "install", "--force", spec], 180)
    except subprocess.TimeoutExpired:
        note.append("sb update timed out (the sleep goes on)")
        return
    note.append(f"sb updated to {tag}" if r.returncode == 0
                else f"sb update failed: pipx install exited {r.returncode} (the sleep goes on)")


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
