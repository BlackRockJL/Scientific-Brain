"""SQLite FTS5 index: text folding, query quoting, incremental refresh."""
from __future__ import annotations

import functools
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter

from . import inbox
from .vault import LOCATIONS, WIKI, ParseError, SbError, Vault

VERSION = 8
LOCK_WAIT = 10  # seconds a writer waits for another process to release the index
# bm25 weights for slug, title, gist, cue, body, links: the cue counts most
RANK = "bm25(fts, 3.0, 3.0, 2.0, 4.0, 1.0, 0.0)"
COLS = "{slug title gist cue body} : "  # searches never look into the links column
MIN_TERM = 3  # the trigram tokenizer cannot match shorter terms; those go through the `short` table
MAX_LEN, MAX_TERMS = 64, 32  # one query never sees longer terms or more terms than this
STOP_WORDS = """the a an and or not of to in on at by for with from as is are was were be been it its this that these
those i we you he she they them our your his her their there here which who what when where how if then than so but
do does did has have had will would can could should may might also into over under about via per
der die das den dem des ein eine einen einem einer und oder aber nicht mit von zu zum zur im am an auf bei aus nach
fur uber vor um ist sind war waren wird werden wurde wurden hat haben hatte kann konnen soll man wenn dass da wie wo
als auch noch nur so es sie wir ihr er sich dies diese dieser dieses""".split()
PUNCT = " \t,.;:!?()[]{}<>\"'“”„‘’«»…*#-"  # stripped from both ends of a term
SPLIT = re.compile("[,;()\\[\\]{}\"“”„«»]+")  # separates terms stuck together
PREFIX = re.compile(r"(?:slug|title|gist|cue|body|links):")  # only at the start of a term
TOKEN = re.compile(r"[^\W_]+")


def fold(s: str) -> str:
    """Lower case, ß->ss, ä/ö/ü->a/o/u, then ae/oe/ue->a/o/u unless after a vowel or q (queue), at a word end (due)
    or before a final s or d there (values, does, continued); accents removed."""
    s = unicodedata.normalize("NFC", s).lower().replace("ß", "ss").translate(str.maketrans("äöü", "aou"))
    s = re.sub(r"(?<![aeiouq])([aou])e(?=[a-z])(?![sd](?![a-z]))", r"\1", s)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


STOP = frozenset(map(fold, STOP_WORDS))


def clean_terms(t: str):
    """Folded pieces of one raw term without punctuation at the edges, column prefix or possessive 's."""
    for piece in SPLIT.split(fold(t[:MAX_LEN]).translate(str.maketrans("–—", "--"))):
        piece = re.sub(r"['’]s$", "", piece.strip(PUNCT))
        yield (piece[m.end():] if (m := PREFIX.match(piece)) else piece).strip(PUNCT)


def prepare(terms, short=False):
    """Fold terms; returns (usable terms, ignored terms). Stop words drop out silently.
    Terms under MIN_TERM characters are usable only with short=True, and then only as plain words."""
    good, ignored = [], []
    for t in terms:
        pieces = [f for f in clean_terms(t) if f]
        if not pieces:
            ignored.append(t)
        for f in pieces:
            if f in STOP:
                continue
            if len(f) >= MIN_TERM or (short and TOKEN.fullmatch(f)):
                good.append(f[:MAX_LEN])
            else:
                ignored.append(t)
    return list(dict.fromkeys(good))[:MAX_TERMS], list(dict.fromkeys(ignored))


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def spaced(s: str) -> str:
    """Hyphens and dashes become spaces and runs of white space one space, so `Crank-Nicolson` meets `Crank - Nicolson`."""
    return re.sub(r"\s+", " ", s.translate(str.maketrans("-–—", "   "))).strip()


def fts_text(s: str) -> str:
    """Folded text as indexed."""
    return spaced(fold(s))


def quote_term(t: str) -> str:
    return '"%s"' % spaced(t).replace('"', '""')


def _corrupt(e: sqlite3.Error) -> bool:
    """A damaged file raises plain DatabaseError (not a database, malformed); locks and I/O errors are OperationalError."""
    return type(e) is sqlite3.DatabaseError


def _busy(e: sqlite3.Error) -> bool:
    return isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e))


def _guarded(fn):
    """A damaged index is set aside and rebuilt once, then the call is repeated; a lock held too long is an SbError."""
    @functools.wraps(fn)
    def run(self, *a, **k):
        try:
            return fn(self, *a, **k)
        except sqlite3.DatabaseError as e:
            if _busy(e):
                raise SbError(f"index error: {e} (another sb process holds the index; try again)")
            if not _corrupt(e):
                raise
            self._heal(e)
            return fn(self, *a, **k)
    return run


def match_query(terms):
    """Quote every term for FTS5 and join with OR; returns (query or None, ignored)."""
    good, ignored = prepare(terms, True)
    return " OR ".join(quote_term(t) for t in good) or None, ignored


def skip_body(body: str, skip) -> str:
    """The body without the sections whose `## ` heading is in skip (up to the next `## ` heading)."""
    if not skip:
        return body
    out, hide = [], False
    for line in body.split("\n"):
        if line.startswith("## "):
            hide = line[3:].strip().lower() in skip
        if not hide:
            out.append(line)
    return "\n".join(out)


def fts5_available() -> bool:
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='trigram')")
        return True
    except sqlite3.Error:
        return False


class Index:
    def __init__(self, v: Vault):
        self.v = v
        self.healed = False
        v.sb_dir.mkdir(exist_ok=True)
        self.skip = self._skip_sections()
        self._open()

    def _open(self):
        """Connect and bring the schema up to date; other processes opening at the same time are waited for."""
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                self.db = sqlite3.connect(self.v.sb_dir / "index.sqlite", timeout=LOCK_WAIT, isolation_level=None)
                if self.db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":  # switching ignores the busy timeout
                    self.db.execute("PRAGMA journal_mode=WAL")
                self.created = self._ensure()
                return
            except sqlite3.Error as e:
                if _busy(e):
                    if time.monotonic() < deadline:
                        self.db.close()
                        time.sleep(0.05)
                        continue
                    raise SbError(f"index error: {e} (another sb process holds the index; try again)")
                if _corrupt(e):
                    return self._heal(e)
                low = str(e).lower()
                hint = " (SQLite built with FTS5 and the trigram tokenizer is required)" if "fts5" in low or "tokenizer" in low else ""
                raise SbError(f"index error: {e}{hint}")

    def _heal(self, e):
        """The index is only a cache: move the damaged file aside as index.sqlite.broken and start a new one (once)."""
        if self.healed:
            raise SbError(f"index error: {e}")
        self.healed = True
        db = self.v.sb_dir / "index.sqlite"
        try:
            self.db.close()
        except (AttributeError, sqlite3.Error):
            pass
        try:
            if db.exists():
                os.replace(db, db.with_name(db.name + ".broken"))
            for ext in ("-wal", "-shm"):  # a stale log must never be replayed into the new file
                db.with_name(db.name + ext).unlink(missing_ok=True)
        except OSError as x:
            raise SbError(f"index error: {e}; cannot set the damaged index aside: {x.strerror or x}")
        print(f"sb: index.sqlite was damaged ({e}); moved to index.sqlite.broken, rebuilding", file=sys.stderr)
        self._open()

    def _skip_sections(self):
        """Lower-cased headings from `[index] skip_sections` in sb.toml; their sections stay out of the full text."""
        return tuple(sorted({x.strip().lower() for x in self.v.cfg["index"]["skip_sections"]}))

    def _ver(self): return self.db.execute("PRAGMA user_version").fetchone()[0]

    def _begin(self) -> bool:
        """Take the write lock; False when another process keeps it past LOCK_WAIT."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            return True
        except sqlite3.OperationalError as e:
            if _busy(e):
                return False
            raise

    def _abort(self):
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")

    def _ensure(self) -> bool:
        if self._ver() == VERSION:
            return False
        if not self._begin():
            raise sqlite3.OperationalError("database is locked")
        built = False
        try:
            if self._ver() != VERSION:
                built = True
                try:  # what the old schema knew of the files, kept until a refresh has used it (also after a crash)
                    self.db.execute("CREATE TABLE IF NOT EXISTS prev AS SELECT path, mt, sz FROM files")
                except sqlite3.OperationalError:
                    pass  # no files table: nothing to report
                for stmt in (
                    "DROP TABLE IF EXISTS fts", "DROP TABLE IF EXISTS files", "DROP TABLE IF EXISTS meta",
                    "DROP TABLE IF EXISTS short", "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)",
                    "CREATE TABLE short(rid INT, tok TEXT, loc TEXT, proj TEXT)",  # words under MIN_TERM in slug and cue
                    "CREATE INDEX short_tok ON short(tok)", "CREATE INDEX short_rid ON short(rid)",
                    "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, location TEXT, mt INT, sz INT, ck INT DEFAULT 0)",
                    "CREATE VIRTUAL TABLE fts USING fts5(slug, title, gist, cue, body, links, project UNINDEXED, type UNINDEXED,"
                    " location UNINDEXED, rslug UNINDEXED, rtitle UNINDEXED, rgist UNINDEXED, rcue UNINDEXED, tokenize='trigram')",
                        f"PRAGMA user_version={VERSION}"):
                    self.db.execute(stmt)
            self.db.execute("COMMIT")
        except BaseException:
            self._abort()
            raise
        return built

    def _drop(self, path):
        row = self.db.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        if row:
            self.db.execute("DELETE FROM fts WHERE rowid=?", row)
            self.db.execute("DELETE FROM short WHERE rid=?", row)
            self.db.execute("DELETE FROM files WHERE id=?", row)

    def _put(self, rel, stat):
        """Index one file; False when it does not parse."""
        self._drop(rel)
        try:
            n = self.v.read(rel)
        except ParseError as e:
            print(f"sb: not indexed: {e}", file=sys.stderr)
            return False
        cur = self.db.execute("INSERT INTO files(path, location, mt, sz) VALUES(?,?,?,?)", (rel, n.location, *stat))
        t, g, c, proj = n.get("title"), n.gist, n.get("cue"), nfc(n.folder)
        body, links = fts_text(WIKI.sub(" ", skip_body(n.body, self.skip))), fts_text(" ".join(f"[[{x}]]" for x in n.targets))
        cols = "rowid, slug, title, gist, cue, body, links, project, type, location, rslug, rtitle, rgist, rcue"
        self.db.execute(f"INSERT INTO fts({cols}) VALUES({','.join('?' * 14)})",
                        (cur.lastrowid, fts_text(n.slug), fts_text(t), fts_text(g), fts_text(c), body, links, proj, n.get("type"),
                         n.location, n.slug, t, g, c))
        self._put_short(cur.lastrowid, fold(n.slug), fold(c), n.location, proj)
        return True

    def _put_short(self, rid, slug, cue, loc, proj):
        toks = {x for x in TOKEN.findall(slug + " " + cue) if len(x) < MIN_TERM}
        self.db.executemany("INSERT INTO short VALUES(?,?,?,?)", [(rid, x, loc, proj) for x in toks])

    def _archive_stamp(self):
        """mtimes of archive/ and its project folders: they change when a file is added, removed or renamed there."""
        root = self.v.root / "archive"
        try:
            dirs = [root, *sorted(d for d in root.iterdir() if d.is_dir())]
            return ",".join(f"{d.name}:{d.stat().st_mtime_ns}" for d in dirs)
        except OSError:
            return ""

    def _stamp(self):
        """mtime and size of both inbox files."""
        out = []
        for f in (inbox.INBOX, inbox.DONE):
            try:
                st = (self.v.sb_dir / f).stat()
                out += [st.st_mtime_ns, st.st_size]
            except OSError:
                out += [0, 0]
        return ",".join(map(str, out))

    def _put_inbox(self):
        """Replace the inbox rows (negative rowids, slug inbox:<id>) by the open lines (location inbox) and the expired ones."""
        stamp = self._stamp()
        self.db.execute("DELETE FROM fts WHERE rowid < 0")
        self.db.execute("DELETE FROM short WHERE rid < 0")
        rows = [(r, "inbox") for r in inbox.open_lines(self.v)] + [(r, "expired") for r in inbox.expired(self.v)]
        for i, ((id, day, proj, src, text), loc) in enumerate(rows, 1):
            cols = "rowid, slug, title, gist, cue, body, links, project, type, location, rslug, rtitle, rgist, rcue"
            self.db.execute(f"INSERT INTO fts({cols}) VALUES({','.join('?' * 14)})",
                            (-i, fts_text("inbox:" + id), "", fts_text(text), "", fts_text(src), "", nfc(proj), "inbox", loc,
                             "inbox:" + id, day, text, src))
            self._put_short(-i, fold("inbox:" + id), fold(text), loc, nfc(proj))  # a source path is no keyword
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('inbox', ?)", (stamp,))

    @_guarded
    def refresh(self, full=False):
        """Sync notes/ and the open inbox lines with disk; archive/ when full or when one of its folders changed.
        Returns the slugs of notes/ files that were indexed before and changed since, also when a full build finds them
        (not those that are new, and none when the old index was unreadable). A locked index is read as it is."""
        rebuild = full
        skipped = self.db.execute("SELECT v FROM meta WHERE k='skip'").fetchone()
        full = full or self.created or (skipped or [None])[0] != ",".join(self.skip)
        astamp = self._archive_stamp()
        astored = self.db.execute("SELECT v FROM meta WHERE k='archive'").fetchone()
        locs = LOCATIONS if full or not astored or astored[0] != astamp else ("notes",)
        disk = {p: s for loc in locs for p, s in self.v.scan(loc).items()}
        have = {p: (mt, sz) for p, mt, sz in self.db.execute(
            f"SELECT path, mt, sz FROM files WHERE location IN ({','.join('?' * len(locs))})", locs)}
        try:
            prev = {p: (mt, sz) for p, mt, sz in self.db.execute("SELECT path, mt, sz FROM prev")}
        except sqlite3.OperationalError:
            prev = None
        known = have if prev is None else prev
        todo = [p for p, s in disk.items() if full or have.get(p) != s]
        gone = [p for p in have if p not in disk]
        stored = self.db.execute("SELECT v FROM meta WHERE k='inbox'").fetchone()
        stale = full or not stored or stored[0] != self._stamp()
        anew = not astored or astored[0] != astamp
        if not (todo or gone or stale or anew or prev is not None):
            return []
        if not self._begin():
            if skipped is None:
                raise SbError("index error: database is locked (another sb process is building the index; try again)")
            print("sb: index is locked by another process; results may be a step behind", file=sys.stderr)
            return []
        changed = []
        try:
            for p in gone:
                self._drop(p)
            for p in todo:
                if self._put(p, disk[p]) and p.startswith("notes/") and p in known and known[p] != disk[p]:
                    changed.append(p)
            if stale:
                self._put_inbox()
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('archive', ?)", (astamp,))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('skip', ?)", (",".join(self.skip),))
            self.db.execute("DROP TABLE IF EXISTS prev")
            self.db.execute("COMMIT")
        except BaseException:
            self._abort()
            raise
        self.created = False
        if rebuild:
            try:
                self.db.execute("INSERT INTO fts(fts) VALUES('optimize')")  # merge the FTS segments, then drop free pages
                self.db.execute("VACUUM")
            except sqlite3.OperationalError as e:
                if not _busy(e):
                    raise
        return [p.rsplit("/", 1)[1][:-3] for p in changed]

    @_guarded
    def update(self, rels):
        """Re-index (or drop) specific files, e.g. after a rename. A locked index catches up at the next refresh."""
        if not self._begin():
            print("sb: index is locked by another process; it catches up at the next call", file=sys.stderr)
            return
        try:
            for rel in rels:
                p = self.v.root / rel
                if p.is_file():
                    st = p.stat()
                    self._put(rel, (st.st_mtime_ns, st.st_size))
                else:
                    self._drop(rel)
            self.db.execute("COMMIT")
        except BaseException:
            self._abort()
            raise

    def _where(self, locs, project):
        sql = f"location IN ({','.join('?' * len(locs))})"
        if set(locs) <= {"inbox", "expired"}:  # inbox rows have negative rowids; the range keeps FTS5 from visiting every note
            sql += " AND rowid < 0"
        return (sql + " AND project=?", [*locs, nfc(project)]) if project else (sql, list(locs))

    def _hits(self, long, short, locs, project):
        """rowid -> number of terms found: long terms through FTS5, short ones as exact words of slug and cue."""
        w, args = self._where(locs, project)
        cnt: Counter = Counter()
        for t in long:
            cnt.update(r for (r,) in self.db.execute(f"SELECT rowid FROM fts WHERE fts MATCH ? AND {w}",
                                                     (COLS + quote_term(t), *args)))
        sw = f"loc IN ({','.join('?' * len(locs))})" + (" AND proj=?" if project else "")
        for t in short:
            cnt.update(r for (r,) in self.db.execute(f"SELECT rid FROM short WHERE tok=? AND {sw}",
                                                     (t, *locs, *([nfc(project)] if project else []))))
        return cnt

    def _need(self, good, cover):
        return math.ceil((self.v.cfg["limits"]["min_coverage"] if cover is None else cover) * len(good))

    @_guarded
    def search(self, terms, locs=("notes",), project=None, limit=5, cover=None):
        """Rows (slug, project, type, location, title, gist, cue) ranked by bm25; at least min_coverage of the terms must occur.
        Matches found only through short terms follow the ranked ones."""
        good, ignored = prepare(terms, True)
        if not good:
            return [], ignored
        long, short = [t for t in good if len(t) >= MIN_TERM], [t for t in good if len(t) < MIN_TERM]
        w, args = self._where(locs, project)
        need = self._need(good, cover)
        cols = "rowid, rslug, project, type, location, rtitle, rgist, rcue"
        cov = self._hits(long, short, locs, project) if need > 1 or short else None
        rows = []
        if long:  # the coverage filter runs while reading the ranked rows, so it never loses a hit to a cut-off
            cur = self.db.execute(f"SELECT {cols} FROM fts WHERE fts MATCH ? AND {w} ORDER BY {RANK}, rslug, project, location",
                                  (COLS + "(" + " OR ".join(map(quote_term, long)) + ")", *args))
            for r in cur:
                if need <= 1 or cov[r[0]] >= need:
                    rows.append(r)
                    if len(rows) >= limit:
                        break
            cur.close()
        if short and len(rows) < limit:  # rows matching short terms only, best covered first
            seen = {r[0] for r in rows}
            more = [r for r, c in cov.items() if c >= need and r not in seen]
            found = [x for i in range(0, len(more), 500) for x in self.db.execute(
                f"SELECT {cols} FROM fts WHERE rowid IN ({','.join('?' * len(more[i:i + 500]))})", more[i:i + 500])]
            found.sort(key=lambda x: (-cov[x[0]], x[1], x[2], x[4]))  # ties by slug, so the order never depends on the index history
            rows += found[:limit - len(rows)]
        return [r[1:] for r in rows[:limit]], ignored

    @_guarded
    def count(self, terms, locs, project=None):
        good, _ = prepare(terms, True)
        if not good:
            return 0
        long, short = [t for t in good if len(t) >= MIN_TERM], [t for t in good if len(t) < MIN_TERM]
        need = self._need(good, None)
        if need > 1 or short:
            return sum(c >= need for c in self._hits(long, short, locs, project).values())
        w, args = self._where(locs, project)
        return self.db.execute(f"SELECT count(*) FROM fts WHERE fts MATCH ? AND {w}",
                               (COLS + "(" + " OR ".join(map(quote_term, long)) + ")", *args)).fetchone()[0]

    @_guarded
    def mentions(self, slug):
        """Candidate files whose body contains [[slug]]: (path, title)."""
        return self.db.execute("SELECT f.path, x.rtitle FROM fts x JOIN files f ON f.id = x.rowid WHERE fts MATCH ?",
                               ('links : ' + quote_term(fts_text("[[" + slug + "]]")),)).fetchall()

    @_guarded
    def listing(self, project=None):
        w, args = self._where(("notes",), project)
        return self.db.execute(f"SELECT project, type, rslug, rtitle FROM fts WHERE {w} ORDER BY project, type, rslug",
                               args).fetchall()

    @_guarded
    def counts(self):
        """(location, project, type) of every indexed note."""
        return self.db.execute("SELECT location, project, type FROM fts WHERE rowid > 0").fetchall()

    @_guarded
    def n_inbox(self):
        return self.db.execute("SELECT count(*) FROM fts WHERE rowid < 0 AND location='inbox'").fetchone()[0]

    @_guarded
    def n_notes(self):
        return self.db.execute("SELECT count(*) FROM files WHERE location='notes'").fetchone()[0]

    @_guarded
    def unchecked(self):
        """Notes not checked since their last change, including those that do not parse (they are not indexed)."""
        known = {p for (p,) in self.db.execute("SELECT path FROM files WHERE location='notes' AND ck=1")}
        return set(self.v.scan("notes")) - known

    @_guarded
    def mark_checked(self, rels):
        if self._begin():  # a locked index keeps the notes unchecked, which only repeats the check
            try:
                self.db.executemany("UPDATE files SET ck=1 WHERE path=?", [(r,) for r in rels])
                self.db.execute("COMMIT")
            except BaseException:
                self._abort()
                raise
