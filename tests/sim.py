"""Simulation helpers for the scenario tests: a big vault, a mechanical sleeper and a working day.

Everything goes through `helpers.sb` (in-process CLI). The date of a day is `SB_TODAY`, set by the caller. The sleeper
follows plugin/agents/sleeper.md step by step; where the agent would judge, it follows a fixed rule driven by the rng.
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

from helpers import add_note, init_vault, sb

from scientific_brain.vault import Vault

PROJECTS = ["heat", "fft", "mag", "damage", "solver", "mesh", "tools", "_general"]
TYPES = ["finding"] * 9 + ["concept"] * 3 + ["derivation"] * 2 + ["decision"] * 3 + ["question"] * 2 + ["source"] * 2
SYLLABLES = "ka la ve no mor tis pra del fon gur hes jin ber tha xo ri su pen dro".split()
HEAD = re.compile(r"^## (\d)\.")
LINE = re.compile(r"^- `(?P<id>\w+)` (?P<age>\d+)d (?P<proj>\S+)(?P<due> DUE)?: (?P<text>.*)$")
HIT = re.compile(r"^([a-z0-9][a-z0-9-]*)  \[")


def today() -> date:
    return date.fromisoformat(os.environ["SB_TODAY"])


def run(v, *args, ok=(0,)):
    """Run `sb`; any exit code outside ok fails with the whole call."""
    code, out, err = sb(v, *args)
    assert code in ok, f"sb {' '.join(args)} -> {code}\n{out}\n{err}"
    return out


class Vocab:
    """Zipf-weighted pseudo words, so that some terms are common and most are rare, as in running text."""

    def __init__(self, rng, size=1500):
        rare = ["", "on", "ity"]
        words = dict.fromkeys(rng.choice(SYLLABLES) + rng.choice(SYLLABLES) + rng.choice(SYLLABLES) + rng.choice(rare)
                              for _ in range(2 * size))
        self.words = list(words)[:size]
        self.weights = [1 / (i + 5) ** 0.7 for i in range(len(self.words))]

    def pick(self, rng, k) -> list:
        out: list = []
        while len(out) < k:
            w = rng.choices(self.words, self.weights)[0]
            if w not in out:
                out.append(w)
        return out


def write_note(v, slug, project, typ, title, cue, gist, day, extra=None):
    """A valid note dated `day` (frontmatter `created` and `updated`)."""
    ex = {"created": day, "updated": day, **({"ref": f"10.1000/{slug}"} if typ == "source" else {}), **(extra or {})}
    return add_note(v, slug, project=project, type=typ, title=title, cue=cue, gist=gist, extra=ex)


def build_vault(tmp_path, rng, vocab, n=2500, wave=0, wave_day=None, d0=None, pairs=50, used_share=0.06, pairs_out=None):
    """A vault of n valid notes over PROJECTS and all note types. `pairs` of the notes are near duplicates of another
    (same title and cue words, other unique tag). `wave` notes are created and used (a `create` row) on wave_day: they
    all reach the forgetting threshold together. used_share of the others got one use at a random earlier day.
    pairs_out, if a list, gets the (original, duplicate) slugs. Returns (root, slugs, wave slugs)."""
    root = init_vault(tmp_path)
    d0 = d0 or today()
    slugs, wave_slugs, made, vault = [], [], [], Vault(root)
    for i in range(n):
        typ, proj = rng.choice(TYPES), rng.choice(PROJECTS)
        slug = f"{typ[:3]}-{i:04d}"
        is_wave = i < wave
        created = wave_day if is_wave else d0 - timedelta(days=rng.randint(1, 120))
        if made and not is_wave and i >= n - pairs:  # a near duplicate of an earlier note
            orig, t, c, g = rng.choice(made)
            t = t.rsplit(" ", 1)[0]
            if pairs_out is not None:
                pairs_out.append((orig, slug))
        else:
            t, c, g = " ".join(vocab.pick(rng, 4)), " ".join(vocab.pick(rng, 3)), " ".join(vocab.pick(rng, 12)) + "."
        made.append((slug, t, c, g))
        write_note(root, slug, proj, typ, f"{t} zq{i}", c, g, created.isoformat())
        slugs.append(slug)
        if is_wave:
            wave_slugs.append(slug)
            vault.append("usage.tsv", wave_day.isoformat(), "work", "create", slug)
        elif rng.random() < used_share:
            vault.append("usage.tsv", (created + timedelta(days=rng.randint(0, (d0 - created).days - 1))).isoformat(),
                         "work", "show", slug)
    return root, slugs, wave_slugs


def notes_dir(v) -> dict:
    """slug -> relative path of every note in notes/."""
    return {rel.rsplit("/", 1)[1][:-3]: rel for rel in Vault(Path(v)).scan("notes")}


def archived(v) -> set:
    return {rel.rsplit("/", 1)[1][:-3] for rel in Vault(Path(v)).scan("archive")}


def title_words(v, slug, rel=None) -> list:
    n = Vault(Path(v)).read(rel or notes_dir(v)[slug])
    return n.get("title").split()


def edit_note(v, rel, sentence, day):
    """Append a sentence to the body and set `updated`: a file change that the next sb call finds as an edit."""
    p = Path(v) / rel
    text = re.sub(r"(?m)^updated: .*$", f"updated: {day}", p.read_text(encoding="utf-8"), count=1)
    p.write_text(text.rstrip("\n") + "\n\n" + sentence + "\n", encoding="utf-8")


def new_note(v, rng, vocab, project, typ="finding", why="a measured result with its condition", dup_of=None, slug=None):
    """`sb new` as the agent calls it (first without --anyway, then with when similar notes block it), then the
    skeleton is replaced by a filled note. Returns the slug."""
    day = today().isoformat()
    slug = slug or f"n{rng.randrange(10 ** 7):07d}"
    while slug in notes_dir(v) or slug in archived(v):
        slug = f"n{rng.randrange(10 ** 7):07d}"
    args = ("new", typ, slug, "-p", project, "--why", why)
    code, out, err = sb(v, *args)
    if code == 2 and "not written: similar" in err:
        code, out, err = sb(v, *args, "--anyway")
    assert code == 0, f"sb new {slug}: {out}{err}"
    if dup_of:
        t, c, g = dup_of
    else:
        t, c, g = " ".join(vocab.pick(rng, 4)), " ".join(vocab.pick(rng, 3)), " ".join(vocab.pick(rng, 12)) + "."
    write_note(v, slug, project, typ, f"{t} zn{slug}", c, g, day)
    return slug


def probe(v, rng, vocab) -> str:
    """Create a note and look for it with `sb recall` by its words: it must be found. Returns the slug."""
    slug = new_note(v, rng, vocab, rng.choice(PROJECTS))
    words = title_words(v, slug)
    out = run(v, "recall", "-n", "5", *words[:2], words[-1])
    assert slug in out, f"the note {slug} just created is not found by recall {words}:\n{out}"
    return slug


def day(v, rng, *, vocab=None, recalls=2, shows=1, captures=(0, 3), news=(0, 2), edits=(0, 2), avoid=frozenset(),
        exact=False, dup_rate=0.1) -> dict:
    """One working day (date = SB_TODAY): recalls with words of existing notes, `sb show` on a hit, captures, new notes
    (`sb new` and filling them) and edits of note files. Notes in `avoid` are not touched on purpose; with exact every
    recall carries the unique tag of its source note, so its first hit is that note (and none other counts as used)."""
    vocab = vocab or Vocab(rng)
    d = today().isoformat()
    paths = {s: r for s, r in notes_dir(v).items() if s not in avoid}
    live = sorted(paths)
    out = {"created": [], "captured": [], "edited": [], "recalled": 0, "shown": 0}
    if not live:
        return out
    hits = []
    for _ in range(recalls):
        s = rng.choice(live)
        ws = title_words(v, s, paths[s])
        terms = [ws[-1], rng.choice(ws[:-1])] if exact or rng.random() < 0.7 else rng.sample(ws[:-1], 2)
        text = run(v, "recall", "-n", "5", *terms)
        out["recalled"] += 1
        hits += [m[1] for ln in text.split("\n") if (m := HIT.match(ln)) and m[1] not in avoid]
    for s in rng.sample(hits, min(shows, len(hits))):
        run(v, "show", s)
        out["shown"] += 1
    for _ in range(rng.randint(*captures)):
        s = rng.choice(live)
        ws = title_words(v, s, paths[s])
        text = " ".join(rng.sample(ws[:-1], 3)) + f" measured {rng.randrange(1000)} at tolerance {rng.randrange(1, 9)}e-{rng.randrange(3, 9)}"
        proj = rng.choice(PROJECTS)
        for _ in range(2 if rng.random() < 0.08 else 1):  # sometimes captured twice
            out["captured"].append(run(v, "capture", "-p", proj, "-s", "tests/sim.py", text).strip())
    for _ in range(rng.randint(*news)):
        dup = None
        if rng.random() < dup_rate:  # a near duplicate of a note that exists
            s = rng.choice(live)
            n = Vault(Path(v)).read(paths[s])
            dup = (n.get("title").rsplit(" ", 1)[0], n.get("cue"), n.gist)
        out["created"].append(new_note(v, rng, vocab, rng.choice(PROJECTS), rng.choice(TYPES), dup_of=dup))
    for s in rng.sample(live, min(rng.randint(*edits), len(live))):
        edit_note(v, paths[s], f"Remeasured {rng.randrange(100)} on {d}.", d)
        out["edited"].append(s)
    return out


# ---- the sleeper -----------------------------------------------------------------------------------------------

def sections(plan: str) -> dict:
    """Section number (1..5) -> its lines; the header lines before section 1 are under 0."""
    out, cur = {0: []}, 0
    for ln in plan.split("\n"):
        if m := HEAD.match(ln):
            cur = int(m[1])
            out[cur] = []
        else:
            out[cur].append(ln)
    return out


def add_link(v, rel, target, why):
    """A bullet in the note's `## Links` section (made if missing)."""
    p = Path(v) / rel
    text = p.read_text(encoding="utf-8").rstrip("\n")
    p.write_text(text + ("\n" if "\n## Links" in text else "\n\n## Links\n") + f"- [[{target}]] {why}\n", encoding="utf-8")


def sleeper(v, rng, veto_every=7, *, vocab=None, lazy_every=11, unverifiable_every=9, link_share=0.6, merge_share=0.5,
            keep=frozenset()) -> dict:
    """One sleep as plugin/agents/sleeper.md describes it, run over the CLI. Rules where the agent would judge:
    inbox line: a DUE line becomes a note or is dropped; any other becomes a note (40%), is dropped (30%) or waits (30%);
    a line captured twice is promoted into one note; one in four promoted lines extends a similar note instead.
    Condense: merge (archive superseded, fix links) or `unrelated`. Forget: every veto_every-th candidate is vetoed, the
    others archived as faded. Verify: every lazy_every-th item is left open (no time), every unverifiable_every-th
    new or changed note is parked as unverifiable, the rest is marked verified. Dream: link both notes (link_share) or
    `unrelated`. Then `sb check` (exit 0) and `sleep done`. Returns what it did."""
    vocab = vocab or Vocab(rng)
    d, root = today().isoformat(), Path(v)
    run(v, "sleep", "begin")
    plan = run(v, "sleep", "plan")
    sec = sections(plan)
    head = re.search(r"touched: (\d+) of (\d+) \(notes and inbox lines\), deferred: (\d+)", plan)
    res = dict(size=int(head[1]), budget=int(head[2]), deferred=int(head[3]), promoted=0, extended=0, dropped=0, waited=0,
               merged=0, unrelated_condense=0, faded=0, vetoed=0, verified=0, parked=0, lazy=0, linked=0, unrelated_dream=0,
               created=[], archived=[], vetoed_slugs=[], forget_listed=0, verify_listed=0, inbox_listed=0, condense_listed=0,
               dream_listed=0, due_listed=0, verified_slugs=[], planned_forget=[], pairs=0)
    live = notes_dir(v)

    # 1. consolidate
    lines, similar, twice = [], {}, []
    for ln in sec[1]:
        if m := LINE.match(ln):
            lines.append(m)
            similar[m["id"]] = []
        elif ln.startswith("  similar: ") and lines:
            similar[lines[-1]["id"]].append(ln[11:].split(" ", 1)[0])
        elif ln.startswith("- captured twice, promote: "):
            twice.append(re.findall(r"`(\w+)`", ln))
    res["inbox_listed"], res["due_listed"] = len(lines), sum(bool(m["due"]) for m in lines)
    res["pairs"] = len(twice)
    pair_note, done_ids = {}, set()
    by_id = {m["id"]: m for m in lines}
    for a, b in twice:  # one note for both lines (a line can be in two pairs: it is settled once)
        todo = [i for i in (a, b) if i in by_id and i not in done_ids]
        if not todo:
            continue
        slug = new_note(v, rng, vocab, by_id[todo[0]]["proj"] if by_id[todo[0]]["proj"] != "_unsorted" else rng.choice(PROJECTS),
                        "finding", why="captured twice, so it recurs")
        live[slug] = notes_dir(v)[slug]
        res["created"].append(slug)
        for i in todo:
            run(v, "inbox", "resolve", i, "--note", slug)
            res["promoted"] += 1
            done_ids.add(i)
    for m in lines:
        if m["id"] in done_ids:
            continue
        r = rng.random()
        if m["due"]:
            r = 0.0 if r < 0.5 else 0.5  # a DUE line cannot wait: note or drop
        if r < 0.4:
            ext = [s for s in similar[m["id"]] if s in live]
            if ext and rng.random() < 0.25:  # extend a similar note
                edit_note(v, live[ext[0]], f"Also: {m['text'][:80]}", d)
                slug = ext[0]
                res["extended"] += 1
            else:
                slug = new_note(v, rng, vocab, m["proj"] if m["proj"] != "_unsorted" else rng.choice(PROJECTS), "finding",
                                why="a line of the inbox with rank")
                live[slug] = notes_dir(v)[slug]
                res["created"].append(slug)
            run(v, "inbox", "resolve", m["id"], "--note", slug)
            res["promoted"] += 1
        elif r < 0.7:
            run(v, "inbox", "resolve", m["id"], "--drop")
            res["dropped"] += 1
        else:
            run(v, "inbox", "resolve", m["id"], "--wait")
            res["waited"] += 1

    # 2. condense
    for ln in sec[2]:
        if m := re.match(r"^- (\S+) ~ (\S+) \(coverage", ln):
            res["condense_listed"] += 1
            a, b = m[1], m[2]
            if a not in live or b not in live:
                continue
            if rng.random() < merge_share and not {a, b} & keep:  # keep: notes a merge must not archive
                edit_note(v, live[a], f"Merged from {b}.", d)
                run(v, "archive", b, "--why", "superseded", "--superseded-by", a, "--fix-links")
                live.pop(b)
                res["merged"] += 1
                res["archived"].append(b)
            else:
                run(v, "sleep", "unrelated", a, b, "different quantities despite shared words")
                res["unrelated_condense"] += 1

    # 3. forget
    cands = [m[1] for ln in sec[3] if (m := re.match(r"^- (\S+) \[", ln))]
    res["forget_listed"], res["planned_forget"] = len(cands), cands
    for i, s in enumerate(cands):
        if s not in live:
            continue
        if veto_every and i % veto_every == veto_every - 1:
            run(v, "sleep", "veto", s, "needed for the next measurement")
            res["vetoed"] += 1
            res["vetoed_slugs"].append(s)
        else:
            run(v, "archive", s, "--why", "faded")
            live.pop(s)
            res["faded"] += 1
            res["archived"].append(s)

    # 4. verify
    items = [(m[1], m[2] != "R=") for ln in sec[4] if (m := re.match(r"^- (\S+) \((new|changed|R=)", ln))]
    res["verify_listed"] = len(items)
    ok = []
    for i, (s, fresh) in enumerate(items, 1):
        if s not in live:
            continue
        if lazy_every and i % lazy_every == 0:
            res["lazy"] += 1
        elif fresh and unverifiable_every and i % unverifiable_every == 0:
            run(v, "sleep", "unverifiable", s, "no source or neighbour states this number")
            res["parked"] += 1
        else:
            ok.append(s)
    for k in range(0, len(ok), 40):
        run(v, "sleep", "verified", *ok[k:k + 40])
    res["verified"], res["verified_slugs"] = len(ok), ok

    # 5. dream
    lim = Vault(root).cfg["limits"]["links"]
    for ln in sec[5]:
        if m := re.match(r"^- (\S+) ~ (\S+) \(", ln):
            res["dream_listed"] += 1
            a, b = m[1], m[2]
            if a not in live or b not in live:
                continue
            na, nb = (Vault(root).read(live[x]) for x in (a, b))
            if rng.random() < link_share and len(na.targets) < lim and len(nb.targets) < lim:
                add_link(v, live[a], b, "shares the measured quantity")
                add_link(v, live[b], a, "shares the measured quantity")
                res["linked"] += 1
            else:
                run(v, "sleep", "unrelated", a, b, "shares words, not substance")
                res["unrelated_dream"] += 1

    out = run(v, "check")
    assert " 0 errors" in out, out
    run(v, "sleep", "done", f"mechanical sleep of {d}")
    res["created"] = list(dict.fromkeys(res["created"]))
    return res
