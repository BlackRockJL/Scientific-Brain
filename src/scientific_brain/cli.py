"""Command line interface of `sb`."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median

from . import __version__, inbox, sleep, usage
from .index import Index, fts5_available, match_query
from .links import WHYS, archive_mark, archive_note, backlinks, rename, restore
from .schema import TYPES, check, project_error, skeleton
from .vault import DEFAULTS, LOCATIONS, SLUG, SbError, find_vault

SB_TOML = """# Scientific Brain vault configuration. Paths are relative to this file.
# projects = ["solver", "_general"]          # allowed projects; empty means any
# folder_map = { "code/solver" = "solver" }  # working folder -> project (used by capture); relative to this
#                                            # file or absolute; the longest folder containing the cwd wins
# source_roots = ["../project"]              # where `sources:` paths are resolved
# bibtex = ["refs.bib"]                      # BibTeX files for `ref:` of source notes
# [limits]
# title_words = 12
# body_words = 300
# links = 5
# min_coverage = 0.5                         # share of the search terms a match must contain
# [activation]                               # weights per event, horizon H in days (h, h_<type>)
# recall = 0.2
# h = 90
# h_decision = 180
# [lifecycle]
# inbox_wait_days = 30                       # an open line older than this is due
# sleep_days = 7                             # "sleep due" after this many days without sleep ...
# sleep_inbox = 30                           # ... or with more open inbox lines than this ...
# sleep_gap = 1                              # ... or a full plan of work waits and this many days have passed
# new_per_day = 3                            # `new` warns once this many notes were created today
# sleep_budget = 40                          # notes and inbox lines one sleep plan touches at most
# forget_budget = 10                         # forget candidates before verify; more only if budget is left
# condense_pairs = 5                         # near-duplicate pairs before verify; more only if budget is left
# condense_coverage = 0.6                    # share of a note's title and cue words found in a near-duplicate
# max_promotion_rate = 0.25                  # `sleep done` warns above this share of promoted inbox lines
# [index]
# skip_sections = []                         # headings whose sections stay out of the full-text search
"""


def _sync(v, idx, full=False):
    """Refresh the index; notes found changed on disk count as edited (usage log)."""
    for slug in idx.refresh(full):
        usage.log(v, "edit", slug)
    return idx


def _vault_index(full=False):
    v = find_vault()
    return v, _sync(v, Index(v), full)


def cmd_init(a):
    root = Path(a.path).resolve()
    if (root / "sb.toml").exists():
        raise SbError(f"{root} is already a vault")
    if not fts5_available():
        raise SbError("this Python's SQLite lacks FTS5 or the trigram tokenizer (SQLite >= 3.34 is required)")
    for d in ("notes", "archive", ".sb"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "sb.toml").write_text(SB_TOML, encoding="utf-8")
    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text(".sb/index.sqlite*\n.sb/sleeping\n", encoding="utf-8")
    print(f"initialised vault at {root}")


def cmd_recall(a):
    v, idx = _vault_index()
    terms = [w for t in a.terms for w in t.split()]
    if match_query(terms)[0] is None:
        raise SbError("no usable search terms")
    locs = ("inbox",) if a.inbox else LOCATIONS if a.archive else ("notes",)
    rows, ignored = idx.search(terms, locs, a.project, a.n)
    if ignored:
        print("ignored (not searchable): " + ", ".join(ignored))
    if not rows:
        print("no match")
    for i, (slug, proj, typ, loc, title, gist, cue) in enumerate(rows):
        if loc == "inbox":  # title holds the capture date, cue the source
            print(f"{slug}  [inbox, {proj}, {title}]\n  {gist}" + (f"\n  source: {cue}" if cue else ""))
            continue
        arch = v.read(v.find(slug)) if loc == "archive" else None
        print(("WRONG: " if arch and arch.get("archived_why") == "wrong" else "") + f"{slug}  [{typ}, {proj}]"
              + (f" ({archive_mark(arch)})" if arch else ""))
        print(f"  {title}\n  {gist}\n  cue: {cue}")
        if loc == "notes" and i == 0:  # only the first hit of a query counts as used
            usage.log(v, "recall", slug)
    if not a.archive and not a.inbox and (n := idx.count(terms, ("archive",), a.project)):
        print(f"+{n} archived matches (use --archive)")
    if not a.inbox and (n := idx.count(terms, ("inbox",), a.project)):
        print(f"+{n} inbox matches (use --inbox)")
    if (due := usage.sleep_due(v, idx.n_notes(), idx.n_inbox(), usage.today())):
        print(due)


def cmd_show(a):
    v, idx = _vault_index()
    rel = v.find(a.slug)
    if not rel:
        raise SbError(f"no note '{a.slug}'")
    n = v.read(rel)
    usage.log(v, "show", n.slug)
    tag = ""
    if n.location == "archive":
        tag = f" ({archive_mark(n)})"
    print(f"== {n.slug}{tag} ==\n{n.raw.rstrip()}\n--")
    if (ref := n.get("ref")):
        e = v.bib().get(ref)
        print("reference: " + (f"{e.get('title', '?')} | {e.get('author', '?')} | {e.get('year', '?')}" if e
                               else f"{ref} (no BibTeX entry)"))
    bl = backlinks(v, idx, a.slug)
    print("backlinks: " + ("; ".join(f"{s} ({t})" for s, t in bl) if bl else "none"))


def cmd_map(a):
    v, idx = _vault_index()
    rows, proj, typ = idx.listing(a.project), None, None
    for p, t, slug, title in rows:
        if p != proj:
            print(f"[{p}]")
            proj, typ = p, None
        if t != typ:
            print(f"  {t}")
            typ = t
        print(f"    {slug} - {title}")
    if not rows:
        print("(no notes)")


def cmd_new(a):
    v = find_vault()
    if a.type not in TYPES:
        raise SbError(f"unknown type '{a.type}' (one of {', '.join(TYPES)})")
    if not SLUG.match(a.slug):
        raise SbError(f"'{a.slug}' is not a valid slug (lower-case letters, digits, hyphens)")
    if (old := v.find(a.slug)):
        raise SbError(f"slug '{a.slug}' already exists at {old}")
    if (m := project_error(v, a.project)):
        raise SbError(m)
    if not a.why.strip():
        raise SbError("--why must state the rank of the note")
    idx = _sync(v, Index(v))
    day = usage.today()
    if usage.creates_today(v, day) >= v.cfg["lifecycle"]["new_per_day"]:
        print(f"warning: {usage.creates_today(v, day)} notes were created today already; capture instead unless this has rank")
    rows, _ = idx.search(a.slug.split("-") + a.why.split(), LOCATIONS, None, 5)
    print("similar notes (extend one instead if it fits):" if rows else "no similar notes")
    for slug, proj, typ, loc, title, *_ in rows:
        print(f"  {slug}  [{typ}, {proj}]{' (archived)' if loc == 'archive' else ''} {title}")
    path = v.root / "notes" / a.project / f"{a.slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(skeleton(a.type, a.project, a.why, day.isoformat()))
    usage.log(v, "create", a.slug, day)
    print(f"created {path.relative_to(v.root).as_posix()} - fill every TODO, then run: sb check")


def cmd_check(a):
    v, idx = _vault_index(a.rebuild)
    only = idx.unchecked() if a.changed else None
    issues, n = check(v, only)
    for i in issues:
        print(i)
    bad = {i.where.rsplit(":", 1)[0] for i in issues if i.level == "error"}
    idx.mark_checked((only if only is not None else set(v.scan("notes"))) - bad)
    errs = sum(i.level == "error" for i in issues)
    print(f"checked {n} notes: {errs} errors, {len(issues) - errs} warnings")
    return 1 if errs else 0


def cmd_rename(a):
    v, idx = _vault_index()
    paths = rename(v, a.old, a.new)
    idx.update(paths)
    usage.log_rename(v, a.old, a.new)
    print(f"renamed {a.old} -> {a.new}; links rewritten in {len(paths) - 2} other notes")


def cmd_capture(a):
    v = find_vault()
    if a.project and a.project != "_unsorted" and (m := project_error(v, a.project)):
        raise SbError(m)
    print(inbox.capture(v, " ".join(a.text), a.project or inbox.project_for_cwd(v), a.source, usage.today()))


def cmd_inbox(a):
    v, day = find_vault(), usage.today()
    if a.sub == "resolve":
        outcome = f"note:{a.note}" if a.note else "dropped" if a.drop else "wait"
        inbox.resolve(v, a.id, outcome, day)
        print(f"{a.id}: {outcome}")
        return
    rows = [(r, inbox.age(r, day)) for r in inbox.open_lines(v) if not a.project or r[2] == a.project]
    rows = [x for x in rows if not a.due or x[1] > v.cfg["lifecycle"]["inbox_wait_days"]]
    for (id, _, proj, src, text), n in rows:
        print(f"{id}  {n}d  {proj}  {text}" + (f"  ({src})" if src else ""))
    if not rows:
        print("(no open inbox lines)")


def cmd_archive(a):
    v, idx = _vault_index()
    dst, inc, fixed = archive_note(v, a.slug, a.why, a.reason, a.superseded_by, a.fix_links, usage.today(), idx)
    usage.log(v, "archive", a.slug)
    print(f"archived {a.slug} ({a.why}) -> {dst}")
    print(f"incoming links from: {', '.join(inc)}" if inc else "no incoming links")
    if inc:
        print(f"links repaired in {len(fixed)} notes" if a.fix_links else "sb check warns about them; --fix-links repairs them")


def cmd_restore(a):
    v, idx = _vault_index()
    paths = restore(v, a.slug, a.force)
    idx.update(paths)
    print(f"restored {a.slug} -> {paths[0]}")


def cmd_stats(a):
    v, idx = _vault_index()
    day = usage.today()
    rows, op = idx.counts(), inbox.open_lines(v)
    where = Counter(r[0] for r in rows)
    print(f"notes: {where['notes']}, archive: {where['archive']}")
    for name, i in (("type", 2), ("project", 1)):
        print(f"by {name}: " + ", ".join(f"{k} {n}" for k, n in sorted(Counter(r[i] for r in rows if r[0] == "notes").items())))
    ages = [inbox.age(r, day) for r in op]
    print(f"inbox: {len(op)} open" + (f", oldest {max(ages)} days" if ages else ""))
    p, t = inbox.promotion(v)
    print("promotion rate: " + (f"{p / t:.0%} ({p} of {t} settled)" if t else "n/a"))
    sc = usage.scores(v, day)
    act = [x[3] for x in sc]
    print("activation A: " + (f"min {min(act):.2f}, median {median(act):.2f}, max {max(act):.2f}" if act else "n/a"))
    print(f"forget candidates: {len(usage.forget_candidates(v, day, sc))}")
    print(f"new notes in the last 7 days: {sum((day - x[2]).days < 7 for x in sc)}")
    work = sleep.backlog(v, day)
    budget = v.cfg["lifecycle"]["sleep_budget"]
    print(f"waiting for a sleep: {sum(work.values())} (" + ", ".join(f"{k} {x}" for k, x in work.items())
          + f"), about {-(-sum(work.values()) // budget)} sleeps of {budget}")
    last = usage.last_sleep(v)
    print("last sleep: " + (f"{last} ({(day - last).days} days ago)" if last else "never"))
    if (due := usage.sleep_due(v, where["notes"], len(op), day, work)):
        print(due)


def cmd_sleep(a):
    day = usage.today()
    if a.sub == "begin":
        print(sleep.begin(find_vault(), day, a.force))
        return
    if a.sub == "baseline":
        n = sleep.baseline(find_vault())
        print(f"baseline: all notes count as checked in their current form ({n} were unchecked); verified is unchanged")
        return
    v, idx = _vault_index()
    if a.sub == "plan":
        text = sleep.plan(v, idx, day)
        print(f"wrote {a.out}" if a.out and Path(a.out).write_text(text, encoding="utf-8") else text, end="")
    elif a.sub == "veto":
        sleep.veto(v, a.slug, a.reason, day)
        print(f"veto for {a.slug} logged")
    elif a.sub == "verified":
        if sleep.started(v) is None:
            raise SbError("sleep verified works only during a sleep (sb sleep begin)")
        idx.update(sleep.mark_verified(v, a.slugs, day))
        print("verified: " + ", ".join(a.slugs))
    else:
        print("\n".join(sleep.finish(v, a.summary, day)))


def cmd_hook(a):
    """Claude Code hooks: silent unless no vault is found, a sleep is due or an edited note fails `check`."""
    try:
        if a.event == "session-start":
            try:
                v, idx = _vault_index()
            except SbError as e:
                print(f"Scientific Brain: {e} (see README, 'Where the vault lives').")
                return 0
            n = sum(r[0] == "notes" for r in idx.counts())
            day = usage.today()
            if (due := usage.sleep_due(v, n, len(inbox.open_lines(v)), day, sleep.backlog(v, day))):
                print(f"Scientific Brain: {due}. Use the sleep skill.")
            return 0
        fp = (json.load(sys.stdin).get("tool_input") or {}).get("file_path")
        if not isinstance(fp, str) or not fp or not (path := Path(fp).resolve()).is_file():
            return 0
        v = find_vault(path.parent)
        rel = path.relative_to(v.root).as_posix()
        if path.suffix != ".md" or rel.split("/")[0] != "notes":
            return 0
    except (SbError, ValueError, OSError, IndexError, AttributeError, TypeError):
        return 0
    idx = _sync(v, Index(v))
    only = {rel}  # only the edited note; other notes' errors must not block this edit
    issues, _ = check(v, only)
    errs = [str(i) for i in issues if i.level == "error"]
    idx.mark_checked(only - {i.where.rsplit(":", 1)[0] for i in issues if i.level == "error"})
    if errs:
        print("sb check found errors:\n" + "\n".join(errs), file=sys.stderr)
        return 2
    return 0


def cmd_rules(a):
    from importlib.resources import files
    try:
        cfg = find_vault().cfg
    except SbError as e:
        if not str(e).startswith(("no vault found", "SB_VAULT=")):  # a broken sb.toml is reported
            raise
        cfg = DEFAULTS  # no vault: the defaults
    vals = {**cfg["limits"], **cfg["lifecycle"]}
    text = files("scientific_brain").joinpath("RULES.md").read_text(encoding="utf-8")
    print(re.sub(r"\{(\w+)\}", lambda m: str(vals.get(m[1], m[0])), text), end="")


def count(s):
    """argparse type: an integer of at least 1."""
    try:
        n = int(s)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"{s!r} is not a whole number of at least 1")
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sb", description="Scientific Brain: a lean second brain for research notes.")
    p.add_argument("--version", action="version", version=f"sb {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    cmd("init", cmd_init, "create a vault and check FTS5").add_argument("path", nargs="?", default=".")
    sp = cmd("recall", cmd_recall, "gists of the best matches")
    sp.add_argument("terms", nargs="+")
    sp.add_argument("-n", type=count, default=5)
    sp.add_argument("-p", "--project")
    sp.add_argument("--archive", action="store_true", help="include archived notes")
    sp.add_argument("--inbox", action="store_true", help="search only the open inbox lines")
    cmd("show", cmd_show, "full note and backlinks").add_argument("slug")
    cmd("map", cmd_map, "titles by project and type").add_argument("project", nargs="?")
    sp = cmd("new", cmd_new, "create a note skeleton")
    sp.add_argument("type")
    sp.add_argument("slug")
    sp.add_argument("-p", "--project", required=True)
    sp.add_argument("--why", required=True, help="one line: why this note has rank")
    sp = cmd("check", cmd_check, "validate schema, limits, links, sources")
    sp.add_argument("--changed", action="store_true", help="only notes not checked since their last change")
    sp.add_argument("--rebuild", action="store_true", help="rebuild the whole index first, archive included")
    sp = cmd("rename", cmd_rename, "rename a note and rewrite links")
    sp.add_argument("old")
    sp.add_argument("new")
    sp = cmd("capture", cmd_capture, "append a line to the inbox")
    sp.add_argument("text", nargs="+")
    sp.add_argument("-p", "--project")
    sp.add_argument("-s", "--source", default="")
    sp = cmd("inbox", cmd_inbox, "list open inbox lines, or settle one with 'resolve'")
    sp.add_argument("--due", action="store_true", help="only lines older than inbox_wait_days")
    sp.add_argument("-p", "--project")
    sp = sp.add_subparsers(dest="sub").add_parser("resolve", help="settle one line")
    sp.add_argument("id")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--note", metavar="SLUG", help="promoted to this existing note")
    g.add_argument("--drop", action="store_true")
    g.add_argument("--wait", action="store_true")
    sp = cmd("archive", cmd_archive, "move a note to the archive")
    sp.add_argument("slug")
    sp.add_argument("--why", required=True, choices=WHYS, help="faded: unused; superseded: replaced; wrong: found false")
    sp.add_argument("--reason", help="one sentence; required for wrong, and for superseded without --superseded-by")
    sp.add_argument("--superseded-by", nargs="+", default=[], metavar="SLUG")
    sp.add_argument("--fix-links", action="store_true", help="repair links from notes/ to the archived note")
    sp = cmd("restore", cmd_restore, "move an archived note back")
    sp.add_argument("slug")
    sp.add_argument("--force", action="store_true", help="restore a note that was archived as wrong")
    sp = cmd("sleep", cmd_sleep, "run a sleep: begin, plan, veto, verified, done; baseline").add_subparsers(dest="sub", required=True)
    sp.add_parser("begin", help="start a sleep").add_argument("--force", action="store_true", help="replace a running sleep")
    sp.add_parser("plan", help="candidate lists for the four phases").add_argument("-o", "--out", help="write to this file")
    q = sp.add_parser("veto", help="keep a forget candidate")
    q.add_argument("slug")
    q.add_argument("reason")
    sp.add_parser("verified", help="mark notes as checked").add_argument("slugs", nargs="+")
    sp.add_parser("done", help="end the sleep").add_argument("summary")
    sp.add_parser("baseline", help="accept all notes as checked in their current form, e.g. after an import")
    cmd("stats", cmd_stats, "counts, activation, inbox, last sleep")
    cmd("rules", cmd_rules, "print the agent rules")
    cmd("hook", cmd_hook, "entry point for Claude Code hooks").add_argument("event", choices=("after-edit", "session-start"))
    a = p.parse_args(argv)
    try:
        usage.today()
    except ValueError:
        print(f"sb: error: SB_TODAY must be YYYY-MM-DD, got {os.environ['SB_TODAY']!r}", file=sys.stderr)
        return 2
    try:
        return a.fn(a) or 0
    except SbError as e:
        print(f"sb: error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
