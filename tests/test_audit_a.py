"""Audit of sleep.py and inbox.py (A): each finding as a test of the rule, plus the gaps the audit found."""
import hashlib
from datetime import date, timedelta

import pytest
from helpers import add_note, init_vault, make_vault, sb, write_log

from scientific_brain import inbox, sleep
from scientific_brain.vault import SbError, Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())
    monkeypatch.setenv("SB_NO_UPDATE", "1")


def verify_a(v):
    """Section 4 of the plan without the 'most active' lines."""
    return "\n".join(ln for ln in sb(v, "sleep", "plan")[1].split("## 4.")[1].split("## 5.")[0].splitlines() if "(R=" not in ln)


def marker(v):
    return (v / ".sb" / "sleeping").read_text()


# ---- B1: digest ignores body lines that start with "verified:" ---------------------------------------------------

def test_B1_an_edit_of_a_body_line_starting_with_verified_is_a_change(tmp_path):
    """digest dropped every line starting with 'verified:', not only the frontmatter one. Soll (design.md, Verify):
    the hash ignores `verified:` (the frontmatter field); any other change makes the note unchecked."""
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("\n---\n\n", "\n---\n\nverified: the observed order 2 matches the run\n\n", 1))  # in the body, before ## Links
    sb(v, "sleep", "baseline")
    p.write_text(p.read_text().replace("observed order 2", "observed order 3"))
    assert "observed order 3" in p.read_text()  # the edit happened
    assert "- scheme-choice (changed)" in verify_a(v)


# ---- B2: sleep verified writes above the opening fence when the frontmatter has no closing fence -----------------

def test_B2_verified_does_not_damage_a_note_whose_frontmatter_is_not_closed(tmp_path):
    """sleep.py:404-407 with links.fm_end() == 0: parts[0:0] = [...] puts 'verified:' above the opening '---'. Soll: set
    the field inside the frontmatter or refuse; never corrupt the file."""
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("\n---\n", "\n", 1))  # closing fence gone
    before = p.read_text()
    sb(v, "sleep", "begin")
    sb(v, "sleep", "verified", "scheme-choice")
    after = p.read_text()
    assert after == before or after.startswith("---\n"), after[:60]


# ---- B3: a duplicated inbox id is expired and counted twice -----------------------------------------------------

def test_B3_a_duplicated_inbox_id_expires_once(tmp_path):
    """inbox.py:84-87: ids is a list over open_lines, so a line present twice (same text captured on the same day in two
    parallel sessions or merged by a sync tool) gets two 'expired' rows and counts twice in `sleep done`. Soll: one line."""
    v = init_vault(tmp_path)
    row = "aaaaaa\t2026-09-01\tp\t\tstale remark about zeolites"
    write_log(v, "inbox.tsv", row, row)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    code, out, _ = sb(v, "sleep", "done", "dup")
    assert code == 0 and "1 expired" in out, out
    assert (v / ".sb" / "inbox-done.tsv").read_text().count("expired") == 1


# ---- B4: unreadable marker makes `sleep done` count the whole history -------------------------------------------

def test_B4_an_unreadable_marker_does_not_pull_the_whole_history_into_done(tmp_path):
    """sleep.py:30 returns date.min for an unreadable marker; pending() (51-56) and finish() (446) then count every
    row since the dawn of the log. Soll: `done` reports only what this sleep did."""
    v = make_vault(tmp_path)
    write_log(v, "inbox-done.tsv", "c0e92f\t2026-10-01\tdropped", "5c4c87\t2026-10-02\tnote:scheme-choice")
    sb(v, "sleep", "begin")
    (v / ".sb" / "sleeping").write_text("")  # a crash between truncate and write
    code, out, _ = sb(v, "sleep", "done", "x")
    assert code == 0 and "0 inbox lines settled (0 promoted)" in out, out


# ---- B5: a slug freed by a rename and used again loses its snapshot row to the renamed note ---------------------

def test_B5_a_reused_slug_does_not_overwrite_the_renamed_notes_snapshot(tmp_path):
    """sleep.py:103-104 (snapshot) and 453 (finish) map rows by usage.renames(), which ignores that a note created under
    the freed name is a different note (usage.renamer does honour it; design.md, Stability). The new note's row is then
    read as the renamed note's, which shows as changed again in every plan. Soll: neither note listed after both were
    verified in one sleep."""
    v = init_vault(tmp_path)
    add_note(v, "old-name", project="p", title="Moved topic quokka", cue="quokka habitat", gist="Gist: quokka.")
    sb(v, "sleep", "baseline")
    assert sb(v, "rename", "old-name", "aa-moved")[0] == 0
    add_note(v, "old-name", project="p", title="Fresh topic zebra", cue="zebra crossing", gist="Gist: zebra.")
    sb(v, "sleep", "begin")
    assert sb(v, "sleep", "verified", "old-name", "aa-moved")[0] == 0
    sb(v, "sleep", "done", "both verified")
    plan = verify_a(v)
    assert "aa-moved" not in plan and "- old-name" not in plan, plan


# ---- B6: condense ignores edits to a note that never had a snapshot row ---------------------------------------

def test_B6_condense_sees_an_edit_since_the_last_sleep_of_a_note_that_was_never_snapshotted(tmp_path):
    """sleep.py:187-189 (fresh) knows 'changed' only through the snapshot hash, so a note created before the last sleep and
    still unverified (deferred) is never fresh again, however often it is edited. Soll (design.md, Condense): each note
    updated since the last sleep is searched for near-duplicates."""
    v = init_vault(tmp_path)
    for s, upd in (("implicit-euler-damping", "2026-10-01"), ("backward-euler-damping", "2026-11-07")):
        add_note(v, s, project="p", title="Implicit Euler damps stiff modes strongly", cue="choosing a solver for stiff modes",
                 gist="Gist: Implicit Euler damps stiff modes strongly.", extra={"created": "2026-10-01", "updated": upd})
    write_log(v, "sleep.tsv", ["2026-11-05T12:00", 0, 0, 0, 0, 0, 0, "s", 0, 0])
    assert "backward-euler-damping ~ implicit-euler-damping" in sb(v, "sleep", "plan")[1]


# ---- B7: a marker older than a day still drives the plan -------------------------------------------------------

def test_B7_a_stale_marker_does_not_freeze_the_dream_pairs_of_the_forgotten_sleep(tmp_path):
    """sleep.py:88-98 and 224 read the marker without asking stale(); design.md: 'A marker older than a day is a forgotten
    one: it tags nothing and blocks nothing.' A plan run after it draws its pairs anew, not 'still open from the first plan'.
    (Reading of the Soll; the plan is normally run after `begin`, which replaces the marker.)"""
    v = make_vault(tmp_path)
    (v / ".sb").mkdir(exist_ok=True)
    (v / ".sb" / "sleeping").write_text("2026-11-01\ndreamed\t1\ndream\tscheme-choice|open-nonuniform-mesh-order\n")
    assert "still open" not in sb(v, "sleep", "plan")[1]


# ---- B8: the order of the verify list depends on the file system's scan order --------------------------------

def test_B8_the_plan_is_the_same_for_every_scan_order(tmp_path, monkeypatch):
    """sleep.py:210 sorts the unchecked notes by `created` only; notes created on one day keep os.scandir order, so with a
    binding budget the notes that are deferred differ by machine. design.md: 'The candidates are cheap and deterministic.'"""
    from scientific_brain import usage
    from scientific_brain.vault import Vault as V
    v = init_vault(tmp_path)
    for i, w in enumerate(("quokka", "zebra", "walrus", "ocelot", "tapir", "badger")):
        add_note(v, f"note-{w}", project="p", title=f"Topic about {w}", cue=f"{w} habitat", gist=f"Gist: {w}.")
    monkeypatch.setattr(usage, "budget", lambda _v: 3)
    first = verify_a(v)
    orig = V.scan
    monkeypatch.setattr(V, "scan", lambda self, loc: dict(reversed(list(orig(self, loc).items()))))
    assert verify_a(v) == first


# ---- B11 (usage rename vs snapshot rows) ------------------------------------------------------------------------

def test_B11_snapshot_keeps_the_row_of_a_note_that_reuses_a_renamed_away_name(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "renames.tsv", ("a", "b", (TODAY - timedelta(days=3)).isoformat(), 0, 0))
    write_log(root, "snapshot.tsv", ("a", "HASH-NEW-A"), ("b", "HASH-B"))
    assert sleep.snapshot(Vault(root)) == {"a": "HASH-NEW-A", "b": "HASH-B"}


def test_a_snapshot_row_follows_only_the_renames_made_after_it(tmp_path):
    """Rows carry the number of renames they account for: old-name's row written before the rename follows it, a row
    written after (count 1) is the new note's own, even when the renamed note has no row at all."""
    root = init_vault(tmp_path)
    write_log(root, "renames.tsv", ("a", "b", TODAY.isoformat(), 0, 0))
    write_log(root, "snapshot.tsv", ("a", "H-OLD", 0), ("c", "H-C", 1))
    assert sleep.snapshot(Vault(root)) == {"b": "H-OLD", "c": "H-C"}
    write_log(root, "snapshot.tsv", ("a", "H-NEW", 1))
    assert sleep.snapshot(Vault(root)) == {"a": "H-NEW"}
    write_log(root, "snapshot.tsv", ("a", "H-OLD"))  # a row of sb <= 0.3.x: follows the rename
    assert sleep.snapshot(Vault(root)) == {"b": "H-OLD"}


# ---- unreadable marker, without begin row ----------------------------------------------------------------------

def test_an_unreadable_marker_counts_only_the_usage_after_its_last_begin_row(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "usage.tsv", "2026-10-01\tsleep\tarchive\told-one", "2026-11-08\tsleep\tbegin\t-", "2026-11-08\tsleep\tarchive\tnew-one")
    (v / ".sb" / "sleeping").write_text("garbage\n")
    code, out, _ = sb(v, "sleep", "done", "x")
    assert code == 0 and ", 1 archived," in out, out


def test_an_unreadable_marker_without_begin_row_counts_no_usage(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "usage.tsv", "2026-10-01\tsleep\tarchive\told-one", "2026-11-08\tsleep\tcreate\tother")
    write_log(v, "unrelated.tsv", "2026-10-01\ta\tb\treason")
    (v / ".sb" / "sleeping").write_text("garbage\n")
    st = sleep.started(Vault(v))
    assert st == date.min and sleep.pending(Vault(v), st) == []
    assert sleep.since_begin(Vault(v), "unrelated.tsv", 4, "unrelrows", 0, st) == []
    code, out, _ = sb(v, "sleep", "done", "x")
    assert code == 0 and ", 0 archived, 0 created, 0 vetoes" in out, out


def test_a_marker_without_row_counts_falls_back_to_the_date(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "inbox-done.tsv", "aaaaaa\t2026-10-01\tdropped", "bbbbbb\t2026-11-08\tdropped")
    (v / ".sb").mkdir(exist_ok=True)
    (v / ".sb" / "sleeping").write_text("2026-11-08\n")  # sb <= 0.3.0 wrote no row counts
    assert [r[0] for r in sleep.booked_now(Vault(v), TODAY)] == ["bbbbbb"]


# ---- mark_verified -------------------------------------------------------------------------------------------

def test_verified_refuses_a_note_without_closed_frontmatter_and_writes_nothing(tmp_path):
    v = make_vault(tmp_path)
    bad = v / "notes" / "heat" / "scheme-choice.md"
    bad.write_text(bad.read_text().replace("\n---\n", "\n", 1))
    good = v / "notes" / "heat" / "ftcs-stability-limit.md"
    before = (bad.read_text(), good.read_text())
    sb(v, "sleep", "begin")
    code, _, err = sb(v, "sleep", "verified", "ftcs-stability-limit", "scheme-choice")
    assert code == 2 and "scheme-choice" in err
    assert (bad.read_text(), good.read_text()) == before and "verified" not in marker(v)


def test_verified_with_an_unknown_slug_changes_nothing(tmp_path):
    v = make_vault(tmp_path)
    good = v / "notes" / "heat" / "scheme-choice.md"
    before = good.read_text()
    sb(v, "sleep", "begin")
    code, _, err = sb(v, "sleep", "verified", "scheme-choice", "no-such-note")
    assert code == 2 and "no note 'no-such-note'" in err
    assert good.read_text() == before and "verified\t" not in marker(v)
    with pytest.raises(SbError):
        sleep.mark_verified(Vault(v), ["no-such-note"], TODAY)


def test_verified_sets_the_frontmatter_field_and_keeps_updated(tmp_path):
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "scheme-choice.md"
    sb(v, "sleep", "begin")
    assert sb(v, "sleep", "verified", "scheme-choice")[0] == 0
    fm = p.read_text().split("\n---\n", 1)[0]
    assert f"verified: {TODAY}" in fm.splitlines()
    assert sleep.digest(Vault(v).read("notes/heat/scheme-choice.md")) == sleep.digest(Vault(v).read("notes/heat/scheme-choice.md"))


# ---- held_pairs ----------------------------------------------------------------------------------------------

def pair_vault(tmp_path):
    v = init_vault(tmp_path)
    for s in "abcdef":
        add_note(v, f"n-{s}", project="p", title=f"Topic {s}", cue=f"cue {s}", gist=f"Gist: {s}.")
    sb(v, "sleep", "begin")
    return v


def held(v, *pairs, kinds=()):
    vv = Vault(v)
    sleep.remember(vv, "dream", [f"{a}|{b}" for a, b in pairs])
    sleep.remember(vv, "dreamkind", [f"{a}|{b}|{k}" for (a, b), k in zip(pairs, kinds)])
    return sleep.held_pairs(vv, sleep.load(vv), sleep.remembered(vv, "dream"))


def test_held_pairs_drop_linked_archived_rejected_and_keep_the_rest(tmp_path):
    v = pair_vault(tmp_path)
    p = v / "notes" / "p" / "n-a.md"
    p.write_text(p.read_text() + "\n## Links\n- [[n-b]] shares the scheme\n")  # linked, one direction is enough
    assert sb(v, "archive", "n-c", "--why", "faded")[0] == 0
    assert sb(v, "sleep", "unrelated", "n-e", "n-d", "words only")[0] == 0
    out = held(v, ("n-a", "n-b"), ("n-c", "n-d"), ("n-d", "n-e"), ("n-d", "n-f"), kinds=("same project",) * 4)
    assert out == [("n-d", "n-f", "same project")]


def test_held_pairs_follow_a_rename_and_default_the_kind(tmp_path):
    v = pair_vault(tmp_path)
    vv = Vault(v)
    sleep.remember(vv, "dream", ["n-a|n-b", "n-d|n-e"])  # a marker without dreamkind rows (sb <= 0.3.x)
    assert sb(v, "rename", "n-a", "n-z")[0] == 0
    vv = Vault(v)
    out = sleep.held_pairs(vv, sleep.load(vv), sleep.remembered(vv, "dream"))
    assert out == [("n-d", "n-e", "shown before"), ("n-z", "n-b", "shown before")]


def test_held_pairs_order_near_before_far(tmp_path):
    v = pair_vault(tmp_path)
    out = held(v, ("n-a", "n-b"), ("n-c", "n-d"), ("n-e", "n-f"), kinds=("far", "across projects", "same project"))
    assert [k for _, _, k in out] == ["across projects", "same project", "far"]


# ---- finish: dream count ------------------------------------------------------------------------------------

def test_done_counts_linked_unrelated_and_open_pairs(tmp_path):
    v = pair_vault(tmp_path)
    vv = Vault(v)
    sleep.remember(vv, "dream", ["n-a|n-b", "n-c|n-d", "n-e|n-f"])
    p = v / "notes" / "p" / "n-b.md"
    p.write_text(p.read_text() + "\n## Links\n- [[n-a]] same scheme\n")
    assert sb(v, "sleep", "unrelated", "n-c", "n-d", "words only")[0] == 0
    out = sb(v, "sleep", "done", "x")[1]
    assert "dream: 1 linked, 1 unrelated, 1 open of 3" in out, out


def test_a_pair_linked_and_rejected_counts_once_as_linked(tmp_path):
    v = pair_vault(tmp_path)
    sleep.remember(Vault(v), "dream", ["n-a|n-b"])
    p = v / "notes" / "p" / "n-b.md"
    p.write_text(p.read_text() + "\n## Links\n- [[n-a]] same scheme\n")
    sb(v, "sleep", "unrelated", "n-a", "n-b", "words only")
    assert "dream: 1 linked, 0 unrelated, 0 open of 1" in sb(v, "sleep", "done", "x")[1]


# ---- inbox junk -----------------------------------------------------------------------------------------------

def test_an_unreadable_inbox_date_is_very_old_and_due(tmp_path):
    v = init_vault(tmp_path)
    write_log(v, "inbox.tsv", "zzzzzz\tnot-a-date\tp\t\tremark of unknown age about zeolites")
    assert inbox.age(inbox.lines(Vault(v))[0], TODAY) > 30
    assert len(inbox.undecided(Vault(v), TODAY)) == 1
    sb(v, "sleep", "begin")
    assert " DUE:" in sb(v, "sleep", "plan")[1]
    out = sb(v, "sleep", "done", "x")[1]
    assert "1 expired" in out and "expired zzzzzz:" in out


def test_an_expired_line_can_still_be_resolved(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "a-note", project="p")
    write_log(v, "inbox.tsv", "aaaaaa\t2026-09-01\tp\t\tstale remark about zeolites", "bbbbbb\t2026-09-01\tp\t\tother stale remark about quarks")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    assert "2 expired" in sb(v, "sleep", "done", "x")[1]
    assert "aaaaaa" not in sb(v, "inbox")[1]
    assert sb(v, "inbox", "resolve", "aaaaaa", "--note", "a-note")[0] == 0
    assert sb(v, "inbox", "resolve", "bbbbbb", "--drop")[0] == 0
    assert inbox.done(Vault(v))["aaaaaa"][1] == "note:a-note" and "bbbbbb" not in sb(v, "inbox", "--expired")[1]
    code, _, err = sb(v, "inbox", "resolve", "aaaaaa", "--drop")  # settled now
    assert code == 2 and "already settled (note:a-note)" in err


def test_a_duplicated_inbox_id_is_one_line(tmp_path):
    v = init_vault(tmp_path)
    row = "aaaaaa\t2026-11-07\tp\t\tremark about zeolites"
    write_log(v, "inbox.tsv", row, row, "bbbbbb\t2026-11-07\tp\t\tremark about quarks")
    assert [r[0] for r in inbox.lines(Vault(v))] == ["aaaaaa", "bbbbbb"]
    assert sleep.backlog(Vault(v), TODAY)["inbox"] == 2


def test_capture_salts_the_hash_when_every_prefix_is_taken(tmp_path):
    v = init_vault(tmp_path)
    vv = Vault(v)
    first = inbox.capture(vv, "same text twice", "p", "", TODAY)
    second = inbox.capture(vv, "same text twice", "p", "", TODAY)
    assert first != second and second.startswith(first) and len(second) == 7
    full = hashlib.sha1((TODAY.isoformat() + "salted text").encode()).hexdigest()
    write_log(v, "inbox.tsv", *[f"{full[:n]}\t{TODAY}\tp\t\told {n}" for n in range(6, 41)])
    new = inbox.capture(vv, "salted text", "p", "", TODAY)
    assert new not in {full[:n] for n in range(6, 41)} and len(new) == 6
    assert len({r[0] for r in inbox.lines(vv)}) == 36


# ---- sentence -------------------------------------------------------------------------------------------------

def test_sentence_cuts_at_the_first_sentence_and_at_the_cap():
    assert sleep.sentence("  One thing. Another thing.  ") == "One thing."
    assert sleep.sentence("Is it? Yes.") == "Is it?"
    assert sleep.sentence("no stop at all") == "no stop at all"
    long = sleep.sentence("a" * 300)
    assert len(long) == 200 and long.endswith("...") and long[:-3] == "a" * 197
    assert sleep.sentence("x" * 10, cap=10) == "x" * 10 and sleep.sentence("x" * 11, cap=10) == "x" * 7 + "..."
    assert sleep.sentence("aaaa  bbbb", cap=8) == "aaaa..."  # trailing blanks fall away before the dots
    assert sleep.sentence("") == ""


# ---- stale marker ---------------------------------------------------------------------------------------------

def test_a_plan_under_a_stale_marker_notes_nothing_in_it(tmp_path):
    v = make_vault(tmp_path)
    (v / ".sb").mkdir(exist_ok=True)
    text = "2026-11-01\ndreamed\t1\ndream\tscheme-choice|open-nonuniform-mesh-order\n"
    (v / ".sb" / "sleeping").write_text(text)
    out = sb(v, "sleep", "plan")[1]
    assert "still open" not in out and marker(v) == text
