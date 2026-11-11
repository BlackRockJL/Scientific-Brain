from datetime import date

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain import usage
from scientific_brain.index import Index
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def sleeping(v):
    return v / ".sb" / "sleeping"


def tsv(v, name):
    return [ln.split("\t") for ln in (v / ".sb" / name).read_text().splitlines()]


def test_begin_twice_force_and_stale(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "sleep", "begin")[0] == 0 and sleeping(v).read_text().strip() == "2026-11-08"
    code, _, err = sb(v, "sleep", "begin")
    assert code == 2 and "running since 2026-11-08" in err
    assert sb(v, "sleep", "begin", "--force")[0] == 0
    sleeping(v).write_text("2026-11-06\n")  # two days old: stale, replaced with a note
    code, out, _ = sb(v, "sleep", "begin")
    assert code == 0 and "replaced the sleep from 2026-11-06" in out and sleeping(v).read_text().strip() == "2026-11-08"
    sleeping(v).write_text("2026-11-07\n")  # one day old is still running
    assert sb(v, "sleep", "begin")[0] == 2


def test_plan_has_four_sections_and_file_output(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "sleep", "plan")
    assert code == 0 and out.startswith("# Sleep plan 2026-11-08")
    for h in ("## 1. Consolidate: 3 open", "## 2. Condense", "## 3. Forget", "## 4. Verify"):
        assert h in out
    assert "`5c4c87` 8d heat" in out and "similar: scheme-choice" in out
    assert "- cn-oscillations-large-time-step (new) [finding, heat] sources: runs/ftcs-sweep/summary.csv, docs/heat-notes.txt" in out
    assert "ftcs-stability-limit (new)" not in out  # verified after its last update: checked before any snapshot
    assert "compare with: " in out and "touched: 11 of 40" in out
    code, out2, _ = sb(v, "sleep", "plan", "-o", str(tmp_path / "plan.md"))
    assert out2.startswith("wrote ") and (tmp_path / "plan.md").read_text() == out


def test_plan_marks_due_lines_and_pairs_of_similar_lines(tmp_path):
    v = make_vault(tmp_path)
    old = v / ".sb" / "inbox.tsv"
    old.write_text(old.read_text() + "aaaaaa\t2026-09-01\theat\t\tGraded mesh lowers the observed order near the boundary\n"
                   "bbbbbb\t2026-11-07\theat\t\tObserved order near the boundary drops on a graded mesh\n")
    out = sb(v, "sleep", "plan")[1]
    assert "`aaaaaa` 68d heat DUE:" in out and "`bbbbbb` 1d heat:" in out and "DUE: Graded" in out
    assert "captured twice, promote: `aaaaaa` ~ `bbbbbb`" in out


def test_condense_finds_planted_pair_and_prefers_same_project(tmp_path):
    v = make_vault(tmp_path)
    for s in ("implicit-euler-damping", "backward-euler-damping"):
        add_note(v, s, title="Implicit Euler damps stiff modes strongly", cue="choosing a solver for stiff modes",
                 gist="Gist: Implicit Euler damps stiff modes strongly.")
    out = sb(v, "sleep", "plan")[1]
    assert "- backward-euler-damping ~ implicit-euler-damping (coverage 1.00; heat, heat)" in out


def test_verify_a_keeps_unverified_notes_until_a_sleep_verifies_them(tmp_path):
    v = make_vault(tmp_path)
    assert "## 4. Verify: 7 new or changed" in sb(v, "sleep", "plan")[1]
    sb(v, "sleep", "begin")
    sb(v, "sleep", "verified", "scheme-choice", "couple-time-step-to-mesh")
    assert sb(v, "sleep", "done", "first")[0] == 0
    assert len(tsv(v, "snapshot.tsv")) == 3  # the two verified notes and the one verified before
    out = sb(v, "sleep", "plan")[1]
    assert "## 4. Verify: 5 new or changed" in out  # what the sleep did not verify stays listed
    assert "scheme-choice (new)" not in out and "scheme-choice (changed)" not in out
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text() + "\nOne more sentence.\n")
    add_note(v, "fresh-note", title="Fresh note about meshes", cue="when meshes change")
    out = sb(v, "sleep", "plan")[1]
    assert "- scheme-choice (changed) [decision, heat]" in out and "- fresh-note (new)" in out
    assert "## 4. Verify: 7 new or changed" in out and out.count("(changed)") == 1 and out.count("(new)") == 6
    assert out.index("scheme-choice (changed)") < out.index("fresh-note (new)")  # edited notes first


def test_verify_a_lists_deferred_notes_again_after_done(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[lifecycle]\nsleep_budget = 5\n")
    sb(v, "sleep", "begin")
    out = sb(v, "sleep", "plan")[1]
    listed = [ln.split()[1] for ln in out.split("## 4.")[1].splitlines() if ln.endswith(("]", "none")) and "(new)" in ln]
    sb(v, "sleep", "done", "nothing verified")
    out = sb(v, "sleep", "plan")[1]
    assert "## 4. Verify: 7 new or changed" not in out or listed  # the plan is cut by the budget ...
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "again")
    assert "(new)" in sb(v, "sleep", "plan")[1]  # ... but unverified notes never drop out of verify (a)


def test_verified_marks_changes_hash_not(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "baseline")
    sb(v, "sleep", "begin")
    p = v / "notes" / "heat" / "scheme-choice.md"
    before = p.read_text()
    assert sb(v, "sleep", "verified", "scheme-choice", "ftcs-stability-limit")[0] == 0
    text = p.read_text()
    assert "verified: 2026-11-08\n" in text and "updated: " + before.split("updated: ")[1].split("\n")[0] in text
    assert sb(v, "check")[0] == 0
    sb(v, "sleep", "done", "second")
    sb(v, "recall", "scheme", "choice")
    assert "work\tedit\tscheme-choice" not in (v / ".sb" / "usage.tsv").read_text()
    assert "(changed)" not in sb(v, "sleep", "plan")[1]  # verified does not make a note look changed
    assert sb(v, "sleep", "verified", "scheme-choice")[0] == 2
    code, _, err = sb(v, "sleep", "verified", "scheme-choice")
    assert "only during a sleep" in err and code == 2


def test_verify_b_takes_oldest_verified_of_the_active_notes(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "verified", "scheme-choice")
    sb(v, "sleep", "done", "s")
    out = sb(v, "sleep", "plan")[1].split("## 4.")[1]
    assert "verified never)" not in out  # unchecked notes are in verify (a), not (b)
    assert out.index("ftcs-stability-limit (A=") < out.index("scheme-choice (A=")  # oldest verified first


def test_budget_caps_the_plan_and_reports_deferred(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[lifecycle]\nsleep_budget = 3\n")
    out = sb(v, "sleep", "plan")[1]
    assert "touched: 3 of 3 (notes and inbox lines), deferred: 8" in out  # 11 items in the unlimited plan (see test above), 3 fit
    assert "## 1. Consolidate: 3 open" in out and "## 4. Verify: 0 new" in out  # consolidate fills first


def test_veto_counts_in_activation_and_is_logged(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    assert sb(v, "sleep", "veto", "morton-mayers-assessment", "still the reference we cite")[0] == 0
    assert tsv(v, "vetoes.tsv") == [["2026-11-08", "morton-mayers-assessment", "still the reference we cite"]]
    assert usage.events(Vault(v))["morton-mayers-assessment"] == {TODAY: 0.5}  # counts although caller=sleep
    assert tsv(v, "usage.tsv")[-1] == ["2026-11-08", "sleep", "veto", "morton-mayers-assessment"]
    assert sb(v, "sleep", "veto", "ftcs-cfl-guess", "archived")[0] == 2  # only notes/
    assert sb(v, "sleep", "veto", "scheme-choice", "  ")[0] == 2


def test_done_expires_logs_snapshot_and_clears_due(tmp_path):
    v = make_vault(tmp_path)
    (v / ".sb" / "inbox.tsv").write_text((v / ".sb" / "inbox.tsv").read_text() + "dddddd\t2026-09-01\theat\t\tstale line\n")
    assert "sleep due" in sb(v, "stats")[1] and sb(v, "sleep", "done", "x")[0] == 2  # no sleep running
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")  # only lines a plan of this sleep showed can expire
    code, out, _ = sb(v, "sleep", "done", "first sleep")
    assert code == 0 and out.splitlines()[0] == "slept 2026-11-08: 0 inbox lines settled (0 promoted), 1 expired, 0 archived, 0 created, 0 vetoes"
    assert out.splitlines()[1] == "expired dddddd: stale line"
    assert out.splitlines()[2].startswith("dream: 0 linked, 0 unrelated, ")  # the demo vault has unlinked pairs
    assert tsv(v, "inbox-done.tsv") == [["dddddd", "2026-11-08", "expired"]]
    assert tsv(v, "sleep.tsv") == [["2026-11-08T12:00", "0", "0", "0", "0", "0", "1", "first sleep"]]
    assert [r[0] for r in tsv(v, "snapshot.tsv")] == ["ftcs-stability-limit"] and not sleeping(v).exists()
    assert "sleep due" not in sb(v, "stats")[1] and "sleep due" not in sb(v, "recall", "observed", "order")[1]


def test_done_expires_only_lines_the_plan_showed(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[lifecycle]\nsleep_budget = 1\n")
    inb = v / ".sb" / "inbox.tsv"
    inb.write_text(inb.read_text() + "eeeeee\t2026-08-01\theat\t\toldest line\nffffff\t2026-08-02\theat\t\tsecond line\n")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    assert sb(v, "inbox", "resolve", "eeeeee", "--drop")[0] == 0  # settling the shown line must not pull in the next
    sb(v, "sleep", "done", "budget of one")
    assert tsv(v, "inbox-done.tsv") == [["eeeeee", "2026-11-08", "dropped"]]  # ffffff was never shown, it waits


def test_forget_cap_binds_only_while_other_work_waits(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[lifecycle]\nforget_budget = 1\n")
    for s, t in (("long-unused-a", "Crank Nicolson oscillates"), ("long-unused-b", "Multigrid smoother choice")):
        add_note(v, s, title=t, cue=t.lower(), extra={"created": "2025-01-01", "updated": "2025-01-01"})
    out = sb(v, "sleep", "plan")[1]
    assert "## 3. Forget: 2 candidates" in out and "deferred: 0" in out  # budget left after verify takes the rest
    (v / "sb.toml").write_text((v / "sb.toml").read_text().replace("forget_budget = 1", "forget_budget = 1\nsleep_budget = 4"))
    out = sb(v, "sleep", "plan")[1]
    assert "## 3. Forget: 1 candidates" in out and "touched: 4 of 4" in out  # a full plan keeps the cap


def test_condense_skips_linked_parts(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "implicit-euler-damping", title="Implicit Euler damps stiff modes strongly", cue="choosing a solver for stiff modes",
             gist="Gist: Implicit Euler damps stiff modes strongly.", body="\n## Links\n- [[implicit-euler-damping-numbers]]\n")
    add_note(v, "implicit-euler-damping-numbers", title="Implicit Euler damps stiff modes strongly: numbers",
             cue="choosing a solver for stiff modes", gist="Gist: Implicit Euler damps stiff modes strongly.")
    assert "implicit-euler-damping ~ implicit-euler-damping-numbers" not in sb(v, "sleep", "plan")[1]


def test_stale_sleep_marker_stops_tagging_work(tmp_path):
    v = make_vault(tmp_path)
    sleeping(v).write_text("2026-11-01\n")
    sb(v, "show", "scheme-choice")
    assert tsv(v, "usage.tsv")[-1] == ["2026-11-08", "work", "show", "scheme-choice"]


def test_whole_sleep_counts_and_promotion_warning(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    assert sb(v, "inbox", "resolve", "5c4c87", "--note", "richardson-error-estimate")[0] == 0
    assert sb(v, "inbox", "resolve", "c0e92f", "--note", "observed-convergence-order")[0] == 0
    assert sb(v, "inbox", "resolve", "d51838", "--drop")[0] == 0
    assert sb(v, "archive", "morton-mayers-assessment", "--why", "faded")[0] == 0
    sb(v, "sleep", "veto", "scheme-choice", "still needed")
    assert sb(v, "new", "finding", "brand-new", "-p", "heat", "--why", "own measurement")[0] == 0
    code, out, _ = sb(v, "sleep", "done", "mixed")
    assert code == 0 and "warning" not in out  # three settled lines are too few for a rate
    assert tsv(v, "sleep.tsv")[0][1:] == ["3", "2", "1", "1", "1", "0", "mixed"]
    assert sb(v, "stats")[1].count("last sleep: 2026-11-08 (0 days ago)") == 1


def test_promotion_warning_needs_enough_lines(tmp_path):
    v = make_vault(tmp_path)
    inb = v / ".sb" / "inbox.tsv"
    inb.write_text(inb.read_text() + "".join(f"{i:06x}\t2026-11-07\theat\t\tline {i}\n" for i in range(7)))
    sb(v, "sleep", "begin")
    for id in ("5c4c87", "c0e92f", "000000"):
        assert sb(v, "inbox", "resolve", id, "--note", "scheme-choice")[0] == 0
    for id in ["d51838"] + [f"{i:06x}" for i in range(1, 7)]:
        assert sb(v, "inbox", "resolve", id, "--drop")[0] == 0
    out = sb(v, "sleep", "done", "ten lines")[1]
    assert "warning: 3 of 10 settled inbox lines were promoted (30%, limit 25%)" in out


def test_work_outside_the_sleep_is_not_counted(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "new", "finding", "made-before", "-p", "heat", "--why", "own measurement")
    sb(v, "sleep", "begin")
    out = sb(v, "sleep", "done", "empty")[1]
    assert "0 created" in out


def test_plan_lists_forget_candidates_with_last_use(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "long-unused", extra={"created": "2025-01-01", "updated": "2025-01-01"})
    out = sb(v, "sleep", "plan")[1]
    assert "## 3. Forget: 1 candidates" in out and "- long-unused [finding] A=-3.26" in out and "last used 2025-01-01" in out


def baseline(v):
    """A first sleep that verifies every note, so the snapshot holds all of them."""
    sb(v, "sleep", "begin")
    sb(v, "sleep", "verified", *sorted(p.stem for p in (v / "notes").rglob("*.md")))
    sb(v, "sleep", "done", "baseline")


def verify_a(v):
    return "\n".join(ln for ln in sb(v, "sleep", "plan")[1].split("## 4.")[1].splitlines() if "(A=" not in ln)


def test_an_edited_note_stays_unchecked_through_rename(tmp_path):
    v = make_vault(tmp_path)
    baseline(v)
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text() + "\nOne more sentence.\n")
    assert sb(v, "rename", "scheme-choice", "time-scheme-choice")[0] == 0
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "nothing verified")
    assert "- time-scheme-choice (changed)" in verify_a(v)


def test_archive_and_restore_keep_the_snapshot_row(tmp_path):
    v = make_vault(tmp_path)
    baseline(v)
    assert sb(v, "archive", "scheme-choice", "--why", "faded")[0] == 0
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "while archived")
    assert sb(v, "restore", "scheme-choice")[0] == 0
    assert "scheme-choice" not in verify_a(v)  # unchanged since its check
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text() + "\nOne more sentence.\n")
    assert "- scheme-choice (changed)" in verify_a(v)


def test_a_second_sleep_on_the_same_day_does_not_trust_the_first(tmp_path):
    v = make_vault(tmp_path)
    baseline(v)
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text() + "\nOne more sentence.\n")  # verified today, edited afterwards
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "second, nothing verified")
    assert "- scheme-choice (changed)" in verify_a(v)


def test_sleep_baseline_accepts_current_notes_without_touching_verified(tmp_path):
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "scheme-choice.md"
    before = p.read_text()
    code, out, _ = sb(v, "sleep", "baseline")
    assert code == 0 and "(7 were unchecked)" in out
    assert p.read_text() == before and not (v / ".sb" / "sleep.tsv").exists()  # no verified date, no sleep logged
    assert "(new)" not in verify_a(v) and "(changed)" not in verify_a(v)
    p.write_text(before + "\nOne more sentence.\n")
    assert "- scheme-choice (changed)" in verify_a(v)  # later edits are checked again
    sb(v, "sleep", "begin")
    code, _, err = sb(v, "sleep", "baseline")
    assert code == 2 and "a sleep is running" in err


def test_baseline_seed_lets_an_import_fade_one_by_one(tmp_path):
    v = make_vault(tmp_path)
    (v / ".sb" / "usage.tsv").unlink(missing_ok=True)
    for i, typ in enumerate(("finding", "decision", "concept", "finding", "question")):
        d = f"2024-0{i + 1}-01"
        add_note(v, f"old-{i}", type=typ, extra={"created": d, "updated": d},
                 body="\n## Rejected\n\n- none\n" if typ == "decision" else "\n## Depends on\n\n- none\n" if typ == "question" else "")
    vv = Vault(v)
    assert len(usage.forget_candidates(vv, TODAY)) >= 5  # an import without use is due to be forgotten at once
    code, out, _ = sb(v, "sleep", "baseline", "--seed", "365")
    n = sum(1 for _ in (v / "notes").rglob("*.md"))
    assert code == 0 and f"seeded {n} notes without use" in out
    fades = {}
    for dt in range(0, 400):
        for s, *_ in usage.forget_candidates(vv, date.fromordinal(TODAY.toordinal() + dt)):
            fades.setdefault(s, dt)
    assert len(fades) == n and min(fades.values()) >= 30 and max(fades.values()) <= 366
    assert [fades[f"old-{i}"] for i in range(5)] == sorted(fades[f"old-{i}"] for i in range(5))  # oldest update first
    assert sb(v, "sleep", "baseline", "--seed", "365")[1].count("seeded 0 notes") == 1  # a rerun seeds nothing twice
    assert sb(v, "sleep", "baseline", "--seed", "20")[0] == 2


def test_sleep_comes_due_when_a_full_plan_of_work_waits(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[lifecycle]\nsleep_budget = 12\n")
    sb(v, "sleep", "baseline")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "first")
    out = sb(v, "stats")[1]
    assert "waiting for a sleep: 3 (inbox 3, verify 0, forget 0), about 1 sleeps of 12" in out and "sleep due" not in out
    for i in range(9):
        add_note(v, f"fresh-note-{i}")
    out = sb(v, "stats")[1]
    assert "waiting for a sleep: 12 (inbox 3, verify 9, forget 0)" in out
    assert "sleep due" not in out  # the last sleep was today: sleep_gap holds it back
    log = v / ".sb" / "sleep.tsv"  # SB_TODAY runs at noon; sleep_gap_hours = 5
    log.write_text(log.read_text().replace("2026-11-08T12:00", "2026-11-08T07:01"))
    assert "sleep due" not in sb(v, "stats")[1]
    log.write_text(log.read_text().replace("2026-11-08T07:01", "2026-11-08T07:00"))
    assert "sleep due: 12 items waiting (inbox 3, verify 9)" in sb(v, "stats")[1]
    log.write_text(log.read_text().replace("2026-11-08T07:00", "2026-11-08"))  # a date-only row counts from midnight
    assert "sleep due: 12 items waiting (inbox 3, verify 9)" in sb(v, "stats")[1]


def import_old_notes(v, n=39):
    """n notes created a long time ago without any use, as an import brings them."""
    kinds = ("finding", "concept", "decision", "derivation", "question")
    for i in range(n):
        typ, d = kinds[i % 5], f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
        add_note(v, f"imp-{i:02d}", type=typ, extra={"created": d, "updated": d}, title=f"Imported note {i}", cue=f"cue {i}",
                 gist=f"Gist: imported {i}.", body="\n## Rejected\n\n- none\n" if typ == "decision" else "\n## Depends on\n\n- none\n" if typ == "question" else "")


def fade_days(v):
    """Slug -> first day from today on that the note is a forget candidate."""
    vv, out = Vault(v), {}
    for dt in range(0, 500):
        for s, *_ in usage.forget_candidates(vv, date.fromordinal(TODAY.toordinal() + dt)):
            out.setdefault(s, dt)
    return out


@pytest.mark.parametrize("check_first", [True, False])
def test_baseline_seed_works_when_the_index_existed_before_the_import(tmp_path, check_first):
    v = tmp_path / "v"
    assert sb(v, "init", str(v))[0] == 0
    sb(v, "stats")  # the index exists now, so the import shows up as edits
    import_old_notes(v)
    if check_first:
        sb(v, "check")  # the sync logs every imported note as edited
    code, out, _ = sb(v, "sleep", "baseline", "--seed", "210")
    assert code == 0 and "seeded 39 notes without use" in out
    sb(v, "stats")
    sb(v, "map")  # later calls must not lift the seeds again
    fades = fade_days(v)
    assert len(fades) == 39 and min(fades.values()) >= 30 and max(fades.values()) <= 211
    assert min(fades.values()) <= 32 and max(fades.values()) >= 205  # spread over the whole range
    assert all(abs(x - (30 + 180 * i / 38)) <= 3 for i, x in enumerate(sorted(fades.values())))  # evenly spread
    assert sb(v, "sleep", "baseline", "--seed", "210")[1].count("seeded 0 notes") == 1


def test_baseline_seed_leaves_a_note_with_real_use_alone(tmp_path):
    v = tmp_path / "v"
    sb(v, "init", str(v))
    sb(v, "stats")
    import_old_notes(v, 3)
    sb(v, "show", "imp-00")
    assert "seeded 2 notes" in sb(v, "sleep", "baseline", "--seed", "100")[1]


def test_done_keeps_expired_lines_apart_and_findable(tmp_path):
    v = make_vault(tmp_path)
    inb = v / ".sb" / "inbox.tsv"
    inb.write_text("aaaaaa\t2026-09-01\theat\tdocs/x\tstale remark about quantum tunnelling\n"
                   "bbbbbb\t2026-11-07\theat\t\tgood line\n")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    assert sb(v, "inbox", "resolve", "bbbbbb", "--note", "scheme-choice")[0] == 0
    out = sb(v, "sleep", "done", "s")[1]
    assert out.splitlines()[0].startswith("slept 2026-11-08: 1 inbox lines settled (1 promoted), 1 expired, ")
    assert "expired aaaaaa: stale remark about quantum tunnelling" in out
    stats = sb(v, "stats")[1]
    assert "promotion rate: 100% (1 of 1 settled), 1 expired" in stats and "inbox: 0 open" in stats
    assert "aaaaaa" not in sb(v, "inbox")[1] and "(no open inbox lines)" in sb(v, "inbox")[1]
    assert sb(v, "inbox", "--expired")[1].strip() == "aaaaaa  expired 2026-11-08  heat  stale remark about quantum tunnelling  (docs/x)"
    assert "(no expired inbox lines)" in sb(v, "inbox", "--expired", "-p", "_general")[1]
    rec = sb(v, "recall", "--inbox", "quantum", "tunnelling")[1]
    assert "inbox:aaaaaa  [inbox expired, heat, 2026-09-01]" in rec
    assert "+1 inbox matches (use --inbox)" in sb(v, "recall", "quantum", "tunnelling")[1]
    assert Index(Vault(v)).n_inbox() == 0  # an expired line is not an open one


def test_a_wait_on_a_due_line_is_not_expired_in_the_same_sleep(tmp_path):
    v = make_vault(tmp_path)
    inb = v / ".sb" / "inbox.tsv"
    inb.write_text("aaaaaa\t2026-09-01\theat\t\tdue but wanted\nbbbbbb\t2026-09-02\theat\t\tdue and forgotten\n")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    assert sb(v, "inbox", "resolve", "aaaaaa", "--wait")[0] == 0
    out = sb(v, "sleep", "done", "s")[1]
    assert "1 expired" in out and "expired bbbbbb:" in out and "aaaaaa" not in out
    assert "aaaaaa" in sb(v, "inbox")[1] and "bbbbbb" not in sb(v, "inbox")[1]


def planned(v):
    return [r.split("\t")[1] for r in sleeping(v).read_text().splitlines()[1:] if r.startswith("planned\t")]


def test_use_during_a_sleep_survives_for_notes_outside_the_plan(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    inside = planned(v)[0]
    add_note(v, "late-note")  # a parallel session writes it after the plan was made
    sb(v, "show", "late-note")
    sb(v, "show", inside)
    ev = lambda s: usage.events(Vault(v)).get(s, {})  # noqa: E731
    assert TODAY not in ev("late-note") and TODAY not in ev(inside)  # nothing counts while the sleep runs
    sb(v, "sleep", "done", "s")
    assert ev("late-note")[TODAY] == 1.0  # the plan did not name it, so nobody but the parallel session read it
    assert TODAY not in ev(inside)  # the sleeper read this one


def test_the_second_sleep_of_a_day_does_not_release_the_first_ones_use(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    inside = planned(v)[0]
    sb(v, "show", inside)
    sb(v, "sleep", "done", "first")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "second, no plan")
    assert TODAY not in usage.events(Vault(v)).get(inside, {})


def test_a_replaced_sleep_releases_its_use_too(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "sleep", "begin")
    add_note(v, "late-note")
    sb(v, "show", "late-note")
    sleeping(v).write_text("2026-11-06\n")  # the sleep was never finished
    assert "replaced the sleep" in sb(v, "sleep", "begin")[1]
    assert usage.events(Vault(v))["late-note"][TODAY] == 1.0


def test_a_marker_older_than_a_day_blocks_no_baseline_and_shows_in_stats(tmp_path):
    v = make_vault(tmp_path)
    sleeping(v).write_text("2026-11-01\n")
    out = sb(v, "stats")[1]
    assert "warning: the sleep marker from 2026-11-01 was left behind" in out
    code, out, _ = sb(v, "sleep", "baseline")
    assert code == 0 and "baseline:" in out
    sleeping(v).write_text("2026-11-07\n")  # a day old still counts as running
    assert sb(v, "sleep", "baseline")[0] == 2 and "left behind" not in sb(v, "stats")[1]


def test_deferred_counts_notes_not_refusals(tmp_path):
    v = make_vault(tmp_path)
    for s, t in (("long-unused-a", "Crank Nicolson oscillates"), ("long-unused-b", "Multigrid smoother choice")):  # forget and verify
        add_note(v, s, title=t, cue=t.lower(), extra={"created": "2025-01-01", "updated": "2025-01-01"})
    full = sb(v, "sleep", "plan")[1]
    assert "## 3. Forget: 2 candidates" in full and "deferred: 0" in full
    n = int(full.split("touched: ")[1].split(" ")[0])
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[lifecycle]\nsleep_budget = 3\n")
    assert f"touched: 3 of 3 (notes and inbox lines), deferred: {n - 3}" in sb(v, "sleep", "plan")[1]
