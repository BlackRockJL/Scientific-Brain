"""Vault location, configuration, notes and the strict frontmatter subset."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

LISTKEYS = {"sources", "superseded_by"}
SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*\Z")  # \Z: "$" would accept a trailing newline
WIKI = re.compile(r"\[\[([^\]\n]+)\]\]")
KEY = re.compile(r"([a-z_][a-z0-9_]*):(?:\s+(.*))?$")
LOCATIONS = ("notes", "archive")
DEFAULTS = {"projects": [], "folder_map": {}, "source_roots": [], "bibtex": [],
            "limits": {"title_words": 12, "body_words": 300, "links": 5, "min_coverage": 0.5},
            "activation": {"create": 1.0, "show": 1.0, "edit": 1.0, "veto": 0.5, "recall": 0.2,
                           "h": 90, "h_decision": 180, "h_derivation": 180},
            "lifecycle": {"inbox_wait_days": 30, "sleep_days": 7, "sleep_gap": 1, "sleep_inbox": 30, "new_per_day": 3,
                          "sleep_budget": 40, "forget_budget": 10, "condense_pairs": 5, "condense_coverage": 0.6,
                          "max_promotion_rate": 0.25},
            "index": {"skip_sections": []}}
RATES = {"min_coverage", "condense_coverage", "max_promotion_rate"}  # shares; the other limits and lifecycle values are counts


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
        val = [x for x in (unquote(i.strip()) for i in re.findall(r'\s*("[^"]*"|[^,"]*)\s*(?:,|$)', v[1:-1])) if x]
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
        return set(re.findall(r"^##\s+(.+?)\s*$", self.body, flags=re.M))


class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        try:
            user = tomllib.loads((self.root / "sb.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise SbError(f"{self.root / 'sb.toml'}: {e}")
        for k, default in DEFAULTS.items():
            val = user.get(k, default)
            bad = (not isinstance(val, type(default)) or isinstance(val, bool)
                   or (k in ("limits", "lifecycle") and not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0
                                                              for y, x in val.items() if y not in RATES))
                   or (k == "index" and not all(isinstance(x, list) and all(isinstance(s, str) for s in x) for x in val.values()))
                   or (isinstance(default, dict) and k not in ("folder_map", "index") and not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                              for x in val.values()))
                   or (isinstance(default, list) and not all(isinstance(x, str) for x in val))
                   or (k == "folder_map" and not all(isinstance(x, str) for x in val.values())))
            if bad:
                raise SbError(f"{self.root / 'sb.toml'}: '{k}' has the wrong type or a non-numeric value")
        self.cfg = {k: (DEFAULTS[k] | user.get(k, {}) if isinstance(DEFAULTS[k], dict) else user.get(k, DEFAULTS[k]))
                    for k in DEFAULTS}
        self.sb_dir = self.root / ".sb"

    def scan(self, loc: str) -> dict:
        """rel path -> (mtime_ns, size) for <loc>/<project>/*.md."""
        out, base = {}, self.root / loc
        if base.is_dir():
            for d in os.scandir(base):
                if d.is_dir() and not d.name.startswith("."):
                    for f in os.scandir(d.path):
                        if f.name.endswith(".md") and not f.name.startswith("."):
                            st = f.stat()
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
            lines = (self.sb_dir / name).read_text(encoding="utf-8").split("\n")
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
            ms = list(re.finditer(r"@\w+\s*\{\s*([^,\s]+)\s*,", text))
            for i, m in enumerate(ms):
                chunk = text[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(text)]
                d = {}
                for fm in re.finditer(r'\b(title|author|year)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|"([^"]*)"|(\d+))',
                                      chunk, flags=re.I):
                    d[fm[1].lower()] = " ".join((fm[2] or fm[3] or fm[4] or "").split())
                out[m[1]] = d
        return out


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
