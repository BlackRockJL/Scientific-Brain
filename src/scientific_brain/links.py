"""Wikilinks: backlinks and safe rename."""
from __future__ import annotations

import re

from . import usage
from .index import Index
from .vault import LOCATIONS, SLUG, SbError, Vault, clean, parse_value, quote


def fm_end(parts) -> int:
    """Index of the line that closes the frontmatter."""
    return next((i for i in range(1, len(parts)) if parts[i].strip() == "---"), 0)


def rewrite(text: str, old: str, new: str) -> str:
    """Replace [[old]] in the body and old in a superseded_by list."""
    parts = text.split("\n")
    end = fm_end(parts)
    for i, ln in enumerate(parts):
        if i <= end and ln.startswith("superseded_by:"):
            items = parse_value("superseded_by", ln.split(":", 1)[1].strip(), f"line {i + 1}")
            parts[i] = "superseded_by: [" + ", ".join(new if x == old else x for x in items) + "]"
        elif i > end:
            parts[i] = re.sub(rf"\[\[\s*{re.escape(old)}\s*\]\]", f"[[{new}]]", ln)
    return "\n".join(parts)


def backlinks(v: Vault, idx, slug: str, loc=None):
    """(slug, title) of notes that link to slug, optionally only from one location; the index narrows, the file decides."""
    out = []
    for rel, title in idx.mentions(slug):
        if loc and not rel.startswith(loc + "/"):
            continue
        try:
            if slug in v.read(rel).targets:
                out.append((rel.rsplit("/", 1)[1][:-3], title))
        except SbError:
            pass
    return sorted(out)


def rename(v: Vault, old: str, new: str):
    """Rename a note and rewrite links in notes and archive; returns relative paths to re-index (new, old, rewritten)."""
    src = v.find(old)
    if not src:
        raise SbError(f"no note '{old}'")
    if not SLUG.match(new):
        raise SbError(f"'{new}' is not a valid slug (lower-case letters, digits, hyphens)")
    if v.find(new):
        raise SbError(f"slug '{new}' already exists at {v.find(new)}")
    plan = {}  # rel -> (old text, new text); everything is computed before the first write
    for loc in LOCATIONS:
        for rel in v.scan(loc):
            try:
                text = (v.root / rel).read_text(encoding="utf-8")
                t2 = rewrite(text, old, new)
            except (UnicodeDecodeError, SbError) as e:
                raise SbError(f"{rel}: {e}; nothing was renamed") from e
            if t2 != text:
                plan[rel] = (text, t2)
    dst = src.rsplit("/", 1)[0] + f"/{new}.md"
    done = []
    try:
        for rel, (_, t2) in plan.items():
            done.append(rel)
            (v.root / rel).write_text(t2, encoding="utf-8")
        (v.root / src).rename(v.root / dst)
    except OSError as e:
        for rel in done:  # put the old texts back
            (v.root / rel).write_text(plan[rel][0], encoding="utf-8")
        raise SbError(f"rename failed ({e}); the notes were left as they were") from e
    changed = list(plan)
    return [dst, src] + [c for c in changed if c != src]


def unlink(text: str, slug: str) -> str:
    """Remove '- [[slug]] ...' bullets of the ## Links section; elsewhere [[slug]] becomes plain text."""
    parts = text.split("\n")
    end, sec, out = fm_end(parts), "", []
    for i, ln in enumerate(parts):
        if i > end:
            sec = ln[3:].strip() if ln.startswith("## ") else sec
            if sec == "Links" and re.match(rf"\s*-\s*\[\[\s*{re.escape(slug)}\s*\]\]", ln):
                continue
            ln = re.sub(rf"\[\[\s*{re.escape(slug)}\s*\]\]", slug, ln)
        out.append(ln)
    return "\n".join(out)


WHYS = ("faded", "superseded", "wrong")
ARCHIVE_KEYS = ("archived:", "archived_why:", "archived_reason:", "superseded_by:")


def archive_mark(n) -> str:
    """Short label of an archived note: 'archived 2026-11-08: wrong - reason; superseded by: a, b'."""
    by, reason = n.get("superseded_by", []), n.get("archived_reason")
    return ("archived" + (f" {n.get('archived')}" if n.get("archived") else "") + (f": {n.get('archived_why')}" if n.get("archived_why") else "")
            + (f" \u2014 {reason}" if reason else "") + (f"; superseded by: {', '.join(by)}" if by else ""))


def archive_note(v: Vault, slug: str, why: str, reason=None, successors=(), fix_links=False, today=None, idx=None):
    """Move notes/P/slug.md to archive/P/ and record archived, archived_why, archived_reason and superseded_by.
    Returns (new path, slugs of notes/ that linked to it, paths of notes whose links were repaired); the index is updated."""
    reason, succ = clean(reason or ""), list(dict.fromkeys(successors))
    if why not in WHYS:
        raise SbError(f"why must be one of {', '.join(WHYS)}")
    if why == "faded" and succ:
        raise SbError("a faded note has no successor (use superseded or wrong)")
    if not reason and (why == "wrong" or (why == "superseded" and not succ)):
        raise SbError(f"--reason (one sentence) is required for '{why}'" + (" without --superseded-by" if why == "superseded" else ""))
    src = v.find(slug, ("notes",))
    if not src:
        raise SbError(f"no note '{slug}' in notes/" + (" (already archived)" if v.find(slug) else ""))
    for t in succ:
        if t == slug or not v.find(t, ("notes",)):
            raise SbError(f"superseded_by target '{t}' must be another note in notes/")
    if idx is None:
        idx = Index(v)
        idx.refresh()
    inc = [x for x, _ in backlinks(v, idx, slug, "notes") if x != slug]
    dst = "archive" + src[len("notes"):]
    (v.root / dst).parent.mkdir(parents=True, exist_ok=True)
    (v.root / src).rename(v.root / dst)
    parts = (v.root / dst).read_text(encoding="utf-8").split("\n")
    add = [f"archived: {(today or usage.today()).isoformat()}", f"archived_why: {why}"]
    add += [f"archived_reason: {quote(reason)}"] * bool(reason) + ["superseded_by: [" + ", ".join(succ) + "]"] * bool(succ)
    parts[fm_end(parts):fm_end(parts)] = add
    (v.root / dst).write_text("\n".join(parts), encoding="utf-8")
    fixed = fix_links_in(v, slug, succ[0] if succ else None, inc) if fix_links else []
    idx.update([dst, src] + fixed)
    return dst, inc, fixed


def _wrong(ln: str) -> bool:
    try:
        return parse_value("archived_why", ln.split(":", 1)[1].strip(), "") == "wrong"
    except SbError:
        return False


def restore(v: Vault, slug: str, force=False):
    """Move archive/P/slug.md back to notes/ and drop the archive fields; a note archived as wrong needs force.
    Returns [new, old] paths to re-index."""
    src = v.find(slug, ("archive",))
    if not src:
        raise SbError(f"no archived note '{slug}'")
    if (clash := v.find(slug, ("notes",))):
        raise SbError(f"slug '{slug}' already exists at {clash}")
    parts = (v.root / src).read_text(encoding="utf-8").split("\n")
    end = fm_end(parts)
    if not force and any(ln.startswith("archived_why:") and _wrong(ln) for ln in parts[1:end]):
        reason = next((ln.split(":", 1)[1].strip() for ln in parts[1:end] if ln.startswith("archived_reason:")), "")
        raise SbError(f"'{slug}' was archived as wrong ({reason}); fix its text first and restore with --force")
    keep = [ln for i, ln in enumerate(parts) if not (0 < i < end and ln.startswith(ARCHIVE_KEYS))]
    dst = "notes" + src[len("archive"):]
    (v.root / dst).parent.mkdir(parents=True, exist_ok=True)
    (v.root / src).rename(v.root / dst)
    (v.root / dst).write_text("\n".join(keep), encoding="utf-8")
    return [dst, src]


def fix_links_in(v: Vault, slug: str, successor, slugs):
    """Repair links from the given notes to an archived note: point to the successor or drop them; returns changed paths."""
    out = []
    for s in slugs:
        rel = v.find(s, ("notes",))
        text = (v.root / rel).read_text(encoding="utf-8")
        new = rewrite(text, slug, successor) if successor and successor != s else unlink(text, slug)  # no self-links
        if new != text:
            (v.root / rel).write_text(new, encoding="utf-8")
            out.append(rel)
    return out
