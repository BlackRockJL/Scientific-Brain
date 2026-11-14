"""Audit group D: cli.py, update.py, hook scripts and plugin texts."""
import argparse
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import add_note, init_vault, make_vault, sb

from scientific_brain import __version__, cli, review, update
from scientific_brain.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
HOOK = str(PLUGIN / "scripts" / "sb-hook.sh")
SH = shutil.which("sh")


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setenv("SB_TODAY", "2026-11-08")  # SB_NO_UPDATE=1 comes from conftest


# ---------- cli.py ----------

@pytest.mark.parametrize("project", ["../outside", "a/b", "", ".hid", ".", "..", "a\\b"])
def test_new_refuses_a_project_that_is_not_one_folder_of_notes(tmp_path, project):
    v = init_vault(tmp_path)
    code, _, _ = sb(v, "new", "finding", "esc-note", "-p", project, "--why", "w")
    assert code == 2
    assert not any(p.name == "esc-note.md" for p in v.rglob("*.md"))
    assert not (v.parent / "outside").exists()


def test_new_accepts_a_plain_project_name(tmp_path):
    v = init_vault(tmp_path)
    code, _, err = sb(v, "new", "finding", "ok-note", "-p", "solver", "--why", "w")
    assert code == 0, err
    assert (v / "notes" / "solver" / "ok-note.md").is_file()


def test_inbox_resolve_with_empty_note_is_not_booked_as_wait(tmp_path):
    v = make_vault(tmp_path)
    iid = sb(v, "capture", "an unsorted line about ftcs", "-p", "heat")[1].strip()
    code, out, _ = sb(v, "inbox", "resolve", iid, "--note", "")
    done = v / ".sb" / "inbox-done.tsv"
    assert code == 2 and not out and (not done.exists() or iid not in done.read_text())


def test_sleep_plan_out_into_a_missing_folder_creates_it(tmp_path):
    v = make_vault(tmp_path)
    target = tmp_path / "nodir" / "plan.md"
    code, out, _ = sb(v, "sleep", "plan", "-o", str(target))
    assert code == 0 and target.read_text(encoding="utf-8").strip()
    assert out == f"wrote {target}\n"


def test_sleep_plan_without_out_prints_the_plan(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "sleep", "plan")
    assert code == 0 and out.strip() and not out.startswith("wrote ")


def test_init_onto_an_existing_file_is_a_clean_error(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    code, _, err = sb(f, "init", str(f))
    assert code == 2 and "sb: error" in err


def _vault_slept_yesterday_with_verify_backlog(tmp_path, monkeypatch, n=45):
    v = init_vault(tmp_path)
    for i in range(n):
        add_note(v, f"note-number-{i}-alpha", title=f"Title {i} alpha", cue=f"cue {i} alpha beta")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "x")
    monkeypatch.setenv("SB_TODAY", "2026-11-09")  # more than GAP_HOURS later, well within a week
    return v


def test_recall_reports_sleep_due_when_stats_and_hook_do(tmp_path, monkeypatch):
    v = _vault_slept_yesterday_with_verify_backlog(tmp_path, monkeypatch)
    assert "sleep due: 45 items waiting" in sb(v, "stats")[1]
    assert "sleep due" in sb(v, "hook", "session-start")[1]
    assert "sleep due: 45 items waiting" in sb(v, "recall", "alpha")[1]


def test_session_start_does_not_announce_a_sleep_while_one_runs(tmp_path):
    v = make_vault(tmp_path)
    assert "sleep due" in sb(v, "hook", "session-start")[1]  # precondition: due (never slept)
    sb(v, "sleep", "begin")
    assert sb(v, "hook", "session-start") == (0, "", "")


def test_session_start_announces_again_when_the_marker_is_stale(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    monkeypatch.setenv("SB_TODAY", "2026-11-11")  # marker three days old
    assert "sleep due" in sb(v, "hook", "session-start")[1]


def test_hook_does_not_announce_a_sleep_for_35_open_inbox_lines(tmp_path, monkeypatch):
    """The README names the learned budget, not a fixed count of 30 lines; the code is the reference."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "30 open inbox lines" not in text and "learned budget" in text
    v = init_vault(tmp_path)
    add_note(v, "one-note")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "x")
    for i in range(35):
        sb(v, "capture", f"line number {i} unique{i}", "-p", "heat")
    monkeypatch.setenv("SB_TODAY", "2026-11-09")
    assert "sleep due" not in sb(v, "hook", "session-start")[1]


def test_readme_promises_no_veto_warning():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "vetoed notes that come back too soon" not in text


@pytest.mark.parametrize("value", ["20261108", "2026-W45-1", "11.08.2026", "2026-11-8"])
def test_sb_today_accepts_only_yyyy_mm_dd(tmp_path, monkeypatch, value):
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", value)
    code, _, err = sb(v, "stats")
    assert code == 2 and "SB_TODAY must be YYYY-MM-DD" in err


@pytest.mark.parametrize("value", ["20261108", "2026-W45-1", "garbage", "2026-13-01"])
def test_review_since_rejects_other_formats(tmp_path, value):
    v = make_vault(tmp_path)
    code, _, err = sb(v, "review", "--since", value)
    assert code == 2 and "--since must be YYYY-MM-DD" in err


def test_review_since_passes_the_date_on(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    seen = {}
    real = review.collect

    def spy(vault, day, project, since, top):
        seen.update(since=since, top=top)
        return real(vault, day, project, since, top)

    monkeypatch.setattr(review, "collect", spy)
    assert sb(v, "review", "--since", "2026-10-01")[0] == 0
    assert seen == {"since": date(2026, 10, 1), "top": 3}


def test_review_top_defaults_and_override(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    tops = []
    real = review.collect
    monkeypatch.setattr(review, "collect", lambda *a: tops.append(a[4]) or real(*a))
    sb(v, "review")
    sb(v, "review", "heat")
    sb(v, "review", "--top", "7")
    assert tops == [3, 6, 7]
    with pytest.raises(SystemExit):  # argparse: not a whole number of at least 1
        sb(v, "review", "--top", "0")


def test_review_exclude_strips_blanks_and_spaces(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    got = []
    monkeypatch.setattr(review, "draw", lambda vault, inv, m, excl: got.append(excl))
    code, _, _ = sb(v, "review", "--map", str(tmp_path / "m.png"), "--exclude", "a, b,,")
    assert code == 0 and got == [{"a", "b"}]


def test_rules_reports_a_broken_sb_toml(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text("projects = [\n", encoding="utf-8")
    code, out, err = sb(v, "rules")
    assert code == 2 and "sb: error" in err and not out


def test_rules_without_a_vault_prints_the_rules(tmp_path):
    code, out, _ = sb(tmp_path / "missing", "rules")
    assert code == 0 and "sb sleep begin" in out and "{sleep_budget}" not in out


def test_show_reports_a_missing_bibtex_entry(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "a-source", type="source", extra={"ref": "no-such-key"})
    code, out, _ = sb(v, "show", "a-source")
    assert code == 0 and "reference: no-such-key (no BibTeX entry)" in out


def test_recall_inbox_wins_over_archive(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "zebra-old", loc="archive", title="Zebra old", cue="zebra cue")
    add_note(v, "zebra-live", title="Zebra live", cue="zebra live cue")
    sb(v, "capture", "a zebra line in the inbox", "-p", "heat")
    code, out, _ = sb(v, "recall", "zebra", "--inbox", "--archive")
    assert code == 0 and "[inbox, heat" in out
    assert "zebra-old" not in out and "zebra-live" not in out


# ---------- update.py (every external call faked) ----------

def _plain(monkeypatch, info=None, claude=False, pipx=False):
    calls = []

    def run(args, timeout):
        calls.append(args)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.delenv("SB_NO_UPDATE", raising=False)
    monkeypatch.setattr(update, "_run", run)
    monkeypatch.setattr(update, "_direct_url", lambda: info)
    monkeypatch.setattr(update.shutil, "which", lambda n: "bin-" + n if {"claude": claude, "pipx": pipx}.get(n) else None)
    return calls


def test_update_never_raises_on_an_unreadable_direct_url(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_NO_UPDATE", raising=False)
    monkeypatch.setattr(update.metadata, "distribution", lambda name: SimpleNamespace(read_text=lambda f: "{not json"))
    monkeypatch.setattr(update, "_run", lambda a, t: pytest.fail("no external call expected"))
    monkeypatch.setattr(update.shutil, "which", lambda n: None)
    assert isinstance(update.update(Vault(make_vault(tmp_path))), list)


def test_plugin_update_is_recognised_in_pretty_printed_json(monkeypatch, tmp_path):
    pretty = json.dumps({"success": True, "oldVersion": "a1", "newVersion": "b2"}, indent=2)

    def run(args, timeout):
        return SimpleNamespace(stdout=pretty if args[:3] == ["claude", "plugin", "update"] else "", stderr="", returncode=0)

    _plain(monkeypatch, claude=True)
    monkeypatch.setattr(update, "_run", run)
    assert update.update(Vault(make_vault(tmp_path))) == ["plugin updated; restart Claude Code to load it"]


@pytest.mark.parametrize("text,expected", [
    ('{"oldVersion": "a", "newVersion": "b"}', True),
    ('progress line\n{"oldVersion": "a", "newVersion": "b"}', True),
    ('{\n  "oldVersion": "a",\n  "newVersion": "a"\n}', False),
    ("Updated scientific-brain to b", False),
    ("", False),
    ("[1, 2]", False),
    ('{"success": false, "oldVersion": "a", "newVersion": "b"}', False),
])
def test_changed_reads_json_and_treats_everything_else_as_unchanged(text, expected):
    assert update._changed(text) is expected


@pytest.mark.parametrize("value,off", [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("", False)])
def test_sb_no_update_switches_off_only_on_truthy_values(monkeypatch, tmp_path, value, off):
    calls = _plain(monkeypatch, claude=True)
    monkeypatch.setenv("SB_NO_UPDATE", value)
    update.update(Vault(make_vault(tmp_path)))
    assert (calls == []) is off


def test_direct_url_reads_the_distribution_file(monkeypatch):
    def dist(raw):
        return lambda name: SimpleNamespace(read_text=lambda f: raw)

    monkeypatch.setattr(update.metadata, "distribution", dist('{"url": "u"}'))
    assert update._direct_url() == {"url": "u"}
    monkeypatch.setattr(update.metadata, "distribution", dist(None))
    assert update._direct_url() is None

    def missing(name):
        raise update.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(update.metadata, "distribution", missing)
    assert update._direct_url() is None


def test_installed_falls_back_to_the_package_version(monkeypatch):
    def missing(name):
        raise update.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(update.metadata, "version", missing)
    assert update._installed() == tuple(map(int, re.match(r"(\d+)\.(\d+)\.(\d+)", __version__).groups()))
    monkeypatch.setattr(update.metadata, "version", lambda n: "weird")
    assert update._installed() is None


def test_pipx_timeout_is_reported_not_raised(monkeypatch):
    tags = f"{'a' * 40}\trefs/tags/v99.0.0\n"
    info = {"url": "https://example.org/x", "vcs_info": {"requested_revision": None}}

    def run(args, timeout):
        if args[0] == "pipx":
            raise subprocess.TimeoutExpired(args, timeout)
        return SimpleNamespace(stdout=tags, stderr="", returncode=0)

    monkeypatch.setattr(update, "_run", run)
    monkeypatch.setattr(update, "_direct_url", lambda: info)
    monkeypatch.setattr(update.shutil, "which", lambda n: "bin-pipx")
    monkeypatch.setattr(update.sys, "prefix", "pipx-home/venvs/scientific-brain")
    monkeypatch.setattr(update.metadata, "version", lambda n: "0.1.0")
    note = []
    update._cli(note)
    assert note == ["sb update timed out (the sleep goes on)"]


# ---------- plugin files ----------

def test_hooks_json_is_valid_and_wires_the_two_events():
    d = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(d) == {"PostToolUse", "SessionStart"}
    post = d["PostToolUse"][0]
    assert set(post["matcher"].split("|")) == {"Write", "Edit", "MultiEdit"}
    assert "sb-hook.sh after-edit" in post["hooks"][0]["command"]
    assert "sb-hook.sh session-start" in d["SessionStart"][0]["hooks"][0]["command"]
    for entry in (post, d["SessionStart"][0]):
        assert entry["hooks"][0]["type"] == "command" and entry["hooks"][0]["timeout"] > 0


@pytest.mark.parametrize("event,says", [("after-edit", False), ("session-start", True)])
def test_hook_script_without_sb_exits_zero(tmp_path, event, says):
    r = subprocess.run([SH, HOOK, event], capture_output=True, text=True, env={"PATH": str(tmp_path)}, timeout=20)
    assert r.returncode == 0
    assert ("not on PATH" in r.stdout) is says and not r.stderr


def test_hook_script_forwards_the_event_to_sb(tmp_path):
    fake = tmp_path / "sb"
    fake.write_text('#!/bin/sh\necho "got $*"\nexit 3\n')
    fake.chmod(0o755)
    r = subprocess.run([SH, HOOK, "after-edit"], capture_output=True, text=True, env={"PATH": str(tmp_path)}, timeout=20)
    assert r.returncode == 3 and r.stdout == "got hook after-edit\n"


def test_sleep_texts_name_the_plugin_agent_and_a_long_timeout():
    assert "`scientific-brain:sleeper`" in (PLUGIN / "skills" / "sleep" / "SKILL.md").read_text(encoding="utf-8")
    assert "timeout of 600000 ms" in (PLUGIN / "agents" / "sleeper.md").read_text(encoding="utf-8")


def test_review_skill_proposes_only_settings_that_exist():
    text = (PLUGIN / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
    assert not [w for w in ("veto weight", "horizons", "budgets") if w in text]


def _parser():
    """The argparse parser main() builds, captured at parse time."""
    class Got(Exception):
        pass

    def grab(self, *a, **k):
        raise Got(self)

    orig = argparse.ArgumentParser.parse_args
    argparse.ArgumentParser.parse_args = grab
    try:
        cli.main([])
    except Got as g:
        return g.args[0]
    finally:
        argparse.ArgumentParser.parse_args = orig


def _choices(p):
    for act in p._actions:
        if isinstance(act, argparse._SubParsersAction):
            return act.choices
    return {}


def _documented_commands():
    files = list(PLUGIN.glob("skills/*/SKILL.md")) + [PLUGIN / "agents" / "sleeper.md", ROOT / "src" / "scientific_brain" / "RULES.md"]
    for f in files:
        for span in re.findall(r"`([^`]+)`", f.read_text(encoding="utf-8")):
            span = " ".join(span.split())
            if span.startswith("sb "):
                yield f.name, span


def test_every_documented_sb_command_and_flag_exists():
    top, spans, bad = _parser(), 0, []
    for fname, span in _documented_commands():
        spans += 1
        words = span.split()[1:]
        path, chain = top, [top]
        for w in words:
            ch = _choices(path)
            if w in ch:
                path = ch[w]
                chain.append(path)
            else:
                break
        if words and not words[0].startswith(("<", "-", "[", '"')) and len(chain) == 1:
            bad.append((fname, span, "unknown command"))
            continue
        known = {o for p in chain for act in p._actions for o in act.option_strings}
        for flag in re.findall(r"(?<![\w-])(--?[a-zA-Z][\w-]*)", " ".join(words)):
            if flag not in known:
                bad.append((fname, span, flag))
    assert spans > 20 and not bad, bad
