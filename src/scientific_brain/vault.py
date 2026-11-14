"""Vault location, configuration, notes and the strict frontmatter subset."""
from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # not POSIX: the vault lock is skipped
    fcntl = None

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

LISTKEYS = {"sources", "superseded_by"}
SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*\Z")  # \Z: "$" would accept a trailing newline
WIKI = re.compile(r"\[\[([^\[\]\n]+)\]\]")  # no '[' inside: a run of '[[' stays linear
ITEM = re.compile(r'(\s*"[^"]*"\s*|[^,"]*)(?:,|$)')  # blanks belong to one branch only: no backtracking over a long run
KEY = re.compile(r"([a-z_][a-z0-9_]*):(?:\s+(.*))?$")
LOCATIONS = ("notes", "archive")
TEMP = re.compile(r"\..+\.[0-9a-f]{8}\.tmp\Z")  # name of a temporary file of write_atomic
DEFAULTS = {"projects": [], "folder_map": {}, "source_roots": [], "bibtex": [],
            "index": {"skip_sections": []},
            "update": {"auto": True}}
# Fixed by design, not settable: what a note may hold, how search and condensing match, inbox and promotion limits.
# What depends on the vault (stability of notes, sleep budget) is learned from its logs, see usage.py.
LIMITS = {"title_words": 16, "body_words": 600, "links": 8, "min_coverage": 0.5}
LIFECYCLE = {"inbox_wait_days": 30, "new_per_day": 3, "condense_coverage": 0.6, "max_promotion_rate": 0.25, "dream_pairs": 5}
RETIRED = ("limits", "activation", "lifecycle")  # sb.toml sections of sb <= 0.2.1; read no more, reported by check


class SbError(Exception):
    pass


class ParseError(SbError):
    def __init__(self, where: str, msg: str):
        super().__init__(f"{where}: {msg}")
        self.where, self.msg = where, msg


def clean(s) -> str:
    """One line: tabs, line breaks and runs of blanks become a single space."""
    return " ".join(str(s).split())


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def read_raw(path) -> str:
    """Text of a note exactly as stored (UTF-8, line endings kept); a file that cannot be read is an SbError."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeError) as e:
        raise SbError(f"{path}: cannot read file: {e}") from e


def write_atomic(path, text: str, mode=None):
    """Replace path by text via a temporary file in the same folder and os.replace, so a failed write leaves the old file
    whole. Line endings are written as given. The file keeps its mode (a new one gets mode, if given); a file without
    write permission is not replaced, as writing it in place would fail too."""
    real = Path(os.path.realpath(path))  # through a symlink, not over it
    try:
        st = os.stat(real)
    except FileNotFoundError:
        st = None
    if st is not None and not os.access(real, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
    tmp = real.with_name(f".{real.name}.{os.urandom(4).hex()}.tmp")  # matches TEMP
    try:
        with open(tmp, "x", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if (m := stat.S_IMODE(st.st_mode) if st is not None else mode) is not None:
            os.chmod(tmp, m)
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def unquote(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v


def quote(v: str) -> str:
    return '"%s"' % v.replace('"', "'") if v and (v[0] in "[\"'" or v != v.strip()) else v


def parse_value(key: str, v: str, where: str):
    if v.startswith("["):
        if not v.endswith("]"):
            raise ParseError(where, "unclosed list, expected [a, b]")
        val, inner, pos = [], v[1:-1], 0
        while True:  # every item is "quoted" or free of quotes; anything else is an error, not a dropped item
            m = ITEM.match(inner, pos)
            if not m:
                raise ParseError(where, f"bad list item near {inner[pos:pos + 20]!r}")
            if (x := unquote(m[1].strip())):
                val.append(x)
            if not m[0].endswith(","):
                break
            pos = m.end()
    else:
        val = unquote(v)
    if (key in LISTKEYS) != isinstance(val, list):
        raise ParseError(where, f"'{key}' expects " + ("a list [a, b]" if key in LISTKEYS else "a single value"))
    return val


def parse_frontmatter(text: str, name: str):
    """Return (meta, key->line, body, first body line)."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        if lines[0].startswith("\ufeff"):
            raise ParseError(f"{name}:1", "file must start with '---', not with a byte order mark (BOM)")
        raise ParseError(f"{name}:1", "file must start with '---'")
    meta, where = {}, {}
    for i in range(1, len(lines)):
        ln, n = lines[i].rstrip(), i + 1
        if ln == "---":
            return meta, where, "\n".join(lines[i + 1:]), n + 1
        if not ln:
            continue
        m = KEY.match(ln)
        if not m:
            raise ParseError(f"{name}:{n}", f"expected 'key: value', got {ln[:40]!r}")
        if m[1] in meta:
            raise ParseError(f"{name}:{n}", f"duplicate key '{m[1]}'")
        meta[m[1]], where[m[1]] = parse_value(m[1], (m[2] or "").strip(), f"{name}:{n}"), n
    raise ParseError(f"{name}:{len(text.rstrip().splitlines())}", "frontmatter is not closed with '---'")


@dataclass
class Note:
    rel: str            # path relative to the vault, posix
    meta: dict
    lines: dict
    body: str
    body_line: int
    raw: str

    @property
    def location(self): return self.rel.split("/")[0]
    @property
    def slug(self): return self.rel.rsplit("/", 1)[1][:-3]
    @property
    def folder(self): return nfc(self.rel.split("/")[1])  # a folder from an NFD file system names the same project
    def get(self, k, d=""): return self.meta.get(k, d)

    @property
    def gist(self):
        para = []
        for ln in self.body.split("\n"):
            if ln.strip():
                para.append(ln.strip())
            elif para:
                break
        return re.sub(r"^Gist:\s*", "", " ".join(para), flags=re.I)

    @property
    def targets(self):
        return list(dict.fromkeys(t.strip() for t in WIKI.findall(self.body)))

    @property
    def headings(self):
        return set(re.findall(r"^##[ \t]+(.+?)[ \t]*$", self.body, flags=re.M))


_HELD = threading.local()  # vault root -> depth of Vault.locked() in this thread


class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        try:
            user = tomllib.loads((self.root / "sb.toml").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            raise SbError(f"{self.root / 'sb.toml'}: {e}")
        for k, default in DEFAULTS.items():
            val = user.get(k, default)
            bad = (not isinstance(val, type(default)) or isinstance(val, bool)
                   or (k == "index" and not all(isinstance(x, list) and all(isinstance(s, str) for s in x) for x in val.values()))
                   or (k == "update" and not all(isinstance(x, bool) for x in val.values()))
                   or (isinstance(default, list) and not all(isinstance(x, str) for x in val))
                   or (k == "folder_map" and not all(isinstance(x, str) for x in val.values())))
            if bad:
                raise SbError(f"{self.root / 'sb.toml'}: '{k}' has the wrong type or a non-numeric value")
        self.cfg = {k: (DEFAULTS[k] | user.get(k, {}) if isinstance(DEFAULTS[k], dict) else user.get(k, DEFAULTS[k]))
                    for k in DEFAULTS}
        self.cfg["limits"], self.cfg["lifecycle"] = dict(LIMITS), dict(LIFECYCLE)
        self.ignored = [f"[{k}]" + (" " + ", ".join(user[k]) if isinstance(user[k], dict) else "") for k in RETIRED if k in user]
        self.sb_dir = self.root / ".sb"

    def scan(self, loc: str) -> dict:
        """rel path -> (mtime_ns, size) for <loc>/<project>/*.md."""
        out, base = {}, self.root / loc
        if base.is_dir():
            for d in os.scandir(base):
                if d.is_dir() and not d.name.startswith("."):
                    for f in os.scandir(d.path):
                        if f.name.endswith(".md") and not f.name.startswith("."):
                            try:
                                st = f.stat()
                            except OSError:  # dangling symlink
                                continue
                            out[f"{loc}/{d.name}/{f.name}"] = (st.st_mtime_ns, st.st_size)
        return out

    @contextlib.contextmanager
    def locked(self):
        """Exclusive lock of the vault (flock on .sb/lock) for a short read-modify-write; other sessions wait for it.
        Nested use in one thread does not wait on itself. Without fcntl (not POSIX) or a lock file that cannot be opened
        (read-only vault, a file system without locks) it runs unlocked, as before."""
        held = _HELD.__dict__.setdefault("roots", {})
        key, f = str(self.root), None
        if not held.get(key) and fcntl is not None:
            try:
                self.sb_dir.mkdir(exist_ok=True)
                f = open(self.sb_dir / "lock", "a")
                fcntl.flock(f, fcntl.LOCK_EX)
            except OSError:
                if f is not None:
                    f.close()
                f = None
        held[key] = held.get(key, 0) + 1
        try:
            yield
        finally:
            held[key] -= 1
            if f is not None:
                f.close()  # closing the file releases the lock

    def ignore_lock(self):
        """A .gitignore written by `sb init` before the lock existed (it ignores .sb/index.sqlite) gets .sb/lock as well,
        once; any other is left alone."""
        p = self.root / ".gitignore"
        try:
            text = p.read_text(encoding="utf-8")
            lines = [x.strip() for x in text.splitlines()]
            if any(x.startswith(".sb/index.sqlite") for x in lines) and ".sb/lock" not in lines:
                with open(p, "a", encoding="utf-8") as f:
                    f.write("\n" * (not text.endswith("\n")) + ".sb/lock\n")
        except (OSError, UnicodeError):
            pass

    def temp_files(self) -> list:
        """Relative paths of temporary files that write_atomic left behind (a killed process) in .sb/ and the project folders."""
        dirs = [self.sb_dir] + [d for loc in LOCATIONS if (self.root / loc).is_dir() for d in (self.root / loc).iterdir()]
        return sorted(f.relative_to(self.root).as_posix() for d in dirs if d.is_dir() for f in d.iterdir() if TEMP.match(f.name))

    def append(self, name: str, *fields):
        """Append one tab-separated line to .sb/<name> (the logs are never rewritten)."""
        self.sb_dir.mkdir(exist_ok=True)
        line = ("\t".join(clean(x) for x in fields) + "\n").encode("utf-8")
        with open(self.sb_dir / name, "a+b") as f:
            if f.seek(0, 2) and (f.seek(-1, 2), f.read(1))[1] != b"\n":  # a row cut off (crash, editor) stays apart
                line = b"\n" + line
            f.write(line)

    def rows(self, name: str, n: int) -> list:
        """Lines of .sb/<name> as lists of exactly n fields (missing ones empty); no file means no rows."""
        try:
            lines = (self.sb_dir / name).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return []
        return [(r := ln.split("\t", n - 1)) + [""] * (n - len(r)) for ln in lines if ln.strip()]

    def find(self, slug: str, locs=LOCATIONS):
        """Relative path of the note with this slug (notes first), or None; file names compare in NFC."""
        end = f"/{nfc(slug)}.md"
        for loc in locs:
            for rel in self.scan(loc):
                if nfc(rel).endswith(end):
                    return rel
        return None

    def find_all(self, slug: str, locs=LOCATIONS) -> list:
        """Relative paths of all notes with this slug (check calls more than one an error), notes first."""
        end = f"/{nfc(slug)}.md"
        return [rel for loc in locs for rel in self.scan(loc) if nfc(rel).endswith(end)]

    def read(self, rel: str) -> Note:
        try:  # as read_raw: a lone CR stays, so the hash of a note is the same however it was read
            raw = read_raw(self.root / rel).replace("\r\n", "\n")
        except SbError as e:
            raise ParseError(f"{rel}:1", f"cannot read file: {e.__cause__}")
        meta, lines, body, bl = parse_frontmatter(raw, rel)
        return Note(rel, meta, lines, body, bl, raw)

    def bib(self) -> dict:
        """Citation key -> {title, author, year} from the configured BibTeX files."""
        out = {}
        for f in self.cfg["bibtex"]:
            p = self.root / f
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            ms = list(re.finditer(r"@(\w+)\s*\{\s*([^,\s]*)", text))
            for i, m in enumerate(ms):
                if m[1].lower() in ("string", "comment", "preamble") or not re.match(r"\s*,", text[m.end():]):
                    continue
                chunk = text[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(text)]
                d = {}
                for fm in re.finditer(r'\b(title|author|year)\s*=\s*', chunk, flags=re.I):
                    val = _bibvalue(chunk, fm.end())
                    if val is not None:
                        d[fm[1].lower()] = " ".join(val.split())
                out[m[2]] = d
        return out


def _bibvalue(t: str, i: int):
    """Value at t[i]: {braced, any depth}, "quoted" or a bare number; None if it is none of these."""
    if t.startswith("{", i):
        depth = 0
        for j in range(i, len(t)):
            depth += (t[j] == "{") - (t[j] == "}")
            if not depth:
                return t[i + 1:j]
        return None
    if t.startswith('"', i):
        j = t.find('"', i + 1)
        return t[i + 1:j] if j > 0 else None
    m = re.match(r"\d+", t[i:])
    return m[0] if m else None


def find_vault(start=None) -> Vault:
    env = os.environ.get("SB_VAULT")
    if env:
        if not (Path(env) / "sb.toml").is_file():
            raise SbError(f"SB_VAULT={env} has no sb.toml")
        return Vault(Path(env))
    here = Path(start or os.getcwd()).resolve()
    for d in (here, *here.parents):
        if (d / "sb.toml").is_file():
            return Vault(d)
    raise SbError("no vault found: set SB_VAULT or run inside a directory below an sb.toml")
