"""Note types, required fields and sections, limits, `check`, note skeletons."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .vault import LOCATIONS, SLUG, ParseError, Vault, quote

COMMON = ("title", "type", "project", "created", "updated", "cue")
ARCHIVED = ("archived", "archived_why", "archived_reason")  # only in archive/; the first two are required there
ALLOWED = set(COMMON) | {"sources", "ref", "why", "verified", "superseded_by", *ARCHIVED}
# type -> (extra required keys, required sections)
TYPES = {"finding": (("sources",), ()), "decision": ((), ("Rejected",)), "derivation": (("sources",), ()),
         "source": (("ref",), ()), "question": ((), ("Depends on",)), "concept": ((), ())}
DOI = re.compile(r"10\.\d{4,9}/\S+$")
# placeholders as `sb new` writes them (a value `TODO`, `[TODO]`, a line `- TODO` or `TODO: ...`), not the word in prose
TODO = re.compile(r"^(?:- )?TODO(?::|$)|^[a-z_]+:\s*\[?TODO\]?$")


@dataclass
class Issue:
    level: str
    where: str
    msg: str

    def __str__(self): return f"{self.where}: {self.level}: {self.msg}"


def project_error(v: Vault, project: str):
    if project == "_unsorted":
        return "'_unsorted' is for the inbox only"
    allowed = v.cfg["projects"]
    if allowed and project not in allowed and project != "_general":
        return f"unknown project '{project}' (sb.toml projects: {', '.join(allowed)})"
    return None


def skeleton(typ: str, project: str, why: str, today: str) -> str:
    extra, sections = TYPES[typ]
    fm = [("title", "TODO"), ("type", typ), ("project", project), ("created", today), ("updated", today),
          ("cue", "TODO")]
    fm += [("sources", "[TODO]")] if "sources" in extra else [("ref", "TODO")] if "ref" in extra else []
    fm.append(("why", quote(" ".join(why.split()))))
    body = "".join(f"\n## {s}\n- TODO\n" for s in sections)
    return "---\n" + "".join(f"{k}: {x}\n" for k, x in fm) + "---\n\nTODO: one or two sentences, the gist.\n" + body


def _date_ok(s: str) -> bool:
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)) and bool(date.fromisoformat(s))
    except ValueError:
        return False


def check(v: Vault, only=None):
    """Check all notes, or only the given relative paths. Returns (issues, number checked)."""
    issues: list[Issue] = []
    add = lambda lvl, where, msg: issues.append(Issue(lvl, where, msg))
    lim, bib = v.cfg["limits"], v.bib()
    for f in v.cfg["bibtex"]:
        if not (v.root / f).is_file():
            add("warning", "sb.toml:1", f"BibTeX file not found: {f}")
    files = {loc: sorted(v.scan(loc)) for loc in LOCATIONS}
    slugs: dict[str, list[str]] = {}
    for loc in LOCATIONS:
        for rel in files[loc]:
            slugs.setdefault(rel.rsplit("/", 1)[1][:-3], []).append(rel)
    for s, rels in slugs.items():
        for rel in rels[1:]:
            add("error", f"{rel}:1", f"slug '{s}' is also used by {rels[0]}")
    roots = [v.root / r for r in v.cfg["source_roots"]]
    refs, graph, n_checked = {}, {}, 0
    for rel in (r for loc in LOCATIONS for r in files[loc] if only is None or r in only):
        n_checked += 1
        try:
            n = v.read(rel)
        except ParseError as e:
            add("error", e.where, e.msg)
            continue
        at = lambda k: f"{rel}:{n.lines.get(k, 1)}"
        err = lambda k, msg: add("error", at(k), msg)
        if not SLUG.match(n.slug):
            err("title", f"slug '{n.slug}' must be lower-case letters, digits and hyphens")
        for k in n.meta:
            if k not in ALLOWED:
                err(k, f"unknown field '{k}'")
        for k in COMMON:
            if not n.get(k):
                err(k, f"missing field '{k}'")
        typ = n.get("type")
        if typ and typ not in TYPES:
            err("type", f"unknown type '{typ}' (one of {', '.join(TYPES)})")
        extra, sections = TYPES.get(typ, ((), ()))
        for k in extra:
            if not n.get(k):
                err(k, f"type '{typ}' requires '{k}'")
        for k in ARCHIVED:
            if n.location != "archive" and n.get(k):
                err(k, f"{k} is only allowed in archive/")
        for k in ARCHIVED[:2] * (n.location == "archive"):
            if not n.get(k):
                err(k, f"archived notes require '{k}'")
        if n.get("archived_why") and n.get("archived_why") not in ("faded", "superseded", "wrong"):
            err("archived_why", "archived_why must be faded, superseded or wrong")
        for k in ("created", "updated", "verified", "archived"):
            if n.get(k) and not _date_ok(n.get(k)):
                err(k, f"'{k}' must be a date YYYY-MM-DD")
        if n.get("project") and n.get("project") != n.folder:
            err("project", f"project '{n.get('project')}' does not match folder '{n.folder}'")
        if n.get("project") and (m := project_error(v, n.get("project"))):
            err("project", m)
        if n.get("superseded_by"):
            if n.location != "archive":
                err("superseded_by", "superseded_by is only allowed in archive/")
            graph[n.slug] = n.get("superseded_by")
            for t in graph[n.slug]:
                if t not in slugs:
                    err("superseded_by", f"superseded_by target '{t}' does not exist")
        if not n.gist or n.gist.startswith("#"):
            add("error", f"{rel}:{n.body_line}", "missing gist (the first paragraph after the frontmatter)")
        for s in sections:
            if s not in n.headings:
                add("error", f"{rel}:{n.body_line}", f"type '{typ}' requires a section '## {s}'")
        if n.location == "notes":
            for ok, msg in ((len(n.get("title").split()) <= lim["title_words"], f"title has more than {lim['title_words']} words"),
                            (len(n.body.split()) <= lim["body_words"], f"body has more than {lim['body_words']} words"),
                            (len(n.targets) <= lim["links"], f"more than {lim['links']} links")):
                if not ok:
                    add("error", f"{rel}:{n.body_line}", msg)
        for t in n.targets:
            ln = next((i for i, x in enumerate(n.body.split("\n")) if f"[[{t}]]" in x), 0) + n.body_line
            if t not in slugs:
                add("error", f"{rel}:{ln}", f"link to unknown note [[{t}]]")
            elif n.location == "notes" and slugs[t][0].startswith("archive/"):
                add("warning", f"{rel}:{ln}", f"link to archived note [[{t}]]")
        for p in n.get("sources", []):
            if roots and p != "TODO" and "://" not in p and not any((r / p).exists() for r in roots):
                add("warning", at("sources"), f"source path not found in source_roots: {p}")
        ref = n.get("ref")
        if ref:
            if not (DOI.match(ref) or ref in bib):
                err("ref", f"unknown citation key '{ref}' (not in the BibTeX files, not a DOI)")
            if refs.setdefault(ref, rel) != rel:
                err("ref", f"ref '{ref}' already used by {refs[ref]}")
        if (i := next((i for i, x in enumerate(n.raw.split("\n")) if TODO.search(x.strip())), None)) is not None:
            add("error", f"{rel}:{i + 1}", "unfilled TODO placeholder")
    seen: dict = {}

    def dfs(u, path):  # depth-first search for a superseded_by cycle
        seen[u] = 1
        for t in graph.get(u, ()):
            if seen.get(t) == 1:
                return path + [u, t]
            if t in graph and t not in seen and (c := dfs(t, path + [u])):
                return c
        seen[u] = 2

    for s in graph:
        if s not in seen and (c := dfs(s, [])):
            add("error", f"{slugs[s][0]}:1", "superseded_by cycle: " + " -> ".join(c))
    return issues, n_checked
