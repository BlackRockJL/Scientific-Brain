"""The rolling condense: besides the notes changed or created since the last sleep, every note is compared once per cycle.

The cycle is fixed at 30 days (`span`, usage.FLOOR, the shortest rest a note can learn; the learned value does not enter);
a note's day in it is sha1(slug) mod span; every calendar day since the last sleep counts (at most `span` of them), so a
pair of old, unchanged near duplicates that no plan listed (budget) returns within `span` days, however irregularly the
sleeps come. A pair judged unrelated (`sb sleep unrelated`) rests like a dream pair; a linked or sharpened pair is no pair.
See docs/design.md, Sleep, Condense.

The oracle below restates the rule from its description (the note's own day lies in the days since the last sleep) and
never calls the code under test. Run: python -m pytest tests/test_workflow_condense.py -q -p no:anyio
"""
import hashlib
import os
import random
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
import sim
from helpers import add_note, init_vault, sb

from scientific_brain import sleep, usage
from scientific_brain.vault import Vault

D0 = date(2026, 3, 1)  # the sleep every scenario starts from
SPAN = 30  # the cycle of the rolling condense
SYL = "ka la ve no mor tis pra del fon gur hes jin ber tha xo ri su pen dro mi".split()
PAIR = re.compile(r"^- (\S+) ~ (\S+) \(coverage")


# ---- helpers ---------------------------------------------------------------------------------------------------

def word(k: int) -> str:
    """The k-th pseudo word: letters only, 'q'..'z' bracketed, so no word is part of another and none is a stop word."""
    return "q" + "".join(SYL[k // 20 ** j % 20] for j in range(5)) + "z"


class Words:
    """Fresh words for every note, so that two notes are near duplicates only when a test makes them so."""

    def __init__(self, start=0):
        self.k = start

    def take(self, n) -> str:
        self.k += n
        return " ".join(word(self.k - i) for i in range(n))


def put(root, slug, title, cue, gist, created=None):
    day = (created or D0 - timedelta(days=10)).isoformat()
    add_note(root, slug, project="p", title=title, cue=cue, gist=gist, extra={"created": day, "updated": day})


def world(tmp_path, pairs=(), fill=12, seed=0, name="v") -> tuple:
    """A vault of `fill` unrelated notes and the near-duplicate `pairs` (slug pairs: same title, cue and gist, no word shared
    with any other note), all created before D0. Returns (root, all slugs)."""
    root, w, slugs = init_vault(tmp_path, name), Words(seed * 100000), []
    for i in range(fill):
        put(root, f"f{i:04d}", w.take(4), w.take(3), w.take(8) + ".")
        slugs.append(f"f{i:04d}")
    for a, b in pairs:
        t, c, g = w.take(4), w.take(3), w.take(8) + "."
        put(root, a, t, c, g)
        put(root, b, t, c, g)
        slugs += [a, b]
    return root, slugs


def home(slug: str) -> int:
    return int(hashlib.sha1(slug.encode()).hexdigest()[:8], 16)


def due(slug, last: date, day: date, span: int) -> bool:
    """The oracle: the slug's own day of the cycle lies in the days since the last sleep (last, day]."""
    return any((last + timedelta(days=k)).toordinal() % span == home(slug) % span for k in range(1, (day - last).days + 1))


def pairs_of(plan: str) -> set:
    return {frozenset(m.groups()) for ln in sim.sections(plan)[2] if (m := PAIR.match(ln))}


def at(mp, day: date):
    mp.setenv("SB_TODAY", day.isoformat())


def fake_sleep(root, day: date):
    """A finished sleep without a plan: the row `sleep done` writes (size 0 leaves the budget alone)."""
    Vault(root).append("sleep.tsv", f"{day.isoformat()}T12:00", 0, 0, 0, 0, 0, 0, "s", 0, 0)


def plan_on(root, mp, day: date) -> str:
    at(mp, day)
    return sim.run(root, "sleep", "plan")


def sleep_on(root, mp, day: date, judge=None) -> str:
    """A full sleep as the sleeper makes it: begin, plan, what `judge(plan)` does, check, done. Returns the plan."""
    at(mp, day)
    sim.run(root, "sleep", "begin")
    plan = sim.run(root, "sleep", "plan")
    if judge:
        judge(plan)
    assert " 0 errors" in sim.run(root, "check")
    sim.run(root, "sleep", "done", "test sleep")
    return plan


@pytest.fixture
def turns(monkeypatch):
    """Counter of the notes the condense loop looked at (each one asks Holders.some_cover once)."""
    calls, orig = Counter(), sleep.Holders.some_cover

    def counting(self, ws, skip, share):
        calls[skip] += 1
        return orig(self, ws, skip, share)
    monkeypatch.setattr(sleep.Holders, "some_cover", counting)
    return calls


# ---- 1. an old pair returns within span days --------------------------------------------------------------------

@pytest.mark.parametrize("gap", [1, 3, 7, 120])
@pytest.mark.parametrize("names", [("zeta-one", "zeta-two"), ("anchor-a", "anchor-b"), ("m1", "m2"), ("old-pair-x", "old-pair-y")])
def test_old_unchanged_pair_is_listed_when_the_day_of_one_note_came_and_at_the_latest_after_span_days(tmp_path, monkeypatch, gap, names):
    root, _ = world(tmp_path, [names])
    fake_sleep(root, D0)
    span = SPAN
    last, first, listed_days = D0, None, []
    while True:
        day = last + timedelta(days=gap)
        listed = frozenset(names) in pairs_of(plan_on(root, monkeypatch, day))
        assert listed == (due(names[0], last, day, span) or due(names[1], last, day, span)), (gap, day)
        if listed:
            listed_days.append(day)
        fake_sleep(root, day)
        last = day
        if day >= D0 + timedelta(days=span):  # every day of one cycle has been in some window by now
            break
    assert listed_days, "the pair was not listed within one cycle"
    assert listed_days[0] <= last


def test_a_pair_where_both_notes_are_due_on_one_day_is_listed_once(tmp_path, monkeypatch):
    names = next((a, b) for a in (f"k{i}" for i in range(200)) for b in ("k-twin",) if home(a) % SPAN == home(b) % SPAN)
    root, _ = world(tmp_path, [names])
    fake_sleep(root, D0)
    day = next(D0 + timedelta(days=k) for k in range(1, SPAN + 1) if due(names[0], D0, D0 + timedelta(days=k), SPAN) and
               not due(names[0], D0, D0 + timedelta(days=k - 1), SPAN))
    plan = plan_on(root, monkeypatch, day)
    assert pairs_of(plan) == {frozenset(names)} and "## 2. Condense: 1 near-duplicate pairs" in plan


# ---- 2. every note once per cycle -------------------------------------------------------------------------------

@pytest.mark.parametrize("gap", [1, 3, 10, 15])
def test_each_note_comes_up_exactly_once_per_cycle_whatever_the_rhythm(tmp_path, monkeypatch, turns, gap):
    root, slugs = world(tmp_path, fill=100)
    fake_sleep(root, D0)
    span = SPAN
    assert span % gap == 0  # the windows tile one cycle exactly
    for cycle in range(2 if gap > 1 else 1):
        turns.clear()
        for k in range(1, span // gap + 1):
            day = D0 + timedelta(days=gap * (cycle * span // gap + k))
            plan_on(root, monkeypatch, day)
            fake_sleep(root, day)
        assert set(turns) == set(slugs) and set(turns.values()) == {1}, (cycle, Counter(turns.values()))


@pytest.mark.parametrize("gap", [30, 31, 120, 400])
def test_a_gap_of_a_cycle_or_more_compares_every_note(tmp_path, monkeypatch, turns, gap):
    root, slugs = world(tmp_path, fill=100)
    fake_sleep(root, D0)
    plan_on(root, monkeypatch, D0 + timedelta(days=gap))
    assert set(turns) == set(slugs) and set(turns.values()) == {1}


def test_a_gap_just_under_a_cycle_leaves_exactly_the_notes_whose_day_was_that_of_the_last_sleep(tmp_path, monkeypatch, turns):
    root, slugs = world(tmp_path, fill=300)
    w = Words(555000)
    skipped = [f"late{i}" for i in range(400) if home(f"late{i}") % SPAN == D0.toordinal() % SPAN][:3]  # their day was D0 itself
    for s in skipped:
        put(root, s, w.take(4), w.take(3), w.take(8) + ".")
    fake_sleep(root, D0)
    day = D0 + timedelta(days=SPAN - 1)
    plan_on(root, monkeypatch, day)
    mine = D0.toordinal() % SPAN
    assert set(turns) == {s for s in slugs if home(s) % SPAN != mine}  # the other days of the cycle all lie in the 29 days
    turns.clear()
    plan_on(root, monkeypatch, day + timedelta(days=1))  # one more day: their turn
    assert set(turns) >= set(skipped) | {s for s in slugs if home(s) % SPAN == mine}


def test_irregular_rhythm_still_gives_every_note_its_turn_within_one_cycle_plus_one_gap(tmp_path, monkeypatch, turns):
    root, slugs = world(tmp_path, fill=80)
    fake_sleep(root, D0)
    rng, last, span, seen, gaps = random.Random(5), D0, SPAN, {s: [] for s in slugs}, []
    while last < D0 + timedelta(days=3 * span):
        gap = rng.choice((1, 1, 2, 3, 5, 7, 14, 30))
        day = last + timedelta(days=gap)
        turns.clear()
        plan_on(root, monkeypatch, day)
        assert set(turns) == {s for s in slugs if due(s, last, day, span)}, (last, day)
        for s in turns:
            seen[s].append(day)
        fake_sleep(root, day)
        gaps.append(gap)
        last = day
    for s, ds in seen.items():  # compared at least twice in three cycles, never more than a cycle and a gap apart
        marks = [D0] + ds
        assert len(ds) >= 2, (s, ds)
        assert all((b - a).days <= span + max(gaps) for a, b in zip(marks, marks[1:])), (s, marks)


def test_a_note_changed_since_the_last_sleep_is_still_compared_on_a_day_that_is_not_its_own(tmp_path, monkeypatch):
    names = ("edit-a", "edit-b")
    root, _ = world(tmp_path, [names])
    fake_sleep(root, D0)
    day = D0 + timedelta(days=1)
    assert not any(due(s, D0, day, SPAN) for s in names), "choose other names: one of them is due on day 1"
    assert pairs_of(plan_on(root, monkeypatch, day)) == set()
    at(monkeypatch, day)
    sim.edit_note(root, f"notes/p/{names[0]}.md", "Remeasured.", day.isoformat())
    assert pairs_of(sim.run(root, "sleep", "plan")) == {frozenset(names)}


# ---- 3. one sleep, one answer; no second turn on the same day ----------------------------------------------------

def test_two_plans_of_one_sleep_list_the_same_pairs_and_a_second_sleep_the_same_day_rolls_nothing(tmp_path, monkeypatch, turns):
    names = ("twin-a", "twin-b")
    root, slugs = world(tmp_path, [names], fill=30)
    sleep_on(root, monkeypatch, D0)  # all notes are new to the first sleep: the pair is listed, nobody judges it
    day = next(D0 + timedelta(days=k) for k in range(1, SPAN + 1) if due(names[0], D0, D0 + timedelta(days=k), SPAN))
    at(monkeypatch, day)
    sim.run(root, "sleep", "begin")
    first, again = sim.run(root, "sleep", "plan"), sim.run(root, "sleep", "plan")
    assert frozenset(names) in pairs_of(first) and pairs_of(first) == pairs_of(again)
    assert sim.sections(first)[2] == sim.sections(again)[2]
    sim.run(root, "sleep", "done", "first sleep of the day")
    turns.clear()
    sim.run(root, "sleep", "begin")
    second = sim.run(root, "sleep", "plan")  # gone = 0: nobody's day came since the sleep that just ended
    assert not turns and pairs_of(second) == set()
    sim.run(root, "sleep", "done", "second sleep of the day")


# ---- 4. pairs that are no pairs, and the rest of a pair judged unrelated --------------------------------------------

REST = 90  # a pair judged unrelated rests as long as the vault's initial stability (usage.PRIOR), as a dream pair does


def test_a_sharpened_pair_never_returns(tmp_path, monkeypatch):
    sharp = ("sharp-a", "sharp-b")
    root, _ = world(tmp_path, [sharp])
    w = Words(777000)

    def judge(plan):
        assert frozenset(sharp) in pairs_of(plan)
        for s in sharp:  # new title, cue and gist: the coverage falls to nothing
            put(root, s, w.take(4), w.take(3), w.take(8) + ".")
        sim.run(root, "sleep", "verified", *sharp)
    sleep_on(root, monkeypatch, D0, judge)
    for k in range(1, 3 * SPAN + 1):
        day = D0 + timedelta(days=k)
        assert pairs_of(plan_on(root, monkeypatch, day)) == set(), day
        fake_sleep(root, day)


@pytest.mark.parametrize("direction", ["a to b", "b to a", "both"])
def test_a_linked_pair_is_never_listed_and_its_unlinked_twin_is(tmp_path, monkeypatch, direction):
    linked, free = ("link-a", "link-b"), ("free-a", "free-b")
    root, _ = world(tmp_path, [linked, free])
    a, b = linked
    if direction != "b to a":
        sim.add_link(root, f"notes/p/{a}.md", b, "two parts of one topic")
    if direction != "a to b":
        sim.add_link(root, f"notes/p/{b}.md", a, "two parts of one topic")
    seen = set()
    first = pairs_of(sleep_on(root, monkeypatch, D0))  # all notes are new to the first sleep
    seen |= first
    for k in range(1, 2 * SPAN + 1):
        day = D0 + timedelta(days=k)
        seen |= pairs_of(plan_on(root, monkeypatch, day))
        fake_sleep(root, day)
    assert frozenset(linked) not in seen and frozenset(free) in first and frozenset(free) in seen, (direction, seen)


def test_an_unrelated_pair_is_gone_from_the_second_plan_of_its_sleep_and_returns_when_its_rest_is_over(tmp_path, monkeypatch):
    judged, other = ("judged-a", "judged-b"), ("other-a", "other-b")
    root, _ = world(tmp_path, [judged, other])
    at(monkeypatch, D0)
    sim.run(root, "sleep", "begin")
    first = sim.run(root, "sleep", "plan")
    assert pairs_of(first) == {frozenset(judged), frozenset(other)}
    sim.run(root, "sleep", "unrelated", *judged, "different quantities despite shared words")
    again = sim.run(root, "sleep", "plan")  # same sleep, same day
    assert pairs_of(again) == {frozenset(other)}, again
    sim.run(root, "sleep", "done", "judged one pair")
    listed, listed_other = [], []
    for k in range(1, REST + 2 * SPAN + 1):  # the rest, then two more cycles
        day = D0 + timedelta(days=k)
        got = pairs_of(plan_on(root, monkeypatch, day))
        listed += [k] * (frozenset(judged) in got)
        listed_other += [k] * (frozenset(other) in got)
        fake_sleep(root, day)
    want = [k for k in range(1, REST + 2 * SPAN + 1) if k > REST and any(due(s, D0 + timedelta(days=k - 1), D0 + timedelta(days=k), SPAN) for s in judged)]
    assert listed == want, (listed, want)
    assert want and want[0] <= REST + SPAN, "it must return within a cycle after its rest"
    wo = [k for k in range(1, REST + 2 * SPAN + 1) if any(due(s, D0 + timedelta(days=k - 1), D0 + timedelta(days=k), SPAN) for s in other)]
    assert listed_other == wo  # the other pair, never judged, comes on every day of its notes


def test_a_pair_judged_unrelated_stays_out_of_every_later_plan_of_the_rest_also_after_edits_elsewhere(tmp_path, monkeypatch):
    """The rest is a property of the pair, not of the plan: a note edited elsewhere in the vault changes nothing."""
    judged = ("rest-a", "rest-b")
    root, _ = world(tmp_path, [judged])
    sleep_on(root, monkeypatch, D0, lambda plan: sim.run(root, "sleep", "unrelated", *judged, "different quantities"))
    for k in range(1, REST + 1):
        day = D0 + timedelta(days=k)
        if k % 10 == 0:
            sim.edit_note(root, "notes/p/f0003.md", f"Remeasured {k}.", day.isoformat())
        assert pairs_of(plan_on(root, monkeypatch, day)) == set(), day
        fake_sleep(root, day)


# ---- 5. a pair the budget cut returns -----------------------------------------------------------------------------

def spread_pairs(n) -> list:
    """n slug pairs whose two days of the cycle are different from each other and from those of the other pairs, so that on
    any day at most one pair is up."""
    out, used = [], set()
    for i in range(10000):
        a, b = f"dup{i:04d}a", f"dup{i:04d}b"
        r = {home(a) % SPAN, home(b) % SPAN}
        if len(r) == 2 and not r & used:
            out.append((a, b))
            used |= r
        if len(out) == n:
            return out
    raise AssertionError("no spread pairs")


def test_a_pair_the_budget_cut_in_the_first_sleep_comes_back_in_its_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "B0", 10)  # the smallest plan
    pairs = spread_pairs(10)
    root, _ = world(tmp_path, pairs, fill=16)
    first = pairs_of(sleep_on(root, monkeypatch, D0))
    want = {frozenset(p) for p in pairs}
    assert first < want and len(want - first) >= 4, f"the budget did not cut: {len(first)} of {len(want)} listed"
    later = set()
    for k in range(1, SPAN + 1):
        day = D0 + timedelta(days=k)
        later |= pairs_of(plan_on(root, monkeypatch, day))
        fake_sleep(root, day)
    assert want - first <= later, f"never listed again: {sorted(sorted(p) for p in want - first - later)}"


# ---- 6. the cycle is fixed: learned does not enter ------------------------------------------------------------------

@pytest.mark.parametrize("learned", [30.0, 47.6, 90.0, 365.0])
def test_span_stays_30_whatever_the_learned_rest_says(tmp_path, monkeypatch, turns, learned):
    monkeypatch.setattr(usage, "learned", lambda v, today: {"": learned})
    root, slugs = world(tmp_path, fill=150)
    fake_sleep(root, D0)
    for gap in (SPAN - 1, SPAN, SPAN + 40):
        turns.clear()
        day = D0 + timedelta(days=gap)
        plan_on(root, monkeypatch, day)
        assert set(turns) == {s for s in slugs if due(s, D0, day, SPAN)}, (learned, gap)
        assert gap < SPAN or set(turns) == set(slugs)  # a whole cycle compares everything
    turns.clear()
    plan_on(root, monkeypatch, D0 + timedelta(days=1))  # one day: about one note in 30
    assert 0 < len(turns) <= 150 * 3 // SPAN + 3, len(turns)


def test_span_stays_30_when_learned_moves_between_two_sleeps(tmp_path, monkeypatch, turns):
    """The learned value is a different one at every sleep (30 to 330): no note may wait longer than a cycle plus a gap."""
    monkeypatch.setattr(usage, "learned", lambda v, today: {"": 30.0 + 50.0 * (today.toordinal() % 7)})
    root, slugs = world(tmp_path, fill=80)
    fake_sleep(root, D0)
    rng, last, seen, gaps = random.Random(9), D0, {s: [D0] for s in slugs}, []
    while last < D0 + timedelta(days=4 * SPAN):
        gap = rng.choice((1, 2, 3, 5, 7, 14))
        day = last + timedelta(days=gap)
        turns.clear()
        plan_on(root, monkeypatch, day)
        assert set(turns) == {s for s in slugs if due(s, last, day, SPAN)}, (last, day)
        for s in turns:
            seen[s].append(day)
        fake_sleep(root, day)
        gaps.append(gap)
        last = day
    for s, ds in seen.items():
        assert len(ds) >= 4 and all((b - a).days <= SPAN + max(gaps) for a, b in zip(ds, ds[1:])), (s, ds)


# ---- 7. simulation ------------------------------------------------------------------------------------------------

SEED = 20261121
SIM_NOTES, SIM_PAIRS = 300, 40


def plant_pairs(root, rng, slugs, n) -> list:
    """n near duplicates of n different notes (title without its last word, same cue and gist), created before the import."""
    v, out = Vault(root), []
    for i, orig in enumerate(rng.sample(slugs, n)):
        rel = next(r for r in v.scan("notes") if r.endswith(f"/{orig}.md"))
        o = v.read(rel)
        slug = f"dup-{i:03d}"
        sim.write_note(root, slug, o.get("project"), "concept", " ".join(o.get("title").split()[:-1]) + f" zd{i}", o.get("cue"), o.gist,
                       (D0 - timedelta(days=rng.randint(1, 60))).isoformat())
        out.append((orig, slug))
    return out


def test_simulation_every_planted_pair_is_listed_or_merged_within_one_cycle(tmp_path, monkeypatch):
    rng = random.Random(SEED)
    vocab = sim.Vocab(rng)
    monkeypatch.setenv("SB_TODAY", D0.isoformat())
    root, slugs, _ = sim.build_vault(tmp_path, rng, vocab, n=SIM_NOTES, pairs=0, d0=D0)
    planted = plant_pairs(root, rng, [s for s in slugs if not s.startswith("sou")], SIM_PAIRS)  # a source note has no duplicate here
    sim.run(root, "sleep", "baseline", "--seed", "600")  # fading starts after 30 days and ends after 600: few notes leave in 100
    span = SPAN
    listed, plans, sizes = {}, [], []
    real_run = sim.run

    def recording(v, *args, ok=(0,)):
        out = real_run(v, *args, ok=ok)
        if args[:2] == ("sleep", "plan"):
            plans.append(out)
        return out
    monkeypatch.setattr(sim, "run", recording)

    nxt, n_sleeps = 2, 0
    for i in range(1, 2 * span + 11):
        at(monkeypatch, D0 + timedelta(days=i))
        sim.day(root, rng, vocab=vocab)
        if i == nxt:
            res = sim.sleeper(root, rng, vocab=vocab, merge_share=0.25)  # checks `sb check` itself before `sleep done`
            assert " 0 errors" in real_run(root, "check")
            assert res["size"] <= res["budget"], res
            for p in pairs_of(plans[-1]):
                listed.setdefault(p, n_sleeps + 1)  # number of the sleep that listed it first
            sizes.append((res["size"], res["budget"]))
            n_sleeps += 1
            nxt = i + rng.choice((2, 3))
    live, arch = set(sim.notes_dir(root)), sim.archived(root)
    still = [p for p in planted if p[0] in live and p[1] in live]
    missing = [p for p in still if frozenset(p) not in listed]
    first = Counter(0 if n == 1 else 1 for p, n in listed.items() if p in {frozenset(q) for q in planted})
    print(f"\nsimulation: {n_sleeps} sleeps over {2 * span + 10} days, {len(planted)} planted pairs, {len(still)} with both notes left, "
          f"{len(listed)} distinct pairs ever listed, first listed at sleep 1: {first[0]}, later: {first[1]}, "
          f"archived (merged or faded) {len(arch & {x for p in planted for x in p})}, missing {len(missing)}")
    assert not missing, missing
    assert first[1] > 0, "every planted pair was listed by the first plan: the rolling part was not exercised"
    assert all(s <= b for s, b in sizes)
    assert not (Path(root) / ".sb" / "sleeping").exists()
