"""Usage log, renames, the stability of each note (learned from the log) and when a sleep is due."""
from __future__ import annotations

import math
import os
from datetime import date, datetime, time, timedelta

from .vault import SbError, Vault

EPS = 1e-12
SEEDS = "seeds.tsv"  # .sb/ file: date, slug, weight of the start use `sleep baseline --seed` gives imported notes


def today() -> date:
    """Today; SB_TODAY (YYYY-MM-DD) overrides it for reproducible runs and tests."""
    return date.fromisoformat(os.environ["SB_TODAY"]) if os.environ.get("SB_TODAY") else date.today()


def stamp(day: date) -> str:
    """day with the current time to the minute; noon under SB_TODAY, so runs stay reproducible."""
    t = time(12) if os.environ.get("SB_TODAY") else datetime.now().time()
    return datetime.combine(day, t).isoformat(timespec="minutes")


def sleeping(v: Vault, day: date) -> bool:
    """A sleep runs: its marker is at most one day old, so a forgotten marker stops tagging work as sleep."""
    try:
        return (day - date.fromisoformat((v.sb_dir / "sleeping").read_text(encoding="utf-8").split("\n", 1)[0].strip())).days <= 1
    except (OSError, ValueError):
        return False


def log(v: Vault, kind: str, slug: str, day: date | None = None, caller: str = "work"):
    """Append an event; calls made while a sleep runs are logged as caller=sleep, edits the index sync found as sync."""
    day = day or today()
    v.append("usage.tsv", day.isoformat(), "sleep" if sleeping(v, day) else caller, kind, slug)


def log_rename(v: Vault, old: str, new: str, day: date | None = None):
    """Append old, new, date and the number of usage and seed rows so far: later rows belong to a note named old anew."""
    v.append("renames.tsv", old, new, (day or today()).isoformat(), len(v.rows("usage.tsv", 4)), len(v.rows(SEEDS, 3)))


def renames(v: Vault) -> dict:
    """Old slug -> current slug; chains and renames back are folded in order."""
    m: dict = {}
    for old, new, *_ in v.rows("renames.tsv", 5):
        m = {k: (new if x == old else x) for k, x in m.items()}
        m.pop(new, None)
        m[old] = new
    return m


def renamer(v: Vault):
    """Function (slug, row number, day, log) -> current slug; log 0 is usage.tsv, 1 is seeds.tsv. A row follows only the
    renames made after it, so a note created under a freed name keeps its events (old rows without numbers compare dates)."""
    by_old: dict = {}
    for pos, (old, new, d, *n) in enumerate(v.rows("renames.tsv", 5)):
        by_old.setdefault(old, []).append((pos, new, d, [int(x) if x.isascii() and x.isdigit() else None for x in n]))

    def current(slug, i, day, k=0):
        pos = -1
        while True:
            for p, new, d, n in by_old.get(slug, ()):
                if p > pos and (i < n[k] if n[k] is not None else day.isoformat() <= d):
                    slug, pos = new, p
                    break
            else:
                return slug
    return current


GAIN = 1.0       # a review at R = 1/2 lengthens a note's rest by half: S *= 1 + GAIN * q * (1 - R)
RECALL = 0.5     # the first hit of recall is half a review (q); create, show, edit and veto are full ones
PRIOR, PRIOR_N = 90.0, 20  # initial stability before the log tells, and its weight in notes
FLOOR, CEIL = 30.0, 365.0  # bounds of the learned initial stability, in days
OLD_H = {"decision": 180, "derivation": 180}  # horizons of sb <= 0.2.1 that seeds.tsv weights refer to; 90 otherwise
STRENGTH = {"create": 1.0, "show": 1.0, "edit": 1.0, "veto": 1.0, "recall": RECALL}
B0, B_MIN, B_MAX = 40, 10, 200  # sleep budget: start and bounds
GAP_HOURS, WEEK = 5, 7  # a full plan of work makes a sleep due GAP_HOURS after the last one; any sleep is due after WEEK days


def seeds(v: Vault) -> dict:
    """Current slug -> (day, weight) of its latest seed, the start use `sleep baseline --seed` gave an imported note."""
    cur, out = renamer(v), {}
    for i, (d, slug, x) in enumerate(v.rows(SEEDS, 3)):
        try:
            day, wt = date.fromisoformat(d), float(x)
        except ValueError:
            continue
        if not (math.isfinite(wt) and wt > 0):
            continue
        s = cur(slug, i, day, 1)
        if s not in out or day >= out[s][0]:
            out[s] = (day, wt)
    return out


def seed_rest(weight: float, typ: str) -> float:
    """Days a seeded note rests: the seed weight w was chosen so that w^2 (H + 1) = T + 1 with the old horizon H."""
    return min(max(min(max(weight, 0.0), 1e3) ** 2 * (OLD_H.get(typ, 90) + 1) - 1, 0.0), 1e6)


def events(v: Vault, ignore_sync: bool = False) -> dict:
    """Current slug -> {day: strength}: the strongest review per day; only caller=work and sync count, except veto.
    Sync edits (found by the index) yield to a later or same-day seed of the note; ignore_sync drops them."""
    cur, out = renamer(v), {}
    seeded = {s: d for s, (d, _) in seeds(v).items()}
    for i, (d, caller, kind, slug) in enumerate(v.rows("usage.tsv", 4)):
        if kind not in STRENGTH or (caller not in ("work", "sync") and kind != "veto"):
            continue
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        s = cur(slug, i, day)
        if caller == "sync" and (ignore_sync or day <= seeded.get(s, date.min)):
            continue
        days = out.setdefault(s, {})
        days[day] = max(days.get(day, 0.0), STRENGTH[kind])
    return out


def replay(days: dict, start: date | None, s0: float, seed: tuple | None = None) -> tuple:
    """(S, last): stability in days and the day of the last review. The earliest of start, the reviews and the seed
    opens the history with S = s0 (a seed: S = its rest); a later start (events logged before a note's created date,
    an import's history) restarts the clock without growth, so no note is forgotten before it is S days old. Each later review on a new day
    multiplies S by 1 + GAIN * q * (1 - R), R the retrievability at that moment, so a review shortly before forgetting
    counts most and reviews in quick succession add almost nothing (spacing). A later seed restarts the clock and sets
    S to its rest; after real reviews it only raises S."""
    items = sorted(days.items())
    timeline = ([(start, "start", 1.0)] if start else []) + [(d, "review", q) for d, q in items]
    if seed:
        timeline.append((seed[0], "seed", seed[1]))
    timeline.sort(key=lambda x: (x[0], x[1] != "start", x[1] == "seed"))
    S, last, reviewed = None, None, False
    for day, kind, x in timeline:
        if S is None:
            S, last, reviewed = max(x if kind == "seed" else s0, 1e-9), day, kind == "review"
        elif kind == "start":
            last = max(last, day)
        elif kind == "seed":
            S, last = max(S if reviewed else 0.0, x, 1e-9), max(last, day)
        elif day > last:
            S *= 1 + GAIN * x * (1 - retrievability(S, (day - last).days))
            last, reviewed = day, True
    return (s0, start) if S is None else (max(S, 1e-9), last)


def retrievability(S: float, t: float) -> float:
    """R = 1 / (1 + t / S): 1 right after a review, 1/2 when the note has rested S days."""
    return 1.0 / (1.0 + max(t, 0) / S)


def use_days(v: Vault) -> dict:
    """Current slug -> set of days with real use: create, show, edit or a recall hit in a work session. Vetoes, sync
    edits and seeds are left out: they say a note was kept or touched, not that it was needed."""
    cur, out = renamer(v), {}
    for i, (d, caller, kind, slug) in enumerate(v.rows("usage.tsv", 4)):
        if caller != "work" or kind not in ("create", "show", "edit", "recall"):
            continue
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        out.setdefault(cur(slug, i, day), set()).add(day)
    return out


def gaps(v: Vault, today: date) -> dict:
    """Type -> [(gap in days, weight)]: gaps between consecutive real use days of a note, over notes and archive. The
    created date counts as the first day only if no use came before it. Only gaps that start more than CEIL days ago
    count, so that every gap up to CEIL had the time to be seen, and each phases in over the following CEIL days, so
    that what is learned moves smoothly once the log is a year old. A gap is capped at CEIL, so one stray old line
    cannot carry the vault. A note's gaps share the weight 1, so that much used notes do not outvote the rest."""
    used, out = use_days(v), {}
    for loc in ("notes", "archive"):
        for rel in v.scan(loc):
            try:
                n = v.read(rel)
                created = date.fromisoformat(n.get("created")) if n.get("created") else None
            except (SbError, ValueError):
                continue
            days = set(used.get(n.slug, ()))
            if created and (not days or created <= min(days)):
                days.add(created)
            ds = sorted(days)
            gs = [(min((b - a).days, CEIL), min(((today - a).days - CEIL) / CEIL, 1.0))
                  for a, b in zip(ds, ds[1:]) if (today - a).days > CEIL and b <= today]
            tot = max(1.0, sum(r for _, r in gs))  # a note weighs at most 1; dividing by at least 1 keeps phase-in steady
            out.setdefault(n.get("type"), []).extend((g, r / tot) for g, r in gs)
    return out


def learned(v: Vault, today: date) -> dict:
    """Initial stability per type, learned from the log: the weighted 90th percentile of the gaps between two real
    uses of a note (a note still in use is rarely left alone longer), pulled towards PRIOR with the weight of PRIOR_N
    notes, within FLOOR..CEIL; a type is pulled towards the vault value in the same way. Key '' holds the vault value.
    A vault with less than a year of log has no gap that counts and stays at PRIOR."""
    by_type = gaps(v, today)

    def shrink(ws, prior):
        if not ws:
            return prior
        ws, total, acc = sorted(ws), sum(w for _, w in ws), 0.0
        for g, w in ws:
            acc += w
            if acc >= 0.9 * total - EPS:
                break
        return min(max((total * g + PRIOR_N * prior) / (total + PRIOR_N), FLOOR), CEIL)
    base = shrink([x for xs in by_type.values() for x in xs], PRIOR)
    return {"": base, **{t: shrink(xs, base) for t, xs in by_type.items() if t}}


def scores(v: Vault, today: date, s0: dict | None = None) -> list:
    """(slug, type, created, R, S, last) for every note in notes/: retrievability today, stability, last review."""
    ev, sd, out = events(v), seeds(v), []
    s0 = learned(v, today) if s0 is None else s0
    for rel in v.scan("notes"):
        try:
            n = v.read(rel)
            created = date.fromisoformat(n.get("created"))
        except (SbError, ValueError):
            continue
        typ = n.get("type")
        seed = (sd[n.slug][0], seed_rest(sd[n.slug][1], typ)) if n.slug in sd and sd[n.slug][0] <= today else None
        days = {d: q for d, q in ev.get(n.slug, {}).items() if d <= today}  # a use dated ahead is a wrong clock
        S, last = replay(days, created, s0.get(typ, s0[""]), seed)
        out.append((n.slug, typ, created, retrievability(S, (today - last).days), S, last))
    return out


def forget_candidates(v: Vault, today: date, sc=None) -> list:
    """(slug, type, R, S) of notes/ whose retrievability fell below 1/2, lowest R first. A note an active note links to
    is in use through it; links from faded notes protect nothing, so faded chains and cycles still fade."""
    sc = scores(v, today) if sc is None else sc
    low = {s: (t, r, S) for s, t, _, r, S, _ in sc if r < 0.5 - EPS}
    used = set()
    for rel in v.scan("notes") if low else ():
        try:
            n = v.read(rel)
        except SbError:
            continue
        if n.slug not in low:
            used.update(n.targets)
    return sorted(((s, t, r, S) for s, (t, r, S) in low.items() if s not in used), key=lambda x: (x[2], x[0]))


def active(x) -> tuple:
    """Sort key over scores rows, most active first: highest retrievability, then highest stability."""
    return -x[3], -x[4], x[0]


def fade_day(S: float, last: date, today: date) -> date:
    """First day on which a note with stability S, last reviewed on last, falls below R = 1/2 without further use."""
    return max(today, last + timedelta(days=math.floor(S) + 1))


def creates_today(v: Vault, today: date) -> int:
    return sum(d == today.isoformat() and c == "work" and k == "create" for d, c, k, _ in v.rows("usage.tsv", 4))


def last_sleep_at(v: Vault, day: date | None = None) -> datetime | None:
    """End of the last sleep as local naive time; a row with a date only (before 0.2.1) counts from midnight, one with a
    zone is converted, one after now (a wrong clock) is skipped."""
    now = datetime.fromisoformat(stamp(day or today()))
    for row in reversed(v.rows("sleep.tsv", 3)):
        try:
            at = datetime.fromisoformat(row[0])
        except ValueError:
            continue
        if at.tzinfo:
            at = at.astimezone().replace(tzinfo=None)
        if at <= now:
            return at
    return None


def last_sleep(v: Vault) -> date | None:
    at = last_sleep_at(v)
    return at.date() if at else None


def budget(v: Vault) -> int:
    """Notes and inbox lines a sleep plan takes, learned from the sleeps so far (sleep.tsv: plan size, items left).
    It starts at B0. A sleep that left more than a tenth of its plan undone sets the next plan to what it did and marks
    its size as too large. A sleep that finished a full plan lets the plan grow by a quarter, but only halfway towards
    a size known to be too large; once just below it, that limit moves up by one per sleep, so a stronger agent is
    found again without a plan far beyond what the last one managed. Sleeps before 0.3 logged no size and count not.
    Within B_MIN..B_MAX."""
    b, high = B0, None
    for r in v.rows("sleep.tsv", 10):
        if not all(x.isascii() and x.isdigit() for x in r[8:10]) or int(r[8]) == 0:
            continue
        size, left = int(r[8]), min(int(r[9]), int(r[8]))
        if left * 10 > size:
            high, b = size, size - left
        elif size >= b:
            nb = round(b * 1.25)
            if high is not None:
                if b >= high - 1:
                    high += 1
                nb = min(nb, (b + high) // 2)
            b = max(nb, b)
        b = min(max(b, B_MIN), B_MAX)
    return b


def sleep_due(v: Vault, n_notes: int, n_inbox: int, today: date, work: dict | None = None) -> str | None:
    """The 'sleep due' line, or None: never slept (with notes), last sleep over WEEK days ago, or a full plan of work
    waiting at least GAP_HOURS after the last sleep. Sleeps thus come as often as work arrives."""
    at, why = last_sleep_at(v, today), []
    last = at.date() if at else None
    gap = at is None or datetime.fromisoformat(stamp(today)) - at >= timedelta(hours=GAP_HOURS)
    n = sum(work.values()) if work else n_inbox
    if last is None and n_notes:
        why.append("never slept")
    elif last and (today - last).days > WEEK:
        why.append(f"{(today - last).days} days since the last sleep")
    elif n >= budget(v) and gap:
        why.append(f"{n} items waiting" + (" (" + ", ".join(f"{k} {x}" for k, x in work.items() if x) + ")" if work else ""))
    return "sleep due: " + "; ".join(why) if why else None
