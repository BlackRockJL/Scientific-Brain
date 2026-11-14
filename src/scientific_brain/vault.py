"""Vault location, configuration, notes and the strict frontmatter subset."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

LISTKEYS = {"sources", "superseded_by"}
SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*\Z")  # \Z: "$" would accept a trailing newline
WIKI = re.compile(r"\[\[([^\]\n]+)\]\]")
ITEM = re.compile(r'\s*("[^"]*"|[^,"]*)\s*(?:,|$)')
KEY = re.compile(r"([a-z_][a-z0-9_]*):(?:\s+(.*))?$")
LOCATIONS = ("notes", "archive")
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
    def folder(self): return self.rel.split("/")[1]
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

    def append(self, name: str, *fields):
        """Append one tab-separated line to .sb/<name> (the logs are never rewritten)."""
        self.sb_dir.mkdir(exist_ok=True)
        with open(self.sb_dir / name, "a", encoding="utf-8") as f:
            f.write("\t".join(clean(x) for x in fields) + "\n")

    def rows(self, name: str, n: int) -> list:
        """Lines of .sb/<name> as lists of exactly n fields (missing ones empty); no file means no rows."""
        try:
            lines = (self.sb_dir / name).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return []
        return [(r := ln.split("\t", n - 1)) + [""] * (n - len(r)) for ln in lines if ln.strip()]

    def find(self, slug: str, locs=LOCATIONS):
        """Relative path of the note with this slug (notes first), or None."""
        for loc in locs:
            for rel in self.scan(loc):
                if rel.endswith(f"/{slug}.md"):
                    return rel
        return None

    def read(self, rel: str) -> Note:
        try:
            raw = (self.root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError) as e:
            raise ParseError(f"{rel}:1", f"cannot read file: {e}")
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
