"""Regression tests from the functional review of the memory model and the sleep protocol (usage.py, sleep.py)."""
import os
import random
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from helpers import add_note, init_vault, make_vault, sb, write_log

import scientific_brain
from scientific_brain import links, sleep, usage
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def at(monkeypatch, day):
    monkeypatch.setenv("SB_TODAY", day.isoformat())


def ago(n):
    return TODAY - timedelta(days=n)


def verify_section(v):
    return sb(v, "sleep", "plan")[1].split("## 4.")[1].split("## 5.")[0]


def work_days(v, slug):
    return usage.use_days(Vault(v)).get(slug, set())


def fillers(v, n=30):
    """n notes nobody needs to check, so a plan names only some of them."""
    for k in range(n):
        add_note(v, f"filler-{k}", title=f"Filler {k} uniqueword{k}x", cue=f"cue uniqueword{k}x", gist=f"Gist: uniqueword{k}x here.",
                 extra={"verified": "2026-11-07"})


def unplanned(v, n=30):
    planned = sleep.remembered(Vault(v), "planned")
    return next(f"filler-{k}" for k in range(n) if f"filler-{k}" not in planned)


# ---- S1: stability is monotone in use ----------------------------------------------------------------------------

def test_a_recall_hit_the_day_before_a_show_does_not_lower_stability():
    c = date(2026, 1, 1)
    show = usage.replay({c + timedelta(days=90): 1.0}, c, 90.0)[0]
    both = usage.replay({c + timedelta(days=89): 0.5, c + timedelta(days=90): 1.0}, c, 90.0)[0]
    assert show == pytest.approx(135.0) and both >= show - 1e-9  # was 113: the half review cost what the show would gain


def _state(days, start, s0, seed, today):
    S, last = usage.replay(days, start, s0, seed)
    return S, usage.retrievability(S, (today - last).days)


@pytest.mark.parametrize("seed", range(4))
def test_one_more_or_a_stronger_use_never_lowers_stability_or_retrievability(seed):
    rng, d0 = random.Random(seed), date(2025, 1, 1)
    today = d0 + timedelta(days=900)
    for _ in range(1500):
        s0 = rng.uniform(30, 365)
        days = {d0 + timedelta(days=d): rng.choice((0.5, 0.5, 1.0)) for d in rng.sample(range(900), rng.randint(0, 12))}
        start = d0 + timedelta(days=rng.randint(0, 600))
        seeded = (d0 + timedelta(days=rng.randint(0, 900)), rng.uniform(1, 400)) if rng.random() < 0.3 else None
        more = dict(days)
        x = d0 + timedelta(days=rng.randint(0, 899))
        more[x] = max(more.get(x, 0.0), rng.choice((0.5, 1.0)))
        (S1, R1), (S2, R2) = _state(days, start, s0, seeded, today), _state(more, start, s0, seeded, today)
        assert S2 >= S1 - 1e-9 and R2 >= R1 - 1e-12, (s0, sorted(days.items()), start, seeded, x, more[x])


def test_daily_recall_hits_over_years_stay_fast_and_grow_stability():
    c = date(2024, 1, 1)
    S, last = usage.replay({c + timedelta(days=i): 0.5 for i in range(1, 1000)}, c, 90.0)
    assert S > 90.0 and last == c + timedelta(days=999)


# ---- S2: an edit found by the index sync is real use; release keeps it a sync edit --------------------------------

def test_an_edit_in_a_work_session_is_real_use_for_learning(tmp_path):
    root = init_vault(tmp_path)
    p = add_note(root, "a", project="p", extra={"created": ago(30).isoformat(), "updated": ago(30).isoformat()})
    sb(root, "stats")
    p.write_text(p.read_text(encoding="utf-8") + "\nA real correction made in a work session.\n", encoding="utf-8")
    sb(root, "stats")  # the index sync finds the edit
    assert TODAY in work_days(root, "a")


def test_an_edit_found_during_a_sleep_is_given_back_as_a_sync_edit(tmp_path):
    v = make_vault(tmp_path)
    fillers(v)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    free = unplanned(v)
    p = v / "notes" / "heat" / f"{free}.md"
    p.write_text(p.read_text() + "\nA hand edit outside sb.\n")
    sb(v, "check")
    sb(v, "sleep", "done", "x")
    rows = [ln.split("\t") for ln in (v / ".sb" / "usage.tsv").read_text().splitlines()]
    assert [r[1] for r in rows if r[3] == free and r[2] == "edit"] == ["sleep", "sync"]  # found by the index, not a work edit


# ---- S3: a seed dated ahead is a wrong clock ----------------------------------------------------------------------

def test_a_future_seed_row_does_not_hide_an_earlier_valid_seed(tmp_path):
    root = init_vault(tmp_path)
    add_note(root, "a", project="p", extra={"created": ago(300).isoformat(), "updated": ago(300).isoformat()})
    w = f"{((200 + 1) / 91) ** 0.5:.6f}"  # rest 200 days from the seed day
    write_log(root, "seeds.tsv", (ago(10).isoformat(), "a", w))
    assert usage.forget_candidates(Vault(root), TODAY) == []
    write_log(root, "seeds.tsv", (ago(10).isoformat(), "a", w), ("2099-01-01", "a", w))
    assert usage.forget_candidates(Vault(root), TODAY) == []
    assert usage.seeds(Vault(root), TODAY) == {"a": (ago(10), float(w))}


# ---- S4: only a full plan teaches the budget ----------------------------------------------------------------------

def entry(planned, left):
    return ["2026-11-01T12:00", 0, 0, 0, 0, 0, 0, "summary", planned, left]


def test_a_small_plan_left_undone_does_not_throttle_the_budget(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", entry(5, 1))  # a plan of five, one left: says nothing about a budget of 40
    assert usage.budget(Vault(root)) == 40
    write_log(root, "sleep.tsv", entry(40, 30))  # a full plan with three quarters left does
    assert usage.budget(Vault(root)) == 10


def test_a_crash_to_the_floor_recovers_within_a_few_full_sleeps(tmp_path):
    root = init_vault(tmp_path)
    rows, got = [entry(40, 40), entry(10, 9)], []  # at the floor, one of ten done: no limit of 10 may hold it there
    for _ in range(6):
        write_log(root, "sleep.tsv", *rows)
        b = usage.budget(Vault(root))
        got.append(b)
        rows.append(entry(b, 0))
    assert got[0] == 10 and got[-1] >= 30, got


# ---- S5 (E2): the main session's use during a sleep counts --------------------------------------------------------

def old_note_vault(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "old-unused-finding", title="Zebra lattice quartz anomaly", cue="zebra quartz lattice anomaly",
             gist="Gist: zebra lattice quartz anomaly seen once.", extra={"created": "2026-01-01", "updated": "2026-01-01"})
    return v


def test_a_show_of_a_forget_candidate_during_a_sleep_counts(tmp_path):
    v = old_note_vault(tmp_path)
    sb(v, "sleep", "begin")
    assert "- old-unused-finding [finding]" in sb(v, "sleep", "plan")[1]
    sb(v, "show", "old-unused-finding")  # the main session needs the note while the sleeper runs
    sb(v, "sleep", "done", "x")
    assert TODAY in work_days(v, "old-unused-finding")
    assert "old-unused-finding" not in [c[0] for c in usage.forget_candidates(Vault(v), TODAY)]


def test_a_show_of_a_listed_neighbour_during_a_sleep_counts(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    nb = re.search(r"similar: ([a-z0-9-]+) ", sb(v, "sleep", "plan")[1]).group(1)
    assert nb in sleep.remembered(Vault(v), "planned")
    sb(v, "show", nb)
    sb(v, "sleep", "done", "x")
    assert TODAY in work_days(v, nb)


def test_use_the_day_after_an_abandoned_sleep_is_given_back_when_it_is_replaced(tmp_path, monkeypatch):
    v = old_note_vault(tmp_path)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    at(monkeypatch, TODAY + timedelta(days=1))
    sb(v, "show", "old-unused-finding")  # the marker is a day old: still tagged as sleep
    at(monkeypatch, TODAY + timedelta(days=2))
    assert sb(v, "sleep", "begin")[0] == 0
    assert TODAY + timedelta(days=1) in work_days(v, "old-unused-finding")


# ---- S6: verified holds the text that was checked ------------------------------------------------------------------

def test_a_note_edited_after_it_was_verified_is_not_blessed_by_done(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    assert "- cn-oscillations-large-time-step (new)" in sb(v, "sleep", "plan")[1]
    sb(v, "sleep", "verified", "cn-oscillations-large-time-step")
    p = v / "notes" / "heat" / "cn-oscillations-large-time-step.md"
    p.write_text(p.read_text().replace("\n---\n\n", "\n---\n\nUnchecked new claim: the order is 2.7 here.\n\n", 1))
    sb(v, "sleep", "done", "x")
    at(monkeypatch, TODAY + timedelta(days=1))
    assert "- cn-oscillations-large-time-step (changed)" in verify_section(v)


# ---- S7: links do not make a note unchecked; old snapshots migrate ----------------------------------------------

def test_a_rename_does_not_send_the_linking_notes_back_to_verify(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    assert sleep.backlog(Vault(v), TODAY)["verify"] == 0
    assert sb(v, "rename", "ftcs-stability-limit", "ftcs-limit")[0] == 0
    assert sleep.backlog(Vault(v), TODAY)["verify"] == 0


def test_archive_with_fix_links_to_a_successor_sends_nothing_back_to_verify(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    code, out, _ = sb(v, "archive", "ftcs-stability-limit", "--why", "superseded", "--superseded-by", "richardson-error-estimate", "--fix-links")
    assert code == 0 and "links repaired in" in out
    assert sleep.backlog(Vault(v), TODAY)["verify"] == 0


def test_links_added_in_a_dream_do_not_return_the_notes_as_changed(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    write_log(v, "sleep.tsv", ["2026-11-01T12:00", 0, 0, 0, 0, 0, 0, "s"])
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    for s, other in (("scheme-choice", "ftcs-stability-limit"), ("ftcs-stability-limit", "scheme-choice")):
        p = v / "notes" / "heat" / f"{s}.md"
        t = p.read_text()
        p.write_text(t.rstrip("\n") + (f"\n- [[{other}]] shares the step-size quantity\n" if "## Links" in t
                                       else f"\n\n## Links\n- [[{other}]] shares the step-size quantity\n"))
    sb(v, "sleep", "done", "dream")
    at(monkeypatch, TODAY + timedelta(days=1))
    assert "(changed)" not in verify_section(v)


def test_an_edit_outside_the_links_section_still_counts(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("\n## Links", "\nSee also [[ftcs-stability-limit]] for the limit.\n\n## Links", 1))
    assert "- scheme-choice (changed)" in verify_section(v)


def test_snapshots_of_the_old_digest_migrate_without_making_every_note_changed(tmp_path):
    v = make_vault(tmp_path)
    notes = sleep.load(Vault(v))
    old = {s: sleep.text_digest(n.raw, links=True) for s, n in notes.items()}  # rows as sb <= 0.3 wrote them
    write_log(v, "snapshot.tsv", *[(s, h, 0) for s, h in sorted(old.items())])
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("\n## Links", "\nAn edit after the old check.\n\n## Links", 1))
    work = sleep.backlog(Vault(v), TODAY)
    assert work["verify"] == 1  # only the note edited since its check
    rows = [r.split("\t") for r in (v / ".sb" / "snapshot.tsv").read_text().splitlines()]
    assert all(r[3] == sleep.DIGEST for r in rows) and {r[0] for r in rows} == set(old)  # written once in the new form
    assert sb(v, "rename", "ftcs-stability-limit", "ftcs-limit")[0] == 0  # after the migration links are ignored
    assert sleep.backlog(Vault(v), TODAY)["verify"] == 1


def test_the_snapshot_migration_writes_under_the_vault_lock(tmp_path, monkeypatch):
    from scientific_brain import vault
    v = make_vault(tmp_path)
    notes = sleep.load(Vault(v))
    write_log(v, "snapshot.tsv", *[(s, sleep.text_digest(n.raw, links=True), 0) for s, n in sorted(notes.items())])
    depth, save = [], sleep.save_snapshot
    monkeypatch.setattr(sleep, "save_snapshot", lambda vv, rows: (depth.append(getattr(vault._HELD, "roots", {}).get(str(vv.root), 0)), save(vv, rows)))
    sb(v, "stats")
    assert depth == [1]


# ---- S8: notes nobody can verify do not starve the others ----------------------------------------------------------

def test_unverifiable_notes_do_not_starve_the_older_ones(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    for k in range(14):
        add_note(v, f"claim-{k:02d}", title=f"Claim {k} uniqueword{k}x", cue=f"claim cue word{k}y", gist=f"Gist: word{k}z measured.",
                 extra={"created": f"2026-10-{10 + k:02d}", "updated": f"2026-10-{10 + k:02d}"})
    write_log(v, "sleep.tsv", entry(40, 40))  # nothing done: budget 10
    assert usage.budget(Vault(v)) == 10
    seen = set()
    for d in range(2):
        at(monkeypatch, TODAY + timedelta(days=d))
        sb(v, "sleep", "begin")
        seen |= set(re.findall(r"^- (claim-\d\d) \((?:new|changed)\)", verify_section(v), re.M))
        sb(v, "sleep", "done", "nothing could be checked")
    assert len(seen) == 14, sorted(seen)


# ---- S9: a second plan in one sleep adds no undone work ------------------------------------------------------------

def test_a_second_plan_does_not_turn_deferred_lines_into_undone_work(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "inbox.tsv")
    for k in range(45):
        sb(v, "capture", f"bulk line {k} subject{k}q{k}")
    write_log(v, "sleep.tsv", ["2026-11-01T12:00", 0, 0, 0, 0, 0, 0, "s"])
    sb(v, "sleep", "begin")
    first = re.findall(r"^- `([0-9a-f]+)` ", sb(v, "sleep", "plan")[1].split("## 1.")[1].split("## 2.")[0], re.M)
    assert len(first) == 40
    for i in first:
        sb(v, "inbox", "resolve", i, "--drop")
    sb(v, "sleep", "plan")  # the sleeper looks at the dream pairs again
    out = sb(v, "sleep", "done", "x")[1]
    assert "left undone" not in out, out


# ---- S10: an abandoned sleep keeps what it verified ---------------------------------------------------------------

def test_a_replaced_sleep_keeps_the_verification_done(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    n = v / "notes" / "heat" / "scheme-choice.md"
    n.write_text(n.read_text().replace("\n---\n\n", "\n---\n\nEdited statement with a number 3.14.\n\n", 1))
    at(monkeypatch, TODAY + timedelta(days=1))
    sb(v, "sleep", "begin")
    assert "- scheme-choice (changed)" in sb(v, "sleep", "plan")[1]
    assert sb(v, "sleep", "verified", "scheme-choice")[0] == 0
    at(monkeypatch, TODAY + timedelta(days=3))  # abandoned; the marker is stale
    assert sb(v, "sleep", "begin")[0] == 0
    assert "- scheme-choice (changed)" not in sb(v, "sleep", "plan")[1]


# ---- S11: unrelated pairs follow renames like the other logs -------------------------------------------------------

def test_a_reused_name_keeps_its_own_unrelated_judgements_apart(tmp_path, monkeypatch):
    at(monkeypatch, date(2026, 11, 1))
    v = init_vault(tmp_path)
    for s in ("aaa", "xxx"):
        add_note(v, s, project="heat", type="concept")
    assert sb(v, "sleep", "unrelated", "aaa", "xxx", "old pair")[0] == 0
    assert sb(v, "rename", "aaa", "bbb")[0] == 0
    add_note(v, "aaa", project="heat", type="concept")  # a new note with the freed name
    at(monkeypatch, date(2026, 11, 5))
    assert sb(v, "sleep", "unrelated", "aaa", "xxx", "new pair")[0] == 0
    assert sleep.rejected(Vault(v)) == {frozenset({"bbb", "xxx"}): [date(2026, 11, 1)], frozenset({"aaa", "xxx"}): [date(2026, 11, 5)]}


def test_renaming_the_new_note_does_not_move_the_old_notes_judgement(tmp_path, monkeypatch):
    at(monkeypatch, date(2026, 11, 1))
    v = init_vault(tmp_path)
    for s in ("aaa", "xxx"):
        add_note(v, s, project="heat", type="concept")
    assert sb(v, "sleep", "unrelated", "aaa", "xxx", "old pair")[0] == 0
    assert sb(v, "rename", "aaa", "bbb")[0] == 0
    add_note(v, "aaa", project="heat", type="concept")
    assert sb(v, "rename", "aaa", "ccc")[0] == 0
    assert sleep.rejected(Vault(v)) == {frozenset({"bbb", "xxx"}): [date(2026, 11, 1)]}


def test_a_note_verified_before_its_name_was_reused_keeps_its_verification(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    sb(v, "sleep", "verified", "scheme-choice")
    assert sb(v, "rename", "scheme-choice", "scheme-decision")[0] == 0
    add_note(v, "scheme-choice", title="Another scheme note")  # the freed name, unverified
    assert sleep.remembered_now(Vault(v), "verified") == {"scheme-decision"}
    sb(v, "sleep", "done", "x")
    sec = verify_section(v)
    assert "- scheme-decision (" not in sec.split("most active")[0] and "- scheme-choice (new)" in sec


# ---- S12: a note restored from wrong is checked again --------------------------------------------------------------

def test_a_note_restored_from_wrong_is_not_trusted_as_checked(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    assert sb(v, "archive", "scheme-choice", "--why", "wrong", "--reason", "the scheme was not chosen that way")[0] == 0
    links.restore(Vault(v), "scheme-choice", force=True)
    sleep.unverify(Vault(v), "scheme-choice")  # what cli restore does for a note archived as wrong
    assert sleep.backlog(Vault(v), TODAY)["verify"] == 1
    assert "- scheme-choice (changed)" in verify_section(v)


# ---- S13: no 'sleep due' while a sleep runs ----------------------------------------------------------------------

def test_no_sleep_is_due_while_one_runs(tmp_path):
    v = make_vault(tmp_path)
    assert sleep.due(Vault(v), 8, TODAY) is not None
    sb(v, "sleep", "begin")
    assert sleep.running(Vault(v), TODAY) and sleep.due(Vault(v), 8, TODAY) is None
    assert not sleep.running(Vault(v), TODAY + timedelta(days=2))  # a forgotten marker runs nothing
    assert sleep.due(Vault(v), 8, TODAY + timedelta(days=2)) is not None


# ---- S14: a clock set back does not hide the latest sleep ----------------------------------------------------------

def test_the_last_sleep_is_the_latest_one_not_the_last_logged(tmp_path):
    v = init_vault(tmp_path)
    write_log(v, "sleep.tsv", ["2026-11-06T12:00", 0, 0, 0, 0, 0, 0, "s"], ["2026-11-02T12:00", 0, 0, 0, 0, 0, 0, "clock behind"])
    assert usage.last_sleep(Vault(v)) == date(2026, 11, 6)


# ---- robustness: parallel begins, line endings -------------------------------------------------------------------

def test_parallel_sleep_begins_let_exactly_one_start(tmp_path):
    v = make_vault(tmp_path)
    src = str(Path(scientific_brain.__file__).resolve().parents[1])
    go = tmp_path / "go"
    code = ("import os, sys, time\nfrom datetime import date\nfrom scientific_brain import sleep\nfrom scientific_brain.vault import Vault, SbError\n"
            "real = sleep.started\n"
            "sleep.started = lambda v: (real(v), time.sleep(0.05))[0]  # widen the gap between looking and writing\n"
            f"while not os.path.exists({str(go)!r}): time.sleep(0.001)\n"
            f"try:\n    sleep.begin(Vault(__import__('pathlib').Path({str(v)!r})), date(2026, 11, 8)); print('ok')\n"
            "except SbError: print('no')\n")
    env = dict(os.environ, PYTHONPATH=src)
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env) for _ in range(8)]
    go.write_text("")
    outs = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert sorted(outs) == ["no"] * 7 + ["ok"], outs
    assert [r for r in (v / ".sb" / "usage.tsv").read_text().splitlines() if "\tbegin\t" in r] == ["2026-11-08\tsleep\tbegin\t-"]


def test_sleep_verified_keeps_the_line_endings_of_the_note(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "crlf-note")
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    before = p.read_bytes()
    sb(v, "sleep", "begin")
    assert sb(v, "sleep", "verified", "crlf-note")[0] == 0
    after = p.read_bytes()
    assert b"verified: 2026-11-08\r\n" in after and re.sub(rb"verified:.*\r\n", b"", after) == before


def test_a_verified_note_with_a_lone_cr_stays_checked(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    sb(v, "sleep", "baseline")
    add_note(v, "lone-cr", body="\nline with a lone\rcarriage return\n")
    sb(v, "sleep", "begin")
    assert "- lone-cr (new)" in verify_section(v)
    assert sb(v, "sleep", "verified", "lone-cr")[0] == 0
    sb(v, "sleep", "done", "s")
    at(monkeypatch, TODAY + timedelta(days=2))
    sb(v, "sleep", "begin")
    assert not re.search(r"^- lone-cr \((new|changed)\)", verify_section(v), re.M)  # both hashes read the file alike


def test_old_snapshots_of_notes_with_crlf_or_a_lone_cr_migrate_as_unchanged(tmp_path):
    v = make_vault(tmp_path)
    p, q = add_note(v, "crlf-note"), add_note(v, "lone-cr", body="\na lone\rcarriage return\n")
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    notes = sleep.load(Vault(v))
    old = {s: sleep.text_digest((v / n.rel).read_text(encoding="utf-8"), links=True)  # sb <= 0.3: universal newlines
           for s, n in notes.items()}
    write_log(v, "snapshot.tsv", *[(s, h, 0) for s, h in sorted(old.items())])
    assert sleep.backlog(Vault(v), TODAY)["verify"] == 0


def test_a_note_verified_and_edited_again_goes_before_never_checked_ones(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    p = add_note(v, "aaa-edited-twice", body="\nfirst text\n")
    sb(v, "sleep", "baseline")
    p.write_text(p.read_text() + "\nsecond text\n")
    sb(v, "sleep", "begin")
    assert "- aaa-edited-twice (changed)" in verify_section(v)  # listed, then verified in the same sleep
    assert sb(v, "sleep", "verified", "aaa-edited-twice")[0] == 0
    sb(v, "sleep", "done", "s1")
    at(monkeypatch, TODAY + timedelta(days=2))
    p.write_text(p.read_text() + "\nthird text\n")
    for k in range(3):
        add_note(v, f"zzz-never-checked-{k}", extra={"created": ago(-1).isoformat(), "updated": ago(-1).isoformat()})
    sb(v, "sleep", "begin")
    order = re.findall(r"^- ([a-z0-9-]+) \((?:new|changed)\)", verify_section(v), re.M)
    assert order[0] == "aaa-edited-twice", order  # its listing before the check does not send it to the back
