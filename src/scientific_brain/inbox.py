"""Inbox: append-only capture, resolution and expiry."""
from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

from .vault import SbError, Vault, clean

INBOX, DONE = "inbox.tsv", "inbox-done.tsv"  # rows: id, date, project, source, text | id, date, outcome


def project_for_cwd(v: Vault, cwd=None) -> str:
    """Project of the folder_map entry whose folder contains the working directory (longest match), else _unsorted."""
    here, best, name = Path(cwd or os.getcwd()).resolve(), -1, "_unsorted"
    for folder, proj in v.cfg["folder_map"].items():
        p = (v.root / folder).resolve()  # an absolute folder replaces the vault root
        if (here == p or p in here.parents) and len(p.parts) > best:
            best, name = len(p.parts), proj
    return name


def lines(v: Vault) -> list:
    return [r for r in v.rows(INBOX, 5) if r[0]]


def done(v: Vault) -> dict:
    """id -> (date, outcome) of the last booking."""
    return {i: (d, o) for i, d, o in v.rows(DONE, 3)}


def open_lines(v: Vault) -> list:
    """Lines without a booking or last booked as wait."""
    fin = done(v)
    return [r for r in lines(v) if fin.get(r[0], ("", "wait"))[1] == "wait"]


def age(row, today: date) -> int:
    try:
        return (today - date.fromisoformat(row[1])).days
    except ValueError:
        return 0


def capture(v: Vault, text: str, project: str, source: str, day: date) -> str:
    """Append a line; the id is the start of sha1(date+text), lengthened on collision."""
    text = clean(text)
    if not text:
        raise SbError("nothing to capture")
    ids, seed = {r[0] for r in lines(v)}, day.isoformat() + text
    n, k = 6, 0
    h = hashlib.sha1(seed.encode()).hexdigest()
    while h[:n] in ids:
        n += 1
        if n > 40:  # the same text twice on one day: salt the hash
            n, k = 6, k + 1
            h = hashlib.sha1(f"{seed}{k}".encode()).hexdigest()
    v.append(INBOX, h[:n], day.isoformat(), project, source, text)
    return h[:n]


def resolve(v: Vault, id: str, outcome: str, day: date):
    """Book an open line as note:<slug>, dropped or wait."""
    if not any(r[0] == id for r in lines(v)):
        raise SbError(f"no inbox line '{id}'")
    if not any(r[0] == id for r in open_lines(v)):
        raise SbError(f"inbox line {id} is already settled ({done(v)[id][1]})")
    if outcome.startswith("note:") and not v.find(outcome[5:], ("notes",)):
        raise SbError(f"no note '{outcome[5:]}' in notes/")
    v.append(DONE, id, day.isoformat(), outcome)


def expire_inbox(v: Vault, today: date, shown=None, since: date | None = None) -> list:
    """Book open lines older than inbox_wait_days as expired, only those in shown if given; returns their ids.
    Lines booked as wait on or after since (the running sleep) are not overbooked."""
    fin, wait = done(v), v.cfg["lifecycle"]["inbox_wait_days"]
    ids = [r[0] for r in open_lines(v) if age(r, today) > wait and (shown is None or r[0] in shown)
           and not (since and r[0] in fin and fin[r[0]][0] >= since.isoformat())]
    for i in ids:
        v.append(DONE, i, today.isoformat(), "expired")
    return ids


def expired(v: Vault) -> list:
    """Lines last booked as expired, in capture order; they stay findable but count neither as open nor as settled."""
    fin = done(v)
    return [r for r in lines(v) if fin.get(r[0], ("", ""))[1] == "expired"]


def promotion(v: Vault):
    """(lines promoted to a note, all lines settled: booked except wait and expired)."""
    out = [o for _, o in done(v).values() if o not in ("wait", "expired")]
    return sum(o.startswith("note:") for o in out), len(out)
