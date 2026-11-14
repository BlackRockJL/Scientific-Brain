import os
from datetime import date, timedelta

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain import usage
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)
OLD = "2025-01-01"  # created long ago


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


@pytest.fixture
def frozen(monkeypatch):
    """A prior so heavy that the learned initial stability stays at PRIOR = 90 whatever the log says."""
    monkeypatch.setattr(usage, "PRIOR_N", 1e12)


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def vault_with(tmp_path, notes, rows=()):
    """Empty vault with (slug, type[, days ago created]) notes and usage rows (days_ago, caller, kind, slug). A note without
    a creation age is created on the day of its first row, or long ago if it has none, so replay starts there."""
    root = tmp_path / "v"
    assert sb(root, "init", str(root))[0] == 0
    for slug, typ, *age in notes:
        first = max((d for d, _, _, s in rows if s == slug), default=None)
        made = ago(age[0]) if age else ago(first) if first is not None else OLD
        add_note(root, slug, project="p", type=typ, extra={"created": made, "updated": made})
    (root / ".sb" / "usage.tsv").write_text("".join(f"{ago(d)}\t{c}\t{k}\t{s}\n" for d, c, k, s in rows), encoding="utf-8")
    return root


def cands(root):
    return {s: (t, r, S) for s, t, r, S in usage.forget_candidates(Vault(root), TODAY)}


def score(root, slug):
    """(R, S, last) of a note today."""
    return {x[0]: x[3:] for x in usage.scores(Vault(root), TODAY)}[slug]


def usage_lines(v):
    return [ln.split("\t") for ln in (v / ".sb" / "usage.tsv").read_text().splitlines()]


def test_forget_boundary_is_t_equal_s_against_t_one_more(tmp_path):
    root = vault_with(tmp_path, [("n90", "finding", 90), ("n91", "finding", 91)])  # no use: S is the prior of 90 days
    assert list(cands(root)) == ["n91"]
    assert score(root, "n90")[0] == pytest.approx(0.5) and score(root, "n90")[1] == pytest.approx(90)
    typ, r, S = cands(root)["n91"]
    assert typ == "finding" and r == pytest.approx(90 / 181) and S == pytest.approx(90)


def test_forget_boundary_after_a_review_moves_with_the_grown_stability(tmp_path, frozen):
    # created 100 days ago, reviewed after 5 days (R = 90/95): S = 90 (1 + 1/19) = 94.74, so t = 95 is past it;
    # reviewed after 6 days (R = 90/96): S = 95.625, so t = 94 is not
    root = vault_with(tmp_path, [("a", "finding", 100), ("b", "finding", 100)], [(95, "work", "show", "a"), (94, "work", "show", "b")])
    assert score(root, "a")[1] == pytest.approx(94.7368, rel=1e-5) and score(root, "b")[1] == pytest.approx(95.625, rel=1e-6)
    assert list(cands(root)) == ["a"]
    assert score(root, "a")[0] == pytest.approx(0.49931, abs=1e-5) and score(root, "b")[0] == pytest.approx(0.50428, abs=1e-5)


def test_a_type_that_rests_longer_by_its_log_fades_later(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR_N", 1)  # let the log speak: ten decisions came back after 300 days, ten findings after 30
    notes = [(f"d{i}", "decision", 1000) for i in range(10)] + [(f"f{i}", "finding", 1000) for i in range(10)]
    notes += [("dec", "decision", 150), ("fin", "finding", 150)]
    root = vault_with(tmp_path, notes, [(700, "work", "show", f"d{i}") for i in range(10)] + [(970, "work", "show", f"f{i}") for i in range(10)])
    lr = usage.learned(Vault(root), TODAY)
    assert lr["finding"] < 150 < lr["decision"]
    assert "fin" in cands(root) and "dec" not in cands(root)  # both rested 150 days; the decision rests ~300, the finding ~50


def test_only_links_from_active_notes_protect(tmp_path):
    names = ("hub", "src", "a", "b", "leaf", "lone")
    root = vault_with(tmp_path, [(s, "finding", 400) for s in names], [(5, "work", "show", "hub")])
    links = {"hub": "[[src]]", "a": "[[b]] [[leaf]]", "b": "[[a]]"}
    for s, body in links.items():
        add_note(root, s, project="p", extra={"created": ago(400), "updated": ago(400)}, body=f"\nSee {body}.\n")
    add_note(root, "gone", loc="archive", project="p", extra={"created": ago(400), "updated": ago(400)}, body="\nSee [[lone]].\n")
    assert set(cands(root)) == {"a", "b", "leaf", "lone"}  # src is used through hub; a faded cycle still fades


def test_same_day_events_count_once_and_the_strongest_wins(tmp_path, frozen):
    root = vault_with(tmp_path, [("a", "finding", 30)],
                      [(10, "work", "show", "a"), (10, "work", "recall", "a"), (10, "work", "create", "a"),
                       (10, "work", "show", "a")])
    ev = usage.events(Vault(root))["a"]
    assert ev == {TODAY - timedelta(days=10): 1.0}
    assert score(root, "a")[1] == pytest.approx(106.3636, rel=1e-6)  # 90 (1 + 1 - 90/110): one full review after 20 days
    (root / ".sb" / "usage.tsv").write_text(f"{ago(10)}\twork\trecall\ta\n{ago(10)}\twork\tshow\ta\n", encoding="utf-8")
    assert usage.events(Vault(root))["a"][TODAY - timedelta(days=10)] == 1.0
    (root / ".sb" / "usage.tsv").write_text(f"{ago(10)}\twork\trecall\ta\n", encoding="utf-8")
    assert usage.events(Vault(root))["a"][TODAY - timedelta(days=10)] == 0.5
    assert score(root, "a")[1] == pytest.approx(98.1818, rel=1e-6)  # a recall alone is half a review


def test_uses_on_different_days_add_up(tmp_path, frozen):
    root = vault_with(tmp_path, [("a", "finding", 91), ("b", "finding", 91)], [(5, "work", "recall", "a")])
    assert set(cands(root)) == {"b"}  # the recent recall keeps a, b has nothing
    R, S, last = score(root, "a")
    assert S == pytest.approx(111.9886, rel=1e-6) and last == TODAY - timedelta(days=5)  # 90 (1 + 0.5 (1 - 90/176))
    assert R == pytest.approx(S / (S + 5), rel=1e-9)


def test_sleep_caller_does_not_count_except_veto(tmp_path, frozen):
    root = vault_with(tmp_path, [("a", "finding", 91), ("b", "finding", 91)],
                      [(1, "sleep", "show", "a"), (1, "sleep", "recall", "a"), (1, "sleep", "veto", "b")])
    assert set(cands(root)) == {"a"}
    R, S, last = score(root, "b")  # created 91 days ago, vetoed 1 day ago: t = 90 = S, R = 1/2 -> S = 135
    assert S == pytest.approx(135.0) and last == TODAY - timedelta(days=1) and R == pytest.approx(135 / 136)


def test_each_further_veto_rests_longer(tmp_path, frozen):
    root = vault_with(tmp_path, [("a", "finding", 200)], [(90, "sleep", "veto", "a"), (40, "sleep", "veto", "a"), (0, "sleep", "veto", "a")])
    assert score(root, "a")[1] == pytest.approx(208.9105, rel=1e-6)  # 90 -> 139.5 (t=110) -> 176.31 (t=50) -> 208.91 (t=40)
    for rows, want in (([(90, "sleep", "veto", "a")], 139.5), ([(90, "sleep", "veto", "a"), (40, "sleep", "veto", "a")], 176.3074)):
        (root / ".sb" / "usage.tsv").write_text("".join(f"{ago(d)}\t{c}\t{k}\t{s}\n" for d, c, k, s in rows), encoding="utf-8")
        assert score(root, "a")[1] == pytest.approx(want, rel=1e-6)


def test_note_without_events_uses_created_and_the_constants_are_live(tmp_path, monkeypatch):
    root = vault_with(tmp_path, [("a", "finding")])  # created long ago, no events
    add_note(root, "b", project="p", extra={"created": ago(91)})
    add_note(root, "c", project="p", extra={"created": ago(89)})
    assert set(cands(root)) == {"a", "b"}
    monkeypatch.setattr(usage, "PRIOR", 10.0)
    monkeypatch.setattr(usage, "FLOOR", 5.0)
    add_note(root, "d", project="p", extra={"created": ago(10)})
    add_note(root, "e", project="p", extra={"created": ago(11)})
    assert set(cands(root)) == {"a", "b", "c", "e"}  # S = 10 now: d (t = 10) stays, e (t = 11) goes
    monkeypatch.setattr(usage, "PRIOR_N", 1e12)  # the one gap of d must not move the learned value
    monkeypatch.setattr(usage, "GAIN", 0.0)
    (root / ".sb" / "usage.tsv").write_text(f"{ago(8)}\twork\tshow\td\n", encoding="utf-8")
    assert score(root, "d")[1] == pytest.approx(10.0)  # without gain a review does not lengthen the rest
    monkeypatch.setattr(usage, "GAIN", 1.0)
    assert score(root, "d")[1] > 10.0


def test_rename_chain_carries_old_usage_to_the_current_slug(tmp_path, frozen):
    root = vault_with(tmp_path, [("c", "finding", 91)], [(91, "work", "show", "a"), (3, "work", "show", "b")])
    (root / ".sb" / "renames.tsv").write_text("a\tb\t2026-11-06\nb\tc\t2026-11-07\n", encoding="utf-8")  # old rows: no row numbers
    ev = usage.events(Vault(root))
    assert set(ev) == {"c"} and len(ev["c"]) == 2
    assert score(root, "c")[1] == pytest.approx(134.4944, rel=1e-6)  # the review after 88 days: 90 (1 + 1 - 90/178)
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
    sb(v, "new", "finding", "zebu-quokka", "-p", "heat", "--why", "vexillology")  # no similar note: written at once
    sb(v, "show", "scheme-choice")
    sb(v, "recall", "stability", "limit")  # hits in notes/ are logged, archived and inbox hits are not
    sb(v, "recall", "--archive", "guess")
    rows = usage_lines(v)
    assert ["2026-11-08", "work", "create", "zebu-quokka"] in rows and ["2026-11-08", "work", "show", "scheme-choice"] in rows
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
    add_note(v, "fresh-note")  # a new file is an import, not an edit
    sb(v, "map")
    assert not any(r[3] == "fresh-note" for r in usage_lines(v))
    (v / ".sb" / "sleeping").write_text("2026-11-08\n", encoding="utf-8")
    bump(p)
    sb(v, "map")
    assert ["2026-11-08", "sleep", "edit", "scheme-choice"] in usage_lines(v)
    sb(v, "check", "--rebuild")
    n = len(usage_lines(v))
    bump(p)
    sb(v, "check", "--rebuild")  # a full rebuild still reports what changed in a note the old index knew
    assert len(usage_lines(v)) == n + 1
    sb(v, "check", "--rebuild")
    assert len(usage_lines(v)) == n + 1
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


def test_new_per_day_is_a_constant(tmp_path, monkeypatch):
    from scientific_brain import vault
    monkeypatch.setitem(vault.LIFECYCLE, "new_per_day", 1)
    v = make_vault(tmp_path)
    (v / ".sb" / "usage.tsv").write_text("", encoding="utf-8")
    assert "warning" not in sb(v, "new", "finding", "fresh-0", "-p", "heat", "--why", "w")[1]
    assert "warning: 1 notes were created today already" in sb(v, "new", "finding", "fresh-1", "-p", "heat", "--why", "w")[1]


def test_stats_on_the_demo_vault(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "stats")
    assert code == 0
    for part in ("notes: 8, archive: 1", "by type: concept 1, decision 2, derivation 1, finding 2, question 1, source 1", "by project: _general 1, heat 7",
                 "inbox: 3 open, oldest 8 days", "promotion rate: n/a", "stability (days a note rests unused): min", "forget candidates: 0",
                 "learned: a new note rests ", "a sleep takes 40 items", "last sleep: never", "sleep due: never slept"):
        assert part in out, part
    assert "no longer read" not in out  # the demo sb.toml holds no retired section
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
    with open(v / ".sb" / "inbox.tsv", "w", encoding="utf-8") as f:  # 38 open lines and the unverified note: 39 of 40
        f.write("".join(f"id{i:04d}\t{ago(1)}\tp\t\tline {i}\n" for i in range(38)))
    out = sb(v, "stats")[1]
    assert "sleep due" not in out and "waiting for a sleep: 39 (inbox 38, verify 1, forget 0)" in out
    with open(v / ".sb" / "inbox.tsv", "a", encoding="utf-8") as f:
        f.write(f"id9999\t{ago(1)}\tp\t\tline 38\n")
    out = sb(v, "stats")[1]
    assert "sleep due: 40 items waiting (inbox 39, verify 1)" in out and "inbox: 39 open, oldest 1 days" in out
    assert "sleep due: 40 items waiting (inbox 39, verify 1)" in sb(v, "recall", "anything")[1]  # recall counts all waiting work, as stats does
    with open(v / ".sb" / "inbox.tsv", "a", encoding="utf-8") as f:
        f.write(f"id9998\t{ago(1)}\tp\t\tline 39\n")
    assert "sleep due: 41 items waiting (inbox 40, verify 1)" in sb(v, "stats")[1]
    assert "sleep due: 41 items waiting (inbox 40, verify 1)" in sb(v, "recall", "anything")[1]  # the same line as stats
    with open(v / ".sb" / "inbox-done.tsv", "w", encoding="utf-8") as f:  # a sleep left them all waiting
        f.write("".join(f"id{i:04d}\t{ago(0)}\twait\n" for i in range(38)) + f"id9999\t{ago(0)}\twait\nid9998\t{ago(0)}\twait\n")
    out = sb(v, "stats")[1]
    assert "sleep due" not in out and "inbox: 40 open" in out and "(inbox 0," in out
    assert "sleep due" not in sb(v, "recall", "anything")[1]
    inb = v / ".sb" / "inbox.tsv"  # a waiting line past inbox_wait_days needs a decision again
    inb.write_text(inb.read_text(encoding="utf-8").replace(f"id0000\t{ago(1)}", f"id0000\t{ago(31)}"), encoding="utf-8")
    assert "(inbox 1," in sb(v, "stats")[1]


def test_stats_on_an_empty_vault_and_new_notes_count(tmp_path):
    root = tmp_path / "e"
    sb(root, "init", str(root))
    out = sb(root, "stats")[1]
    assert "notes: 0, archive: 0" in out and "stability (days a note rests unused): n/a" in out and "sleep due" not in out
    assert "a sleep takes 40 items" in out
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
    assert ev["a"] == {TODAY: 1.0}
    assert usage.seeds(Vault(root)) == {"b": (TODAY - timedelta(days=7), 0.5), "a": (TODAY, 0.3)}  # each seed stays with its note
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
    assert "a" not in ev and ev["b"] == {TODAY: 1.0}  # a work use is no sync edit; seeds are no events any more
    assert usage.seeds(Vault(root)) == {"a": (TODAY, 0.5), "b": (TODAY, 0.5)}
    with open(root / ".sb" / "usage.tsv", "a", encoding="utf-8") as f:
        f.write(f"{TODAY + timedelta(days=1)}\tsync\tedit\ta\n")
    assert usage.events(Vault(root))["a"] == {TODAY + timedelta(days=1): 1.0}
    assert "a" not in usage.events(Vault(root), ignore_sync=True)  # the seed is no use either
