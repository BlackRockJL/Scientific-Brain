import math
import os
from datetime import date, timedelta

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain import usage
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)
OLD = "2025-01-01"  # created long ago, so the 'created' fallback never applies when events exist


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def vault_with(tmp_path, notes, rows=()):
    """Empty vault with the given (slug, type) notes created long ago, and usage rows (days_ago, caller, kind, slug)."""
    root = tmp_path / "v"
    assert sb(root, "init", str(root))[0] == 0
    for slug, typ in notes:
        add_note(root, slug, project="p", type=typ, extra={"created": OLD, "updated": OLD})
    (root / ".sb" / "usage.tsv").write_text("".join(f"{ago(d)}\t{c}\t{k}\t{s}\n" for d, c, k, s in rows), encoding="utf-8")
    return root


def cands(root):
    return {s: (t, a, th) for s, t, a, th in usage.forget_candidates(Vault(root), TODAY)}


def a_of(root, slug):
    return {x[0]: x[3] for x in usage.scores(Vault(root), TODAY)}[slug]


def usage_lines(v):
    return [ln.split("\t") for ln in (v / ".sb" / "usage.tsv").read_text().splitlines()]


def test_activation_boundary_90_and_91_days(tmp_path):
    root = vault_with(tmp_path, [("n90", "finding"), ("n91", "finding")],
                      [(90, "work", "show", "n90"), (91, "work", "show", "n91")])
    c = cands(root)
    assert list(c) == ["n91"]
    typ, a, th = c["n91"]
    assert typ == "finding" and a == pytest.approx(math.log(92 ** -0.5)) and th == pytest.approx(math.log(91 ** -0.5))


def test_horizon_180_for_decision_and_derivation(tmp_path):
    notes = [("dec", "decision"), ("der", "derivation"), ("fin", "finding")]
    root = vault_with(tmp_path, notes, [(150, "work", "show", s) for s, _ in notes])
    assert set(cands(root)) == {"fin"}
    root2 = vault_with(tmp_path / "b", notes, [(181, "work", "show", s) for s, _ in notes])
    assert set(cands(root2)) == {"dec", "der", "fin"}
    root3 = vault_with(tmp_path / "c", notes, [(180, "work", "show", s) for s, _ in notes])
    assert set(cands(root3)) == {"fin"}


def test_only_links_from_active_notes_protect(tmp_path):
    names = ("hub", "src", "a", "b", "leaf", "lone")
    root = vault_with(tmp_path, [(s, "finding") for s in names], [(5, "work", "show", "hub")])
    links = {"hub": "[[src]]", "a": "[[b]] [[leaf]]", "b": "[[a]]"}
    for s, body in links.items():
        add_note(root, s, project="p", extra={"created": OLD, "updated": OLD}, body=f"\nSee {body}.\n")
    add_note(root, "gone", loc="archive", project="p", extra={"created": OLD, "updated": OLD}, body="\nSee [[lone]].\n")
    assert set(cands(root)) == {"a", "b", "leaf", "lone"}  # src is used through hub; a faded cycle still fades


def test_same_day_events_count_once_and_the_strongest_wins(tmp_path):
    root = vault_with(tmp_path, [("a", "finding")],
                      [(10, "work", "show", "a"), (10, "work", "recall", "a"), (10, "work", "create", "a"),
                       (10, "work", "show", "a")])
    ev = usage.events(Vault(root))["a"]
    assert ev == {TODAY - timedelta(days=10): 1.0}
    assert a_of(root, "a") == pytest.approx(math.log(11 ** -0.5))
    (root / ".sb" / "usage.tsv").write_text(f"{ago(10)}\twork\trecall\ta\n{ago(10)}\twork\tshow\ta\n", encoding="utf-8")
    assert usage.events(Vault(root))["a"][TODAY - timedelta(days=10)] == 1.0
    (root / ".sb" / "usage.tsv").write_text(f"{ago(10)}\twork\trecall\ta\n", encoding="utf-8")
    assert usage.events(Vault(root))["a"][TODAY - timedelta(days=10)] == 0.2


def test_uses_on_different_days_add_up(tmp_path):
    root = vault_with(tmp_path, [("a", "finding")], [(91, "work", "show", "a"), (5, "work", "recall", "a")])
    assert a_of(root, "a") == pytest.approx(math.log(92 ** -0.5 + 0.2 * 6 ** -0.5))
    assert cands(root) == {}  # the recent recall keeps it, as ACT-R intends


def test_sleep_caller_does_not_count_except_veto(tmp_path):
    root = vault_with(tmp_path, [("a", "finding"), ("b", "finding")],
                      [(91, "work", "show", "a"), (1, "sleep", "show", "a"), (1, "sleep", "recall", "a"),
                       (91, "work", "show", "b"), (1, "sleep", "veto", "b")])
    assert set(cands(root)) == {"a"}
    assert a_of(root, "b") == pytest.approx(math.log(92 ** -0.5 + 0.5 * 2 ** -0.5))


def test_note_without_events_uses_created_and_weights_are_configurable(tmp_path):
    root = vault_with(tmp_path, [("a", "finding")])  # created long ago, no events
    add_note(root, "b", project="p", extra={"created": ago(91)})
    add_note(root, "c", project="p", extra={"created": ago(89)})
    assert set(cands(root)) == {"a", "b"}
    (root / "sb.toml").write_text("[activation]\nrecall = 1.0\nh = 10\n", encoding="utf-8")
    (root / ".sb" / "usage.tsv").write_text(f"{ago(11)}\twork\trecall\tc\n{ago(10)}\twork\trecall\tb\n", encoding="utf-8")
    assert set(cands(root)) == {"a", "c"}  # H = 10 now, recall weighs 1.0: b (10 days) stays, c (11 days) goes


def test_rename_chain_carries_old_usage_to_the_current_slug(tmp_path):
    root = vault_with(tmp_path, [("c", "finding")], [(91, "work", "show", "a"), (3, "work", "show", "b")])
    (root / ".sb" / "renames.tsv").write_text("a\tb\t2026-11-06\nb\tc\t2026-11-07\n", encoding="utf-8")  # old rows: no row numbers
    ev = usage.events(Vault(root))
    assert set(ev) == {"c"} and len(ev["c"]) == 2
    assert a_of(root, "c") == pytest.approx(math.log(92 ** -0.5 + 4 ** -0.5))
    (root / ".sb" / "renames.tsv").write_text("a\tb\t2026-11-06\nb\ta\t2026-11-07\n", encoding="utf-8")  # renamed back
    assert set(usage.events(Vault(root))) == {"a"}


def test_cli_rename_appends_to_renames_and_keeps_usage(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "rename", "ftcs-stability-limit", "ftcs-limit")[0] == 0
    assert sb(v, "rename", "ftcs-limit", "ftcs-lim")[0] == 0
    assert (v / ".sb" / "renames.tsv").read_text().splitlines() == [
        "ftcs-stability-limit\tftcs-limit\t2026-11-08\t7\t0", "ftcs-limit\tftcs-lim\t2026-11-08\t7\t0"]
    assert "ftcs-lim" in usage.events(Vault(v)) and "ftcs-stability-limit" not in usage.events(Vault(v))


def test_commands_log_usage_with_caller(tmp_path):
    v = make_vault(tmp_path)
    (v / ".sb" / "usage.tsv").write_text("", encoding="utf-8")
    sb(v, "new", "finding", "fresh-one", "-p", "heat", "--why", "own measurement")
    sb(v, "show", "scheme-choice")
    sb(v, "recall", "stability", "limit")  # hits in notes/ are logged, archived and inbox hits are not
    sb(v, "recall", "--archive", "guess")
    rows = usage_lines(v)
    assert ["2026-11-08", "work", "create", "fresh-one"] in rows and ["2026-11-08", "work", "show", "scheme-choice"] in rows
    assert ["2026-11-08", "work", "recall", "ftcs-stability-limit"] in rows
    assert not any(r[3] == "ftcs-cfl-guess" and r[2] == "recall" for r in rows)
    (v / ".sb" / "sleeping").write_text("2026-11-08\n", encoding="utf-8")
    sb(v, "show", "ftcs-stability-limit")
    assert ["2026-11-08", "sleep", "show", "ftcs-stability-limit"] in usage_lines(v)


def bump(p):
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_edit_is_detected_from_a_changed_file(tmp_path):
    v = make_vault(tmp_path)
    log = v / ".sb" / "usage.tsv"
    log.write_text("", encoding="utf-8")
    sb(v, "map")  # first build: nothing counts as edited
    assert log.read_text() == ""
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("second order", "2nd order"), encoding="utf-8")
    bump(p)
    sb(v, "map")
    assert usage_lines(v) == [["2026-11-08", "sync", "edit", "scheme-choice"]]  # found by the index, not done in a session
    sb(v, "map")
    assert len(usage_lines(v)) == 1  # seen once
    add_note(v, "fresh-note")  # a new file counts as an edit too
    sb(v, "map")
    assert ["2026-11-08", "sync", "edit", "fresh-note"] in usage_lines(v)
    (v / ".sb" / "sleeping").write_text("2026-11-08\n", encoding="utf-8")
    bump(p)
    sb(v, "map")
    assert ["2026-11-08", "sleep", "edit", "scheme-choice"] in usage_lines(v)
    sb(v, "check", "--rebuild")  # a full rebuild is not an edit
    n = len(usage_lines(v))
    bump(p)
    sb(v, "check", "--rebuild")
    assert len(usage_lines(v)) == n
    assert sb(v, "rename", "scheme-choice", "scheme-pick")[0] == 0  # rewriting links is not an edit
    sb(v, "map")
    assert not any(r[2] == "edit" and r[3] == "ftcs-stability-limit" for r in usage_lines(v))


def test_new_warns_from_the_fourth_creation_of_the_day(tmp_path):
    v = make_vault(tmp_path)
    (v / ".sb" / "usage.tsv").write_text("", encoding="utf-8")
    for i in range(3):
        out = sb(v, "new", "finding", f"fresh-{i}", "-p", "heat", "--why", "w")[1]
        assert "warning" not in out
    out = sb(v, "new", "finding", "fresh-3", "-p", "heat", "--why", "w")[1]
    assert "warning: 3 notes were created today already" in out
    (v / ".sb" / "usage.tsv").write_text(f"{ago(1)}\twork\tcreate\tx\n" * 5 + f"{ago(0)}\tsleep\tcreate\tx\n" * 5, encoding="utf-8")
    assert "warning" not in sb(v, "new", "finding", "fresh-4", "-p", "heat", "--why", "w")[1]  # other days, sleep calls


def test_stats_on_the_demo_vault(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "stats")
    assert code == 0
    for part in ("notes: 8, archive: 1", "by type: concept 1, decision 2, derivation 1, finding 2, question 1, source 1", "by project: _general 1, heat 7",
                 "inbox: 3 open, oldest 8 days", "promotion rate: n/a", "activation A: min", "forget candidates: 0",
                 "last sleep: never", "sleep due: never slept"):
        assert part in out, part
    sb(v, "inbox", "resolve", "5c4c87", "--note", "scheme-choice")
    sb(v, "inbox", "resolve", "c0e92f", "--drop")
    sb(v, "inbox", "resolve", "d51838", "--wait")
    out = sb(v, "stats")[1]
    assert "promotion rate: 50% (1 of 2 settled)" in out and "inbox: 1 open" in out


def test_stats_sleep_due_rules(tmp_path):
    v = vault_with(tmp_path, [("a", "finding")], [(1, "work", "show", "a")])
    assert "sleep due: never slept" in sb(v, "stats")[1] and "last sleep: never" in sb(v, "stats")[1]
    sleep = v / ".sb" / "sleep.tsv"
    sleep.write_text(f"{ago(7)}\t3 notes\tok\n", encoding="utf-8")
    out = sb(v, "stats")[1]
    assert "sleep due" not in out and f"last sleep: {ago(7)} (7 days ago)" in out
    sleep.write_text(f"{ago(8)}\t3 notes\tok\n", encoding="utf-8")
    assert "sleep due: 8 days since the last sleep" in sb(v, "stats")[1]
    sleep.write_text(f"{ago(1)}\t3 notes\tok\n", encoding="utf-8")
    with open(v / ".sb" / "inbox.tsv", "w", encoding="utf-8") as f:
        f.write("".join(f"id{i:04d}\t{ago(1)}\tp\t\tline {i}\n" for i in range(31)))
    out = sb(v, "stats")[1]
    assert "sleep due: 31 open inbox lines" in out and "inbox: 31 open, oldest 1 days" in out
    assert "sleep due: 31 open inbox lines" in sb(v, "recall", "anything")[1]  # recall ends with the same line
    with open(v / ".sb" / "inbox-done.tsv", "w", encoding="utf-8") as f:  # a sleep left them all waiting
        f.write("".join(f"id{i:04d}\t{ago(0)}\twait\n" for i in range(31)))
    out = sb(v, "stats")[1]
    assert "sleep due" not in out and "inbox: 31 open" in out and "(inbox 0," in out
    assert "sleep due" not in sb(v, "recall", "anything")[1]
    inb = v / ".sb" / "inbox.tsv"  # a waiting line past inbox_wait_days needs a decision again
    inb.write_text(inb.read_text(encoding="utf-8").replace(f"id0000\t{ago(1)}", f"id0000\t{ago(31)}"), encoding="utf-8")
    assert "(inbox 1," in sb(v, "stats")[1]


def test_stats_on_an_empty_vault_and_new_notes_count(tmp_path):
    root = tmp_path / "e"
    sb(root, "init", str(root))
    out = sb(root, "stats")[1]
    assert "notes: 0, archive: 0" in out and "activation A: n/a" in out and "sleep due" not in out
    add_note(root, "young", project="p", extra={"created": ago(2)})
    add_note(root, "old", project="p", extra={"created": ago(30)})
    assert "new notes in the last 7 days: 1" in sb(root, "stats")[1]


def test_recall_ends_with_sleep_due_on_the_demo_vault(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "recall", "stability")[1].rstrip().endswith("sleep due: never slept")


def test_a_reused_name_keeps_its_own_events_apart_from_the_renamed_note(tmp_path):
    root = vault_with(tmp_path, [("b", "finding")], [(7, "work", "show", "a")])
    (root / ".sb" / "seeds.tsv").write_text(f"{ago(7)}\ta\t0.5\n", encoding="utf-8")
    usage.log_rename(Vault(root), "a", "b", TODAY)  # counts the usage and seed rows so far
    with open(root / ".sb" / "usage.tsv", "a", encoding="utf-8") as f:
        f.write(f"{ago(0)}\twork\tcreate\ta\n{ago(0)}\twork\tshow\ta\n")  # a new note a, same day as the rename
    with open(root / ".sb" / "seeds.tsv", "a", encoding="utf-8") as f:
        f.write(f"{ago(0)}\ta\t0.3\n")
    ev = usage.events(Vault(root))
    assert ev["b"] == {TODAY - timedelta(days=7): 1.0}  # not the new note's create and show
    assert ev["a"] == {TODAY: 1.0}  # the seed of the new note (0.3) stays below its create
    assert usage.renames(Vault(root)) == {"a": "b"}


def test_rename_then_new_through_the_cli(tmp_path):
    v = make_vault(tmp_path)
    before = usage.events(Vault(v))["scheme-choice"]
    assert sb(v, "rename", "scheme-choice", "scheme-pick")[0] == 0
    assert sb(v, "new", "concept", "scheme-choice", "-p", "heat", "--why", "again")[0] == 0
    sb(v, "show", "scheme-choice")
    ev = usage.events(Vault(v))
    assert ev["scheme-pick"] == before and ev["scheme-choice"] == {TODAY: 1.0}


def test_sync_edits_yield_to_a_seed_of_the_same_day_but_not_to_a_later_edit(tmp_path):
    root = vault_with(tmp_path, [("a", "finding"), ("b", "finding")],
                      [(0, "sync", "edit", "a"), (0, "sync", "edit", "b"), (0, "work", "show", "b")])
    (root / ".sb" / "seeds.tsv").write_text(f"{ago(0)}\ta\t0.5\n{ago(0)}\tb\t0.5\n", encoding="utf-8")
    ev = usage.events(Vault(root))
    assert ev["a"] == {TODAY: 0.5} and ev["b"] == {TODAY: 1.0}  # a work use is no sync edit
    with open(root / ".sb" / "usage.tsv", "a", encoding="utf-8") as f:
        f.write(f"{TODAY + timedelta(days=1)}\tsync\tedit\ta\n")
    assert usage.events(Vault(root))["a"][TODAY + timedelta(days=1)] == 1.0
    assert usage.events(Vault(root), ignore_sync=True)["a"] == {TODAY: 0.5}  # only the seed is use
