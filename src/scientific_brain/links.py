"""Wikilinks: backlinks and safe rename."""
from __future__ import annotations

import contextlib
import os
import re
import stat

from . import usage
from .index import Index
from .vault import LOCATIONS, SLUG, SbError, Vault, clean, parse_value, quote, read_raw, write_atomic


def fm_end(parts) -> int:
    """Index of the line that closes the frontmatter."""
    return next((i for i in range(1, len(parts)) if parts[i].strip() == "---"), 0)


def cr(parts) -> str:
    """'\\r' if the note has CRLF line ends (split at '\\n', its lines keep the '\\r'), else ''; added lines end the same way."""
    return "\r" if parts and parts[0].endswith("\r") else ""


def rewrite(text: str, old: str, new: str) -> str:
    """Replace [[old]] in the body and old in a superseded_by list; every other line stays as it is."""
    parts = text.split("\n")
    end = fm_end(parts)
    for i, ln in enumerate(parts):
        if i <= end and ln.startswith("superseded_by:"):
            items = parse_value("superseded_by", ln.split(":", 1)[1].strip(), f"line {i + 1}")
            if old in items:
                parts[i] = "superseded_by: [" + ", ".join(new if x == old else x for x in items) + "]" + "\r" * ln.endswith("\r")
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


def _read(v: Vault, rel: str, what: str) -> str:
    try:
        return read_raw(v.root / rel)
    except SbError as e:
        raise SbError(f"{e}; nothing was {what}") from e


def _one(v: Vault, slug: str, locs, what: str):
    """The path of the note with this slug, None if there is none; two notes with it are an error."""
    found = v.find_all(slug, locs)
    if len(found) > 1:
        raise SbError(f"slug '{slug}' is used by {len(found)} notes ({', '.join(found)}; see sb check); nothing was {what}")
    return found[0] if found else None


def _writable(v: Vault, rels, dirs, what: str):
    """Check before the first write that every file and folder involved can be written."""
    for p in [v.root / r for r in rels] + [v.root / d for d in dirs]:
        if not os.access(p, os.W_OK | (os.X_OK if p.is_dir() else 0)):
            raise SbError(f"{p.relative_to(v.root).as_posix()}: not writable; nothing was {what}")


def _apply(v: Vault, texts: dict, last, what: str):
    """Write rel -> (old text, new text), then call last(); if anything fails, put the old texts back with their times
    (the index must not take the roll-back for an edit) and raise SbError."""
    done = []
    try:
        for rel, (_, t2) in texts.items():
            st = os.stat(v.root / rel)
            write_atomic(v.root / rel, t2)
            done.append((rel, st))
        last()
    except (OSError, UnicodeError) as e:
        bad = []
        for rel, st in reversed(done):
            try:
                write_atomic(v.root / rel, texts[rel][0])
                os.utime(v.root / rel, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                bad.append(rel)
        left = f"could not put back {', '.join(bad)}" if bad else "the notes were left as they were"
        raise SbError(f"{what} failed ({e}); {left}") from e


def _move(v: Vault, src: str, dst: str, text: str):
    """Write the new file first, then remove the old one; a failure leaves only the old one."""
    write_atomic(v.root / dst, text, mode=stat.S_IMODE(os.stat(v.root / src).st_mode))
    try:
        os.unlink(v.root / src)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(v.root / dst)
        raise


def _rename_plan(v: Vault, old: str, new: str) -> dict:
    """rel -> (old text, new text) of the notes the rename changes."""
    plan = {}
    for loc in LOCATIONS:
        for rel in v.scan(loc):
            text = _read(v, rel, "renamed")
            try:
                t2 = rewrite(text, old, new)
            except SbError as e:
                raise SbError(f"{rel}: {e}; nothing was renamed") from e
            if t2 != text:
                plan[rel] = (text, t2)
    return plan


def rename(v: Vault, old: str, new: str):
    """Rename a note and rewrite links in notes and archive, all or nothing, under the vault lock; returns relative paths
    to re-index (new, old, rewritten)."""
    with v.locked():
        src = _one(v, old, LOCATIONS, "renamed")
        if not src:
            raise SbError(f"no note '{old}'")
        if not SLUG.match(new):
            raise SbError(f"'{new}' is not a valid slug (lower-case letters, digits, hyphens)")
        if (clash := v.find(new)):
            raise SbError(f"slug '{new}' already exists at {clash}")
        plan = _rename_plan(v, old, new)
        folder = src.rsplit("/", 1)[0]
        dst = f"{folder}/{new}.md"
        _writable(v, plan, {r.rsplit("/", 1)[0] for r in plan} | {folder}, "renamed")
        _apply(v, plan, lambda: (v.root / src).rename(v.root / dst), "rename")
    return [dst, src] + [c for c in plan if c != src]


def _bullets(parts, slug) -> set:
    """Indices of the '- [[slug]] ...' bullets of the ## Links section."""
    end, sec, out = fm_end(parts), "", set()
    for i in range(end + 1, len(parts)):
        ln = parts[i]
        sec = ln[3:].strip() if ln.startswith("## ") else sec
        if sec == "Links" and re.match(rf"\s*-\s*\[\[\s*{re.escape(slug)}\s*\]\]", ln):
            out.add(i)
    return out


def unlink(text: str, slug: str) -> str:
    """Remove '- [[slug]] ...' bullets of the ## Links section; elsewhere [[slug]] becomes plain text."""
    parts = text.split("\n")
    end, drop = fm_end(parts), _bullets(parts, slug)
    return "\n".join(re.sub(rf"\[\[\s*{re.escape(slug)}\s*\]\]", slug, ln) if i > end else ln
                     for i, ln in enumerate(parts) if i not in drop)


def relink(text: str, slug: str, successor: str) -> str:
    """Point links to slug at successor; a ## Links bullet to slug goes if the section has one to successor already."""
    parts = text.split("\n")
    if _bullets(parts, successor):
        drop = _bullets(parts, slug)
        text = "\n".join(ln for i, ln in enumerate(parts) if i not in drop)
    return rewrite(text, slug, successor)


WHYS = ("faded", "superseded", "wrong")
ARCHIVE_KEYS = ("archived:", "archived_why:", "archived_reason:", "superseded_by:")


def archive_mark(n) -> str:
    """Short label of an archived note: 'archived 2026-11-08: wrong - reason; superseded by: a, b'."""
    by, reason = n.get("superseded_by", []), n.get("archived_reason")
    return ("archived" + (f" {n.get('archived')}" if n.get("archived") else "") + (f": {n.get('archived_why')}" if n.get("archived_why") else "")
            + (f" \u2014 {reason}" if reason else "") + (f"; superseded by: {', '.join(by)}" if by else ""))


def archive_note(v: Vault, slug: str, why: str, reason=None, successors=(), fix_links=False, today=None, idx=None):
    """Move notes/P/slug.md to archive/P/ and record archived, archived_why, archived_reason and superseded_by; with
    fix_links the links to it are repaired in the same step, all or nothing.
    Returns (new path, slugs of notes/ that linked to it, paths of notes whose links were repaired); updates the index."""
    reason, succ = clean(reason or ""), list(dict.fromkeys(successors))
    if why not in WHYS:
        raise SbError(f"why must be one of {', '.join(WHYS)}")
    if why == "faded" and succ:
        raise SbError("a faded note has no successor (use superseded or wrong)")
    if not reason and (why == "wrong" or (why == "superseded" and not succ)):
        raise SbError(f"--reason (one sentence) is required for '{why}'" + (" without --superseded-by" if why == "superseded" else ""))
    if idx is None:
        idx = Index(v)
        idx.refresh()  # the long part, before the lock
    with v.locked():
        src = _one(v, slug, ("notes",), "archived")
        if not src:
            raise SbError(f"no note '{slug}' in notes/" + (" (already archived)" if v.find(slug) else ""))
        for t in succ:
            if t == slug or not v.find(t, ("notes",)):
                raise SbError(f"superseded_by target '{t}' must be another note in notes/")
        dst = "archive" + src[len("notes"):]
        if (clash := v.find(slug, ("archive",))) or (v.root / dst).exists():
            raise SbError(f"slug '{slug}' already exists at {clash or dst}; nothing was archived")
        inc = [x for x, _ in backlinks(v, idx, slug, "notes") if x != slug]
        parts = _read(v, src, "archived").split("\n")
        if not (end := fm_end(parts)):
            raise SbError(f"{src}: the frontmatter is not closed; fix the note first")
        add = [f"archived: {(today or usage.today()).isoformat()}", f"archived_why: {why}"]
        add += [f"archived_reason: {quote(reason)}"] * bool(reason) + ["superseded_by: [" + ", ".join(succ) + "]"] * bool(succ)
        parts[end:end] = [x + cr(parts) for x in add]
        fixes = {}
        for s in inc if fix_links else ():
            rel = v.find(s, ("notes",))
            text = _read(v, rel, "archived")
            new = relink(text, slug, succ[0]) if succ and succ[0] != s else unlink(text, slug)  # no self-links
            if new != text:
                fixes[rel] = (text, new)
        try:
            (v.root / dst).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SbError(f"{dst}: {e}; nothing was archived") from e
        _writable(v, fixes, {r.rsplit("/", 1)[0] for r in [*fixes, src, dst]}, "archived")
        _apply(v, fixes, lambda: _move(v, src, dst, "\n".join(parts)), "archive")
    idx.update([dst, src] + list(fixes))
    return dst, inc, list(fixes)


def _wrong(ln: str) -> bool:
    try:
        return parse_value("archived_why", ln.split(":", 1)[1].strip(), "") == "wrong"
    except SbError:
        return False


def restore(v: Vault, slug: str, force=False):
    """Move archive/P/slug.md back to notes/ and drop the archive fields; a note archived as wrong needs force.
    Returns [new, old] paths to re-index."""
    with v.locked():
        src = _one(v, slug, ("archive",), "restored")
        if not src:
            raise SbError(f"no archived note '{slug}'")
        if (clash := v.find(slug, ("notes",))):
            raise SbError(f"slug '{slug}' already exists at {clash}")
        parts = _read(v, src, "restored").split("\n")
        end = fm_end(parts)
        if not force and any(ln.startswith("archived_why:") and _wrong(ln) for ln in parts[1:end]):
            reason = next((ln.split(":", 1)[1].strip() for ln in parts[1:end] if ln.startswith("archived_reason:")), "")
            raise SbError(f"'{slug}' was archived as wrong ({reason}); fix its text first and restore with --force")
        keep = [ln for i, ln in enumerate(parts) if not (0 < i < end and ln.startswith(ARCHIVE_KEYS))]
        dst = "notes" + src[len("archive"):]
        try:
            (v.root / dst).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SbError(f"{dst}: {e}; nothing was restored") from e
        _writable(v, (), {src.rsplit("/", 1)[0], dst.rsplit("/", 1)[0]}, "restored")
        _apply(v, {}, lambda: _move(v, src, dst, "\n".join(keep)), "restore")
    return [dst, src]
