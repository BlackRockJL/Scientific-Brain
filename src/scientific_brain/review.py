"""`sb review`: a read-only inventory of the vault, as a Markdown report and a mind map (Mermaid; PNG or SVG with matplotlib)."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from . import inbox, sleep, usage
from .vault import SbError, Vault

TYPES = ("finding", "decision", "derivation", "source", "question", "concept")
RECENT = 30     # days that count as recent use
MONTHS = 6      # months of the fading outlook
WINDOW = 90     # days of veto and archive history the report shows


def uses(v: Vault, since: date | None = None) -> dict:
    """Current slug -> [(day, strength)]: work and sync events from usage.tsv; seeds and calls during a sleep do not count."""
    cur, out = usage.renamer(v), defaultdict(list)
    for i, (d, caller, kind, slug) in enumerate(v.rows("usage.tsv", 4)):
        if caller not in ("work", "sync") or kind not in usage.STRENGTH or kind == "veto":
            continue
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        if since is None or day >= since:
            out[cur(slug, i, day)].append((day, usage.STRENGTH[kind]))
    return out


def _int(x) -> int:
    try:
        return int(x)
    except ValueError:
        return 0


def prefix_mismatches(notes: dict) -> Counter:
    """(prefix, type) -> count for notes whose first slug word is the dominant prefix of another type.
    A type has a dominant prefix when at least 5 of its notes and 60 % of them share the first slug word."""
    first = {s: s.split("-", 1)[0] for s in notes}
    dom = {}
    for t in TYPES:
        c = Counter(first[s] for s, n in notes.items() if n.get("type") == t)
        if c:
            p, k = c.most_common(1)[0]
            if k >= 5 and k >= 0.6 * sum(c.values()):
                dom[p] = t
    return Counter((first[s], n.get("type")) for s, n in notes.items() if first[s] in dom and dom[first[s]] != n.get("type"))


def collect(v: Vault, day: date, project: str | None = None, since: date | None = None, top: int = 3) -> dict:
    """Everything the report and the map show. Reads only; nothing is logged."""
    notes = sleep.load(v)
    arch = {}
    for rel in v.scan("archive"):
        try:
            n = v.read(rel)
        except SbError:
            continue
        arch[n.slug] = n
    proj = {s: n.get("project") or n.folder for s, n in notes.items()}
    if project and project not in proj.values():
        raise SbError(f"no notes in project '{project}'")
    sel = {s: n for s, n in notes.items() if not project or proj[s] == project}
    lc = v.cfg["lifecycle"]

    inbound, dangling, cross, outdeg = Counter(), Counter(), Counter(), {}
    for s, n in notes.items():
        ts = n.targets
        outdeg[s] = len(ts)
        for t in ts:
            if t in notes:
                inbound[t] += 1
                if proj[t] != proj[s]:
                    cross[tuple(sorted((proj[s], proj[t])))] += 1
            elif t not in arch and s in sel:
                dangling[s] += 1
    used = uses(v, since)
    total = {s: sum(w for _, w in used.get(s, ())) for s in sel}
    recent = {s: sum(w for d, w in used.get(s, ()) if (day - d).days < RECENT) for s in sel}
    last = {s: max(d for d, _ in used[s]) for s in sel if used.get(s)}

    sc = {x[0]: x for x in usage.scores(v, day, usage.learned(v, day))}
    cands = {s for s, *_ in usage.forget_candidates(v, day, list(sc.values()))} & set(sel)
    outlook = Counter()
    end = day + timedelta(days=31 * MONTHS)
    for s in sel:
        if s in sc and s not in cands:
            f = usage.fade_day(sc[s][4], sc[s][5], day)
            if f <= end:
                outlook[f.strftime("%Y-%m")] += 1
    learned, budget = usage.learned(v, day), usage.budget(v)
    stab = sorted(sc[s][4] for s in sel if s in sc)

    sleeps = [r for r in v.rows("sleep.tsv", 8) if r[0][:10] >= (day - timedelta(RECENT)).isoformat()]
    decided = sum(_int(r[3]) + _int(r[5]) for r in sleeps)
    from_day = (day - timedelta(WINDOW)).isoformat()
    vetoes = sum(r[0] >= from_day for r in v.rows("vetoes.tsv", 3))
    faded = sum(n.get("archived_why") == "faded" and str(n.get("archived")) >= from_day for n in arch.values())

    op = inbox.open_lines(v)
    due = sum(inbox.age(r, day) > lc["inbox_wait_days"] for r in op)
    prom, settled = inbox.promotion(v)
    questions = sorted(str(n.get("created")) for n in sel.values() if n.get("type") == "question")

    group = (lambda s: sel[s].get("type")) if project else (lambda s: proj[s])
    members = defaultdict(list)
    for s in sel:
        members[group(s)].append(s)
    order = [t for t in TYPES if t in members] + sorted(set(members) - set(TYPES)) if project else \
        sorted(members, key=lambda g: (-len(members[g]), g))

    def rank(s):
        return (-(inbound[s] + 2 * recent[s]), sel[s].get("title"))
    groups = [{"name": g, "n": len(members[g]), "types": Counter(sel[s].get("type") for s in members[g]),
               "recent": sum(recent[s] for s in members[g]), "total": sum(total[s] for s in members[g]),
               "used": sum(total[s] > 0 for s in members[g]),
               "leaves": [(s, sel[s].get("title"), inbound[s], sel[s].get("type")) for s in sorted(members[g], key=rank)[:top]]}
              for g in order]

    weeks = Counter()
    for n in sel.values():
        try:
            c = date.fromisoformat(n.get("created"))
        except ValueError:
            continue
        if (day - c).days < 56:
            weeks[(c - timedelta(c.weekday())).isoformat()] += 1

    warnings = []
    need = len(cands) + sum(n for m, n in outlook.items() if m <= (day + timedelta(RECENT)).strftime("%Y-%m"))
    if len(cands) > budget and decided < len(cands):
        warnings.append(f"{len(cands)} forget candidates wait, but the sleeps of the last {RECENT} days decided only {decided}; "
                        f"sleep more often (the plan grows when sleeps finish it)")
    if lc["inbox_wait_days"] and due:
        warnings.append(f"{due} inbox lines are older than {lc['inbox_wait_days']} days")
    if (mm := prefix_mismatches(sel)):
        warnings.append(f"{sum(mm.values())} notes carry the slug prefix of another type")
    if dangling:
        warnings.append(f"{sum(dangling.values())} links point to no note in {len(dangling)} notes")

    return {"day": day, "project": project, "since": since, "notes": len(sel), "archive": Counter(
                n.get("archived_why") or "?" for s, n in arch.items() if not project or (n.get("project") or n.folder) == project),
            "groups": groups, "by_type": Counter(n.get("type") for n in sel.values()),
            "matrix": Counter((proj[s], sel[s].get("type")) for s in sel),
            "top_used": sorted((s for s in sel if total[s] > 0), key=lambda s: (-total[s], s))[:10],
            "total": total, "recent": recent, "last": last, "titles": {s: n.get("title") for s, n in sel.items()},
            "candidates": len(cands), "outlook": dict(sorted(outlook.items())), "need": need,
            "learned": learned, "budget": budget, "stability": stab,
            "sleeps": len(sleeps), "decided": decided, "vetoes": vetoes, "faded": faded,
            "inbound": inbound, "outdeg": {s: outdeg[s] for s in sel}, "dangling": dangling,
            "cross": cross if not project else Counter(),
            "unverified": sum(not n.get("verified") for n in sel.values()),
            "questions": len(questions), "oldest_question": questions[0] if questions else None,
            "inbox_open": len(op), "inbox_due": due, "promotion": (prom, settled), "mismatch": prefix_mismatches(sel),
            "weeks": dict(sorted(weeks.items())), "last_sleep": usage.last_sleep(v), "warnings": warnings}


def _table(head, rows) -> list:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]


def _num(x) -> str:
    return f"{x:g}" if isinstance(x, float) else str(x)


def report(inv: dict, image: str | None = None) -> str:
    """The Markdown report; with image, the map is linked, else it is drawn as a Mermaid mind map."""
    day, p, g = inv["day"], inv["project"], inv["groups"]
    arch = sum(inv["archive"].values())
    out = [f"# Review of the brain{' – ' + p if p else ''}, {day}", "",
           f"{inv['notes']} notes" + ("" if p else f" in {len(g)} projects") + f", {arch} archived; "
           f"{inv['inbox_open']} open inbox lines; last sleep "
           + (f"{inv['last_sleep']} ({(day - inv['last_sleep']).days} days ago)." if inv["last_sleep"] else "never."),
           ""]
    if inv["since"]:
        out += [f"Use counts only events from {inv['since']} on.", ""]
    if inv["warnings"]:
        out += ["## Warnings", ""] + [f"- {w}" for w in inv["warnings"]] + [""]

    out += ["## Inventory", ""]
    present = [t for t in TYPES if inv["by_type"][t]] + sorted(set(inv["by_type"]) - set(TYPES))
    if p:
        out += _table(("type", "notes"), [(t, inv["by_type"][t]) for t in present])
    else:
        out += _table(("project", *present, "all"), [(x["name"], *(inv["matrix"][(x["name"], t)] for t in present), x["n"]) for x in g]
                      + [("**all**", *(inv["by_type"][t] for t in present), inv["notes"])])
    if arch:
        out += ["", "Archive: " + ", ".join(f"{k} {n}" for k, n in sorted(inv["archive"].items())) + "."]

    out += ["", "## Use", "", f"Weighted use: show, edit and create count 1, the first hit of recall counts less; "
            f"recent means the last {RECENT} days.", ""]
    out += _table(("project" if not p else "type", "notes", "ever used", "use", f"use last {RECENT} d"),
                  [(x["name"], x["n"], f"{x['used'] / x['n']:.0%}", _num(round(x["total"], 1)), _num(round(x["recent"], 1))) for x in g])
    if inv["top_used"]:
        out += ["", "Most used notes:", ""] + [f"{i}. `{s}` – {_num(round(inv['total'][s], 1))}, last {inv['last'][s]}"
                                               for i, s in enumerate(inv["top_used"], 1)]

    st, lr = inv["stability"], inv["learned"]
    out += ["", "## Fading", "",
            f"A note rests unused for its stability, which grows with each use or veto, the more the closer it was to "
            f"fading. Learned from this vault: a new note rests {lr[''] :.0f} days ("
            + ", ".join(f"{t} {x:.0f}" for t, x in sorted(lr.items()) if t) + "); a sleep takes "
            f"{inv['budget']} items. Stability now: " + (f"median {median(st):.0f} days, at most {max(st):.0f}." if st else "n/a."),
            f"Forget candidates now: {inv['candidates']}.",
            f"Sleeps in the last {RECENT} days: {inv['sleeps']}, deciding {inv['decided']} candidates "
            f"(archived or vetoed). In the last {WINDOW} days: {inv['vetoes']} vetoes, {inv['faded']} faded.", ""]
    if inv["outlook"]:
        out += [f"Notes that reach the threshold without further use, by month (next {MONTHS} months); "
                "the forget list may still pass over some of them:", ""]
        out += _table(("month", "notes"), list(inv["outlook"].items()))

    deg = list(inv["outdeg"].values())
    no_in = sum(inv["inbound"][s] == 0 for s in inv["outdeg"])
    alone = sum(inv["inbound"][s] == 0 and d == 0 for s, d in inv["outdeg"].items())
    hubs = sorted(inv["outdeg"], key=lambda s: (-inv["inbound"][s], s))[:10]
    out += ["", "## Structure", "",
            f"Links per note: median {median(deg) if deg else 0:g}, at most {max(deg) if deg else 0}. "
            f"{no_in} notes have no incoming link, {alone} no link at all; "
            f"{sum(inv['dangling'].values())} links point to no note.", ""]
    if hubs and inv["inbound"][hubs[0]]:
        out += ["Hubs (incoming links):", ""] + [f"- `{s}` – {inv['inbound'][s]}" for s in hubs if inv["inbound"][s]]
    if inv["cross"]:
        out += ["", "Links between projects:", ""] + [f"- {a} – {b}: {n}" for (a, b), n in inv["cross"].most_common(10)]

    prom, settled = inv["promotion"]
    out += ["", "## Upkeep", "",
            f"- never verified: {inv['unverified']}",
            f"- open questions: {inv['questions']}" + (f", oldest from {inv['oldest_question']}" if inv["oldest_question"] else ""),
            f"- inbox: {inv['inbox_open']} open, {inv['inbox_due']} due; promotion rate "
            + (f"{prom / settled:.0%} of {settled}" if settled else "n/a")]
    if inv["mismatch"]:
        out += [f"- slug prefix of another type: " + ", ".join(f"{pre}- as {t} {n}" for (pre, t), n in inv["mismatch"].most_common())]
    if inv["weeks"]:
        out += ["", "New notes per week: " + ", ".join(f"{w} {n}" for w, n in inv["weeks"].items()) + "."]

    out += ["", "## Mind map", ""]
    out += [f"![mind map]({image})"] if image else ["```mermaid", *mermaid(inv), "```"]
    return "\n".join(out) + "\n"


def _label(s: str, cap: int = 48) -> str:
    s = re.sub(r"[\[\](){}\"`<>]", "", " ".join(str(s).split()))
    return s if len(s) <= cap else s[:cap - 1].rstrip() + "…"


def mermaid(inv: dict) -> list:
    out = ["mindmap", f"  root(({_label(inv['project'] or 'brain')}))"]
    for i, x in enumerate(inv["groups"]):
        out.append(f"    g{i}[{_label(x['name'])} · {x['n']}]")
        out += [f"      g{i}n{j}[{_label(t)}]" for j, (_, t, _, _) in enumerate(x["leaves"])]
    return out


BG, FAINT, SOFT, OTHER = "#070a14", "#6b7591", "#c8d0e6", "#8d99ae"
NEON = ("#4cc9f0", "#f72585", "#7b61ff", "#ffd166", "#06d6a0", "#ff9f1c", "#b5e48c", "#fb5607",
        "#90e0ef", "#2ec4b6", "#ff4d4d", "#b8c0ff", "#ff99c8", "#e0e0e0", "#caffbf", "#ffafcc")  # by group size; the rest is grey
SHAPES = {"concept": ("o", 1.0), "source": ("s", 0.7), "derivation": ("^", 1.1), "finding": ("D", 0.65),
          "question": ("o", 1.1), "decision": ("h", 1.1)}
WORD = re.compile(r"[^\W\d_]{3,}")


def _layout(np, notes: dict, groups: list, rng):
    """2D positions: exact t-SNE on an affinity of links, text similarity (TF-IDF of title, cue and body) and group."""
    slugs = list(notes)
    n, at = len(slugs), {s: i for i, s in enumerate(slugs)}
    A = np.zeros((n, n), np.float32)
    edges = set()
    for i, s in enumerate(slugs):
        for t in notes[s].targets:
            j = at.get(t)
            if j is not None and j != i:
                A[i, j] = A[j, i] = 1.0
                edges.add((min(i, j), max(i, j)))
    bags = []
    for s in slugs:
        c, note = Counter(), notes[s]
        c.update(w.lower() for w in WORD.findall(note.body))
        for w in WORD.findall(f"{note.get('title')} {note.get('cue')}"):
            c[w.lower()] += 3
        bags.append(c)
    df = Counter(w for c in bags for w in c)
    vocab = {w: k for k, (w, _) in enumerate(sorted(((w, d) for w, d in df.items() if 2 <= d <= 0.4 * n),
                                                    key=lambda x: (-x[1], x[0]))[:6000])}
    X = np.zeros((n, max(1, len(vocab))), np.float32)
    for i, c in enumerate(bags):
        for w, k in c.items():
            if w in vocab:
                X[i, vocab[w]] = (1 + math.log(k)) * (math.log((1 + n) / (1 + df[w])) + 1)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    S = X @ X.T
    np.fill_diagonal(S, 0)
    k = min(12, n - 1)
    T = np.zeros_like(S)
    nn = np.argpartition(-S, k - 1, axis=1)[:, :k]
    T[np.arange(n)[:, None], nn] = S[np.arange(n)[:, None], nn]
    T = np.maximum(T, T.T)
    g = np.array(groups)
    W = A + 1.2 * T + 0.04 * (g[:, None] == g[None, :]) * (S > 0.05)
    np.fill_diagonal(W, 0)
    P = W / (W.sum(1, keepdims=True) + 1e-12)
    P = np.maximum((P + P.T) / (2 * n), 1e-12)
    Y = rng.normal(0, 1e-2, (n, 2))
    dY, gains = np.zeros_like(Y), np.ones_like(Y)
    for it in range(1000):
        ex, mom = (12.0, 0.5) if it < 250 else (1.0, 0.8)
        sq = (Y ** 2).sum(1)
        num = 1.0 / (1.0 + sq[:, None] + sq[None, :] - 2 * Y @ Y.T)
        np.fill_diagonal(num, 0)
        Q = np.maximum(num / num.sum(), 1e-12)
        PQ = (ex * P - Q) * num
        grad = 4 * (PQ.sum(1)[:, None] * Y - PQ @ Y)
        gains = np.where(np.sign(grad) != np.sign(dY), gains + 0.2, gains * 0.8).clip(0.01)
        dY = mom * dY - 200.0 * gains * grad
        Y += dY
        Y -= Y.mean(0)
    lo, hi = np.percentile(Y, 0.5, axis=0), np.percentile(Y, 99.5, axis=0)
    Y = (Y - (lo + hi) / 2) / (max((hi - lo).max(), 1e-9) / 2) * 0.81 + np.array([0.05, 0.04])
    return Y, sorted(edges), A.sum(1)


def draw(v: Vault, inv: dict, path: Path, exclude=()):
    """Map of the notes: closeness = closeness in content (links, text), glow = group, shape = type, size = links."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib import patheffects as pe
        from matplotlib.collections import LineCollection
        from matplotlib.colors import to_rgb
    except ImportError:
        raise SbError("the map as PNG or SVG needs numpy and matplotlib: pip install 'scientific-brain[review]'")
    if path.suffix.lower() not in (".png", ".svg"):
        raise SbError(f"the map must end in .png or .svg, got {path.name}")

    def proj(n):
        return n.get("project") or n.folder
    notes = {s: n for s, n in sleep.load(v).items()
             if proj(n) not in exclude and (not inv["project"] or proj(n) == inv["project"])}
    if len(notes) < 3:
        raise SbError("too few notes to draw a map")
    by_type = bool(inv["project"])
    grp = [n.get("type") if by_type else proj(n) for n in notes.values()]
    typ = np.array([n.get("type") for n in notes.values()])
    size_of = Counter(grp)
    order = [t for t in TYPES if t in size_of] + sorted(set(size_of) - set(TYPES)) if by_type else [g for g, _ in size_of.most_common()]
    color = {g: NEON[k] if k < len(NEON) else OTHER for k, g in enumerate(order)}
    rng = np.random.default_rng(7)
    Y, edges, deg = _layout(np, notes, grp, rng)
    G_ = np.array(grp)
    sc = {x[0]: x for x in usage.scores(v, inv["day"])}
    cand = {s for s, *_ in usage.forget_candidates(v, inv["day"], list(sc.values()))}
    up = np.array([max(sc[s][4] - (inv["day"] - sc[s][5]).days, 0.0) if s in sc else 0.0 for s in notes])  # days left before fading
    rel = np.where([s in cand for s in notes], 0.0, np.clip(up / max(np.percentile(up, 95), 1e-9), 0, 1) ** 0.7)
    alpha = 0.12 + 0.83 * rel  # opacity = relevance; forget candidates stay faint

    E = 1.08
    plt.rcParams["font.family"] = ["Avenir Next", "Helvetica Neue", "DejaVu Sans"]
    fig = plt.figure(figsize=(20, 20), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(-E, E)
    ax.set_ylim(-E, E)
    ax.set_aspect("equal")
    ax.axis("off")

    R = 800
    gx = np.linspace(-E, E, R)
    XX, YY = np.meshgrid(gx, gx)
    kx = np.fft.fftfreq(R, d=gx[1] - gx[0])
    blur = np.exp(-2 * (np.pi * 0.045) ** 2 * (kx[:, None] ** 2 + kx[None, :] ** 2))
    img, dens = np.zeros((R, R, 3)), {}
    for g in order:
        m = G_ == g
        if m.sum() < 3:
            continue
        H, _, _ = np.histogram2d(Y[m, 1], Y[m, 0], bins=R, range=[[-E, E], [-E, E]])
        d = np.real(np.fft.ifft2(np.fft.fft2(H) * blur)).clip(0)
        dens[g] = d / (d.max() or 1)
        img += np.array(to_rgb(color[g]))[None, None, :] * (dens[g] ** 0.8)[..., None] * 0.30
    vig = (1 - 0.6 * np.clip(np.sqrt(XX ** 2 + YY ** 2) / 1.5, 0, 1) ** 2)[..., None]
    img = np.clip((1 - (1 - np.array(to_rgb(BG))) * np.exp(-img * 1.6)) * vig, 0, 1)
    ax.imshow(img, extent=(-E, E, -E, E), origin="lower", zorder=0, interpolation="bicubic")
    for rr in np.arange(0.2, 1.6, 0.2):
        ax.add_patch(plt.Circle((0.05, 0.04), rr, fill=False, ec="#8fa3ff", lw=0.4, alpha=0.05, zorder=0.5))
    sx, sy = rng.uniform(-E, E, (2, 1400))
    ax.scatter(sx, sy, s=rng.uniform(0.05, 1.3, 1400), c="white", alpha=0.28, lw=0, zorder=1)
    for g, d in dens.items():
        for lv, a, lw in ((0.10, 0.18, 0.45), (0.28, 0.26, 0.55), (0.55, 0.34, 0.65)):
            ax.contour(XX, YY, d, levels=[lv], colors=[color[g]], linewidths=lw, alpha=a, zorder=1.5)

    tt = np.linspace(0, 1, 15)[:, None]
    tm = (tt[:-1] + tt[1:]) / 2
    segs, cols, lws, gsegs, gcols = [], [], [], [], []
    for i, j in edges:
        a, b = Y[i], Y[j]
        d = b - a
        c = (a + b) / 2 + np.array([-d[1], d[0]]) * 0.2
        pts = (1 - tt) ** 2 * a + 2 * (1 - tt) * tt * c + tt ** 2 * b
        far = G_[i] != G_[j]
        rgb = (1 - tm) * np.array(to_rgb(color[G_[i]])) + tm * np.array(to_rgb(color[G_[j]]))
        al = (0.20 if far else 0.13) * (1 - 0.65 * np.sin(np.pi * tm)) * (0.3 + 0.7 * (rel[i] + rel[j]) / 2)
        s_ = np.stack([pts[:-1], pts[1:]], axis=1)
        segs.extend(s_)
        cols.extend(np.hstack([rgb, al]))
        lws.extend([0.5 if far else 0.35] * len(tm))
        if far:
            gsegs.extend(s_)
            gcols.extend(np.hstack([rgb, np.full_like(al, 0.035)]))
    if gsegs:
        ax.add_collection(LineCollection(gsegs, colors=gcols, linewidths=2.6, zorder=2, capstyle="round"))
    if segs:
        ax.add_collection(LineCollection(segs, colors=cols, linewidths=lws, zorder=2.1, capstyle="round"))

    size = 5 + 9 * np.sqrt(deg)
    ncol = np.array([to_rgb(color[g]) for g in grp])
    def rgba(rgb, a):
        return np.hstack([rgb, np.asarray(a, float).reshape(-1, 1)])
    for f, a in ((14, 0.018), (7, 0.04), (3.2, 0.09)):
        ax.scatter(Y[:, 0], Y[:, 1], s=size * f, c=rgba(ncol, a * (0.2 + 0.8 * rel)), lw=0, zorder=3)
    for t, (mk, sf) in SHAPES.items():
        m = typ == t
        if not m.any():
            continue
        if t == "question":
            ax.scatter(Y[m, 0], Y[m, 1], s=size[m] * sf, facecolors="none", edgecolors=rgba(ncol[m], alpha[m]), lw=0.9, zorder=4)
        else:
            ax.scatter(Y[m, 0], Y[m, 1], s=size[m] * sf, c=rgba(ncol[m], alpha[m]), marker=mk, lw=0, zorder=4)
    rest = np.logical_not(np.isin(typ, list(SHAPES)))
    ax.scatter(Y[rest, 0], Y[rest, 1], s=size[rest], c=rgba(ncol[rest], alpha[rest]), lw=0, zorder=4)
    hub = deg > np.percentile(deg, 96)
    ax.scatter(Y[hub, 0], Y[hub, 1], s=size[hub] * 0.22, c=rgba(np.ones((hub.sum(), 3)), alpha[hub]), lw=0, zorder=5)
    for f, a in ((4.5, 0.55), (9, 0.25)):
        ax.scatter(Y[hub, 0], Y[hub, 1], s=size[hub] * f, facecolors="none", edgecolors=rgba(ncol[hub], a * alpha[hub]), lw=0.5, zorder=5)

    big = max(size_of.values())
    labels = []
    for g in order:
        m = G_ == g
        if m.sum() < 10:
            continue
        fs = 12 + 13 * math.sqrt(m.sum() / big)
        txt = " ".join(str(g).upper())
        labels.append([g, np.median(Y[m], axis=0).astype(float), fs, len(txt) * fs * 0.00064 + 0.03, fs * 0.0024, txt, int(m.sum())])
    for _ in range(300):
        for a in labels:
            for b in labels:
                if a is b:
                    continue
                d = a[1] - b[1]
                ox, oy = (a[3] + b[3]) / 2 - abs(d[0]), (a[4] + b[4]) / 2 - abs(d[1])
                if ox > 0 and oy > 0:
                    if oy < ox:
                        a[1][1] += np.sign(d[1] or 1) * oy * 0.25
                    else:
                        a[1][0] += np.sign(d[0] or 1) * ox * 0.25
    for g, cen, fs, _, _, txt, k in labels:
        col = np.array(to_rgb(color[g]))
        neon = [pe.withStroke(linewidth=22, foreground=col, alpha=0.035), pe.withStroke(linewidth=12, foreground=col, alpha=0.08),
                pe.withStroke(linewidth=5, foreground=col, alpha=0.22), pe.withStroke(linewidth=2, foreground=BG, alpha=0.6), pe.Normal()]
        ax.text(cen[0], cen[1], txt, color=0.55 * col + 0.45, fontsize=fs, weight="bold", ha="center", va="center", zorder=8,
                path_effects=neon)
        ax.text(cen[0], cen[1] - fs * 0.0021, f"{k} notes", color=col, fontsize=fs * 0.36, alpha=0.9, ha="center", va="top",
                zorder=8, path_effects=[pe.withStroke(linewidth=3, foreground=BG, alpha=0.7)])

    title = "Scientific Brain" + (f" · {inv['project']}" if inv["project"] else "")
    ax.text(-1.04, 1.035, title, color="white", fontsize=40, weight="bold", va="top", zorder=10,
            path_effects=[pe.withStroke(linewidth=14, foreground="#8fa3ff", alpha=0.06), pe.Normal()])
    ax.text(-1.04, 0.975, f"{len(notes)} notes  ·  {len(edges)} links  ·  " + (f"{len(order)} types" if by_type else f"{len(order)} projects")
            + f"  ·  {inv['day']}", color="#9aa4bf", fontsize=13, va="top", zorder=10)
    ax.text(-1.04, 0.948, "closeness = closeness in content (links, text) · size = number of links · shape = note type"
            + (" · colour = type" if by_type else " · colour = project")
            + " · opacity = relevance (days left before fading; faint = forget candidate)", color=FAINT, fontsize=10.5, va="top", zorder=10)
    y0, rows = -0.80, 7
    if not by_type:
        for k, g in enumerate(order):
            c_, r_ = divmod(k, rows)
            x, y = -1.03 + c_ * 0.30, y0 - r_ * 0.032
            ax.scatter([x], [y], s=90, c=color[g], lw=0, zorder=10)
            ax.scatter([x], [y], s=400, c=color[g], alpha=0.12, lw=0, zorder=10)
            ax.text(x + 0.022, y, f"{g}  {size_of[g]}", color=SOFT, fontsize=10.5, va="center", zorder=10)
    tz = Counter(typ.tolist())
    x = -1.03 if by_type else -1.03 + -(-len(order) // rows) * 0.30 + 0.02
    for k, (t, (mk, sf)) in enumerate(SHAPES.items()):
        y = y0 - k * 0.032
        c_ = color.get(t, SOFT) if by_type else SOFT
        if t == "question":
            ax.scatter([x], [y], s=80, facecolors="none", edgecolors=c_, lw=1.1, zorder=10)
        else:
            ax.scatter([x], [y], s=80 * sf, c=c_, marker=mk, lw=0, zorder=10)
        ax.text(x + 0.022, y, f"{t}  {tz[t]}", color=SOFT, fontsize=10.5, va="center", zorder=10)
    y = y0 - len(SHAPES) * 0.032
    for k, a in enumerate((0.95, 0.55, 0.12)):
        ax.scatter([x + k * 0.025], [y], s=80, c=[(*to_rgb(SOFT), a)], lw=0, zorder=10)
    ax.text(x + 0.072, y, f"relevance: high → forget candidate ({len(cand & set(notes))})", color=SOFT, fontsize=10.5,
            va="center", zorder=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=BG, dpi=200)
    plt.close(fig)
    return len(notes), len(edges)
