"""Sleep: the plan of candidate lists for the four phases, veto, verified and done."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date

from . import inbox, usage
from .index import fold, prepare
from .links import fm_end
from .vault import SbError, Vault, clean

SLEEPING, SNAP = "sleeping", "snapshot.tsv"  # .sb/ files: running sleep (start date, then shown/verified ids), slug+hash of checked notes
HOT = 20  # verify (b) looks at the HOT most active notes and takes the oldest verified of them
MIN_PROMOTION = 10  # the promotion warning needs at least this many settled lines to mean anything


def started(v: Vault) -> date | None:
    """Start date of the running sleep, None if none runs; an unreadable file counts as ancient."""
    try:
        return date.fromisoformat((v.sb_dir / SLEEPING).read_text(encoding="utf-8").split("\n", 1)[0].strip())
    except OSError:
        return None
    except ValueError:
        return date.min


def begin(v: Vault, day: date, force=False) -> str:
    start, msg = started(v), f"sleep started {day}"
    if start is not None:
        if not force and (day - start).days <= 1:
            raise SbError(f"a sleep has been running since {start}; finish it with 'sleep done' or use --force")
        msg += f" (replaced the sleep from {start})"
    v.sb_dir.mkdir(exist_ok=True)
    (v.sb_dir / SLEEPING).write_text(day.isoformat() + "\n", encoding="utf-8")
    return msg


def digest(n) -> str:
    """Content hash of a note without its verified line, which sleep itself sets."""
    return hashlib.sha1("\n".join(x for x in n.raw.split("\n") if not x.startswith("verified:")).encode()).hexdigest()


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
    """Slug -> content hash at its last check; rows follow renames."""
    m = usage.renames(v)
    return {m.get(s, s): h for s, h in v.rows(SNAP, 2)}


def baseline(v: Vault) -> int:
    """Accept every note in notes/ as checked in its current form, e.g. after an import; `verified` stays untouched.
    Returns the number of notes that were unchecked before."""
    if started(v) is not None:
        raise SbError("a sleep is running; finish it first (sb sleep done)")
    notes, snap = load(v), snapshot(v)
    new = sum(not checked(n, snap) for n in notes.values())
    rows = snap | {s: digest(n) for s, n in notes.items()}  # archived notes keep their row
    (v.sb_dir / SNAP).write_text("".join(f"{s}\t{h}\n" for s, h in sorted(rows.items())), encoding="utf-8")
    return new


def backlog(v: Vault, day: date) -> dict:
    """Work waiting for a sleep: open inbox lines, notes not checked since they changed, forget candidates."""
    notes, snap = load(v), snapshot(v)
    return {"inbox": len(inbox.open_lines(v)), "verify": sum(not checked(n, snap) for n in notes.values()),
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
    lc, notes = v.cfg["lifecycle"], load(v)
    txt = {s: fold(f"{n.get('title')} {n.get('cue')} {n.body}") for s, n in notes.items()}
    df, lim = Counter(w for t in txt.values() for w in set(re.findall(r"\w+", t))), max(3, len(notes) // 2)

    def keys(text, lo=0, cap=None) -> list:
        """Folded words of text that occur in lo..lim notes (stop words fall out), the rarest cap of them."""
        ws = [w for w in prepare(words(text))[0] if lo <= df[w] <= lim]
        return sorted(ws, key=lambda w: df[w])[:cap]

    def near(ws, skip, n, cover=None, locs=("notes",)) -> list:
        return [r for r in idx.search(ws, locs, None, n + 1, cover)[0] if r[0] != skip][:n] if ws else []

    touched, deferred = set(), [0]

    def take(*ids) -> bool:
        """Count the items against the budget; refuse (and count as deferred) when it would overflow."""
        if len(touched | set(ids)) > lc["sleep_budget"]:
            deferred[0] += 1
            return False
        touched.update(ids)
        return True

    # filled in the order consolidate, forget, condense (both capped), verify (a), rest of forget and condense, verify (b);
    # shown in the order of the phases
    sc, ev = usage.scores(v, day), usage.events(v)
    lines = [r for r in sorted(inbox.open_lines(v), key=lambda r: r[1]) if take("inbox:" + r[0])]
    ids = {r[0] for r in lines}
    remember(v, "shown", ids)
    pairs = {frozenset((r[0], h[0][6:])) for r in lines for h in near(keys(r[4]), "inbox:" + r[0], 3, locs=("inbox",))
             if h[0][6:] in ids}
    cands = usage.forget_candidates(v, day, sc)
    forget = [(s, t, a, th) for s, t, a, th in cands[:lc["forget_budget"]] if take(s)]
    snap, last = snapshot(v), usage.last_sleep(v)
    found = {}  # near-duplicate pairs: frozenset -> coverage; after the first sleep only notes changed or created since
    for s, n in notes.items():
        if last and not (s in snap and snap[s] != digest(n)) and str(n.get("created") or "") < last.isoformat():
            continue
        ws = keys(f"{n.get('title')} {n.get('cue')}", 0, 8)
        if ws and (hit := near(ws, s, 1, 0)) and (o := hit[0][0]) in notes:  # coverage is measured below, not by the index
            if o in n.targets or s in notes[o].targets:  # linked parts of one topic are not duplicates
                continue
            back = keys(f"{notes[o].get('title')} {notes[o].get('cue')}", 0, 8) or ws
            # both ways: an overview note contains the words of every note it summarises, yet duplicates neither
            pair, cov = frozenset((s, o)), min(sum(w in txt[o] for w in ws) / len(ws), sum(w in txt[s] for w in back) / len(back))
            if cov >= lc["condense_coverage"]:
                found[pair] = max(cov, found.get(pair, 0))
    found_all = sorted(found, key=lambda p: (len({notes[s].folder for s in p}) > 1, -found[p], sorted(p)))
    dup = [p for p in found_all[:lc["condense_pairs"]] if take(*p)]
    unchecked = sorted((s for s, n in notes.items() if not checked(n, snap)), key=lambda s: notes[s].get("created"), reverse=True)
    changed = sorted(unchecked, key=lambda s: s not in snap)  # edited notes before never-checked ones, newest first
    verify_a = [s for s in changed if take(s)]
    # the caps only keep forget and condense from crowding out the rest; budget left over goes to what they held back
    forget += [(s, t, a, th) for s, t, a, th in cands[lc["forget_budget"]:] if take(s)]
    dup += [p for p in found_all[lc["condense_pairs"]:] if take(*p)]
    hot = sorted((x for x in sc if x[0] not in changed), key=lambda x: -x[3])[:HOT]
    verify_b = [x for x in sorted(hot, key=lambda x: notes[x[0]].get("verified") or "")[:10] if take(x[0])]

    out = [f"# Sleep plan {day}", f"touched: {len(touched)} of {lc['sleep_budget']} (notes and inbox lines), deferred: {deferred[0]}",
           "", f"## 1. Consolidate: {len(lines)} open inbox lines",
           "Settle each with `sb inbox resolve <id> --note <slug>` (extend or create the note first), `--drop` or `--wait`."]
    for id, d, proj, src, text in lines:
        n = inbox.age((id, d), day)
        out.append(f"- `{id}` {n}d {proj}" + (" DUE" if n > lc["inbox_wait_days"] else "") + f": {text}" + (f" (source: {src})" if src else ""))
        out += [f"  similar: {r[0]} ({r[4]})" for r in near(keys(text, 1), "", 3)]
    out += [f"- captured twice, promote: `{min(p)}` ~ `{max(p)}`" for p in sorted(pairs, key=sorted)]
    out += ["", f"## 2. Condense: {len(dup)} near-duplicate pairs", "Merge into one note (archive the other as superseded) or keep apart with sharper titles."]
    for p in dup:
        a, b = sorted(p)
        out.append(f"- {a} ~ {b} (coverage {found[p]:.2f}; {notes[a].folder}, {notes[b].folder})")
    out += ["", f"## 3. Forget: {len(forget)} candidates",
            "Archive with `sb archive <slug> --why faded`, or `sb sleep veto <slug> \"reason\"`."]
    out += [f"- {s} [{t}] A={a:.2f} threshold={th:.2f} last used {max(ev.get(s) or [date.fromisoformat(notes[s].get('created'))])}"
            for s, t, a, th in forget]
    out += ["", f"## 4. Verify: {len(verify_a)} new or changed, {len(verify_b)} by activation",
            "Check numbers against the sources and compare with the neighbours; then `sb sleep verified <slug>`."]
    for s in verify_a:
        n = notes[s]
        out.append(f"- {s} ({'changed' if s in snap else 'new'}) [{n.get('type')}, {n.folder}] sources: " + (", ".join(n.get("sources", [])) or "none"))
        out.append("  compare with: " + ("; ".join(f"{r[0]} ({r[4]})" for r in near(keys(f"{n.get('title')} {n.gist}", 2, 8), s, 3, 0.2)) or "none"))
    out += [f"- {s} (A={a:.2f}, verified {notes[s].get('verified') or 'never'})" for s, _, _, a, _ in verify_b]
    return "\n".join(out) + "\n"


def veto(v: Vault, slug: str, reason: str, day: date):
    if not v.find(slug, ("notes",)):
        raise SbError(f"no note '{slug}' in notes/")
    if not clean(reason):
        raise SbError("a veto needs a reason of one sentence")
    usage.log(v, "veto", slug, day)
    v.append("vetoes.tsv", day.isoformat(), slug, reason)


def mark_verified(v: Vault, slugs, day: date) -> list:
    """Set verified: <day> in the frontmatter, leave updated alone; returns the paths."""
    rels = [v.find(s, ("notes",)) or "" for s in slugs]
    if (bad := [s for s, r in zip(slugs, rels) if not r]):
        raise SbError(f"no note '{bad[0]}' in notes/")
    for rel in rels:
        parts = (v.root / rel).read_text(encoding="utf-8").split("\n")
        end = fm_end(parts)
        i = next((i for i in range(1, end) if parts[i].startswith("verified:")), None)
        lo, hi = (end, end) if i is None else (i, i + 1)
        parts[lo:hi] = [f"verified: {day}"]
        (v.root / rel).write_text("\n".join(parts), encoding="utf-8")
    remember(v, "verified", slugs)
    return rels


def finish(v: Vault, summary: str, day: date) -> list:
    """End the sleep: expire due inbox lines the plan showed, snapshot the checked notes, log the sleep; returns the output lines."""
    start = started(v)
    if start is None:
        raise SbError("no sleep is running (sb sleep begin)")
    inbox.expire_inbox(v, day, remembered(v, "shown"))
    since = start.isoformat()
    booked = [o for _, d, o in v.rows(inbox.DONE, 3) if d >= since and o != "wait"]
    use = Counter(k for d, c, k, _ in v.rows("usage.tsv", 4) if d >= since and c == "sleep")
    prom = sum(o.startswith("note:") for o in booked)
    v.append("sleep.tsv", day, len(booked), prom, use["archive"], use["create"], use["veto"], summary)
    m, notes = usage.renames(v), load(v)
    snap, done = snapshot(v), {m.get(s, s) for s in remembered(v, "verified")}
    rows = dict(snap)  # archived notes keep their row, so a restored note is compared, not trusted
    for s, n in notes.items():  # verified in this sleep, or checked before any snapshot: the current hash
        if s in done or (s not in snap and checked(n, snap)):
            rows[s] = digest(n)
    (v.sb_dir / SNAP).write_text("".join(f"{s}\t{h}\n" for s, h in sorted(rows.items())), encoding="utf-8")
    (v.sb_dir / SLEEPING).unlink()
    rate, top = prom / len(booked) if booked else 0, v.cfg["lifecycle"]["max_promotion_rate"]
    return ([f"warning: {prom} of {len(booked)} settled inbox lines were promoted ({rate:.0%}, limit {top:.0%}); capture less, or check the rank"]
            * (rate > top and len(booked) >= MIN_PROMOTION)
            + [f"slept {day}: {len(booked)} inbox lines settled ({prom} promoted), {use['archive']} archived, {use['create']} created, {use['veto']} vetoes"])
