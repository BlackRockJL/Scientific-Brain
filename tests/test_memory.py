"""The memory model of sb 0.3: replay, seeds, learned initial stability, forget candidates."""
import math
import random
from datetime import date, timedelta

import pytest
from helpers import add_note, init_vault, sb, write_log

from scientific_brain import usage
from scientific_brain.vault import Vault

D = date(2026, 1, 1)
TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def day(n):
    return D + timedelta(days=n)


def ago(n):
    return TODAY - timedelta(days=n)


def put(root, slug, made, typ="finding", loc="notes", project="p", **kw):
    """A note created `made` days ago (negative: in the future)."""
    add_note(root, slug, loc=loc, project=project, type=typ, extra={"created": ago(made).isoformat(), "updated": ago(made).isoformat()}, **kw)


def rows(*items):
    """usage.tsv lines from (days_ago, caller, kind, slug)."""
    return [(ago(d).isoformat(), c, k, s) for d, c, k, s in items]


def score(root, slug, today=TODAY):
    return {x[0]: x[3:] for x in usage.scores(Vault(root), today)}[slug]  # (R, S, last)


# ---- replay -----------------------------------------------------------------------------------------------------

def test_replay_without_history_returns_the_start_values():
    assert usage.replay({}, None, 90.0) == (90.0, None)
    assert usage.replay({}, D, 90.0) == (90.0, D)


def test_the_first_element_sets_s_to_s0_and_a_review_on_the_start_day_changes_nothing():
    assert usage.replay({D: 1.0}, D, 90.0) == (90.0, D)  # no growth at the same day
    assert usage.replay({day(5): 1.0}, None, 90.0) == (90.0, day(5))  # without a start the first review opens the history
    assert usage.replay({day(5): 1.0}, D, 90.0)[0] == pytest.approx(90 * (1 + 5 / 95))  # the next new day does grow it


def test_each_review_on_a_new_day_grows_s_by_one_plus_q_times_one_minus_r():
    S, last = usage.replay({day(30): 1.0}, D, 90.0)
    assert S == pytest.approx(112.5) and last == day(30)  # R = 90/120 = 3/4: 90 (1 + 1/4)
    assert usage.replay({day(30): 0.5}, D, 90.0)[0] == pytest.approx(101.25)  # a recall is half a review
    S2, last2 = usage.replay({day(30): 1.0, day(60): 1.0}, D, 90.0)
    assert S2 == pytest.approx(112.5 * (1 + 30 / 142.5)) and last2 == day(60)  # the second review starts from the grown S
    assert usage.replay({day(30): 1.0}, D, 200.0)[0] > 200  # any s0


def test_growth_is_linear_in_the_strength_and_follows_the_gain(monkeypatch):
    g1 = usage.replay({day(30): 1.0}, D, 90.0)[0] - 90
    assert usage.replay({day(30): 0.5}, D, 90.0)[0] - 90 == pytest.approx(g1 / 2)
    monkeypatch.setattr(usage, "GAIN", 2.0)
    assert usage.replay({day(30): 1.0}, D, 90.0)[0] - 90 == pytest.approx(2 * g1)


def test_a_late_review_grows_s_more_than_an_early_one():
    early = usage.replay({day(5): 1.0}, D, 90.0)[0]
    late = usage.replay({day(80): 1.0}, D, 90.0)[0]
    assert early == pytest.approx(90 * (1 + 5 / 95)) and late == pytest.approx(90 * (1 + 80 / 170))
    assert 90 < early < late < 180  # spacing: the closer to forgetting, the more a review adds, but less than doubling at R > 0


def test_growth_per_review_never_exceeds_the_days_since_the_last_one():
    S, last = usage.replay({day(1): 1.0}, D, 90.0)
    assert 90 < S < 91 and S - 90 < 1  # a review a day later adds a little less than a day


def test_a_seed_sets_the_rest_and_restarts_the_clock():
    assert usage.replay({}, D, 90.0, (day(10), 200.0)) == (200.0, day(10))
    assert usage.replay({}, None, 90.0, (D, 50.0)) == (50.0, D)  # a seed alone opens the history with its rest
    S, last = usage.replay({day(50): 1.0}, D, 90.0, (day(10), 200.0))
    assert S == pytest.approx(200 * (1 + 40 / 240)) and last == day(50)  # the review grows from the seed
    assert usage.replay({day(10): 1.0}, D, 90.0, (day(10), 200.0)) == (200.0, day(10))  # same day: the seed wins
    assert usage.replay({day(5): 1.0}, D, 90.0, (day(10), 200.0)) == (200.0, day(10))  # reviews before the seed are overwritten


def test_created_after_earlier_events_restarts_the_clock_without_growth():
    assert usage.replay({D: 1.0}, day(50), 90.0) == (90.0, day(50))  # a review on day 0, created on day 50: S stays, the clock restarts
    S, last = usage.replay({D: 1.0, day(30): 1.0}, day(50), 90.0)
    assert S == pytest.approx(112.5) and last == day(50)  # the review on day 30 grew S; created adds nothing and moves last
    assert usage.replay({day(-10): 1.0}, D, 90.0) == (90.0, D)
    assert usage.replay({day(60): 1.0}, D, 90.0)[0] == pytest.approx(90 * (1 + 60 / 150))  # created first: it opens the history
    S, last = usage.replay({day(-400): 1.0, day(-200): 1.0}, D, 90.0)
    assert S == pytest.approx(90 * (1 + 200 / 290)) and last == D  # one review grew S; created later moves only the clock
    assert usage.replay({day(5): 1.0}, day(5), 90.0) == (90.0, day(5))  # created on the day of the review: nothing to restart


def test_a_note_logged_before_its_created_date_is_not_forgotten_before_it_is_s_days_old(tmp_path):
    root = init_vault(tmp_path)
    put(root, "imported", 50)  # created 50 days ago, its history reaches back 100 days
    write_log(root, "usage.tsv", *rows((100, "work", "show", "imported")))
    R, S, last = score(root, "imported")
    assert S == 90 and last == ago(50) and R == pytest.approx(90 / 140)
    fading = lambda n: {x for x, *_ in usage.forget_candidates(Vault(root), ago(50) + timedelta(n))}  # noqa: E731
    assert fading(90) == set() and fading(91) == {"imported"}  # 90 days after created, as for a note without history


def test_a_seed_sets_the_rest_of_an_unused_note_and_only_raises_s_after_reviews():
    assert usage.replay({}, D, 90.0, (day(10), 200.0)) == (200.0, day(10))
    assert usage.replay({}, D, 90.0, (day(10), 40.0)) == (40.0, day(10))  # an unused note rests what its seed says
    assert usage.replay({day(60): 1.0}, D, 150.0, (day(10), 40.0))[0] == pytest.approx(40 * (2 - 40 / 90))  # grows from the seeded rest
    assert usage.replay({day(5): 1.0}, D, 90.0, (day(10), 40.0))[0] > 90  # after a real review a seed never lowers S
    assert usage.replay({}, None, 90.0, (day(3), 40.0)) == (40.0, day(3))  # a seed alone opens the history with its rest
    assert usage.replay({}, D, 90.0, (day(-5), 200.0)) == (200.0, D)  # a seed before created opens the history; created moves the clock
    assert usage.replay({}, None, 90.0, (D, 1e-30))[0] > 0  # a tiny rest stays positive


def test_events_dated_ahead_of_today_are_ignored(tmp_path):
    root = init_vault(tmp_path)
    for slug in "abc":
        put(root, slug, 100)
    write_log(root, "usage.tsv", *rows((-30, "work", "show", "a"), (-30, "work", "show", "b"), (40, "work", "show", "b"), (0, "work", "show", "c")),
              "2099-01-01\twork\tshow\ta")  # a clock that ran ahead
    R, S, last = score(root, "a")  # the future rows do not exist for a: it rests its prior from creation
    assert last == ago(100) and S == 90 and R == pytest.approx(90 / 190)
    assert [x for x, *_ in usage.forget_candidates(Vault(root), TODAY)] == ["a"]  # a use in 2099 does not make a note unforgettable
    assert score(root, "b")[1:] == (pytest.approx(126.0), ago(40))  # only the use of 40 days ago counts: 90 (1 + 1 - 90/150)
    assert score(root, "c")[2] == TODAY and score(root, "c")[0] == 1.0  # a use dated today counts
    assert usage.fade_day(S, last, TODAY) == TODAY  # finite, and already due
    assert score(root, "a", date(2099, 2, 1))[2] == date(2099, 1, 1)  # on that day the row is real
    assert sb(root, "stats")[0] == 0 and sb(root, "review")[0] == 0 and sb(root, "sleep", "plan")[0] == 0


def test_a_note_created_in_the_future_is_fresh(tmp_path):
    root = init_vault(tmp_path)
    put(root, "later", -20)
    R, S, last = score(root, "later")
    assert R == 1.0 and last == TODAY + timedelta(20) and not usage.forget_candidates(Vault(root), TODAY)


def test_a_thousand_vetoes_on_a_thousand_days_keep_s_finite_and_bounded():
    S, last = usage.replay({day(i): 1.0 for i in range(1, 1001)}, D, 90.0)
    assert math.isfinite(S) and 1000 < S <= 90 + 1000 and last == day(1000)  # each day adds just under one day
    S, _ = usage.replay({day(i): 1.0 for i in range(1, 30001)}, D, 90.0)
    assert math.isfinite(S) and S <= 90 + 30000


def test_a_thousand_vetoes_in_the_log_do_not_overflow(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 1000)
    write_log(root, "usage.tsv", *rows(*[(d, "sleep", "veto", "a") for d in range(1, 1001)]))
    R, S, last = score(root, "a")
    assert math.isfinite(S) and 1000 < S <= 1090 and 0 < R <= 1 and last == ago(1)
    assert not usage.forget_candidates(Vault(root), TODAY)
    assert usage.fade_day(S, last, TODAY) > TODAY + timedelta(900)
    assert sb(root, "stats")[0] == 0 and sb(root, "review")[0] == 0
    write_log(root, "usage.tsv", *rows(*[(5, "sleep", "veto", "a")] * 1000))  # a thousand in one day count once
    assert usage.events(Vault(root))["a"] == {ago(5): 1.0} and math.isfinite(score(root, "a")[1])


def test_replay_invariants_on_random_histories():
    rng = random.Random(7)
    for _ in range(300):
        s0 = rng.uniform(30, 365)
        days = {day(rng.randint(-50, 400)): rng.choice([0.5, 1.0]) for _ in range(rng.randint(0, 12))}
        start = day(rng.randint(0, 100)) if rng.random() < 0.8 else None
        seed = (day(rng.randint(0, 300)), rng.uniform(1, 500)) if rng.random() < 0.3 else None
        S, last = usage.replay(days, start, s0, seed)
        stamps = list(days) + ([seed[0]] if seed else []) + ([start] if start else [])  # created always moves the clock forward
        assert math.isfinite(S) and S > 0
        assert last == (max(stamps) if stamps else None)
        assert S >= min(s0, seed[1] if seed else s0)
        if not seed and days:
            lo = min(days) if not start or start > min(days) else start  # growth only between reviews (created opens, never grows)
            assert s0 <= S <= s0 + (max(days) - lo).days
        if not seed and not days:
            assert S == s0


def test_retrievability_is_one_half_after_s_days():
    assert usage.retrievability(90, 0) == 1.0 and usage.retrievability(90, 90) == pytest.approx(0.5)
    assert usage.retrievability(90, -5) == 1.0  # a use ahead of today is not a negative rest
    assert usage.retrievability(90, 180) == pytest.approx(1 / 3)
    assert usage.retrievability(10, 50) < usage.retrievability(100, 50)


def test_fade_day_is_the_first_day_past_s():
    assert usage.fade_day(90.0, D, day(10)) == day(91)  # t = 91 > 90
    assert usage.fade_day(90.9, D, day(10)) == day(91)  # t = 90 is below 90.9, t = 91 is past it
    assert usage.fade_day(91.0, D, day(10)) == day(92)
    assert usage.fade_day(30.0, D, day(500)) == day(500)  # already faded: today


def test_active_sorts_by_retrievability_then_stability_then_slug():
    sc = [("c", "finding", D, 0.5, 100, D), ("a", "finding", D, 0.9, 50, D), ("b", "finding", D, 0.9, 80, D), ("d", "finding", D, 0.9, 80, D)]
    assert [x[0] for x in sorted(sc, key=usage.active)] == ["b", "d", "a", "c"]


# ---- seeds ------------------------------------------------------------------------------------------------------

def test_seed_rest_inverts_the_weight_that_baseline_writes():
    assert usage.seed_rest(0.5, "finding") == pytest.approx(0.25 * 91 - 1)
    assert usage.seed_rest(0.5, "decision") == pytest.approx(0.25 * 181 - 1) and usage.seed_rest(0.5, "derivation") == pytest.approx(0.25 * 181 - 1)
    assert usage.seed_rest(0.0, "finding") == 0.0 and usage.seed_rest(0.01, "finding") == 0.0  # never negative
    for t in (30, 100, 365):
        for typ, h in (("finding", 90), ("decision", 180)):
            assert usage.seed_rest(((t + 1) / (h + 1)) ** 0.5, typ) == pytest.approx(t)


def test_seeds_take_the_latest_row_per_note_and_skip_broken_ones(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "seeds.tsv", ("2026-11-01", "a", "0.5"), ("2026-11-05", "a", "0.7"), ("not-a-date", "a", "0.9"),
              ("2026-11-03", "a", "x"), "2026-11-02\tb", ("2026-11-04", "c", "0.2"), ("2026-11-04", "c", "0.3"), "", "\t\t")
    assert usage.seeds(Vault(root)) == {"a": (date(2026, 11, 5), 0.7), "c": (date(2026, 11, 4), 0.3)}  # the later row of a day wins


@pytest.mark.parametrize("weight", ["nan", "inf", "-inf", "0", "-1", "-0.5", "1e-3x"])
def test_seeds_skip_weights_that_are_not_finite_and_positive(tmp_path, weight):
    root = init_vault(tmp_path)
    put(root, "a", 10)
    write_log(root, "seeds.tsv", (ago(5).isoformat(), "a", weight))
    assert usage.seeds(Vault(root)) == {}
    assert score(root, "a")[1] == 90 and sb(root, "stats")[0] == 0 and sb(root, "review")[0] == 0 and sb(root, "sleep", "plan")[0] == 0
    write_log(root, "seeds.tsv", (ago(5).isoformat(), "a", weight), (ago(4).isoformat(), "a", "1.5"))
    assert usage.seeds(Vault(root)) == {"a": (ago(4), 1.5)}  # a good row after a bad one still counts


def test_a_seed_gives_the_note_its_rest_from_the_seed_day(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 300)
    put(root, "d", 300, typ="decision")
    write_log(root, "seeds.tsv", (ago(10).isoformat(), "a", 1.5), (ago(10).isoformat(), "d", 1.5))
    R, S, last = score(root, "a")
    assert S == pytest.approx(1.5 ** 2 * 91 - 1) and last == ago(10) and R == pytest.approx(S / (S + 10))  # 203.75 days on a finding
    assert score(root, "d")[1] == pytest.approx(1.5 ** 2 * 181 - 1)  # the same weight stands for a longer rest on a decision (old horizon 180)
    fading = lambda n: {s for s, *_ in usage.forget_candidates(Vault(root), ago(10) + timedelta(n))}  # noqa: E731
    assert fading(203) == set() and fading(204) == {"a"} and fading(406) == {"a"} and fading(407) == {"a", "d"}


def test_a_seed_below_the_initial_stability_sets_the_rest(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 300)
    write_log(root, "seeds.tsv", (ago(10).isoformat(), "a", 0.5))  # a rest of 21.75 days is less than the 90 the note has
    R, S, last = score(root, "a")
    assert S == pytest.approx(21.75) and last == ago(10)


# ---- use days, gaps and learned ---------------------------------------------------------------------------------

def test_use_days_are_real_work_use_only(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 100)
    write_log(root, "usage.tsv", *rows((70, "work", "show", "a"), (70, "work", "edit", "a"), (60, "work", "recall", "a"), (50, "work", "create", "a"),
                                       (40, "sleep", "veto", "a"), (30, "sync", "edit", "a"), (20, "sleep", "show", "a"), (10, "work", "veto", "a"),
                                       (9, "work", "archive", "a"), (8, "work", "begin", "a")), "short", "x\twork\tshow\ta")
    assert usage.use_days(Vault(root)) == {"a": {ago(70), ago(60), ago(50)}}  # no veto, no sync edit, no sleep call, no bad date


def test_use_days_follow_renames(tmp_path):
    root = init_vault(tmp_path)
    put(root, "b", 100)
    write_log(root, "usage.tsv", *rows((70, "work", "show", "a"), (10, "work", "show", "b")))
    write_log(root, "renames.tsv", "a\tb\t2026-11-06")
    assert usage.use_days(Vault(root)) == {"b": {ago(70), ago(10)}}


def old_note(root, slug, uses, made=1000, **kw):
    """A note created `made` days ago and used `uses` days ago (a list)."""
    put(root, slug, made, **kw)
    return [(u, "work", "show", slug) for u in uses]


def flat(g):
    return [x for t in sorted(g) for x in t]


def test_gaps_start_at_created_only_without_earlier_use_each_note_weighs_one_and_a_gap_is_capped(tmp_path):
    root = init_vault(tmp_path)
    r = old_note(root, "a", [900, 900, 500])  # created 1000 days ago: days 1000, 900, 500: gaps 100 and 400
    r += old_note(root, "early", [1100, 500])  # used before it was created: created is skipped, one gap of 600
    write_log(root, "usage.tsv", *rows(*r, (400, "sleep", "show", "a"), (300, "sleep", "veto", "a")))  # neither counts
    g = usage.gaps(Vault(root), TODAY)["finding"]
    assert sorted(g) == [(100, 0.5), (365, 0.5), (365, 1.0)]  # 400 and 600 are cut to 365; a's two gaps share the weight 1


def test_only_gaps_that_started_more_than_a_year_ago_count_and_phase_in_over_the_second_year(tmp_path):
    root = init_vault(tmp_path)
    r = old_note(root, "a", [900, 100, 50])  # days 1000, 900, 100, 50: the gaps starting at 1000 and 900 count, the one at 100 does not
    r += old_note(root, "edge", [300], made=365)  # a gap starting exactly 365 days ago does not count yet ...
    r += old_note(root, "first", [300], made=366)  # ... one day more: it counts with the weight 1/365
    r += old_note(root, "half", [300], made=365 + 182)  # 182 days into the second year: weight 182/365
    r += old_note(root, "late", [300], made=364)
    write_log(root, "usage.tsv", *rows(*r))
    g = usage.gaps(Vault(root), TODAY)["finding"]
    assert flat(g) == pytest.approx(flat([(100, 0.5), (365, 0.5), (66, 1 / 365), (247, 182 / 365)]))
    assert flat(usage.gaps(Vault(root), TODAY - timedelta(30))["finding"]) == pytest.approx(flat([(100, 0.5), (365, 0.5), (247, 152 / 365)]))  # half has had less time to phase in then


def test_a_gap_must_have_ended_by_today(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "usage.tsv", *rows(*old_note(root, "a", [-20], made=1000)))  # the only use lies 20 days ahead
    assert usage.gaps(Vault(root), TODAY) == {"finding": []}  # the gap from created to that use has not ended
    write_log(root, "usage.tsv", *rows(*old_note(root, "a", [-20, 100], made=1000)))
    assert usage.gaps(Vault(root), TODAY)["finding"] == [(365, 1.0)]  # 1000 -> 100 counts (cut to 365); the gap into the future is dropped


def test_gaps_cover_archived_notes_and_skip_broken_ones(tmp_path):
    root = init_vault(tmp_path)
    r = old_note(root, "kept", [700])
    r += old_note(root, "gone", [700], loc="archive")
    add_note(root, "nodate", project="p", drop=("created",))
    add_note(root, "baddate", project="p", extra={"created": "yesterday"})
    r += [(900, "work", "show", "nodate"), (500, "work", "show", "nodate"), (900, "work", "show", "baddate")]
    write_log(root, "usage.tsv", *rows(*r))
    g = usage.gaps(Vault(root), TODAY)
    assert sorted(g["finding"]) == [(300, 1.0), (300, 1.0), (365, 1.0)]  # nodate has days 900 and 500 and no created (a gap of 400 cut to 365)


def test_gaps_for_a_young_vault_are_empty_lists(tmp_path):
    root = init_vault(tmp_path)
    r = old_note(root, "a", [200, 100], made=300)
    write_log(root, "usage.tsv", *rows(*r))
    assert usage.gaps(Vault(root), TODAY) == {"finding": []}


def test_learned_without_data_is_the_prior(tmp_path):
    assert usage.learned(Vault(init_vault(tmp_path)), TODAY) == {"": 90.0}
    root = init_vault(tmp_path, "w")
    put(root, "a", 10)
    put(root, "b", 10, typ="decision")
    assert usage.learned(Vault(root), TODAY) == {"": 90.0, "finding": 90.0, "decision": 90.0}  # a type without gaps takes the vault value
    root = init_vault(tmp_path, "y")  # a log shorter than a year teaches nothing
    r = old_note(root, "x", [200, 100], made=300)
    write_log(root, "usage.tsv", *rows(*r))
    assert usage.learned(Vault(root), TODAY)[""] == 90.0


def test_learned_is_the_weighted_ninetieth_percentile_without_the_prior(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR_N", 0)
    root = init_vault(tmp_path)
    r = []
    for j in range(10):  # ten notes with one gap each: 40, 50, ..., 130 days
        r += old_note(root, f"n{j}", [1000 - 40 - 10 * j])
    write_log(root, "usage.tsv", *rows(*r))
    assert usage.learned(Vault(root), TODAY)[""] == 120  # the weight reaches 90 % of 10 with the ninth note, not the tenth
    r += old_note(root, "n10", [1000 - 500])  # an eleventh note with a gap of 500 days: 9.9 of 11 is reached by the tenth
    write_log(root, "usage.tsv", *rows(*r))
    assert usage.learned(Vault(root), TODAY)[""] == 130


def test_a_note_with_many_gaps_does_not_outvote_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR_N", 0)
    root = init_vault(tmp_path)
    r = old_note(root, "busy", [1000 - 30 * k for k in range(1, 11)])  # ten gaps of 30 days: weight 0.1 each
    r += old_note(root, "calm", [700])  # one gap of 300 days: weight 1
    write_log(root, "usage.tsv", *rows(*r))
    assert sorted(g for g, _ in usage.gaps(Vault(root), TODAY)["finding"]) == [30] * 10 + [300]
    assert usage.learned(Vault(root), TODAY)[""] == 300  # unweighted, the 90th percentile of eleven gaps would be 30


def test_learned_shrinks_towards_the_prior_with_the_weight_of_twenty_notes(tmp_path):
    root = init_vault(tmp_path)
    r = []
    for i in range(20):
        r += old_note(root, f"n{i}", [700], made=900)  # twenty notes, one gap of 200 days each
    write_log(root, "usage.tsv", *rows(*r))
    lr = usage.learned(Vault(root), TODAY)
    assert lr[""] == pytest.approx((20 * 200 + 20 * 90) / 40) == pytest.approx(145)
    assert lr["finding"] == pytest.approx((20 * 200 + 20 * 145) / 40)  # the type is pulled towards the vault value in the same way
    write_log(root, "usage.tsv", *rows(*r[:1]))  # one note barely moves the prior
    assert usage.learned(Vault(root), TODAY)[""] == pytest.approx((200 + 20 * 90) / 21)


def test_learned_is_bounded_by_floor_and_ceil(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR_N", 0)
    root = init_vault(tmp_path)
    write_log(root, "usage.tsv", *rows(*old_note(root, "a", [995])))  # one gap of 5 days
    assert usage.learned(Vault(root), TODAY) == {"": usage.FLOOR, "finding": usage.FLOOR} == {"": 30.0, "finding": 30.0}
    write_log(root, "usage.tsv", *rows(*old_note(root, "b", [10], made=5000)))  # one gap of almost 5000 days
    assert usage.learned(Vault(root), TODAY)[""] == usage.CEIL == 365.0
    monkeypatch.setattr(usage, "FLOOR", 50.0)
    write_log(root, "usage.tsv", *rows(*old_note(root, "b", [4999], made=5000)))
    assert usage.learned(Vault(root), TODAY)[""] == 50.0  # the bounds are constants too


def test_a_type_is_pulled_to_the_vault_value_not_to_the_prior(tmp_path):
    root = init_vault(tmp_path)
    r = []
    for i in range(25):
        r += old_note(root, f"d{i}", [700], typ="decision")  # gap of 300
        r += old_note(root, f"f{i}", [970])  # gap of 30
    write_log(root, "usage.tsv", *rows(*r))
    lr = usage.learned(Vault(root), TODAY)
    assert lr[""] == pytest.approx((50 * 300 + 20 * 90) / 70) == pytest.approx(240)
    assert lr["decision"] == pytest.approx((25 * 300 + 20 * 240) / 45)
    assert lr["finding"] == pytest.approx((25 * 30 + 20 * 240) / 45)
    assert lr["finding"] < lr[""] < lr["decision"] and lr["finding"] > 100  # a short gap alone would give 30


def test_a_veto_a_sync_edit_and_a_seed_teach_nothing_but_an_archived_note_does(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR_N", 1)
    root = init_vault(tmp_path)
    put(root, "a", 1000)
    write_log(root, "usage.tsv", *rows((700, "sleep", "veto", "a"), (700, "work", "veto", "a"), (700, "sync", "edit", "a")))
    write_log(root, "seeds.tsv", (ago(700).isoformat(), "a", 1.5))
    assert usage.learned(Vault(root), TODAY)[""] == 90.0  # kept or touched is not needed
    root2 = init_vault(tmp_path, "w")
    put(root2, "old", 1000, loc="archive")
    write_log(root2, "usage.tsv", *rows((700, "work", "show", "old")))
    assert usage.learned(Vault(root2), TODAY)[""] == pytest.approx((300 + 90) / 2)  # a forgotten note was still a case of how long notes rest


def test_the_learning_window_moves_with_today(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "usage.tsv", *rows(*old_note(root, "a", [200], made=400)))  # created 400 days ago, used 200 days ago
    w = 35 / 365  # the gap started 400 days ago: 35 days into the second year
    assert usage.learned(Vault(root), TODAY)[""] == pytest.approx((w * 200 + 20 * 90) / (w + 20))
    assert usage.learned(Vault(root), TODAY - timedelta(40))[""] == 90.0  # at the time the gap's start was not yet a year old


# ---- scores and forget candidates -------------------------------------------------------------------------------

def test_scores_skip_notes_without_a_usable_creation_date_and_stay_quiet_on_an_empty_vault(tmp_path):
    assert usage.scores(Vault(init_vault(tmp_path)), TODAY) == [] and usage.forget_candidates(Vault(init_vault(tmp_path, "w")), TODAY) == []
    root = init_vault(tmp_path, "n")
    add_note(root, "nodate", project="p", drop=("created",))
    add_note(root, "baddate", project="p", extra={"created": "yesterday"})
    put(root, "fine", 3)
    assert [x[0] for x in usage.scores(Vault(root), TODAY)] == ["fine"]


def test_a_vault_without_any_use_rests_each_note_for_the_prior_from_its_creation(tmp_path):
    root = init_vault(tmp_path)
    put(root, "fresh", 0)
    put(root, "mid", 45)
    put(root, "old", 200)
    sc = {x[0]: x for x in usage.scores(Vault(root), TODAY)}
    assert sc["fresh"][3] == 1.0 and sc["mid"][3] == pytest.approx(90 / 135) and sc["old"][3] == pytest.approx(90 / 290)
    assert all(x[4] == 90 and x[5] == x[2] for x in sc.values())  # S is the prior, the last review is the creation
    assert [c[0] for c in usage.forget_candidates(Vault(root), TODAY)] == ["old"]


def test_forget_candidates_come_lowest_retrievability_first_then_by_slug(tmp_path):
    root = init_vault(tmp_path)
    for slug, made in (("b", 200), ("a", 200), ("c", 500), ("d", 120)):
        put(root, slug, made)
    got = usage.forget_candidates(Vault(root), TODAY)
    assert [x[0] for x in got] == ["c", "a", "b", "d"]
    slug, typ, R, S = got[0]
    assert typ == "finding" and S == 90 and R == pytest.approx(90 / 590)


def link(root, slug, made, *targets, **kw):
    put(root, slug, made, body="\nSee " + " ".join(f"[[{t}]]" for t in targets) + ".\n" if targets else "", **kw)


def test_a_note_linked_from_an_active_note_is_not_a_candidate_but_the_link_protects_one_hop_only(tmp_path):
    root = init_vault(tmp_path)
    link(root, "hub", 5, "src")
    link(root, "src", 400, "deep")
    put(root, "deep", 400)
    put(root, "lone", 400)
    assert {s for s, *_ in usage.forget_candidates(Vault(root), TODAY)} == {"deep", "lone"}  # src is used through hub, deep only through src


def test_links_from_faded_notes_protect_nothing_and_chains_and_cycles_fade(tmp_path):
    root = init_vault(tmp_path)
    link(root, "a", 400, "b")
    link(root, "b", 400, "c")
    put(root, "c", 400)
    link(root, "x", 400, "y")
    link(root, "y", 400, "x")
    assert {s for s, *_ in usage.forget_candidates(Vault(root), TODAY)} == {"a", "b", "c", "x", "y"}


def test_the_protection_ends_when_the_linking_note_fades(tmp_path):
    root = init_vault(tmp_path)
    link(root, "hub", 5, "src")
    put(root, "src", 400)
    assert usage.forget_candidates(Vault(root), TODAY) == []
    later = [s for s, *_ in usage.forget_candidates(Vault(root), TODAY + timedelta(86))]  # hub: created 91 days ago, S = 90
    assert sorted(later) == ["hub", "src"]
    assert [s for s, *_ in usage.forget_candidates(Vault(root), TODAY + timedelta(85))] == []  # hub is at t = 90: not faded yet


def test_links_from_archived_notes_and_to_missing_notes_protect_nothing_and_break_nothing(tmp_path):
    root = init_vault(tmp_path)
    link(root, "gone", 5, "lone", loc="archive")
    link(root, "hub", 5, "nowhere", "lone2")
    put(root, "lone", 400)
    put(root, "lone2", 400)
    assert {s for s, *_ in usage.forget_candidates(Vault(root), TODAY)} == {"lone"}


def test_a_recently_created_note_protects_what_it_links_to(tmp_path):
    root = init_vault(tmp_path)
    link(root, "summary", 0, "detail")
    put(root, "detail", 400)
    assert usage.forget_candidates(Vault(root), TODAY) == []


def test_forget_candidates_accept_a_scores_list_and_agree_with_it(tmp_path):
    root = init_vault(tmp_path)
    put(root, "old", 300)
    put(root, "new", 1)
    v = Vault(root)
    sc = usage.scores(v, TODAY)
    assert usage.forget_candidates(v, TODAY, sc) == usage.forget_candidates(v, TODAY)
    assert usage.forget_candidates(v, TODAY, []) == []  # an empty list is not 'compute it'


def test_archive_and_restore_leave_the_stability_of_a_note_as_it_was(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 100)
    put(root, "b", 100)
    write_log(root, "usage.tsv", *rows((60, "work", "show", "a"), (20, "work", "recall", "a")))
    before = score(root, "a")
    assert sb(root, "archive", "a", "--why", "faded")[0] == 0
    assert "a" not in {x[0] for x in usage.scores(Vault(root), TODAY)}  # scores cover notes/ only
    assert sb(root, "restore", "a")[0] == 0
    assert score(root, "a") == before


# ---- a stray old line, smooth learning, bounded seeds -----------------------------------------------------------

def test_one_stray_line_from_1999_moves_the_vault_value_only_a_little(tmp_path):
    root = init_vault(tmp_path)
    add_note(root, "stray", project="p", extra={"created": "1999-01-01", "updated": "1999-01-01"})
    write_log(root, "usage.tsv", f"{ago(100)}\twork\tshow\tstray")  # one gap of 9900 days, cut to 365, weight 1
    lr = usage.learned(Vault(root), TODAY)
    assert lr[""] == pytest.approx((1 * 365 + 20 * 90) / 21)  # 103.1, not the 365 the raw gap would give
    assert lr["finding"] == pytest.approx((365 + 20 * lr[""]) / 21)  # 115.6
    assert 100 < lr[""] < 105
    put(root, "other", 200)  # a vault of one year of ordinary notes: the stray gap stays one note among them
    assert usage.learned(Vault(root), TODAY)[""] == lr[""]  # a note that never came back adds no gap


def test_learned_moves_in_small_steps_around_the_one_year_point(tmp_path):
    root = init_vault(tmp_path)
    start = date(2025, 1, 1)
    for i in range(40):  # many notes begin on the same day and come back once, 200 days later
        add_note(root, f"n{i}", project="p", extra={"created": start.isoformat(), "updated": start.isoformat()})
    write_log(root, "usage.tsv", *[(str(start + timedelta(200)), "work", "show", f"n{i}") for i in range(40)])
    vv = Vault(root)
    series = [usage.learned(vv, start + timedelta(300 + k))[""] for k in range(461)]  # day 300 to day 760 of the log
    steps = [b - a for a, b in zip(series, series[1:])]
    assert series[0] == 90.0 and series[65] == 90.0 and series[66] > 90  # nothing before day 365 of the log, then it starts to rise
    assert min(steps) >= 0 and max(steps) < 1.0  # a rise of at most about 0.6 days per day, not a jump of 70
    assert series[-1] == pytest.approx((40 * 200 + 20 * 90) / 60)  # the full weight after the second year


def test_seed_rest_is_bounded():
    assert usage.seed_rest(1e200, "finding") == 1e6 == usage.seed_rest(float("inf"), "decision")
    assert usage.seed_rest(1e3, "finding") == 1e6  # 1e6 * 91 - 1 is cut to 1e6
    assert usage.seed_rest(1.5, "finding") == pytest.approx(203.75)  # ordinary weights are untouched
    assert usage.seed_rest(0.0, "finding") == 0.0 and usage.seed_rest(0.05, "finding") == 0.0  # never negative


def test_a_seed_dated_ahead_of_today_is_ignored_like_an_event(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 300)
    write_log(root, "seeds.tsv", "2099-01-01\ta\t1.5")
    assert score(root, "a")[2] <= TODAY
    assert [x for x, *_ in usage.forget_candidates(Vault(root), TODAY)] == ["a"]
