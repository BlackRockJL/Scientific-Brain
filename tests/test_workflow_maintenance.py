"""Workflows of upkeep, each played through the CLI from start to end with a check after every step: the reconsolidate
chain of a wrong note, the archive routes, the form of a real sb.toml, logs of older versions, init without FTS5,
an import that fades note by note, and the closing of inbox lines. Days pass through SB_TODAY, never through edited logs
(except where the test is about an old log)."""
import hashlib
import re
import shutil
from pathlib import Path

import pytest
from helpers import add_note, init_vault, sb, write_log

from scientific_brain import cli, sleep
from scientific_brain.vault import Vault

D0 = "2026-11-01"


@pytest.fixture
def at(monkeypatch):
    """Set the day of the following calls: at("2026-11-05")."""
    return lambda d: monkeypatch.setenv("SB_TODAY", d)


def ok(v, *args):
    code, out, err = sb(v, *args)
    assert code == 0, f"sb {' '.join(args)} exited {code}\n{out}\n{err}"
    return out


def fails(v, *args, code=2):
    got, out, err = sb(v, *args)
    assert got == code, f"sb {' '.join(args)} exited {got}, expected {code}\n{out}\n{err}"
    return out + err


def tsv(v, name):
    p = Path(v) / ".sb" / name
    return [ln.split("\t") for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def waiting(v) -> dict:
    """The 'waiting for a sleep' line of `sb stats` as {inbox, verify, forget}."""
    out = ok(v, "stats")
    m = re.search(r"waiting for a sleep: \d+ \(inbox (\d+), verify (\d+), forget (\d+)\)", out)
    assert m, out
    return dict(zip(("inbox", "verify", "forget"), map(int, m.groups())))


def section(plan, n):
    """Text of '## n. ...' up to the next section of a sleep plan."""
    m = re.search(rf"^## {n}\. .*?(?=^## \d\. |\Z)", plan, flags=re.M | re.S)
    assert m, plan
    return m[0]


def verify_slugs(plan):
    """(changed or new notes, most active notes) listed under '## 4. Verify'."""
    s = section(plan, 4)
    return (re.findall(r"^- (\S+) \((?:changed|new)\)", s, flags=re.M), re.findall(r"^- (\S+) \(R=", s, flags=re.M))


def read(v, rel):
    return (Path(v) / rel).read_text(encoding="utf-8")


def forget_candidates(v) -> int:
    return int(re.search(r"forget candidates: (\d+)", ok(v, "stats"))[1])


# ---- L11: a note found wrong while it is used travels recall, archive, fix, restore, verify ---------------------------

def test_reconsolidate_chain_wrong_note_returns_through_verify(tmp_path, at):
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "cfl-limit", title="FTCS stability limit", cue="choosing dt for the explicit heat scheme",
             gist="Gist: FTCS diverges above r = 0.52.")
    add_note(v, "scheme-choice", type="decision", title="Scheme choice", cue="which time stepping to use",
             gist="Gist: Crank-Nicolson was chosen for stiff runs.")
    add_note(v, "old-grid", type="concept", title="Coarse grid convention", cue="meaning of the coarse grid label",
             gist="Gist: the coarse grid has 32 cells per side.")
    add_note(v, "bystander", type="concept", title="Bystander note", cue="unrelated bookkeeping", gist="Gist: nothing to see.")
    assert ok(v, "check").endswith("0 errors, 0 warnings\n")
    ok(v, "sleep", "baseline")
    assert waiting(v)["verify"] == 0

    # use: the main session recalls and shows the note it later finds false
    at("2026-11-05")
    assert "cfl-limit" in ok(v, "recall", "FTCS", "stability", "limit")
    assert "r = 0.52" in ok(v, "show", "cfl-limit")

    # archive as wrong needs the reason and writes nothing without it
    assert "--reason" in fails(v, "archive", "cfl-limit", "--why", "wrong")
    assert (v / "notes/heat/cfl-limit.md").is_file() and not (v / "archive/heat/cfl-limit.md").exists()
    reason = "the limit is r = 0.5, measured at r = 0.52 only through round-off"
    out = ok(v, "archive", "cfl-limit", "--why", "wrong", "--reason", reason)
    assert "archived cfl-limit (wrong)" in out and "no incoming links" in out
    assert not (v / "notes/heat/cfl-limit.md").exists()
    arch = read(v, "archive/heat/cfl-limit.md")
    assert "archived: 2026-11-05\narchived_why: wrong\n" in arch and f"archived_reason: {reason}" in arch
    assert " 0 errors" in ok(v, "check")

    # it is out of the default search, found with --archive and marked WRONG with its reason; show says so too
    plain = ok(v, "recall", "FTCS", "stability", "limit")
    assert "no match" in plain and "+1 archived matches" in plain
    deep = ok(v, "recall", "--archive", "FTCS", "stability", "limit")
    assert deep.startswith("WRONG: cfl-limit") and reason in deep and "archived 2026-11-05: wrong" in deep
    assert f"(archived 2026-11-05: wrong — {reason})" in ok(v, "show", "cfl-limit")

    # restore refuses without --force and moves nothing
    msg = fails(v, "restore", "cfl-limit")
    assert "archived as wrong" in msg and "--force" in msg
    assert (v / "archive/heat/cfl-limit.md").is_file() and not (v / "notes/heat/cfl-limit.md").exists()

    # the text is fixed in place (the file is in archive/), then the note comes back with --force
    fixed = arch.replace("FTCS diverges above r = 0.52.", "FTCS diverges above r = 0.5.")
    assert fixed != arch
    (v / "archive/heat/cfl-limit.md").write_text(fixed, encoding="utf-8")
    assert "restored cfl-limit -> notes/heat/cfl-limit.md" in ok(v, "restore", "--force", "cfl-limit")
    back = read(v, "notes/heat/cfl-limit.md")
    assert not (v / "archive/heat/cfl-limit.md").exists() and "r = 0.5." in back
    assert not re.search(r"^archived", back, flags=re.M) and "archived_reason" not in back
    assert " 0 errors" in ok(v, "check")
    assert tsv(v, "usage.tsv")[-1][:4] == ["2026-11-05", "work", "show", "cfl-limit"]  # restoring is a full review

    # a wrong note comes back unchecked, even when its text is unchanged (second case: scheme-choice)
    awkward = "[1] says Crank-Nicolson holds for r < 0.5: see #12"  # starts like a list, holds a colon and a hash
    ok(v, "archive", "scheme-choice", "--why", "wrong", "--reason", awkward)
    assert awkward in ok(v, "show", "scheme-choice") and awkward in ok(v, "recall", "--archive", "scheme", "choice", "stiff")
    assert " 0 errors" in ok(v, "check")
    ok(v, "restore", "scheme-choice", "--force")
    # a faded note comes back as checked as it was: no force, the snapshot row still matches its text
    before = read(v, "notes/heat/old-grid.md")
    ok(v, "archive", "old-grid", "--why", "faded")
    ok(v, "restore", "old-grid")
    assert read(v, "notes/heat/old-grid.md") == before
    assert waiting(v) == {"inbox": 0, "verify": 2, "forget": 0}  # cfl-limit and scheme-choice, no candidate at once
    at("2026-11-06")
    ok(v, "sleep", "begin")
    plan = ok(v, "sleep", "plan")
    changed, _ = verify_slugs(plan)
    assert sorted(changed) == ["cfl-limit", "scheme-choice"]  # old-grid is not among the changed ones
    assert section(plan, 4).count("(changed)") == 2  # both had a snapshot row: edited, not new
    ok(v, "sleep", "verified", "cfl-limit", "scheme-choice")
    assert "slept 2026-11-06" in ok(v, "sleep", "done", "rechecked the two reopened notes")
    for s, p in (("cfl-limit", "heat"), ("scheme-choice", "heat")):
        assert f"verified: 2026-11-06" in read(v, f"notes/{p}/{s}.md")
    assert " 0 errors" in ok(v, "check")

    # the next plan does not list them; the backlog is empty
    assert waiting(v)["verify"] == 0
    changed, _ = verify_slugs(ok(v, "sleep", "plan"))
    assert changed == []


# ---- L12: the form of a real sb.toml: many projects, folder_map with ../ paths, source_roots = [..] ----------------------

PROJECTS = [f"topic-{i:02d}" for i in range(1, 20)] + ["solver", "teaching"]  # 21 entries; _general is always allowed


def workspace(tmp_path, extra=(), inline=False):
    """A workspace with the vault in ws/vault (as in a real setup: vault beside the code and the drafts it serves);
    folder_map as a table or, as the template of `sb init` shows it, as an inline table."""
    ws = tmp_path / "ws"
    v = ws / "vault"
    assert sb(v, "init", str(v))[0] == 0
    (ws / "abs-work").mkdir()
    (v / "refs.bib").write_text("% references\n", encoding="utf-8")
    pairs = [("../src-tree/solver", "solver"), ("../src-tree/solver/legacy", "topic-01"), ("../Drafts/paper-a", "topic-02"),
             ("../Drafts/paper-b", "topic-02"), ("../Teaching/course", "teaching"), ((ws / "abs-work").as_posix(), "topic-03"), *extra]
    fmap = ("folder_map = { " + ", ".join(f'"{k}" = "{x}"' for k, x in pairs) + " }\n" if inline
            else "[folder_map]\n" + "".join(f'"{k}" = "{x}"\n' for k, x in pairs))
    (v / "sb.toml").write_text(
        "projects = [" + ", ".join(f'"{p}"' for p in PROJECTS) + "]\n"
        'source_roots = [".."]\nbibtex = ["refs.bib"]\n' + (fmap if inline else "") + "\n" + ("" if inline else fmap + "\n") +
        '[index]\nskip_sections = ["Legacy metadata"]\n\n[update]\nauto = false\n\n'
        "[limits]\ntitle_words = 16\nbody_words = 600\nlinks = 8\n", encoding="utf-8")
    return ws, v


@pytest.mark.parametrize("inline", [False, True])
def test_real_form_of_sb_toml_routes_captures_sources_and_search(tmp_path, at, monkeypatch, inline):
    ws, v = workspace(tmp_path, inline=inline)
    at(D0)
    assert ok(v, "check") == "checked 0 notes: 0 errors, 0 warnings\n"
    assert "no longer read" not in ok(v, "stats")

    # capture takes the project of the longest folder_map folder that contains the working folder
    here = [("src-tree/solver/src", "solver"), ("src-tree/solver/legacy/deep", "topic-01"), ("src-tree/solver/legacy", "topic-01"),
            ("Drafts/paper-b/figs", "topic-02"), ("Teaching/course", "teaching"), ("abs-work", "topic-03"),
            ("src-tree/solver-extra", "_unsorted"),  # a sibling whose name starts like a mapped folder is no child of it
            ("src-tree", "_unsorted"), ("vault/notes", "_unsorted"), ("Other", "_unsorted")]
    for i, (folder, want) in enumerate(here):
        (ws / folder).mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(ws / folder)
        ok(v, "capture", f"line number {i} from {folder.replace('/', ' ')}")
        assert tsv(v, "inbox.tsv")[-1][2] == want, (folder, tsv(v, "inbox.tsv")[-1])
    monkeypatch.chdir(ws)
    assert [r[2] for r in tsv(v, "inbox.tsv")] == [w for _, w in here]
    assert ok(v, "inbox", "-p", "topic-02").count("\n") == 1  # filter by project

    # -p is held to the 21 projects (and _general); an unknown one writes nothing
    assert "unknown project 'nowhere'" in fails(v, "capture", "x", "-p", "nowhere")
    assert "unknown project" in fails(v, "new", "concept", "stray", "-p", "nowhere", "--why", "x")
    ok(v, "capture", "general line", "-p", "_general")
    assert len(tsv(v, "inbox.tsv")) == len(here) + 1

    # sources resolve below source_roots = [..], i.e. in the workspace; BibTeX is read from the vault folder
    (ws / "src-tree/solver/run.py").write_text("print('r')\n", encoding="utf-8")
    add_note(v, "run-note", project="solver", extra={"sources": "[src-tree/solver/run.py]"}, title="Run note", cue="where the run script lives")
    add_note(v, "ghost-source", project="solver", extra={"sources": "[src-tree/solver/missing.py]"}, title="Ghost source",
             cue="a source that is gone")
    out = ok(v, "check")
    assert re.search(r"notes/solver/ghost-source\.md:\d+: warning: source path not found in source_roots: src-tree/solver/missing\.py", out)
    assert "run-note" not in out and out.endswith("checked 2 notes: 0 errors, 1 warnings\n")
    (v / "refs.bib").write_text("@article{morton-mayers-2005,\n  title = {Numerical Solution of PDEs},\n  author = {Morton, K.},\n"
                                "  year = {2005}\n}\n", encoding="utf-8")
    add_note(v, "morton-source", type="source", project="topic-05", title="Morton Mayers", cue="textbook on FTCS limits")
    add_note(v, "typo-source", type="source", project="topic-05", extra={"ref": "morton-meyers-2005"}, title="Typo", cue="wrong key")
    out = fails(v, "check", code=1)
    assert "unknown citation key 'morton-meyers-2005'" in out and "morton-source" not in out
    assert "reference: Numerical Solution of PDEs | Morton, K. | 2005" in ok(v, "show", "morton-source")

    # `[index] skip_sections` keeps the section out of the search, the rest of the note stays findable
    add_note(v, "imported-one", type="concept", project="topic-07", title="Imported concept", cue="legacy import check",
             gist="Gist: the quasistatic limit is meant.", body="\n## Legacy metadata\nzebrafishlegacyword was a tag.\n")
    assert "no match" in ok(v, "recall", "zebrafishlegacyword")
    assert "imported-one" in ok(v, "recall", "quasistatic", "limit")

    # unverifiable is refused while a source file exists (found through source_roots), accepted when there is none
    ok(v, "sleep", "begin")
    plan = ok(v, "sleep", "plan")
    changed, _ = verify_slugs(plan)
    assert {"run-note", "ghost-source"} <= set(changed)
    assert "has a source at src-tree/solver/run.py" in fails(v, "sleep", "unverifiable", "run-note", "no neighbour states it")
    ok(v, "sleep", "unverifiable", "ghost-source", "script is gone, nothing to check against")
    assert "1 parked as unverifiable" in ok(v, "sleep", "done", "parked the ghost source")


def test_a_folder_map_project_outside_projects_is_held_to_the_projects_list_like_p(tmp_path, at, monkeypatch):
    ws, v = workspace(tmp_path, extra=[("../Misc/loose", "ghost")])
    at(D0)
    (ws / "Misc/loose").mkdir(parents=True)
    monkeypatch.chdir(ws / "Misc/loose")
    assert "unknown project 'ghost'" in fails(v, "capture", "x", "-p", "ghost")  # given with -p, it is refused
    code, out, err = sb(v, "capture", "same thing from the mapped folder")
    inbox = tsv(v, "inbox.tsv")
    assert code != 0 or not inbox or inbox[-1][2] in PROJECTS + ["_general", "_unsorted"], (out, err, inbox)


# ---- L13: logs written by older versions: the vault behaves as one in the current format -----------------------------

def life(tmp_path, at):
    """A vault with a history in the current format: baseline, use, a rename, a sleep that verifies and parks, edits after it.
    Returns (vault, day of the state)."""
    v = init_vault(tmp_path, "new")
    at(D0)
    add_note(v, "ftcs-limit", title="FTCS stability limit", cue="choosing dt for the explicit heat scheme",
             gist="Gist: FTCS diverges above r = 0.5; see [[cell-grid]].", body="\n## Links\n- [[scheme-choice]] why the scheme was dropped\n")
    add_note(v, "scheme-choice", type="decision", title="Scheme choice", cue="which time stepping to use",
             gist="Gist: Crank-Nicolson for stiff runs.")
    add_note(v, "heat-source", type="source", title="Morton Mayers assessment", cue="textbook on explicit schemes",
             gist="Gist: the textbook states the stability limit without proof.", extra={"ref": "10.1000/xyz123"})
    add_note(v, "cell-grid", project="mesh", type="concept", title="Coarse grid convention", cue="meaning of the coarse grid label",
             gist="Gist: the coarse grid has 32 cells per side.")
    add_note(v, "mesh-deriv", project="mesh", type="derivation", title="Cell volume derivation", cue="volume of a skewed cell",
             gist="Gist: the volume follows from the determinant of the edge matrix, see [[cell-grid]].")
    add_note(v, "edge-question", project="mesh", type="question", title="Edge length open question", cue="whether edges may collapse",
             gist="Gist: can an edge collapse under remeshing?")
    add_note(v, "remesh", project="mesh", title="Remesh outcome", cue="what remeshing did to the cell count",
             gist="Gist: remeshing doubled the cell count.")
    assert ok(v, "check").endswith("0 errors, 0 warnings\n")
    ok(v, "sleep", "baseline")
    ok(v, "show", "remesh")
    at("2026-11-02")
    ok(v, "rename", "remesh", "remesh-run")
    ok(v, "show", "ftcs-limit")
    ok(v, "capture", "dt = 0.1 is stable at r = 0.4", "-s", "runs/sweep.csv", "-p", "heat")
    ok(v, "capture", "remesh doubles the cell count again", "-p", "mesh")
    add_note(v, "gamma", project="mesh", type="concept", title="Gamma bookkeeping", cue="what gamma counts here",
             gist="Gist: gamma counts boundary cells.")
    p = v / "notes/heat/scheme-choice.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8")
    at("2026-11-03")
    ok(v, "sleep", "begin")
    plan = ok(v, "sleep", "plan")
    changed, _ = verify_slugs(plan)
    assert {"scheme-choice", "gamma"} <= set(changed)
    ok(v, "sleep", "verified", "scheme-choice")
    ok(v, "sleep", "unverifiable", "gamma", "nothing states what gamma counts")
    ok(v, "sleep", "unrelated", "ftcs-limit", "remesh-run", "they share words only")
    ok(v, "sleep", "done", "first sleep after the baseline")
    at("2026-11-04")
    p = v / "notes/mesh/cell-grid.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nThe fine grid has 64.\n", encoding="utf-8")
    add_note(v, "late-note", project="mesh", type="concept", title="Late note", cue="added after the sleep", gist="Gist: a late one.")
    at("2026-11-12")
    ok(v, "stats")  # flush the index sync before the logs are copied
    return v


def hashes(v):
    return sleep.snapshot(Vault(v))


def downgrade(v, snapshot_columns=2):
    """Rewrite the logs as sb 0.2.1 wrote them: two-column snapshot with the old digest (three columns, with the number of
    renames, as sb 0.3.0 did), rows without rename counters, sleep rows of eight columns, plus older sleeps with and without a clock
    time."""
    vault = Vault(v)
    notes = sleep.load(vault)
    old = {}
    for s, h in sleep.snapshot(vault).items():
        if s in notes and h == sleep.digest(notes[s]):  # the digest of sb 0.2.1: every line starting with verified:, links in
            old[s] = hashlib.sha1("\n".join(x for x in notes[s].raw.split("\n") if not x.startswith("verified:")).encode()).hexdigest()
        else:
            old[s] = h
    n = len(tsv(v, "renames.tsv"))
    write_log(v, "snapshot.tsv", *[(s, h, n)[:snapshot_columns] for s, h in sorted(old.items())])
    write_log(v, "renames.tsv", *[r[:3] for r in tsv(v, "renames.tsv")])
    write_log(v, "listed.tsv", *[r[:2] for r in tsv(v, "listed.tsv")])
    write_log(v, "unrelated.tsv", *[r[:4] for r in tsv(v, "unrelated.tsv")])
    write_log(v, "sleep.tsv", ("2026-09-01", 3, 1, 0, 1, 0, 0, "before 0.2.1: a date only"),
              ("2026-09-20T12:00", 2, 1, 1, 0, 0, 0, "0.2.1: no plan size"), *[r[:8] for r in tsv(v, "sleep.tsv")])
    return old


@pytest.mark.parametrize("snapshot_columns", [2, 3])
def test_logs_of_older_versions_give_the_same_numbers_and_are_rewritten_by_the_next_sleep(tmp_path, at, snapshot_columns):
    new = life(tmp_path, at)
    oldv = tmp_path / "old"
    shutil.copytree(new, oldv, ignore=shutil.ignore_patterns("index.sqlite*"))
    rows_before = tsv(new, "snapshot.tsv")
    assert rows_before and all(len(r) == 4 and r[3] == "2" for r in rows_before)  # the current format
    old = downgrade(oldv, snapshot_columns)
    assert all(len(r) == snapshot_columns for r in tsv(oldv, "snapshot.tsv")) and old != hashes(new)  # really another digest

    # reading: every report agrees (path names aside)
    for args in (("stats",), ("check",), ("sleep", "plan"), ("review",), ("map",), ("inbox",)):
        a, b = ok(new, *args), ok(oldv, *args)
        assert a.replace(str(new), "V") == b.replace(str(oldv), "V"), args
    assert tsv(oldv, "snapshot.tsv")[0][-1] == "2"  # reading migrated the snapshot once
    assert hashes(new) == hashes(oldv)  # unchanged notes stay checked, the edited and the new one stay open
    assert waiting(oldv) == waiting(new)
    assert waiting(oldv)["verify"] == 2  # cell-grid (edited) and late-note (new); gamma is parked

    # writing: a sleep on top of the old logs ends in the same state and appends rows of the current formats
    for v in (new, oldv):
        ok(v, "sleep", "begin")
        changed, _ = verify_slugs(ok(v, "sleep", "plan"))
        assert sorted(changed) == ["cell-grid", "late-note"]
        ok(v, "sleep", "verified", *changed)
    outs = [ok(v, "sleep", "done", "second sleep") for v in (new, oldv)]
    assert outs[0] == outs[1]
    assert hashes(new) == hashes(oldv)
    assert [len(r) for r in tsv(oldv, "sleep.tsv")][-1] == 11 and all(len(r) == 4 for r in tsv(oldv, "snapshot.tsv"))
    assert tsv(oldv, "sleep.tsv")[-1][1:7] == tsv(new, "sleep.tsv")[-1][1:7]
    assert waiting(oldv) == waiting(new) == {"inbox": 2, "verify": 0, "forget": 0}
    assert sb(oldv, "check")[0] == 0


def test_an_old_log_row_without_rename_counter_or_digest_is_read_not_choked_on(tmp_path, at):
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "ftcs-limit", title="FTCS stability limit", cue="choosing dt for the explicit heat scheme", gist="Gist: r = 0.5.")
    add_note(v, "cell-grid", project="mesh", type="concept", title="Coarse grid", cue="grid label", gist="Gist: 32 cells.")
    ok(v, "sleep", "baseline")
    at("2026-11-02")
    ok(v, "rename", "cell-grid", "coarse-grid")
    at("2026-11-05")
    ok(v, "sleep", "begin")
    plan = ok(v, "sleep", "plan")
    assert verify_slugs(plan)[0] == []
    ok(v, "sleep", "done", "nothing to do")
    # rows of a log older than the renames counter, of listed and unverifiable rows of sb 0.3.1 and of a half written line
    write_log(v, "unverifiable.tsv", ("2026-11-03", "coarse-grid", "no counter and no digest"), "2026-11-03\tftcs-limit")
    write_log(v, "listed.tsv", ("2026-11-03", "cell-grid"), ("2026-11-03", "ftcs-limit"), "garbage")
    write_log(v, "unrelated.tsv", ("2026-11-03", "cell-grid", "ftcs-limit", "old row without counter"))
    for args in (("stats",), ("check",), ("sleep", "plan"), ("review",)):
        assert ok(v, *args)
    assert waiting(v)["verify"] == 0  # a parking without a digest parks nothing: the notes are checked anyway
    p = v / "notes/mesh/coarse-grid.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nEdited later.\n", encoding="utf-8")
    assert waiting(v)["verify"] == 1  # an old parking row does not hide the edited note


def test_seeds_with_the_old_horizons_rest_for_the_days_they_were_written_for(tmp_path, at):
    """seeds.tsv rows of sb <= 0.2.1 hold w = sqrt((T + 1) / (H + 1)) with H = 180 for decisions and derivations, 90 otherwise."""
    types = {"a-finding": "finding", "a-decision": "decision", "a-derivation": "derivation", "a-concept": "concept"}
    horizon = {"decision": 180, "derivation": 180}

    def make(name):
        v = init_vault(tmp_path, name)
        for slug, typ in types.items():  # imported long ago, never used: only the seed decides when they fade
            add_note(v, slug, type=typ, title=f"{slug} title", cue=f"{slug} cue", extra={"created": "2026-01-01", "updated": "2026-01-01"},
                     gist=f"Gist: {slug}.")
        return v

    # writer: baseline --seed 100 spreads T from 30 to 100 days and writes the weight of the horizon of its type
    w = make("written")
    at(D0)
    assert "seeded 4 notes" in ok(w, "sleep", "baseline", "--seed", "100")
    rows = {r[1]: (r[0], float(r[2])) for r in tsv(w, "seeds.tsv")}
    assert len(rows) == 4 and {d for d, _ in rows.values()} == {D0}
    spread = sorted(((typ, slug) for slug, typ in types.items()), key=lambda x: x[1])  # equal `updated`: ties go by slug
    for i, (typ, slug) in enumerate(spread):
        T = 30 + 70 * i / 3
        assert rows[slug][1] == pytest.approx(((T + 1) / (horizon.get(typ, 90) + 1)) ** 0.5, abs=1e-6), (slug, typ)

    # reader: the same rows, written by hand for T = 100 days, let every type rest 100 days, not 50 or 200
    v = make("old")
    write_log(v, "seeds.tsv", *[(D0, slug, f"{(101 / (horizon.get(typ, 90) + 1)) ** 0.5:.6f}") for slug, typ in types.items()])
    ok(v, "sleep", "baseline")
    at("2026-12-01")  # 30 days after the seed
    assert forget_candidates(v) == 0
    at("2027-02-05")  # 96 days after the seed
    assert forget_candidates(v) == 0
    at("2027-02-15")  # 106 days after the seed
    assert forget_candidates(v) == 4
    assert sorted(re.findall(r"^- (\S+) \[", section(ok(v, "sleep", "plan"), 3), flags=re.M)) == sorted(types)


# ---- L16: init without FTS5 ---------------------------------------------------------------------------------------------

def test_init_without_fts5_is_a_clean_error_that_creates_nothing_and_works_once_it_is_there(tmp_path, monkeypatch):
    import sqlite3

    def no_trigram(*_a, **_k):
        class Conn:
            def execute(self, *_x):
                raise sqlite3.OperationalError("no such tokenizer: trigram")
        return Conn()

    root = tmp_path / "v"
    with monkeypatch.context() as m:
        m.setattr(sqlite3, "connect", no_trigram)  # fts5_available() asks SQLite itself
        code, out, err = sb(root, "init", str(root))
    assert code == 2 and out == "" and "FTS5" in err and "SQLite >= 3.34" in err
    assert not root.exists()  # not even the folder
    root.mkdir()  # an existing empty folder stays empty, too
    with monkeypatch.context() as m:
        m.setattr(cli, "fts5_available", lambda: False)
        assert sb(root, "init", str(root))[0] == 2 and list(root.iterdir()) == []
    assert sb(root, "init", str(root))[0] == 0  # with FTS5 the same call goes through
    assert {p.name for p in root.iterdir()} == {"notes", "archive", ".sb", "sb.toml", ".gitignore"}
    assert sb(root, "init", str(root))[0] == 2  # and is refused as a second run


# ---- L4: import, baseline --seed, first sleep, then the seeded notes fade one by one over the following year -------------

def test_import_baseline_first_sleep_and_notes_fade_one_by_one_up_to_day_400(tmp_path, at):
    from datetime import date, timedelta

    def import_note(i, extra=None, **kw):
        typ = ["finding", "concept", "decision", "derivation", "question", "concept"][i % 6]
        word = ["amber", "basalt", "cobalt", "dolomite", "emerald", "feldspar", "garnet", "hematite", "ilmenite", "jasper",
                "kyanite", "lazurite"][i]
        day = (date(2026, 2, 1) + timedelta(days=i)).isoformat()  # `updated` rises with i, so i = 0 is seeded to fade first
        return add_note(v, f"import-{i:02d}", project="imp", type=typ, title=f"{word} note number {i}", cue=f"when {word} matters",
                        gist=f"Gist: {word} was measured in run {i}.", extra={"created": day, "updated": day, **(extra or {})}, **kw)

    v = init_vault(tmp_path)
    at(D0)
    for i in range(12):
        import_note(i)
    # three files of the old collection are not valid notes: a missing cue, a placeholder, a field sb does not know
    import_note(3, drop=("cue",))
    import_note(6, body="\n- TODO\n")
    import_note(9, extra={"legacy_id": "17"})
    out = fails(v, "check", code=1)
    for slug, what in (("import-03", "missing field 'cue'"), ("import-06", "unfilled TODO placeholder"), ("import-09", "unknown field 'legacy_id'")):
        assert re.search(rf"notes/imp/{slug}\.md:\d+: error: {what}", out), out
    assert out.endswith("checked 12 notes: 3 errors, 0 warnings\n")
    assert waiting(v)["verify"] == 12  # nothing is accepted yet: no snapshot, no `verified`
    for i in (3, 6, 9):
        import_note(i)  # fixed
    assert ok(v, "check").endswith("checked 12 notes: 0 errors, 0 warnings\n")
    assert "sleep due: never slept" in ok(v, "hook", "session-start")  # the hook of the first session after the import

    # baseline accepts the notes as they are, seeds them; it logs no sleep, so the hook still says never slept
    out = ok(v, "sleep", "baseline", "--seed", "365")
    assert "12 were unchecked" in out and "seeded 12 notes" in out and "within 365 days" in out
    assert waiting(v) == {"inbox": 0, "verify": 0, "forget": 0}
    assert tsv(v, "sleep.tsv") == [] and len(tsv(v, "seeds.tsv")) == 12
    assert "sleep due: never slept" in ok(v, "hook", "session-start")

    # the first sleep after the import has no new or changed note to verify and no forget candidate
    ok(v, "sleep", "begin")
    plan = ok(v, "sleep", "plan")
    assert verify_slugs(plan)[0] == [] and section(plan, 3).startswith("## 3. Forget: 0 candidates")
    assert "slept 2026-11-01" in ok(v, "sleep", "done", "first sleep after the import")
    assert ok(v, "hook", "session-start") == ""  # the hook is quiet now

    # one note is recalled on day 100; the others are never used and fade one by one in the order of their seeds
    fades, archived = [], []
    for n in range(5, 401, 5):
        at((date.fromisoformat(D0) + timedelta(days=n)).isoformat())
        if n == 100:
            assert "import-11" in ok(v, "recall", "lazurite", "matters")
        got = forget_candidates(v)
        assert got <= 1, f"day {n}: {got} candidates at once"  # a wave would show up here
        if got:
            ok(v, "sleep", "begin")
            listed = re.findall(r"^- (\S+) \[", section(ok(v, "sleep", "plan"), 3), flags=re.M)
            assert len(listed) == 1
            ok(v, "archive", listed[0], "--why", "faded")
            ok(v, "sleep", "done", f"faded {listed[0]}")
            fades.append(n)
            archived.append(listed[0])
    assert fades[0] >= 30  # no seeded note fades before SEED_MIN days (day 30 itself: it has rested 30 days)
    assert all(b - a >= 25 for a, b in zip(fades, fades[1:])), fades  # one every 30 days (335 days over 11 notes), no wave
    assert archived == [f"import-{i:02d}" for i in range(11)]  # in the order of their seeds
    assert "notes: 1, archive: 11" in ok(v, "stats") and (v / "notes/imp/import-11.md").is_file()
    assert forget_candidates(v) == 0  # the recalled note, seeded for 365 days, has grown past day 400 by that one use
    assert ok(v, "check").endswith("0 errors, 0 warnings\n")
    assert "import-00" in ok(v, "recall", "--archive", "amber", "matters")


# ---- archive routes: faded, superseded (with --fix-links), wrong; restore; the archive search --------------------------

def tree(v):
    return {p.relative_to(v).as_posix(): p.read_bytes() for p in Path(v).rglob("*.md")}


def test_archive_routes_faded_superseded_and_wrong_with_links_restore_and_index_parity(tmp_path, at):
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "run-hub", type="concept", title="Overview of the runs", cue="start here for the run notes",
             gist="Gist: the runs are told in [[ftcs-old]] and the question [[q-answered]] asks what followed.",
             body="\n## Links\n- [[ftcs-old]] the older run\n- [[ftcs-new]] the newer run\n- [[scheme-faded]] a choice nobody uses\n"
                  "- [[q-wrong]] a question built on a false premise\n")
    add_note(v, "ftcs-old", title="Older FTCS run", cue="the first sweep of the explicit scheme", gist="Gist: the sweep stopped at r = 0.52.")
    add_note(v, "ftcs-new", title="Newer FTCS run", cue="the repeated sweep with a finer step", gist="Gist: the repeat shows r = 0.5.",
             body="\n## Links\n- [[ftcs-old]] what this replaces\n")
    add_note(v, "scheme-faded", type="decision", title="Scheme once chosen", cue="a decision that nobody needs any more",
             gist="Gist: Lax-Friedrichs was chosen early.")
    add_note(v, "q-wrong", type="question", title="Premise question", cue="a question that rests on a false premise",
             gist="Gist: why does FTCS never diverge?")
    add_note(v, "q-answered", type="question", title="Edge question", cue="whether edges may collapse",
             gist="Gist: can an edge collapse under remeshing?")
    add_note(v, "answer-note", title="Edge answer", cue="edges collapse below a length", gist="Gist: edges collapse below 1e-3.")
    add_note(v, "other-note", type="concept", title="Edge length concept", cue="how short an edge is", gist="Gist: the length in cells.")
    assert ok(v, "check").endswith("0 errors, 0 warnings\n")
    original = tree(v)

    # refused archives change nothing
    for args, msg in ((("scheme-faded", "--why", "faded", "--superseded-by", "ftcs-new"), "a faded note has no successor"),
                      (("scheme-faded", "--why", "superseded"), "--reason (one sentence) is required"),
                      (("scheme-faded", "--why", "superseded", "--superseded-by", "no-such-note"), "must be another note in notes/"),
                      (("scheme-faded", "--why", "superseded", "--superseded-by", "scheme-faded"), "must be another note in notes/"),
                      (("q-wrong", "--why", "wrong"), "--reason (one sentence) is required"),
                      (("no-such-note", "--why", "faded"), "no note 'no-such-note' in notes/")):
        assert msg in fails(v, "archive", *args)
        assert tree(v) == original

    # faded, links left alone: the incoming link is named and `check` warns, no error
    at("2026-11-02")
    out = ok(v, "archive", "scheme-faded", "--why", "faded")
    assert "incoming links from: run-hub" in out and "--fix-links repairs them" in out
    assert "run-hub" in ok(v, "show", "scheme-faded") and "archived 2026-11-02: faded" in ok(v, "show", "scheme-faded")
    chk = ok(v, "check")
    assert re.search(r"run-hub\.md:\d+: warning: link to archived note \[\[scheme-faded\]\]", chk) and chk.endswith("0 errors, 1 warnings\n")

    # superseded with successor and --fix-links: links go to the successor, a duplicate bullet and a self-link go away
    out = ok(v, "archive", "ftcs-old", "--why", "superseded", "--superseded-by", "ftcs-new", "--fix-links")
    assert "incoming links from: ftcs-new, run-hub" in out and "links repaired in 2 notes" in out
    hub, new = read(v, "notes/heat/run-hub.md"), read(v, "notes/heat/ftcs-new.md")
    assert "[[ftcs-old]]" not in hub and "the runs are told in [[ftcs-new]] and" in hub and hub.count("- [[ftcs-new]] the newer run") == 1
    assert "[[ftcs-old]]" not in new and "[[ftcs-new]]" not in new and "- [[" not in new  # no link to itself, no empty bullet
    assert "archived_why: superseded\nsuperseded_by: [ftcs-new]\n" in read(v, "archive/heat/ftcs-old.md")
    assert "superseded by: ftcs-new" in ok(v, "recall", "--archive", "older", "FTCS", "run")

    # wrong with --fix-links and no successor: the bullet goes, the inline link becomes plain text
    ok(v, "archive", "q-wrong", "--why", "wrong", "--reason", "FTCS does diverge above r = 0.5", "--fix-links")
    hub = read(v, "notes/heat/run-hub.md")
    assert "q-wrong" not in hub.split("## Links")[1] and "[[q-wrong]]" not in hub

    # an answered question goes to its answer; two successors are kept, the first one takes the links
    ok(v, "archive", "q-answered", "--why", "superseded", "--superseded-by", "answer-note", "other-note", "--fix-links")
    assert "the question [[answer-note]] asks" in read(v, "notes/heat/run-hub.md")
    assert "superseded_by: [answer-note, other-note]" in read(v, "archive/heat/q-answered.md")
    chk = ok(v, "check")
    assert chk.count(": warning: ") == 1 and "scheme-faded" in chk and chk.endswith("0 errors, 1 warnings\n")
    assert "notes: 4, archive: 4" in ok(v, "stats")

    # restore: the archive fields go, the old text returns byte for byte, the warning goes with the archived target
    for slug in ("ftcs-old", "scheme-faded", "q-answered"):
        ok(v, "restore", slug)
        assert read(v, f"notes/heat/{slug}.md").encode() == original[f"notes/heat/{slug}.md"]
    assert "was archived as wrong" in fails(v, "restore", "q-wrong")
    ok(v, "restore", "q-wrong", "--force")
    assert read(v, "notes/heat/q-wrong.md").encode() == original["notes/heat/q-wrong.md"]
    assert not list((v / "archive").rglob("*.md"))
    assert ok(v, "check").endswith("checked 8 notes: 0 errors, 0 warnings\n")
    # the links repaired earlier stay as they were repaired: restoring does not undo them
    assert "[[ftcs-new]]" in read(v, "notes/heat/run-hub.md") and "[[ftcs-old]]" not in read(v, "notes/heat/run-hub.md")

    # index equals disk: a rebuilt index answers the same
    ok(v, "archive", "scheme-faded", "--why", "faded")
    ok(v, "archive", "other-note", "--why", "superseded", "--superseded-by", "answer-note")
    before = [ok(v, *a) for a in (("map",), ("recall", "--archive", "length", "edge"), ("recall", "--archive", "scheme", "chosen"), ("stats",))]
    ok(v, "check", "--rebuild")
    assert [ok(v, *a) for a in (("map",), ("recall", "--archive", "length", "edge"), ("recall", "--archive", "scheme", "chosen"),
                                ("stats",))] == before


# ---- closing inbox lines: note, drop, wait, a DUE line that cannot wait, expiry after 30 days, late booking ---------------

def plan_lines(plan):
    """{id: (age in days, DUE shown)} of the inbox lines of '## 1. Consolidate'."""
    return {m[1]: (int(m[2]), bool(m[3])) for m in re.finditer(r"^- `(\w+)` (\d+)d \S+( DUE)?:", section(plan, 1), flags=re.M)}


def last_outcomes(v):
    out = {}
    for i, _, o in tsv(v, "inbox-done.tsv"):
        out[i] = o
    return out


def test_inbox_lines_are_closed_one_by_one_and_expire_only_when_due_and_shown(tmp_path, at):
    v = init_vault(tmp_path)
    at(D0)
    texts = {"amber": "amber ratio measured at 0.31 in the first sweep", "basalt": "basalt mesh converged at level 4 of the hierarchy",
             "cobalt": "cobalt solver needs preconditioning on skewed cells", "dolomite": "dolomite flux limiter oscillates near shocks",
             "emerald": "emerald timestep halves the error in every run"}
    ids = {k: ok(v, "capture", t, "-p", "heat").strip() for k, t in texts.items()}
    assert len(set(ids.values())) == 5 and [r[0] for r in tsv(v, "inbox.tsv")] == list(ids.values())
    for k in ("amber", "basalt"):
        add_note(v, f"n-{k}", title=f"{k} note", cue=f"when {k} matters", gist=f"Gist: {k}.")
    add_note(v, "n-cobalt", title="cobalt note", cue="when cobalt matters", gist="Gist: cobalt.")
    add_note(v, "n-dolomite", title="dolomite note", cue="when dolomite matters", gist="Gist: dolomite.")

    # sleep 1 on day 3: promote one line, drop one, let one wait, leave two open; nothing is due yet
    at("2026-11-04")
    ok(v, "sleep", "begin")
    shown = plan_lines(ok(v, "sleep", "plan"))
    assert shown == {i: (3, False) for i in ids.values()}
    assert ok(v, "inbox", "resolve", ids["amber"], "--note", "n-amber").strip() == f"{ids['amber']}: note:n-amber"
    assert ok(v, "inbox", "resolve", ids["basalt"], "--drop").strip() == f"{ids['basalt']}: dropped"
    ok(v, "inbox", "resolve", ids["cobalt"], "--wait")
    assert len(tsv(v, "inbox-done.tsv")) == 3
    for args, msg in ((("nosuchid", "--drop"), "no inbox line 'nosuchid'"),
                      ((ids["amber"], "--drop"), "already settled (note:n-amber)"),
                      ((ids["dolomite"], "--note", "no-such-note"), "no note 'no-such-note' in notes/"),
                      ((ids["dolomite"], "--note", " "), "--note needs the slug")):
        code, out, err = sb(v, "inbox", "resolve", *args)
        assert code == 2 and msg in err, (args, err)
        assert len(tsv(v, "inbox-done.tsv")) == 3  # a refused booking writes nothing
    with pytest.raises(SystemExit) as e:  # two outcomes at once are refused by the parser
        sb(v, "inbox", "resolve", ids["dolomite"], "--note", "n-amber", "--drop")
    assert e.value.code == 2 and len(tsv(v, "inbox-done.tsv")) == 3
    assert [ln.split("  ")[0] for ln in ok(v, "inbox").splitlines()] == [ids["cobalt"], ids["dolomite"], ids["emerald"]]
    assert ok(v, "inbox", "--due") == "(no open inbox lines)\n"
    out = ok(v, "sleep", "done", "first inbox sleep")
    assert "slept 2026-11-04: 2 inbox lines settled (1 promoted), 0 expired" in out and "expired " not in out.replace("0 expired", "")
    stats = ok(v, "stats")
    assert "inbox: 3 open, oldest 3 days" in stats and "promotion rate: 50% (1 of 2 settled)" in stats
    assert set(plan_lines(ok(v, "sleep", "plan"))) == {ids["dolomite"], ids["emerald"]}  # the waiting line left the plan

    # day 19: one more line; day 32: three lines of day 0 are due, the waiting one among them
    at("2026-11-20")
    ids["feldspar"] = ok(v, "capture", "feldspar dike appears at r = 3 in the mesh", "-p", "mesh").strip()
    at("2026-12-03")
    assert [ln.split("  ")[0] for ln in ok(v, "inbox", "--due").splitlines()] == [ids["cobalt"], ids["dolomite"], ids["emerald"]]
    ok(v, "sleep", "begin")
    shown = plan_lines(ok(v, "sleep", "plan"))
    assert shown == {ids["cobalt"]: (32, True), ids["dolomite"]: (32, True), ids["emerald"]: (32, True), ids["feldspar"]: (13, False)}
    rows = len(tsv(v, "inbox-done.tsv"))
    assert "is due" in fails(v, "inbox", "resolve", ids["cobalt"], "--wait") and len(tsv(v, "inbox-done.tsv")) == rows  # a DUE line cannot wait
    ok(v, "inbox", "resolve", ids["cobalt"], "--note", "n-cobalt")  # but can still be promoted (or dropped)
    ok(v, "inbox", "resolve", ids["feldspar"], "--wait")  # the young one may wait
    out = ok(v, "sleep", "done", "second inbox sleep")
    assert "slept 2026-12-03: 1 inbox lines settled (1 promoted), 2 expired" in out
    assert f"expired {ids['dolomite']}: dolomite flux limiter oscillates near shocks" in out
    assert f"expired {ids['emerald']}: emerald timestep halves the error in every run" in out
    assert out.index(f"expired {ids['dolomite']}") < out.index(f"expired {ids['emerald']}")  # in capture order
    assert [ln.split("  ")[0] for ln in ok(v, "inbox").splitlines()] == [ids["feldspar"]]
    expired = ok(v, "inbox", "--expired")
    assert [ln.split("  ")[:2] for ln in expired.splitlines()] == [[ids["dolomite"], "expired 2026-12-03"], [ids["emerald"], "expired 2026-12-03"]]
    found = ok(v, "recall", "--inbox", "dolomite", "flux", "limiter")
    assert f"{ids['dolomite']}  [inbox expired, heat, 2026-11-01]" in found
    stats = ok(v, "stats")
    assert "promotion rate: 67% (2 of 3 settled), 2 expired" in stats and "inbox: 1 open" in stats  # expired is not settled

    # an expired line is not gone: it can be promoted or dropped afterwards, never put on wait (it is due)
    at("2026-12-04")
    ok(v, "inbox", "resolve", ids["dolomite"], "--note", "n-dolomite")
    assert "promotion rate: 75% (3 of 4 settled), 1 expired" in ok(v, "stats")
    assert "is due" in fails(v, "inbox", "resolve", ids["emerald"], "--wait")
    ok(v, "inbox", "resolve", ids["emerald"], "--drop")
    assert "promotion rate: 60% (3 of 5 settled)" in ok(v, "stats") and ", 0 expired" not in ok(v, "stats")
    assert ok(v, "inbox", "--expired") == "(no expired inbox lines)\n"
    assert "is already settled" in fails(v, "inbox", "resolve", ids["emerald"], "--note", "n-dolomite")

    # day 49: a line waits at most 30 days from its capture, not from its wait
    at("2026-12-20")
    ok(v, "sleep", "begin")
    assert plan_lines(ok(v, "sleep", "plan")) == {}  # age 30 is not older than 30
    assert "0 expired" in ok(v, "sleep", "done", "nothing due")
    at("2026-12-21")
    assert [ln.split("  ")[0] for ln in ok(v, "inbox", "--due").splitlines()] == [ids["feldspar"]]
    ok(v, "sleep", "begin")  # a sleep that never printed its plan: the due line was not shown to anybody, so it does not expire
    assert "0 expired" in ok(v, "sleep", "done", "no plan was read")
    assert [ln.split("  ")[0] for ln in ok(v, "inbox").splitlines()] == [ids["feldspar"]] and ok(v, "inbox", "--expired").startswith("(no")
    ok(v, "sleep", "begin")
    assert plan_lines(ok(v, "sleep", "plan")) == {ids["feldspar"]: (31, True)}
    assert "1 expired" in ok(v, "sleep", "done", "the due line was shown and left open")
    assert ok(v, "inbox") == "(no open inbox lines)\n" and ids["feldspar"] in ok(v, "inbox", "--expired")

    # every line ended with exactly one last outcome; the log only grew
    assert last_outcomes(v) == {ids["amber"]: "note:n-amber", ids["basalt"]: "dropped", ids["cobalt"]: "note:n-cobalt",
                                ids["dolomite"]: "note:n-dolomite", ids["emerald"]: "dropped", ids["feldspar"]: "expired"}
    assert [r[0] for r in tsv(v, "inbox.tsv")] == list(ids.values()) and [r[4] for r in tsv(v, "inbox.tsv")][0] == texts["amber"]
    assert [(r[1], r[2], r[6]) for r in tsv(v, "sleep.tsv")] == [("2", "1", "0"), ("1", "1", "2"), ("0", "0", "0"), ("0", "0", "0"), ("0", "0", "1")]


def tokens(n):
    return [f"{a}{b}" for b in ("ron", "dux", "mer") for a in ("al", "be", "ca", "do", "el", "fa", "ga", "hi", "il", "jo", "ka")][:n]


def test_promotion_warning_follows_real_captures_and_settlements(tmp_path, at):
    """More than a quarter of at least ten settled lines promoted is a warning; a quarter, or fewer than ten settled, is not."""
    v = init_vault(tmp_path)
    words = tokens(33)

    def sleep_over(day, batch, promote):
        at(day)
        ids = [ok(v, "capture", f"{w} observation of the sweep", "-p", "heat").strip() for w in batch]
        ok(v, "sleep", "begin")
        assert len(plan_lines(ok(v, "sleep", "plan"))) == len(batch)
        for i, (w, id) in enumerate(zip(batch, ids)):
            if i < promote:
                add_note(v, f"n-{w}", title=f"{w} note", cue=f"when {w} matters", gist=f"Gist: {w}.")
                ok(v, "inbox", "resolve", id, "--note", f"n-{w}")
            else:
                ok(v, "inbox", "resolve", id, "--drop")
        return ok(v, "sleep", "done", f"batch of {len(batch)}")

    out = sleep_over("2026-11-02", words[:12], 4)  # 4 of 12 = 33 %
    assert "warning: 4 of 12 settled inbox lines were promoted (33%, limit 25%)" in out
    assert "slept 2026-11-02: 12 inbox lines settled (4 promoted), 0 expired" in out
    out = sleep_over("2026-11-03", words[12:24], 3)  # 3 of 12 = 25 %: not above the limit
    assert "warning" not in out and "12 inbox lines settled (3 promoted)" in out
    out = sleep_over("2026-11-04", words[24:33], 9)  # all promoted, but only nine settled: too few to mean anything
    assert "warning" not in out and "9 inbox lines settled (9 promoted)" in out
    assert "promotion rate: 48% (16 of 33 settled)" in ok(v, "stats")
    assert ok(v, "inbox") == "(no open inbox lines)\n"


def test_a_note_parked_as_unverifiable_and_restored_from_wrong_is_not_parked_any_more(tmp_path, at):
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "gamma", title="Gamma bookkeeping", cue="what gamma counts here", gist="Gist: gamma counts boundary cells.")
    ok(v, "sleep", "begin")
    assert "gamma" in verify_slugs(ok(v, "sleep", "plan"))[0]
    ok(v, "sleep", "unverifiable", "gamma", "nothing states what gamma counts")
    assert "1 parked as unverifiable" in ok(v, "sleep", "done", "parked gamma")
    assert waiting(v)["verify"] == 0  # parked: out of the backlog

    at("2026-11-03")
    ok(v, "archive", "gamma", "--why", "wrong", "--reason", "gamma counts interior cells")
    ok(v, "restore", "gamma", "--force")  # the text is unchanged, the parking would still match it
    assert waiting(v)["verify"] == 1  # a note restored from wrong is checked again, not parked (design.md, Sleep, Verify)
    ok(v, "sleep", "begin")
    assert verify_slugs(ok(v, "sleep", "plan"))[0] == ["gamma"]


def test_an_interlinked_import_still_fades_one_by_one(tmp_path, at):
    from datetime import date, timedelta
    v = init_vault(tmp_path)
    at(D0)
    for i, word in enumerate(["amber", "basalt", "cobalt", "hub"]):  # `updated` rises with i: the hub is seeded for the full 365 days
        day = (date(2026, 2, 1) + timedelta(days=i)).isoformat()
        gist = f"Gist: {word} was measured." if word != "hub" else "Gist: the series overview: [[import-0]], [[import-1]] and [[import-2]]."
        add_note(v, f"import-{i}", project="imp", type="concept", title=f"{word} note {i}", cue=f"when {word} matters", gist=gist,
                 extra={"created": day, "updated": day})
    ok(v, "sleep", "baseline", "--seed", "365")
    counts = []
    for n in range(5, 401, 5):
        at((date.fromisoformat(D0) + timedelta(days=n)).isoformat())
        counts.append(forget_candidates(v))
    new = [b - a for a, b in zip([0] + counts, counts)]  # nobody acts on a candidate, so the count is the number that fell due so far
    assert max(new) <= 1, counts


def test_a_hub_in_real_use_still_keeps_the_notes_it_links_to(tmp_path, at):
    """Counterpart: only a hub kept active by its seed alone stops protecting; one the agent reads keeps its targets."""
    from datetime import date, timedelta
    v = init_vault(tmp_path)
    at(D0)
    for i, word in enumerate(["amber", "basalt", "cobalt", "hub"]):
        day = (date(2026, 2, 1) + timedelta(days=i)).isoformat()
        gist = f"Gist: {word} was measured." if word != "hub" else "Gist: the series overview: [[import-0]], [[import-1]] and [[import-2]]."
        add_note(v, f"import-{i}", project="imp", type="concept", title=f"{word} note {i}", cue=f"when {word} matters", gist=gist,
                 extra={"created": day, "updated": day})
    ok(v, "sleep", "baseline", "--seed", "365")
    counts = []
    for n in range(5, 401, 5):
        at((date.fromisoformat(D0) + timedelta(days=n)).isoformat())
        if n % 60 == 0:
            ok(v, "show", "import-3")  # the hub is read every 60 days
        counts.append(forget_candidates(v))
    assert counts[-1] == 0, counts  # the read hub never fades and holds its three targets

def test_changing_skip_sections_in_sb_toml_rebuilds_the_search_and_counts_nothing_as_use(tmp_path, at):
    v = init_vault(tmp_path)
    at(D0)
    for slug, extra in (("alpha", "\n## Legacy metadata\nzebrafishword was a tag.\n"), ("beta", "")):
        add_note(v, slug, title=f"{slug} note", cue=f"{slug} cue", gist=f"Gist: {slug} gist.", body=extra)
    assert "alpha" in ok(v, "recall", "zebrafishword")  # the legacy section is searchable by default
    toml = (v / "sb.toml").read_text(encoding="utf-8")
    (v / "sb.toml").write_text(toml + '\n[index]\nskip_sections = ["Legacy metadata"]\n', encoding="utf-8")
    assert "no match" in ok(v, "recall", "zebrafishword")  # the next call rebuilds the index under the new setting
    (v / "sb.toml").write_text(toml, encoding="utf-8")
    assert "alpha" in ok(v, "recall", "zebrafishword")
    assert [r[1:3] for r in tsv(v, "usage.tsv")] == [["work", "recall"]] * 2  # no note was taken for edited by the two rebuilds
    assert waiting(v)["verify"] == 2 and ok(v, "check").endswith("0 errors, 0 warnings\n")


def test_a_note_archived_as_wrong_during_verify_is_not_left_undone(tmp_path, at):
    """sleeper.md, Verify: 'archive what is wrong beyond repair (--why wrong --reason ...)'."""
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "good-note", title="Good note", cue="a fine note", gist="Gist: this one holds.")
    add_note(v, "bad-note", title="Bad note", cue="a broken note", gist="Gist: this one is false.")
    ok(v, "sleep", "begin")
    assert sorted(verify_slugs(ok(v, "sleep", "plan"))[0]) == ["bad-note", "good-note"]
    ok(v, "sleep", "verified", "good-note")
    ok(v, "archive", "bad-note", "--why", "wrong", "--reason", "the number contradicts the source file")
    assert "no note 'bad-note' in notes/" in fails(v, "sleep", "verified", "bad-note")
    out = ok(v, "sleep", "done", "verified one, archived one")
    assert "1 archived" in out and "left undone" not in out
    assert tsv(v, "sleep.tsv")[-1][3] == "1" and tsv(v, "sleep.tsv")[-1][9] == "0"  # one archived, nothing left of the plan
    assert waiting(v)["verify"] == 0 and "bad-note" not in ok(v, "map")
    assert "WRONG: bad-note" in ok(v, "recall", "--archive", "broken", "note")


def hook_after_edit(v, path, monkeypatch):
    import io
    import json
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(path)}})))
    return sb(v, "hook", "after-edit")


def test_check_reads_archive_too_so_the_broken_fix_of_a_wrong_note_is_found(tmp_path, at, monkeypatch):
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "cfl-limit", title="FTCS limit", cue="choosing dt", gist="Gist: r = 0.52.")
    ok(v, "archive", "cfl-limit", "--why", "wrong", "--reason", "the limit is 0.5")
    p = v / "archive/heat/cfl-limit.md"
    p.write_text(p.read_text(encoding="utf-8").replace("Gist: r = 0.52.", "TODO: put the right limit here"), encoding="utf-8")
    out = fails(v, "check", code=1)
    assert "archive/heat/cfl-limit.md:" in out and "unfilled TODO placeholder" in out


def test_the_hook_reports_an_error_in_the_text_fixed_in_archive_before_a_restore(tmp_path, at, monkeypatch):
    v = init_vault(tmp_path)
    at(D0)
    add_note(v, "cfl-limit", title="FTCS limit", cue="choosing dt", gist="Gist: r = 0.52.")
    ok(v, "archive", "cfl-limit", "--why", "wrong", "--reason", "the limit is 0.5")
    p = v / "archive/heat/cfl-limit.md"
    p.write_text(p.read_text(encoding="utf-8").replace("Gist: r = 0.52.", "TODO: put the right limit here"), encoding="utf-8")
    code, out, err = hook_after_edit(v, p, monkeypatch)
    assert code == 2 and "unfilled TODO placeholder" in err


def test_a_seeded_hub_edited_by_hand_after_its_seed_keeps_its_targets(tmp_path, at):
    """Only the seed and index-found edits up to the seed day are no use; a hand edit after it is, as in usage.scores."""
    from datetime import date, timedelta
    v = init_vault(tmp_path)
    at(D0)
    for i, word in enumerate(["amber", "basalt", "cobalt", "hub"]):
        day = (date(2026, 2, 1) + timedelta(days=i)).isoformat()
        gist = f"Gist: {word} was measured." if word != "hub" else "Gist: the series overview: [[import-0]], [[import-1]] and [[import-2]]."
        add_note(v, f"import-{i}", project="imp", type="concept", title=f"{word} note {i}", cue=f"when {word} matters", gist=gist,
                 extra={"created": day, "updated": day})
    ok(v, "sleep", "baseline", "--seed", "365")
    later = (date.fromisoformat(D0) + timedelta(days=300)).isoformat()
    with open(v / ".sb" / "usage.tsv", "a", encoding="utf-8") as f:
        f.write(f"{later}\tsync\tedit\timport-3\n")  # the hub edited by hand, found by the index
    at((date.fromisoformat(D0) + timedelta(days=330)).isoformat())
    assert forget_candidates(v) == 0  # the edited hub is in use and holds its three targets


def test_a_note_gone_between_listing_and_stat_is_skipped(tmp_path):
    from scientific_brain.vault import Vault
    v = init_vault(tmp_path)
    add_note(v, "soon-gone", project="imp", type="concept", title="Soon gone", cue="removed by another session")
    add_note(v, "stays", project="imp", type="concept", title="Stays", cue="still there")
    listing = Vault(v).scan("notes")
    (v / "notes" / "imp" / "soon-gone.md").unlink()  # another session archives it now
    assert [rel for rel, _ in listing.items()] == ["notes/imp/stays.md"]


def test_an_index_file_without_the_sqlite_header_is_rebuilt_whatever_error_sqlite_gives(tmp_path, monkeypatch):
    """With a stale WAL beside it, SQLite may call a damaged index a disk I/O error instead of 'not a database'."""
    from scientific_brain import index
    v = init_vault(tmp_path)
    add_note(v, "kept-note", project="imp", type="concept", title="Kept note", cue="survives the rebuild")
    ok(v, "map")
    import gc
    gc.collect()  # closes the connection `map` left behind, as the end of its process would, so the WAL is written back
    assert not (v / ".sb" / "index.sqlite-wal").exists()
    (v / ".sb" / "index.sqlite").write_bytes(b"garbage" * 500)
    monkeypatch.setattr(index, "_corrupt", lambda e: False)  # as if SQLite gave an error of another kind
    assert "kept-note" in ok(v, "recall", "survives", "rebuild")
    assert (v / ".sb" / "index.sqlite.broken").exists()


def test_a_healthy_index_that_fails_for_another_reason_is_not_set_aside(tmp_path, monkeypatch):
    from scientific_brain import index
    v = init_vault(tmp_path)
    ok(v, "map")
    assert not index._no_header(v / ".sb" / "index.sqlite")  # a real database has the header
    assert not index._no_header(v / ".sb" / "missing.sqlite")  # no file: nothing to set aside
