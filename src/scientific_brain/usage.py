"""Usage log, renames and ACT-R base-level activation."""
from __future__ import annotations

import math
import os
from datetime import date

from .vault import SbError, Vault

EPS = 1e-12
SEEDS = "seeds.tsv"  # .sb/ file: date, slug, weight of the start use `sleep baseline --seed` gives imported notes


def today() -> date:
    """Today; SB_TODAY (YYYY-MM-DD) overrides it for reproducible runs and tests."""
    return date.fromisoformat(os.environ["SB_TODAY"]) if os.environ.get("SB_TODAY") else date.today()


def sleeping(v: Vault, day: date) -> bool:
    """A sleep runs: its marker is at most one day old, so a forgotten marker stops tagging work as sleep."""
    try:
        return (day - date.fromisoformat((v.sb_dir / "sleeping").read_text(encoding="utf-8").split("\n", 1)[0].strip())).days <= 1
    except (OSError, ValueError):
        return False


def log(v: Vault, kind: str, slug: str, day: date | None = None):
    """Append an event; calls made while a sleep runs are logged as caller=sleep."""
    day = day or today()
    v.append("usage.tsv", day.isoformat(), "sleep" if sleeping(v, day) else "work", kind, slug)


def log_rename(v: Vault, old: str, new: str, day: date | None = None):
    v.append("renames.tsv", old, new, (day or today()).isoformat())


def renames(v: Vault) -> dict:
    """Old slug -> current slug; chains and renames back are folded in order."""
    m: dict = {}
    for old, new, _ in v.rows("renames.tsv", 3):
        m = {k: (new if x == old else x) for k, x in m.items()}
        m.pop(new, None)
        m[old] = new
    return m


def events(v: Vault) -> dict:
    """Current slug -> {day: weight}: the strongest event per day; only caller=work counts, except veto."""
    w, m, out = {k: v.cfg["activation"][k] for k in ("create", "show", "edit", "veto", "recall")}, renames(v), {}
    for d, caller, kind, slug in v.rows("usage.tsv", 4):
        if kind not in w or (caller != "work" and kind != "veto"):
            continue
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        days = out.setdefault(m.get(slug, slug), {})
        days[day] = max(days.get(day, 0.0), w[kind])
    for d, slug, x in v.rows(SEEDS, 3):
        try:
            day, wt = date.fromisoformat(d), float(x)
        except ValueError:
            continue
        days = out.setdefault(m.get(slug, slug), {})
        days[day] = max(days.get(day, 0.0), wt)
    return out


def horizon(v: Vault, typ: str) -> float:
    a = v.cfg["activation"]
    return a.get("h_" + typ, a["h"])


def activation(days: dict, today: date) -> float:
    """A = ln(sum w * (dt + 1)^-0.5), dt in days."""
    s = sum(w * (max((today - d).days, 0) + 1) ** -0.5 for d, w in days.items())
    return math.log(s) if s > 0 else -math.inf


def threshold(v: Vault, typ: str) -> float:
    """ln((H + 1)^-0.5): the value of one full use H days ago."""
    return math.log((horizon(v, typ) + 1) ** -0.5)


def scores(v: Vault, today: date) -> list:
    """(slug, type, created, A, threshold) for every note in notes/; without events 'created' counts as a creation."""
    ev, out = events(v), []
    for rel in v.scan("notes"):
        try:
            n = v.read(rel)
            created = date.fromisoformat(n.get("created"))
        except (SbError, ValueError):
            continue
        days = ev.get(n.slug) or {created: v.cfg["activation"]["create"]}
        out.append((n.slug, n.get("type"), created, activation(days, today), threshold(v, n.get("type"))))
    return out


def forget_candidates(v: Vault, today: date, sc=None) -> list:
    """(slug, type, A, threshold) of notes/ below their threshold, lowest A first."""
    sc = scores(v, today) if sc is None else sc
    return sorted(((s, t, a, th) for s, t, _, a, th in sc if a < th - EPS), key=lambda r: r[2])


def creates_today(v: Vault, today: date) -> int:
    return sum(d == today.isoformat() and c == "work" and k == "create" for d, c, k, _ in v.rows("usage.tsv", 4))


def last_sleep(v: Vault) -> date | None:
    for row in reversed(v.rows("sleep.tsv", 3)):
        try:
            return date.fromisoformat(row[0])
        except ValueError:
            pass
    return None


def sleep_due(v: Vault, n_notes: int, n_inbox: int, today: date, work: dict | None = None) -> str | None:
    """The 'sleep due' line, or None: last sleep over sleep_days ago (or never, with notes), too many open inbox lines,
    or, given the waiting work, a full sleep of it at least sleep_gap days after the last sleep. Sleeps thus come as
    often as work arrives, so no backlog builds up."""
    lc, last, why = v.cfg["lifecycle"], last_sleep(v), []
    if last is None and n_notes:
        why.append("never slept")
    elif last and (today - last).days > lc["sleep_days"]:
        why.append(f"{(today - last).days} days since the last sleep")
    elif work and (n := sum(work.values())) >= lc["sleep_budget"] and (last is None or (today - last).days >= lc["sleep_gap"]):
        why.append(f"{n} items waiting (" + ", ".join(f"{k} {x}" for k, x in work.items() if x) + ")")
    if n_inbox > lc["sleep_inbox"]:
        why.append(f"{n_inbox} open inbox lines")
    return "sleep due: " + "; ".join(why) if why else None
