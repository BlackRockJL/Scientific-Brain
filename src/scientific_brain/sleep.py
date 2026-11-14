"""Sleep: the plan of candidate lists for the five phases, veto, unrelated, verified and done."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date
from itertools import permutations

from . import inbox, usage
from .index import fold, prepare
from .links import fm_end
from .vault import SbError, Vault, clean

SLEEPING, SNAP = "sleeping", "snapshot.tsv"  # .sb/ files: running sleep (start date, then shown/planned/verified ids), slug+hash of checked notes
HOT = 20  # verify (b) looks at the HOT most active notes and takes the oldest verified of them
UNRELATED = "unrelated.tsv"  # .sb/ file: date, slug, slug, reason of every pair judged unrelated
SEEDS, FAR_POOL = 20, 50  # dream: notes that seed the near pairs; most active concepts and derivations for the far pair
MIN_PROMOTION = 10  # the promotion warning needs at least this many settled lines to mean anything
SEED_MIN = 30  # `baseline --seed`: days before the first seeded note can fade


def started(v: Vault) -> date | None:
    """Start date of the running sleep, None if none runs; an unreadable file counts as ancient."""
    try:
        return date.fromisoformat((v.sb_dir / SLEEPING).read_text(encoding="utf-8").split("\n", 1)[0].strip())
    except OSError:
        return None
    except ValueError:
        return date.min


def stale(v: Vault, day: date) -> bool:
    """A marker more than a day old was left behind: it blocks nothing and tags nothing as sleep."""
    return (start := started(v)) is not None and (day - start).days > 1


def begin(v: Vault, day: date, force=False) -> str:
    start, msg = started(v), f"sleep started {day}"
    if start is not None:
        if not force and not stale(v, day):
            raise SbError(f"a sleep has been running since {start}; finish it with 'sleep done' or use --force")
        release(v, start)
        msg += f" (replaced the sleep from {start})"
    v.sb_dir.mkdir(exist_ok=True)
    (v.sb_dir / SLEEPING).write_text(day.isoformat() + "\n", encoding="utf-8")
    v.append("usage.tsv", day.isoformat(), "sleep", "begin", "-")  # the sleep's usage starts after this row
    remember(v, "donerows", [len(v.rows(inbox.DONE, 3))])  # bookings and rejections of this sleep follow these rows,
    remember(v, "unrelrows", [len(v.rows(UNRELATED, 4))])  # so a second sleep on one day does not count the first's
    return msg


def since_begin(v: Vault, name: str, n: int, kind: str, at: int, start: date) -> list:
    """Rows of .sb/<name> written in the running sleep: after the row count its begin noted under kind; a marker
    without it (sb <= 0.3.0) falls back to rows dated from the sleep's date on (column at)."""
    rows, mark = v.rows(name, n), [int(x) for x in remembered(v, kind) if x.isdigit()]
    if mark:
        return rows[min(mark):]
    return [] if start == date.min else [r for r in rows if r[at] >= start.isoformat()]  # unreadable marker: no history


def booked_now(v: Vault, start: date) -> list:
    """(id, date, outcome) of the inbox bookings of the running sleep."""
    return since_begin(v, inbox.DONE, 3, "donerows", 1, start)


def rejected_now(v: Vault, start: date) -> set:
    """Pairs (frozensets of current slugs) judged unrelated in the running sleep."""
    m = usage.renames(v)
    return {frozenset((m.get(a, a), m.get(b, b))) for _, a, b, _ in since_begin(v, UNRELATED, 4, "unrelrows", 0, start)}


def pending(v: Vault, start: date) -> list:
    """(row number, row) of the usage logged as sleep since the running sleep began (its begin row, else its date)."""
    rows = v.rows("usage.tsv", 4)
    first = max((i for i, r in enumerate(rows) if r[1:3] == ["sleep", "begin"] and (start == date.min or r[0] == start.isoformat())),
                default=None)  # an unreadable marker has no date: its last begin row stands in
    if first is None and start == date.min:
        return []
    return [(i, r) for i, r in enumerate(rows)
            if r[1] == "sleep" and (i > first if first is not None else r[0] >= start.isoformat())]


def release(v: Vault, start: date):
    """Use logged during the sleep counts again for notes the plan did not list (the sleeper read only those)."""
    if start == date.min:
        return
    m, cur = usage.renames(v), usage.renamer(v)
    held = {m.get(s, s) for s in remembered(v, "planned") | remembered(v, "verified")}
    for i, (d, _, kind, slug) in pending(v, start):
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        if kind in ("show", "recall", "edit") and (now := cur(slug, i, day)) not in held:  # veto counts anyway, create is dated
            v.append("usage.tsv", d, "work", kind, now)


def digest(n) -> str:
    """Content hash of a note without its frontmatter `verified:` field, which sleep itself sets."""
    parts = n.raw.split("\n")
    end = fm_end(parts)
    return hashlib.sha1("\n".join(x for i, x in enumerate(parts) if not (0 < i < end and x.startswith("verified:"))).encode()).hexdigest()


def checked(n, snap: dict) -> bool:
    """Unchanged since its last check: the snapshot hash matches, or, never snapshotted, verified on or after updated."""
    if (h := snap.get(n.slug)) is not None:
        return h == digest(n)
    return bool(n.get("verified")) and str(n.get("verified")) >= str(n.get("updated") or "")


def remember(v: Vault, kind: str, ids):
    """Note in the running sleep's marker which inbox lines a plan showed or which notes were verified."""
    if started(v) is not None:
        with open(v.sb_dir / SLEEPING, "a", encoding="utf-8") as f:
            f.writelines(f"{kind}\t{i}\n" for i in ids)


def remembered(v: Vault, kind: str) -> set:
    try:
        rows = (v.sb_dir / SLEEPING).read_text(encoding="utf-8").split("\n")[1:]
    except OSError:
        return set()
    return {r.split("\t", 1)[1] for r in rows if r.startswith(kind + "\t")}


def snapshot(v: Vault) -> dict:
    """Slug -> content hash at its last check. A row follows only the renames made after it was written (third column:
    rows of renames.tsv then), so a note created under a freed name keeps its own row; an old row without it follows
    all renames unless the target has a row of its own."""
    ren, rows = v.rows("renames.tsv", 5), [r for r in v.rows(SNAP, 3) if r[0]]
    m, own, out = usage.renames(v), {s for s, _, _ in rows}, {}
    for s, h, c in rows:
        if c.isascii() and c.isdigit():
            for old, new, *_ in ren[int(c):]:
                s = new if s == old else s
        elif (t := m.get(s, s)) not in own:
            s = t
        out[s] = h
    return out


def save_snapshot(v: Vault, rows: dict):
    """Write the rows with the number of renames they already account for."""
    n = len(v.rows("renames.tsv", 5))
    (v.sb_dir / SNAP).write_text("".join(f"{s}\t{h}\t{n}\n" for s, h in sorted(rows.items())), encoding="utf-8")


def current(m: dict, notes, s: str) -> str:
    """Slug a name stands for now: itself while a note has it (a freed name may be reused), else its rename target."""
    return s if s in notes else m.get(s, s)


def baseline(v: Vault, day: date, seed_days: int | None = None) -> tuple:
    """Accept every note in notes/ as checked in its current form, e.g. after an import; `verified` stays untouched.
    With seed_days, notes without any use (index-found edits do not count) get one start use today, weighted so that they fade one by one between
    SEED_MIN and seed_days days from now, the least recently updated first. Returns (unchecked before, seeded)."""
    if started(v) is not None and not stale(v, day):
        raise SbError("a sleep is running; finish it first (sb sleep done)")
    if seed_days is not None and seed_days <= SEED_MIN:
        raise SbError(f"--seed needs more than {SEED_MIN} days")
    notes, snap = load(v), snapshot(v)
    new = sum(not checked(n, snap) for n in notes.values())
    rows = snap | {s: digest(n) for s, n in notes.items()}  # archived notes keep their row
    save_snapshot(v, rows)
    if seed_days is None:
        return new, 0
    used = set(usage.events(v, ignore_sync=True)) | set(usage.seeds(v))  # index-found edits are no use; seeded once is enough
    bare = sorted((str(n.get("updated") or n.get("created") or ""), s) for s, n in notes.items() if s not in used)
    for i, (_, s) in enumerate(bare):
        t = SEED_MIN + (seed_days - SEED_MIN) * (i / (len(bare) - 1) if len(bare) > 1 else 1)
        # one use of weight w fades after T days when w^2 (H + 1) = T + 1
        v.append(usage.SEEDS, day.isoformat(), s, f"{((t + 1) / (usage.OLD_H.get(notes[s].get('type'), 90) + 1)) ** 0.5:.6f}")
    return new, len(bare)


def backlog(v: Vault, day: date) -> dict:
    """Work waiting for a sleep: undecided inbox lines, notes not checked since they changed, forget candidates."""
    notes, snap = load(v), snapshot(v)
    return {"inbox": len(inbox.undecided(v, day)), "verify": sum(not checked(n, snap) for n in notes.values()),
            "forget": len(usage.forget_candidates(v, day))}


def load(v: Vault) -> dict:
    out = {}
    for rel in v.scan("notes"):
        try:
            n = v.read(rel)
        except SbError:
            continue
        out[n.slug] = n
    return out


def words(text) -> list:
    return list(dict.fromkeys(re.findall(r"\w+", text)))


def plan(v: Vault, idx, day: date) -> str:
    lc, notes, budget = v.cfg["lifecycle"], load(v), usage.budget(v)
    live = (st := started(v)) is not None and not stale(v, day)  # a forgotten marker is no running sleep: remember nothing, read nothing

    def rem(kind, ids):
        if live:
            remember(v, kind, ids)
    txt = {s: fold(f"{n.get('title')} {n.get('cue')} {n.body}") for s, n in notes.items()}
    df, lim = Counter(w for t in txt.values() for w in set(re.findall(r"\w+", t))), max(3, len(notes) // 2)

    def keys(text, lo=0, cap=None) -> list:
        """Folded words of text that occur in lo..lim notes (stop words fall out), the rarest cap of them."""
        ws = [w for w in prepare(words(text))[0] if lo <= df[w] <= lim]
        return sorted(ws, key=lambda w: df[w])[:cap]

    def near(ws, skip, n, cover=None, locs=("notes",)) -> list:
        return [r for r in idx.search(ws, locs, None, n + 1, cover)[0] if r[0] != skip][:n] if ws else []

    touched, deferred, listed = set(), set(), set()  # listed: notes the plan names, neighbours included

    def take(*ids) -> bool:
        """Count the items against the budget; refuse (and note as deferred) when it would overflow."""
        if len(touched | set(ids)) > budget:
            deferred.update(ids)
            return False
        touched.update(ids)
        return True

    # filled in the order consolidate, forget, condense (both capped by their share of the queues), verify (a), rest of
    # forget and condense, verify (b); shown in the order of the phases
    s0 = usage.learned(v, day)
    sc = usage.scores(v, day, s0)
    last_use = {x[0]: x[5] for x in sc}
    lines = [r for r in sorted(inbox.open_lines(v), key=lambda r: r[1]) if take("inbox:" + r[0])]
    ids = {r[0] for r in lines}
    rem("shown", ids)
    pairs = {frozenset((r[0], h[0][6:])) for r in lines for h in near(keys(r[4]), "inbox:" + r[0], 3, locs=("inbox",))
             if h[0][6:] in ids}
    cands = usage.forget_candidates(v, day, sc)
    snap, last = snapshot(v), usage.last_sleep(v)
    def fresh(s, n) -> bool:
        """Changed or created since the last sleep (everything before the first); a note without a snapshot row counts
        as changed when updated after it."""
        return (not last or (s in snap and snap[s] != digest(n)) or str(n.get("created") or "") >= last.isoformat()
                or (s not in snap and str(n.get("updated") or "") > last.isoformat()))

    def cover(s, o, ws=None) -> float:
        """Share of the title and cue words of s found in o, the smaller of both ways: an overview note contains the
        words of every note it summarises, yet duplicates neither."""
        ws = ws or keys(f"{notes[s].get('title')} {notes[s].get('cue')}", 0, 8)
        back = keys(f"{notes[o].get('title')} {notes[o].get('cue')}", 0, 8) or ws
        return min(sum(w in txt[o] for w in ws) / len(ws), sum(w in txt[s] for w in back) / len(back)) if ws else 0.0

    found = {}  # near-duplicate pairs: frozenset -> coverage; after the first sleep only notes changed or created since
    for s, n in notes.items():
        if not fresh(s, n):
            continue
        ws = keys(f"{n.get('title')} {n.get('cue')}", 0, 8)
        if ws and (hit := near(ws, s, 1, 0)) and (o := hit[0][0]) in notes:  # coverage is measured below, not by the index
            if o in n.targets or s in notes[o].targets:  # linked parts of one topic are not duplicates
                continue
            pair, cov = frozenset((s, o)), cover(s, o, ws)
            if cov >= lc["condense_coverage"]:
                found[pair] = max(cov, found.get(pair, 0))
    found_all = sorted(found, key=lambda p: (len({notes[s].folder for s in p}) > 1, -found[p], sorted(p)))
    unchecked = sorted(sorted(s for s, n in notes.items() if not checked(n, snap)), key=lambda s: str(notes[s].get("created")), reverse=True)
    changed = sorted(unchecked, key=lambda s: s not in snap)  # edited notes before never-checked ones, newest first
    room = max(budget - len(touched), 0)
    queue = len(cands) + 2 * len(found_all) + len(changed)
    cap_f, cap_c = (-(-room * len(cands) // queue), -(-room * len(found_all) // queue)) if queue else (0, 0)
    forget = [x for x in cands[:cap_f] if take(x[0])]
    dup = [p for p in found_all[:cap_c] if take(*p)]
    verify_a = [s for s in changed if take(s)]
    # the caps only keep forget and condense from crowding out the rest; budget left over goes to what they held back
    forget += [x for x in cands[cap_f:] if take(x[0])]
    dup += [p for p in found_all[cap_c:] if take(*p)]
    hot = sorted((x for x in sc if x[0] not in changed), key=usage.active)[:HOT]
    verify_b = [x for x in sorted(hot, key=lambda x: (str(notes[x[0]].get("verified") or ""), x[0]))[:10] if take(x[0])]

    if again := live and bool(remembered(v, "dreamed") or remembered(v, "dream")):  # one set per sleep: a new plan repeats the open pairs
        dream = held_pairs(v, notes, remembered(v, "dream"))
    else:
        dream = dream_pairs(v, notes, sc, day, found, keys, near, cover, fresh, s0)
        rem("dreamed", ["1"])  # also when no pair was found
        rem("dream", (f"{a}|{b}" for a, b, _ in dream))
        rem("dreamkind", (f"{a}|{b}|{k}" for a, b, k in dream))
    rem("work", sorted([f"inbox:{r[0]}" for r in lines] + [f"forget:{x[0]}" for x in forget] + [f"verify:{x}" for x in verify_a]))
    rem("size", [str(len(touched))])
    out = [f"# Sleep plan {day}", f"touched: {len(touched)} of {budget} (notes and inbox lines), deferred: {len(deferred - touched)}",
           "", f"## 1. Consolidate: {len(lines)} open inbox lines",
           "Settle each with `sb inbox resolve <id> --note <slug>` (extend or create the note first), `--drop` or `--wait`."]
    for id, d, proj, src, text in lines:
        n = inbox.age((id, d), day)
        out.append(f"- `{id}` {n}d {proj}" + (" DUE" if n > lc["inbox_wait_days"] else "") + f": {text}" + (f" (source: {src})" if src else ""))
        sim = near(keys(text, 1), "", 3)
        listed.update(r[0] for r in sim)
        out += [f"  similar: {r[0]} ({r[4]})" for r in sim]
    out += [f"- captured twice, promote: `{min(p)}` ~ `{max(p)}`" for p in sorted(pairs, key=sorted)]
    out += ["", f"## 2. Condense: {len(dup)} near-duplicate pairs", "Merge into one note (archive the other as superseded) or keep apart with sharper titles."]
    for p in dup:
        a, b = sorted(p)
        out.append(f"- {a} ~ {b} (coverage {found[p]:.2f}; {notes[a].folder}, {notes[b].folder})")
    out += ["", f"## 3. Forget: {len(forget)} candidates",
            "Archive with `sb archive <slug> --why faded`, or `sb sleep veto <slug> \"reason\"`."]
    out += [f"- {s} [{t}] R={r:.2f} stability {S:.0f} days, last used {last_use.get(s)}" for s, t, r, S in forget]
    out += ["", f"## 4. Verify: {len(verify_a)} new or changed, {len(verify_b)} most active",
            "Check numbers against the sources and compare with the neighbours; then `sb sleep verified <slug>`. "
            "Mark only what you checked: that a note came from an inbox line is no check, and a note you could not "
            "test stays unverified and returns."]
    for s in verify_a:
        n = notes[s]
        out.append(f"- {s} ({'changed' if s in snap else 'new'}) [{n.get('type')}, {n.folder}] sources: " + (", ".join(n.get("sources", [])) or "none"))
        comp = near(keys(f"{n.get('title')} {n.gist}", 2, 8), s, 3, 0.2)
        listed.update(r[0] for r in comp)
        out.append("  compare with: " + ("; ".join(f"{r[0]} ({r[4]})" for r in comp) or "none"))
    out += [f"- {x[0]} (R={x[3]:.2f}, verified {notes[x[0]].get('verified') or 'never'})" for x in verify_b]
    out += ["", f"## 5. Dream: {len(dream)} candidate pairs" + (" (still open from this sleep's first plan)" if again else ""),
            "For each pair read both notes. Link them when they meet in substance: one names, uses, implements, corrects, "
            "measures or contradicts what the other states, or both share a mechanism, quantity or method. Add the link in both "
            "notes under `## Links` with a half-sentence why. If the connection is real but unnamed and has rank, create a question "
            "or concept note linking both. Use `sb sleep unrelated <a> <b> \"reason\"` only when they share words, not substance. "
            "The far pair needs a shared mechanism, quantity or method; a hunch with rank becomes a question note."]
    for a, b, kind in dream:
        out.append(f"- {a} ~ {b} ({kind}; {notes[a].folder}, {notes[b].folder})")
        out += [f"  {s} [{notes[s].get('type')}]: {sentence(notes[s].gist)}" for s in (a, b)]
    rem("planned", sorted(listed | {t for t in touched if not t.startswith("inbox:")} | {s for a, b, _ in dream for s in (a, b)}))
    return "\n".join(out) + "\n"


def sentence(text: str, cap=200) -> str:
    """First sentence, cut at cap characters."""
    s = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return s if len(s) <= cap else s[:cap - 3].rstrip() + "..."


def rejected(v: Vault) -> dict:
    """Pair (frozenset of current slugs) -> dates it was judged unrelated; rows follow renames."""
    m, out = usage.renames(v), {}
    for d, a, b, _ in v.rows(UNRELATED, 4):
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        out.setdefault(frozenset((m.get(a, a), m.get(b, b))), []).append(day)
    return out


def locked(v: Vault, day: date, s0: dict | None = None) -> set:
    """Pairs judged unrelated that still rest: like a note, a pair rests for its stability (the vault's initial one,
    grown by each further rejection) after its last rejection."""
    base = (s0 or usage.learned(v, day))[""]
    out = set()
    for p, ds in rejected(v).items():
        S, last = usage.replay({d: 1.0 for d in ds}, None, base)
        if usage.retrievability(S, (day - last).days) >= 0.5 - usage.EPS:
            out.add(p)
    return out


def held_pairs(v: Vault, notes, held) -> list:
    """(a, b, kind) of the pairs the sleep's first plan showed that are still open: both notes there, not linked,
    not judged unrelated in this sleep."""
    m = usage.renames(v)
    kinds = {p.rsplit("|", 1)[0]: p.rsplit("|", 1)[1] for p in remembered(v, "dreamkind")}
    nope, out = rejected_now(v, started(v)), []
    for p in held:
        a, b = (m.get(s, s) for s in p.split("|"))
        if (a != b and a in notes and b in notes and b not in notes[a].targets and a not in notes[b].targets
                and frozenset((a, b)) not in nope):
            out.append((a, b, kinds.get(p, "shown before")))
    order = {"across projects": 0, "same project": 1, "far": 3}  # near pairs first, the far pair last
    return sorted(out, key=lambda x: (order.get(x[2], 2), x[0], x[1]))


def dream_pairs(v: Vault, notes, sc, day, found, keys, near, cover, fresh, s0=None) -> list:
    """(a, b, kind) of unlinked notes worth a look: near pairs across projects and within one, seeded by new, changed and
    active notes, then one far pair. Duplicates, saturated notes, locked pairs stay out; a note appears once."""
    lc, lim = v.cfg["lifecycle"], v.cfg["limits"]["links"]
    if not (want := lc["dream_pairs"]):
        return []
    lock, used, out = locked(v, day, s0), set(), []

    def free(a, b) -> bool:
        return (a != b and a in notes and b in notes and b not in notes[a].targets and a not in notes[b].targets
                and frozenset((a, b)) not in found and frozenset((a, b)) not in lock
                and max(len(notes[a].targets), len(notes[b].targets)) < lim and cover(a, b) < lc["condense_coverage"])

    def pick(pool, kind, k, cap):
        for a, b in pool:
            if k <= 0 or len(out) >= cap:
                return
            if not used & {a, b}:
                used.update((a, b))
                out.append((*sorted((a, b)), kind))
                k -= 1

    new = sorted((s for s, n in notes.items() if fresh(s, n)), key=lambda s: (str(notes[s].get("created")), s), reverse=True)
    seeds = list(dict.fromkeys(new + [x[0] for x in sorted(sc, key=usage.active)]))[:SEEDS]
    across, within, seen = [], [], set()
    for s in seeds:
        n = notes[s]
        for r in near(keys(f"{n.get('title')} {n.get('cue')} {n.gist}", 0, 8), s, 10, 0.2):
            if (p := frozenset((s, r[0]))) not in seen and free(s, r[0]):
                seen.add(p)
                (across if n.folder != notes[r[0]].folder else within).append((s, r[0]))
    near_n = max(want - 1, 1)  # the last place is the far pair
    pick(across, "across projects", (near_n + 1) // 2, near_n)
    pick(within, "same project", near_n // 2, near_n)
    pick(across, "across projects", near_n, near_n)  # a kind with too few candidates is filled up by the other
    pick(within, "same project", near_n, near_n)
    if want > 1:
        top = [x[0] for x in sorted(sc, key=usage.active) if x[1] in ("concept", "derivation") and x[0] in notes][:FAR_POOL]
        ks = {}  # slug -> title and cue keys

        def key_set(s):
            return ks.setdefault(s, set(keys(f"{notes[s].get('title')} {notes[s].get('cue')}")))

        far = sorted(((a, b) for a in top for b, n in notes.items() if n.get("type") in ("finding", "question")
                      and n.folder != notes[a].folder and len(key_set(a) & key_set(b)) <= 1),
                     key=lambda p: hashlib.sha256(f"{day}{p[0]}{p[1]}".encode()).hexdigest())
        for a, b in far:
            if not used & {a, b} and free(a, b):
                used.update((a, b))
                out.append((a, b, "far"))
                break
        else:
            pick(across, "across projects", want, want)
            pick(within, "same project", want, want)
    return out


def veto(v: Vault, slug: str, reason: str, day: date):
    if not v.find(slug, ("notes",)):
        raise SbError(f"no note '{slug}' in notes/")
    if not clean(reason):
        raise SbError("a veto needs a reason of one sentence")
    usage.log(v, "veto", slug, day)
    v.append("vetoes.tsv", day.isoformat(), slug, reason)


def unrelated(v: Vault, a: str, b: str, reason: str, day: date):
    """Log a dream pair as unrelated; it is locked for a while (see `locked`)."""
    for s in (a, b):
        if not v.find(s, ("notes",)):
            raise SbError(f"no note '{s}' in notes/")
    if a == b:
        raise SbError("a pair needs two different notes")
    if not clean(reason):
        raise SbError("unrelated needs a reason of one sentence")
    v.append(UNRELATED, day.isoformat(), *sorted((a, b)), reason)


def mark_verified(v: Vault, slugs, day: date) -> list:
    """Set verified: <day> in the frontmatter, leave updated alone; returns the paths."""
    rels = [v.find(s, ("notes",)) or "" for s in slugs]
    if (bad := [s for s, r in zip(slugs, rels) if not r]):
        raise SbError(f"no note '{bad[0]}' in notes/")
    texts = [(v.root / rel).read_text(encoding="utf-8").split("\n") for rel in rels]
    for rel, parts in zip(rels, texts):
        if not fm_end(parts):
            raise SbError(f"{rel}: the frontmatter is not closed; fix the note first")
    for rel, parts in zip(rels, texts):
        end = fm_end(parts)
        i = next((i for i in range(1, end) if parts[i].startswith("verified:")), None)
        lo, hi = (end, end) if i is None else (i, i + 1)
        parts[lo:hi] = [f"verified: {day}"]
        (v.root / rel).write_text("\n".join(parts), encoding="utf-8")
    remember(v, "verified", slugs)
    return rels


def leftover(v: Vault, day: date, start: date) -> tuple:
    """(size, left) of the running sleep: the largest plan it printed, and the work items of its plans still waiting
    (an inbox line not booked in this sleep, a forget candidate neither archived nor vetoed, a changed note unverified).
    Called before due lines expire, so an expired line counts as left."""
    work = remembered(v, "work")
    size = max((int(x) for x in remembered(v, "size") if x.isascii() and x.isdigit()), default=0)
    if not work:
        return size, 0
    m = usage.renames(v)
    booked = {i for i, d, o in booked_now(v, start) if o != "expired"}
    cands = {s for s, *_ in usage.forget_candidates(v, day)}
    notes, snap = load(v), snapshot(v)
    done = {current(m, notes, s) for s in remembered(v, "verified")}
    left = 0
    for item in work:
        kind, _, x = item.partition(":")
        if kind == "inbox":
            left += x not in booked
        elif kind == "forget":
            left += current(m, notes, x) in cands
        elif kind == "verify":
            s = current(m, notes, x)
            left += s in notes and s not in done and not checked(notes[s], snap)
    return size, min(left, size)


def finish(v: Vault, summary: str, day: date) -> list:
    """End the sleep: expire due inbox lines the plan showed, snapshot the checked notes, log the sleep; returns the output lines."""
    start = started(v)
    if start is None:
        raise SbError("no sleep is running (sb sleep begin)")
    planned, left = leftover(v, day, start)
    expired = inbox.expire_inbox(v, day, remembered(v, "shown"), {i for i, _, _ in booked_now(v, start)})
    pairs, pend = remembered(v, "dream"), pending(v, start)
    booked = [o for _, d, o in booked_now(v, start) if o not in ("wait", "expired")]
    use = Counter(r[2] for _, r in pend)
    prom = sum(o.startswith("note:") for o in booked)
    v.append("sleep.tsv", usage.stamp(day), len(booked), prom, use["archive"], use["create"], use["veto"], len(expired), summary,
             planned, left)
    release(v, start)
    m, notes = usage.renames(v), load(v)
    snap, done = snapshot(v), {current(m, notes, s) for s in remembered(v, "verified")}
    rows = dict(snap)  # archived notes keep their row, so a restored note is compared, not trusted
    for s, n in notes.items():  # verified in this sleep, or checked before any snapshot: the current hash
        if s in done or (s not in snap and checked(n, snap)):
            rows[s] = digest(n)
    save_snapshot(v, rows)
    (v.sb_dir / SLEEPING).unlink()
    rate, top = prom / len(booked) if booked else 0, v.cfg["lifecycle"]["max_promotion_rate"]
    shown = {frozenset(m.get(s, s) for s in p.split("|")) for p in pairs}
    linked = {p for p in shown if len(p) == 2 and all(s in notes for s in p) and any(b in notes[a].targets for a, b in permutations(p))}
    nope = rejected_now(v, start) & (shown - linked)
    text = {r[0]: r[4] for r in inbox.lines(v)}
    return ([f"warning: {prom} of {len(booked)} settled inbox lines were promoted ({rate:.0%}, limit {top:.0%}); capture less, or check the rank"]
            * (rate > top and len(booked) >= MIN_PROMOTION)
            + [f"slept {day}: {len(booked)} inbox lines settled ({prom} promoted), {len(expired)} expired, {use['archive']} archived, "
               f"{use['create']} created, {use['veto']} vetoes"
               + (f"; {left} of {planned} planned items left undone" if left else "")]
            + [f"expired {i}: {sentence(text.get(i, ''), 80)}" for i in expired]
            + [f"dream: {len(linked)} linked, {len(nope)} unrelated, {len(shown) - len(linked) - len(nope)} open of {len(shown)}"] * bool(shown))
