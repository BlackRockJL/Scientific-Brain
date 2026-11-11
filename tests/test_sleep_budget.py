"""What a sleep takes (budget), what it leaves (leftover), when the next one is due, how long a rejected pair rests."""
from datetime import date, timedelta

import pytest
from helpers import add_note, init_vault, sb, write_log

from scientific_brain import sleep, usage
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def ago(n):
    return TODAY - timedelta(days=n)


def entry(planned=None, left=None, stamp="2026-11-01T12:00"):
    """A sleep.tsv row: eight fields as of 0.2.1, planned and left appended as of 0.3."""
    return [stamp, 0, 0, 0, 0, 0, 0, "summary"] + ([] if planned is None else [planned, left])


def budget(root, *rows):
    write_log(root, "sleep.tsv", *rows)
    return usage.budget(Vault(root))


# ---- budget -----------------------------------------------------------------------------------------------------

def test_budget_starts_at_forty_without_a_log(tmp_path):
    root = init_vault(tmp_path)
    assert usage.budget(Vault(root)) == usage.B0 == 40  # no sleep.tsv at all
    assert budget(root) == 40  # an empty one


def test_old_sleep_rows_with_six_and_eight_fields_leave_the_budget_alone(tmp_path):
    root = init_vault(tmp_path)
    six = ["2026-09-01", 3, 1, 0, 2, "summary"]  # before 0.2.1: date, settled, promoted, archived, created, summary
    assert budget(root, six, entry(), entry(stamp="2026-10-01")) == 40
    assert budget(root, six, entry(), entry(40, 0)) == 50  # the first row with a plan size starts the learning
    assert budget(root, entry(40, 0), six, entry()) == 50  # old rows between do nothing


def test_a_finished_full_plan_grows_the_budget_by_a_quarter_up_to_two_hundred(tmp_path):
    root = init_vault(tmp_path)
    want = [40, 50, 62, 78, 98, 122, 152, 190, 200, 200, 200]  # round(b * 1.25), capped
    for n, b in enumerate(want):
        assert budget(root, *[entry(999, 0)] * n) == b, n


def test_a_plan_with_more_than_a_tenth_left_sets_the_next_to_what_was_done(tmp_path):
    root = init_vault(tmp_path)
    assert budget(root, entry(40, 4)) == 50  # exactly a tenth left counts as finished
    assert budget(root, entry(40, 5)) == 35  # more than a tenth: the plan was too large, 35 were done
    assert budget(root, entry(40, 20)) == 20 and budget(root, entry(40, 30)) == 10  # 10 is the floor
    assert budget(root, entry(40, 40)) == 10 and budget(root, entry(40, 99)) == 10  # left beyond the size counts as the size


def test_after_an_overshoot_the_budget_creeps_back_up_below_the_known_limit(tmp_path):
    root = init_vault(tmp_path)
    limit = entry(40, 20)  # 40 was too much: 20 done, and 40 is the limit
    want = [20, 25, 31, 35, 37, 38, 39, 40, 41, 42]  # growth by a quarter, halfway to the limit; just below it the limit rises by one
    for n, b in enumerate(want):
        assert budget(root, limit, *[entry(b_, 0) for b_ in [20, 25, 31, 35, 37, 38, 39, 40, 41][:n]]) == b, n


def test_a_small_plan_that_was_done_changes_nothing_and_size_zero_rows_do_not_count(tmp_path):
    root = init_vault(tmp_path)
    assert budget(root, entry(5, 0)) == 40 and budget(root, entry(39, 0)) == 40  # a plan below the budget, done
    assert budget(root, entry(40, 0)) == 50  # a plan of exactly the budget is full
    assert budget(root, entry(0, 0)) == 40 and budget(root, entry(0, 7)) == 40 and budget(root, entry(0, 0), entry(40, 0)) == 50


def test_a_plan_larger_than_the_budget_that_was_done_counts_as_full(tmp_path):
    root = init_vault(tmp_path)
    assert budget(root, entry(60, 0)) == 50  # grows from the budget, not from the plan
    assert budget(root, entry(40, 20), entry(25, 0)) == 25 and budget(root, entry(40, 20), entry(20, 0)) == 25


def test_broken_plan_fields_leave_the_budget_alone(tmp_path):
    root = init_vault(tmp_path)
    for bad in (["x", "y"], ["", ""], ["-3", "0"], ["3.5", "0"], ["40", ""], ["", "5"], [" 40", "0"], ["40", "-1"]):
        assert budget(root, entry(*bad)) == 40, bad
    assert budget(root, "2026-11-01T12:00\t0\t0\t0\t0\t0\t0\tsummary\t40") == 40  # nine fields: left is missing
    assert budget(root, "2026-11-01T12:00\t0\t0\t0\t0\t0\t0\tsummary\t40\t0\textra") == 40  # more fields than known: 'left' is '0 extra'
    assert budget(root, "garbage", "", "\t\t\t", entry(40, 0)) == 50  # a good row among broken ones still counts
    assert budget(root, entry("9" * 30, 0)) == 50  # a huge number is a number


def test_a_superscript_digit_in_a_plan_field_does_not_crash(tmp_path):
    root = init_vault(tmp_path)
    assert budget(root, entry("\u00b2", "0")) == 40


def test_the_budget_constants_are_live(tmp_path, monkeypatch):
    root = init_vault(tmp_path)
    monkeypatch.setattr(usage, "B0", 5)
    monkeypatch.setattr(usage, "B_MIN", 1)
    assert budget(root) == 5 and budget(root, entry(5, 0)) == 6  # round(6.25)
    monkeypatch.setattr(usage, "B0", 100)
    assert budget(root, entry(100, 20)) == 80
    monkeypatch.setattr(usage, "B_MAX", 60)
    monkeypatch.setattr(usage, "B0", 50)
    assert budget(root, entry(50, 0)) == 60 and budget(root, entry(50, 0), entry(60, 0)) == 60  # capped
    monkeypatch.setattr(usage, "B_MIN", 45)
    assert budget(root, entry(50, 20)) == 45  # 30 were done, 45 is the floor


def test_the_plan_header_and_stats_show_the_learned_budget(tmp_path):
    root = init_vault(tmp_path)
    for i in range(3):
        add_note(root, f"n{i}", project="p", title=f"Note {i}", cue=f"cue {i}")
    write_log(root, "sleep.tsv", entry(40, 8))  # 8 of 40 left: 32 were done
    assert "touched: 3 of 32 (notes and inbox lines)" in sb(root, "sleep", "plan")[1]
    stats = sb(root, "stats")[1]
    assert "a sleep takes 32 items" in stats and "about 1 sleeps of 32" in stats


# ---- leftover and finish ----------------------------------------------------------------------------------------

def put(root, slug, made, **kw):
    add_note(root, slug, project="p", extra={"created": ago(made).isoformat(), "updated": ago(made).isoformat()}, **kw)


def work_vault(tmp_path):
    """Two forget candidates (checked by a baseline), one new unchecked note, two open inbox lines."""
    root = init_vault(tmp_path)
    put(root, "c1", 400, title="Crank Nicolson oscillates", cue="crank nicolson oscillation")
    put(root, "c2", 400, title="Multigrid smoother choice", cue="multigrid smoother")
    assert sb(root, "sleep", "baseline")[0] == 0
    put(root, "n1", 1, title="Fresh result on meshes", cue="fresh mesh result")
    write_log(root, "inbox.tsv", "aaaaaa\t2026-11-07\tp\t\tfirst remark about zeolites", "bbbbbb\t2026-11-07\tp\t\tsecond remark about pendulums")
    return root


def left(root):
    v = Vault(root)
    return sleep.leftover(v, TODAY, sleep.started(v))


def sleep_rows(root):
    return [ln.split("\t") for ln in (root / ".sb" / "sleep.tsv").read_text().splitlines()]


def test_leftover_counts_the_size_of_the_plan_and_what_is_still_waiting(tmp_path):
    root = work_vault(tmp_path)
    assert sb(root, "sleep", "begin")[0] == 0
    assert left(root) == (0, 0)  # a sleep without a plan has nothing planned
    sb(root, "sleep", "plan")
    work = sleep.remembered(Vault(root), "work")
    assert work == {"inbox:aaaaaa", "inbox:bbbbbb", "forget:c1", "forget:c2", "verify:n1"}  # verify (b) is no work item
    size, n = left(root)
    assert size == 5 and n == 5  # the plan touched two lines and three notes
    assert sb(root, "inbox", "resolve", "aaaaaa", "--drop")[0] == 0 and left(root) == (5, 4)
    assert sb(root, "archive", "c1", "--why", "faded")[0] == 0 and left(root) == (5, 3)  # no longer a candidate
    assert sb(root, "sleep", "veto", "c2", "still the reference")[0] == 0 and left(root) == (5, 2)  # a veto is use
    assert sb(root, "sleep", "verified", "n1")[0] == 0 and left(root) == (5, 1)
    assert sb(root, "inbox", "resolve", "bbbbbb", "--wait")[0] == 0 and left(root) == (5, 0)  # a wait booked in this sleep is a decision too


def test_a_line_booked_before_this_sleep_still_counts_as_left_until_it_is_booked_in_it(tmp_path):
    root = work_vault(tmp_path)
    write_log(root, "inbox-done.tsv", f"aaaaaa\t{ago(1)}\twait")  # waited yesterday: still open, so the plan lists it again
    sb(root, "sleep", "begin")
    sb(root, "sleep", "plan")
    assert "inbox:aaaaaa" in sleep.remembered(Vault(root), "work")
    assert left(root) == (5, 5)
    assert sb(root, "inbox", "resolve", "aaaaaa", "--wait")[0] == 0 and left(root) == (5, 4)


def test_the_size_is_the_largest_plan_of_the_sleep(tmp_path):
    root = work_vault(tmp_path)
    sb(root, "sleep", "begin")
    sb(root, "sleep", "plan")
    sb(root, "inbox", "resolve", "aaaaaa", "--drop")
    sb(root, "inbox", "resolve", "bbbbbb", "--drop")
    sb(root, "sleep", "plan")  # a second plan lists only the three notes
    assert sleep.remembered(Vault(root), "size") == {"5", "3"}
    assert left(root) == (5, 3)  # the largest plan, and the work of both


def test_leftover_follows_a_rename_during_the_sleep(tmp_path):
    root = work_vault(tmp_path)
    sb(root, "sleep", "begin")
    sb(root, "sleep", "plan")
    size, n = left(root)
    assert sb(root, "rename", "c2", "c2-renamed")[0] == 0
    assert left(root) == (size, n)  # still a candidate under its new name
    assert sb(root, "archive", "c2-renamed", "--why", "faded")[0] == 0
    assert left(root) == (size, n - 1)


def test_a_duplicate_pair_is_part_of_the_plan_size_but_never_left(tmp_path):
    root = init_vault(tmp_path)
    for s in ("implicit-euler-damping", "backward-euler-damping"):
        add_note(root, s, project="p", title="Implicit Euler damps stiff modes strongly", cue="choosing a solver for stiff modes",
                 gist="Gist: Implicit Euler damps stiff modes strongly.")
    sb(root, "sleep", "baseline")
    sb(root, "sleep", "begin")
    out = sb(root, "sleep", "plan")[1]
    assert "backward-euler-damping ~ implicit-euler-damping" in out
    assert sleep.remembered(Vault(root), "work") == set()  # a pair is for the sleeper to judge; nothing to check afterwards
    assert left(root) == (2, 0)


def test_finish_logs_size_and_left_and_reports_what_stayed_undone(tmp_path):
    root = work_vault(tmp_path)
    sb(root, "sleep", "begin")
    sb(root, "sleep", "plan")
    sb(root, "inbox", "resolve", "aaaaaa", "--drop")
    code, out, _ = sb(root, "sleep", "done", "partly")
    assert code == 0 and out.splitlines()[0].endswith("; 4 of 5 planned items left undone")
    assert sleep_rows(root)[-1][8:] == ["5", "4"]
    assert usage.budget(Vault(root)) == 10  # 5 - 4 = 1 were done: the floor
    assert "a sleep takes 10 items" in sb(root, "stats")[1]
    assert "of 10 (notes and inbox lines)" in sb(root, "sleep", "plan")[1]


def test_a_due_line_that_expires_in_the_sleep_counts_as_left(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "inbox.tsv", "aaaaaa\t2026-09-01\tp\t\tstale remark about zeolites", f"bbbbbb\t{ago(1)}\tp\t\tfresh remark about pendulums")
    sb(root, "sleep", "begin")
    sb(root, "sleep", "plan")
    sb(root, "inbox", "resolve", "bbbbbb", "--drop")
    code, out, _ = sb(root, "sleep", "done", "one expired")
    assert code == 0 and "1 expired" in out and out.splitlines()[0].endswith("; 1 of 2 planned items left undone")  # leftover runs before the expiry
    assert sleep_rows(root)[-1][8:] == ["2", "1"]


def test_finish_after_a_sleep_without_a_plan_logs_zeros_and_keeps_the_budget(tmp_path):
    root = work_vault(tmp_path)
    sb(root, "sleep", "begin")
    code, out, _ = sb(root, "sleep", "done", "nothing planned")
    assert code == 0 and "left undone" not in out
    assert sleep_rows(root)[-1][8:] == ["0", "0"] and usage.budget(Vault(root)) == 40


def test_a_plan_worked_off_completely_enlarges_the_next_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "B0", 3)  # the plan holds exactly the three open inbox lines
    monkeypatch.setattr(usage, "B_MIN", 1)
    root = init_vault(tmp_path)
    write_log(root, "inbox.tsv", *[f"{i:06x}\t2026-11-07\tp\t\tremark {w}" for i, w in enumerate(("about zeolites", "on pendulums", "of lichen"))])
    sb(root, "sleep", "begin")
    assert "touched: 3 of 3" in sb(root, "sleep", "plan")[1]
    for i in range(3):
        assert sb(root, "inbox", "resolve", f"{i:06x}", "--drop")[0] == 0
    code, out, _ = sb(root, "sleep", "done", "all")
    assert code == 0 and "left undone" not in out and sleep_rows(root)[-1][8:] == ["3", "0"]
    assert usage.budget(Vault(root)) == 4  # round(3 * 1.25)
    assert "a sleep takes 4 items" in sb(root, "stats")[1]


def test_a_plan_left_half_done_shrinks_the_budget_to_what_was_done(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "B0", 20)
    monkeypatch.setattr(usage, "B_MIN", 1)
    root = work_vault(tmp_path)
    sb(root, "sleep", "begin")
    sb(root, "sleep", "plan")
    for i in ("aaaaaa", "bbbbbb"):
        sb(root, "inbox", "resolve", i, "--drop")
    sb(root, "archive", "c1", "--why", "faded")
    code, out, _ = sb(root, "sleep", "done", "three of five")
    assert code == 0 and "2 of 5 planned items left undone" in out
    assert usage.budget(Vault(root)) == 3
    assert "of 3 (notes and inbox lines)" in sb(root, "sleep", "plan")[1]


# ---- sleep due --------------------------------------------------------------------------------------------------

def due(root, notes=3, inbox=0, work=None, today=TODAY):
    return usage.sleep_due(Vault(root), notes, inbox, today, work)


def test_never_slept_is_due_only_with_notes(tmp_path):
    root = init_vault(tmp_path)
    assert due(root, notes=1) == "sleep due: never slept"
    assert due(root, notes=0) is None  # nothing to consolidate yet
    assert due(root, notes=0, inbox=40) == "sleep due: 40 items waiting"  # a full inbox is work even without notes


def test_a_sleep_is_due_after_more_than_seven_days(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", (ago(7).isoformat() + "T12:00", 0, 0, 0, 0, 0, 0, "s"))
    assert due(root) is None
    write_log(root, "sleep.tsv", (ago(8).isoformat() + "T12:00", 0, 0, 0, 0, 0, 0, "s"))
    assert due(root) == "sleep due: 8 days since the last sleep"
    write_log(root, "sleep.tsv", (ago(8).isoformat(), "old row with a date only"))  # before 0.2.1
    assert due(root) == "sleep due: 8 days since the last sleep"
    assert due(root, work={"inbox": 100}) == "sleep due: 8 days since the last sleep"  # one reason is enough


def test_a_full_plan_of_work_is_due_five_hours_after_the_last_sleep_not_before(tmp_path):
    root = init_vault(tmp_path)
    full = {"inbox": 10, "verify": 30, "forget": 0}
    write_log(root, "sleep.tsv", (ago(0).isoformat() + "T07:01", 0, 0, 0, 0, 0, 0, "s"))  # SB_TODAY is noon: 4 h 59 min ago
    assert due(root, work=full) is None
    write_log(root, "sleep.tsv", (ago(0).isoformat() + "T07:00", 0, 0, 0, 0, 0, 0, "s"))  # exactly five hours
    assert due(root, work=full) == "sleep due: 40 items waiting (inbox 10, verify 30)"  # zero queues stay out of the line
    assert due(root, work={"inbox": 10, "verify": 29}) is None  # one short of the budget
    assert due(root, inbox=40) == "sleep due: 40 items waiting"  # without queues: the inbox alone
    assert due(root, inbox=39) is None


def test_the_threshold_is_the_learned_budget(tmp_path):
    root = init_vault(tmp_path)
    log = [(ago(1).isoformat() + "T12:00", 0, 0, 0, 0, 0, 0, "s", 40, 0)]  # a full plan done: the next one takes 50
    write_log(root, "sleep.tsv", *log)
    assert due(root, work={"inbox": 49}) is None
    assert due(root, work={"inbox": 50}) == "sleep due: 50 items waiting (inbox 50)"
    write_log(root, "sleep.tsv", (ago(1).isoformat() + "T12:00", 0, 0, 0, 0, 0, 0, "s", 40, 8))  # shrunk to 32
    assert due(root, work={"inbox": 32}) == "sleep due: 32 items waiting (inbox 32)" and due(root, work={"inbox": 31}) is None


def test_garbled_sleep_rows_do_not_hide_an_earlier_good_one(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", (ago(20).isoformat() + "T12:00", 0, 0, 0, 0, 0, 0, "s"), "garbage\tx", "", "\t\t")
    assert usage.last_sleep(Vault(root)) == ago(20) and due(root) == "sleep due: 20 days since the last sleep"
    write_log(root, "sleep.tsv", "garbage", "also bad")
    assert usage.last_sleep(Vault(root)) is None and due(root) == "sleep due: never slept"


def test_the_constants_for_the_week_and_the_gap_are_live(tmp_path, monkeypatch):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", (ago(3).isoformat() + "T12:00", 0, 0, 0, 0, 0, 0, "s"))
    assert due(root) is None
    monkeypatch.setattr(usage, "WEEK", 2)
    assert due(root) == "sleep due: 3 days since the last sleep"
    monkeypatch.setattr(usage, "WEEK", 7)
    monkeypatch.setattr(usage, "GAP_HOURS", 80)
    assert due(root, work={"inbox": 40}) is None  # 72 hours are not 80
    monkeypatch.setattr(usage, "GAP_HOURS", 72)
    assert due(root, work={"inbox": 40}) == "sleep due: 40 items waiting (inbox 40)"


# ---- locked pairs -----------------------------------------------------------------------------------------------

def reject(root, *rows):
    write_log(root, "unrelated.tsv", *[(d.isoformat(), a, b, "no link") for d, a, b in rows])


def locked(root, n):
    return sleep.locked(Vault(root), TODAY + timedelta(n))


def test_a_rejected_pair_rests_for_the_vault_stability_and_then_returns(tmp_path):
    root = init_vault(tmp_path)
    reject(root, (TODAY, "a", "b"))
    assert locked(root, 0) == {frozenset("ab")} and locked(root, 90) == {frozenset("ab")}  # t = S = 90: R = 1/2 still rests
    assert locked(root, 91) == set()
    assert locked(root, -5) == {frozenset("ab")}  # a rejection dated after today has not rested at all


def test_a_second_rejection_rests_longer(tmp_path):
    root = init_vault(tmp_path)
    reject(root, (TODAY, "a", "b"), (TODAY + timedelta(91), "b", "a"))  # rejected again right after the first lock ran out
    # S = 90 (1 + 1 - 90/181) = 135.25, counted from the second rejection
    assert locked(root, 91 + 135) == {frozenset("ab")} and locked(root, 91 + 136) == set()
    reject(root, (TODAY, "a", "b"), (TODAY + timedelta(30), "a", "b"))  # rejected again while locked: less is gained
    assert locked(root, 30 + 112) == {frozenset("ab")} and locked(root, 30 + 113) == set()  # S = 90 (1 + 30/120) = 112.5


def test_pairs_are_unordered_and_independent(tmp_path):
    root = init_vault(tmp_path)
    reject(root, (TODAY, "a", "b"), (TODAY, "c", "a"), (TODAY - timedelta(100), "d", "e"))
    assert locked(root, 0) == {frozenset("ab"), frozenset("ca")}  # d, e were rejected 100 days ago: back
    assert frozenset("ba") == frozenset("ab")
    assert locked(root, 91) == set()


def test_the_lock_follows_the_learned_vault_stability(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR_N", 1)
    root = init_vault(tmp_path)
    add_note(root, "n", project="p", extra={"created": ago(1000).isoformat(), "updated": ago(1000).isoformat()})
    write_log(root, "usage.tsv", (ago(700).isoformat(), "work", "show", "n"))  # one gap of 300 days: the vault value is 195
    reject(root, (TODAY, "a", "b"))
    assert usage.learned(Vault(root), TODAY)[""] == pytest.approx(195)
    assert locked(root, 195) == {frozenset("ab")} and locked(root, 196) == set()


def test_short_and_empty_rows_in_unrelated_tsv_do_not_crash(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "unrelated.tsv", f"{TODAY}\ta\tb\tno link", f"{TODAY}\te", "", "\t\t\t", f"{TODAY}\t\t\t")
    assert frozenset("ab") in sleep.locked(Vault(root), TODAY)
    assert sb(root, "sleep", "plan")[0] == 0


def test_a_row_with_an_unreadable_date_in_unrelated_tsv_does_not_crash(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "unrelated.tsv", f"{TODAY}\ta\tb\tno link", "not-a-date\tc\td\twhy")
    assert frozenset("ab") in sleep.locked(Vault(root), TODAY)
    assert sb(root, "sleep", "plan")[0] == 0
