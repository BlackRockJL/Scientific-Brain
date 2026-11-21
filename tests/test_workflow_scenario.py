"""Scenarios in full size: a vault of 2500 notes over simulated days with regular sleeps (mechanical sleeper, see sim.py).

Run: python -m pytest tests/test_workflow_scenario.py -q -p no:anyio
SB_SCENARIO_DAYS / SB_SCENARIO_DAYS_STABLE set the simulated days (defaults keep each scenario near five minutes);
SB_SCENARIO_LOG names a file that gets one line per sleep.
"""
import os
import random
import re
import time
from datetime import date, timedelta
from pathlib import Path

from helpers import sb
from sim import Vocab, archived, build_vault, day, notes_dir, probe, run, sleeper

from scientific_brain import usage
from scientific_brain.vault import Vault

SEED = 20261121
D0 = date(2026, 12, 1)  # simulated day 0; day i is D0 + i days
NOTES, WAVE = 2500, 900
WAVE_LEAD = 81  # the wave was created and used 81 days before day 0, so it reaches R = 1/2 on day 10 (S = 90, t > 90)
DAYS = int(os.environ.get("SB_SCENARIO_DAYS", 48))
DAYS_STABLE = int(os.environ.get("SB_SCENARIO_DAYS_STABLE", 92))

STATS = re.compile(r"waiting for a sleep: (\d+) \(inbox (\d+), verify (\d+), forget (\d+)\)")
REVIEW = re.compile(r"- verify: (\d+) wait for a sleep")


def stats(root) -> dict:
    out = run(root, "stats")
    n = re.search(r"notes: (\d+), archive: (\d+)", out)
    w = STATS.search(out)
    return dict(notes=int(n[1]), archive=int(n[2]), inbox=int(w[2]), verify=int(w[3]), forget_wait=int(w[4]),
                forget=int(re.search(r"forget candidates: (\d+)", out)[1]), budget=int(re.search(r"a sleep takes (\d+) items", out)[1]))


def review_verify(root) -> int:
    return int(REVIEW.search(run(root, "review"))[1])


class Sim:
    """State of one simulation: vault, registry of everything ever created, and one record per sleep."""

    def __init__(self, tmp_path, monkeypatch, *, wave, seed_days, regular=0, idle=0):
        self.rng = random.Random(SEED)
        self.mp, self.vocab, self.wave, self.rows = monkeypatch, Vocab(self.rng), wave, []
        self.t = dict(build=0.0, day=0.0, sleep=0.0, check=0.0)
        self.mp.setenv("SB_TODAY", D0.isoformat())
        t0 = time.perf_counter()
        self.dup_pairs: list = []
        self.root, slugs, self.wave_slugs = build_vault(tmp_path, self.rng, self.vocab, n=NOTES, wave=wave, pairs_out=self.dup_pairs,
                                                         wave_day=D0 - timedelta(days=WAVE_LEAD), d0=D0)
        self.created = set(slugs)
        run(self.root, "sleep", "baseline", "--seed", str(seed_days))
        self.vault = Vault(self.root)
        pool = sorted(set(usage.seeds(self.vault)) - set(usage.events(self.vault, ignore_sync=True)))  # seeded, never used
        picks = self.rng.sample(pool, regular + idle)
        self.regular, self.idle = picks[:regular], picks[regular:]
        self.t["build"] = time.perf_counter() - t0
        self.S0 = {x[0]: x[4] for x in usage.scores(self.vault, D0)}
        self.ever_candidate: set = set()
        self.usage_prefix = b""
        self.sleep_days: list = []
        self.keep = frozenset()
        self.vetoed: set = set()
        self.changes = 0  # notes created or edited so far: the most that can have entered the verify backlog

    def set_day(self, i) -> date:
        d = D0 + timedelta(days=i)
        self.mp.setenv("SB_TODAY", d.isoformat())
        return d

    def sleep(self, i):
        """One sleep on day i, then every invariant."""
        d, root, v, t0 = self.set_day(i), self.root, self.vault, time.perf_counter()
        sc = usage.scores(v, d)
        cands = usage.forget_candidates(v, d, sc)
        before = dict(cands=len(cands), budget=usage.budget(v))
        self.ever_candidate |= {c[0] for c in cands}
        res = sleeper(root, self.rng, vocab=self.vocab, keep=self.keep)
        res.update(self.new_candidates({c[0] for c in cands}, res, d))
        self.created |= set(res["created"])
        self.vetoed |= set(res["vetoed_slugs"])
        self.changes += len(res["created"]) + res["extended"] + res["merged"] + 1  # +1: the probe note
        t1 = time.perf_counter()
        inv = self.invariants(res, before, cands)
        after = self.after
        self.sleep_days.append(i)
        self.rows.append(dict(day=i, **{f"c_{k}": x for k, x in before.items()}, c_after=after["forget"], verify_after=after["verify"], changes=self.changes,
                              inbox_after=after["inbox"], notes=after["notes"], archive=after["archive"], budget_next=after["budget"],
                              **{k: x for k, x in res.items() if not isinstance(x, list)}, **inv))
        self.t["sleep"] += t1 - t0
        self.t["check"] += time.perf_counter() - t1

    def new_candidates(self, was: set, res, d) -> dict:
        """Candidates that the sleep itself made: c_new notes that were no candidate at its start and are one at its end, of
        which c_new_shielded are targets of a note the sleep archived that was no candidate then (a merge archives a note
        whose links kept its targets from fading; the link is gone with it)."""
        v = self.vault
        now = {c[0] for c in usage.forget_candidates(v, d)}
        shield = set()
        for s in res["archived"]:
            if s not in was and (rel := v.find(s, ("archive",))):
                shield |= set(v.read(rel).targets)
        new = now - was
        return dict(c_new=len(new), c_new_shielded=len(new & shield), c_new_open=" ".join(sorted(new - shield)[:5]))

    def invariants(self, res, before, cands) -> dict:
        root, v = self.root, self.vault
        assert not (root / ".sb" / "sleeping").exists(), "a sleep marker is left after done"
        assert " 0 errors" in run(root, "check")
        # nothing lost, nothing doubled: notes + archive = everything ever created
        probe_slug = probe(root, self.rng, self.vocab)  # a note just created is found by recall (asserted inside)
        self.created.add(probe_slug)
        after = stats(root)  # after the probe: counts of notes and archive match what is on disk
        live, arch = set(notes_dir(root)), archived(root)
        assert not live & arch, f"in notes/ and archive/ at once: {sorted(live & arch)[:5]}"
        assert live | arch == self.created and len(live) + len(arch) == len(self.created), (
            len(live), len(arch), len(self.created), sorted(self.created - live - arch)[:5], sorted(live | arch - self.created)[:5])
        # the usage log only grows
        raw = (root / ".sb" / "usage.tsv").read_bytes()
        assert raw.startswith(self.usage_prefix), "usage.tsv changed in its old part"
        self.usage_prefix = raw
        # review and stats report the same verify backlog
        assert review_verify(root) == after["verify"], (review_verify(root), after["verify"])
        # no inbox line expired undecided and none is left due
        assert "(no expired inbox lines)" in run(root, "inbox", "--expired"), run(root, "inbox", "--expired")
        assert "(no open inbox lines)" in run(root, "inbox", "--due"), run(root, "inbox", "--due")
        # the plan stays within the learned budget, and wastes none of it while candidates wait
        assert res["budget"] == before["budget"] and res["size"] <= res["budget"], (res["size"], res["budget"])
        sleeps = [r.split("\t") for r in (root / ".sb" / "sleep.tsv").read_text().splitlines()]
        assert int(sleeps[-1][8]) == res["size"] - res["parked"] and int(sleeps[-1][10]) == res["parked"], (sleeps[-1], res["size"], res["parked"])
        assert set(res["planned_forget"]) <= {c[0] for c in cands}
        if res["forget_listed"] < before["cands"]:
            assert res["size"] == res["budget"], (res["forget_listed"], before["cands"], res["size"], res["budget"])
        self.after = after
        return dict(live_probe=probe_slug)

    def work(self, i, **kw) -> dict:
        t0 = time.perf_counter()
        self.set_day(i)
        out = day(self.root, self.rng, vocab=self.vocab, **kw)
        self.created |= set(out["created"])
        self.changes += len(out["created"]) + len(out["edited"])
        self.t["day"] += time.perf_counter() - t0
        return out

    def log(self, name):
        if path := os.environ.get("SB_SCENARIO_LOG"):
            keys = list(self.rows[0])
            Path(path.replace("{}", name)).write_text(
                "\t".join(keys) + "\n" + "".join("\t".join(str(r[k]) for k in keys) + "\n" for r in self.rows), encoding="utf-8")


def test_wave_of_forget_candidates_is_worked_off_by_the_sleeps(tmp_path, monkeypatch):
    t0 = time.perf_counter()
    sim = Sim(tmp_path, monkeypatch, wave=WAVE, seed_days=70)
    nxt = 2
    for i in range(1, DAYS + 1):
        sim.work(i)
        if i == nxt:
            sim.sleep(i)
            nxt = i + sim.rng.choice((2, 3))
    sim.log("wave")
    rows = sim.rows
    peak = max(range(len(rows)), key=lambda k: rows[k]["c_cands"])
    c0, b0 = rows[peak]["c_cands"], rows[peak]["c_budget"]
    assert c0 >= 0.9 * WAVE, f"the wave of {WAVE} did not arrive: {c0} candidates at its sleep"

    # while a wave waits, forget takes at least four fifths of the plan (the other phases are capped by their share of
    # the queue), and a sleep leaves at most what it did not list: archived and vetoed candidates are gone
    for r in rows[peak:]:
        if r["c_cands"] >= r["c_budget"]:
            assert r["forget_listed"] >= int(0.8 * r["c_budget"]), r
        assert r["c_new"] == r["c_new_shielded"], r  # a candidate the sleep made is the target of a note it merged away
        assert r["c_after"] <= max(0, r["c_cands"] - r["faded"] - r["vetoed"]) + r["c_new"], r

    # the budget model: every sleep finishes its plan (the sleeper leaves under a tenth), so a full plan grows the budget by a
    # quarter up to B_MAX. With four fifths of each plan for forget, the wave of c0 candidates needs n sleeps:
    model, done, bb = [], 0, b0
    while done < c0:
        model.append(bb)
        done += int(0.8 * bb)
        bb = min(round(bb * 1.25), usage.B_MAX)
    n = len(model)
    assert [r["c_budget"] for r in rows[peak:peak + n]] == model, ([r["c_budget"] for r in rows[peak:peak + n]], model)
    assert len(rows) >= peak + n, f"the run is too short for the {n} sleeps the wave needs"
    # so after n sleeps no candidate is left but those that arrived meanwhile (notes of the seeded stream fading)
    arrived = sum(max(0, rows[k + 1]["c_cands"] - rows[k]["c_after"]) for k in range(peak, peak + n - 1))
    assert rows[peak + n - 1]["c_after"] <= arrived, (rows[peak + n - 1]["c_after"], arrived, n, model)
    assert rows[-1]["c_after"] <= int(0.2 * rows[-1]["c_budget"]), rows[-1]  # a settled run leaves next to nothing

    # inbox: DUE lines and waiting lines occurred, none expired (checked after every sleep); verify backlog: only changed
    # notes enter it, it does not grow past that, and after the wave it falls back to the level before it
    assert sum(r["due_listed"] for r in rows) > 0 and sum(r["waited"] for r in rows) > 0
    base = rows[0]
    for r in rows:
        assert r["verify_after"] <= base["verify_after"] + r["changes"] - base["changes"], r
    pre = max(r["verify_after"] for r in rows[:peak])
    assert max(r["verify_after"] for r in rows[-3:]) <= 2 * pre + 3, ([r["verify_after"] for r in rows], pre)

    print(f"\nwave scenario: {time.perf_counter() - t0:.0f} s, {len(rows)} sleeps, parts {sim.t}")
    print("candidates before/after, budget:", [(r["day"], r["c_cands"], r["c_after"], r["c_budget"]) for r in rows])
    live, arch = set(notes_dir(sim.root)), archived(sim.root)
    left = [p for p in sim.dup_pairs if p[0] in live and p[1] in live]
    print("near-duplicate pairs built in:", len(sim.dup_pairs), "; both notes still in notes/ at the end:", len(left),
          "; listed by the first plan:", rows[0]["condense_listed"], "of which merged", rows[0]["merged"])
    print("wave needs", n, "sleeps (model budgets", model, "); verify backlog after each sleep", [r["verify_after"] for r in rows])


def test_regularly_used_notes_gain_stability_and_idle_notes_reach_the_threshold(tmp_path, monkeypatch):
    t0 = time.perf_counter()
    sim = Sim(tmp_path, monkeypatch, wave=0, seed_days=80, regular=40, idle=60)
    regular, idle, v = sim.regular, sim.idle, sim.vault
    sim.keep = frozenset(regular)  # a merge never archives them; forgetting would be a finding
    next_use = {s: sim.rng.randint(1, 8) for s in regular}
    last_S, nxt, quiet = dict(sim.S0), 7, dict(recalls=1, shows=0, captures=(0, 1), news=(0, 1), edits=(0, 1), exact=True)
    for i in range(1, DAYS_STABLE + 1):
        sim.work(i, avoid=set(regular) | set(idle), **quiet)
        for s in regular:
            if next_use[s] <= i:
                run(sim.root, "show", s)
                next_use[s] = i + sim.rng.randint(5, 12)
        d = D0 + timedelta(days=i)
        now = {x[0]: x for x in usage.scores(v, d)}
        for s in regular:  # never a forget candidate, and its stability never falls
            assert s in now and now[s][3] >= 0.5, (i, s, now.get(s))
            assert now[s][4] >= last_S[s] - 1e-9, (i, s, now[s][4], last_S[s])
            last_S[s] = now[s][4]
        if i == nxt:
            sim.sleep(i)
            nxt = i + 7  # sleep.WEEK: a sleep is due after a quiet week
    sim.log("stable")
    end = {x[0]: x for x in usage.scores(v, D0 + timedelta(days=DAYS_STABLE))}
    up = [end[s][4] / sim.S0[s] for s in regular]
    assert all(r > 1.0 for r in up), sorted(up)[:5]
    assert not set(regular) & sim.ever_candidate and not set(regular) & archived(sim.root)
    # unused notes reached the threshold: each was a forget candidate at a sleep, or is one now, or was merged away
    gone = {}
    for s in idle:
        rel = next((r for r in Vault(sim.root).scan("archive") if r.endswith(f"/{s}.md")), None)
        gone[s] = Vault(sim.root).read(rel).get("archived_why") if rel else None
    now_c = {c[0] for c in usage.forget_candidates(v, D0 + timedelta(days=DAYS_STABLE))}
    missing = [s for s in idle if s not in sim.ever_candidate | now_c and gone[s] != "superseded"]
    # the one other way out: a note an active note links to is in use through it (usage.forget_candidates)
    active = {s for s, x in end.items() if x[3] >= 0.5}
    shielded = {t for s in active for t in v.read(notes_dir(sim.root)[s]).targets} if missing else set()
    assert not [s for s in missing if s not in shielded], [s for s in missing if s not in shielded][:5]
    for s in idle:  # no use, no growth: a note still there has the stability it was seeded with, unless a veto kept it
        if s in end and s not in sim.vetoed:
            rows_ = [ln for ln in (sim.root / ".sb" / "usage.tsv").read_text().splitlines() if ln.endswith("\t" + s)]
            assert abs(end[s][4] - sim.S0[s]) < 1e-9, (s, end[s][4], sim.S0[s], rows_)
    print(f"\nstability scenario: {time.perf_counter() - t0:.0f} s, {len(sim.rows)} sleeps, parts {sim.t}")
    print("regular S start/end (min, median, max):",
          [(round(f(sim.S0[s] for s in regular), 1), round(f(end[s][4] for s in regular), 1)) for f in (min, lambda xs: sorted(xs)[len(regular) // 2], max)])
    print("idle: candidate", sum(s in sim.ever_candidate | now_c for s in idle), "of", len(idle), "archived why", {w: list(gone.values()).count(w) for w in set(gone.values())})


def test_far_pair_computes_the_keys_of_each_note_once(tmp_path, monkeypatch):
    from helpers import add_note, init_vault

    from scientific_brain import sleep
    monkeypatch.setenv("SB_TODAY", "2026-11-08")
    root = init_vault(tmp_path)
    for i in range(20):
        add_note(root, f"c{i}", project="a", type="concept", title=f"concept alpha{i} beta{i}", cue=f"cue gamma{i} delta{i}")
    for i in range(40):
        add_note(root, f"f{i}", project="b", title=f"finding epsilon{i} zeta{i}", cue=f"cue eta{i} theta{i}")
    calls, orig = [0], sleep.prepare

    def counting(*a, **k):
        calls[0] += 1
        return orig(*a, **k)
    monkeypatch.setattr(sleep, "prepare", counting)
    assert sb(root, "sleep", "plan")[0] == 0
    assert calls[0] <= 10 * 60, calls[0]  # a memo makes it a few per note; without it 20 x 40 x 2 pairs of keys
