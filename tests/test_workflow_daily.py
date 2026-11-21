"""Everyday workflows of `sb`, replayed from the first call to the last through the CLI and the real hook entry.

Every test plays one sequence the way a user or an agent runs it (setup, note creation, hook checks, recall, rename, the
forgetting clock, the inbox clock, the hook scripts of the plugin) and asserts after each step. State is written only
by `sb` itself or by the file edits an agent makes with Write or Edit. Days move with SB_TODAY. Tests marked
`xfail(strict=True)` state the intended behaviour of defects found while writing them; they turn red as soon as a
defect is fixed, so the marker has to go then.
"""
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from helpers import add_note, init_vault, make_vault, sb

from scientific_brain.schema import TYPES

ROOT = Path(__file__).resolve().parents[1]
PLUGIN, SRC = ROOT / "plugin", ROOT / "src"
BASE = date(2026, 1, 1)


def clock(monkeypatch, n=0, base=BASE):
    """Today is day n after base."""
    monkeypatch.setenv("SB_TODAY", (base + timedelta(days=n)).isoformat())


@pytest.fixture(autouse=True)
def day0(monkeypatch):
    clock(monkeypatch)


def run(v, *args):
    """sb in-process; an argparse refusal (SystemExit) is returned as its exit code, with a marker in stderr."""
    try:
        return sb(v, *args)
    except SystemExit as e:
        return e.code, "", "argparse refused the arguments"


def edit_event(monkeypatch, path, tool="Write", **extra):
    """The PostToolUse input Claude Code sends to the hook."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": tool, "tool_input": {"file_path": str(path), **extra}})))


def edit_hook(monkeypatch, v, path, tool="Write", **extra):
    edit_event(monkeypatch, path, tool, **extra)
    return sb(v, "hook", "after-edit")


def fill(path, title="A filled title", cue="a filled cue", gist="A filled gist.", entry="an entry", ref="10.1000/sb.test.1",
         tail="", sources="src/ftcs.py"):
    """What an agent does to a skeleton of `sb new`: replace every TODO and write the file."""
    t = Path(path).read_text(encoding="utf-8")
    t = re.sub(r"^title: TODO$", f"title: {title}", t, flags=re.M)
    t = re.sub(r"^cue: TODO$", f"cue: {cue}", t, flags=re.M)
    t = t.replace("sources: [TODO]", f"sources: [{sources}]")
    t = re.sub(r"^ref: TODO$", f"ref: {ref}", t, flags=re.M)
    t = t.replace("TODO: one or two sentences, the gist.", gist).replace("- TODO", f"- {entry}")
    assert "TODO" not in t
    Path(path).write_text(t + tail, encoding="utf-8")
    return Path(path)


def make(v, typ, slug, project="heat", why="own measurement", **kw):
    """`sb new` (similar-note stop lifted) and fill; returns the path."""
    code, out, err = sb(v, "new", typ, slug, "-p", project, "--why", why, "--anyway")
    assert code == 0, err
    return fill(v / "notes" / project / f"{slug}.md", **kw)


def forget_list(v):
    """Slugs of the plan's forget section (a plan outside a sleep remembers nothing)."""
    code, out, err = sb(v, "sleep", "plan")
    assert code == 0, err
    sec = out.split("## 3. Forget")[1].split("## 4.")[0]
    return {m[1] for m in re.finditer(r"^- (\S+) \[", sec, flags=re.M)}


def usage_rows(v):
    return [ln.split("\t") for ln in (v / ".sb" / "usage.tsv").read_text(encoding="utf-8").splitlines() if ln.strip()]


def bytes_of(v):
    """Every file of the vault except the index and the lock: path -> bytes."""
    return {p.relative_to(v).as_posix(): p.read_bytes() for p in sorted(v.rglob("*"))
            if p.is_file() and ".sb/index" not in p.as_posix() and p.name != "lock"}


# ---- Nr. 2: first setup after the README ----------------------------------------------------------------------------

def test_first_setup_chain_from_init_to_the_first_recall(tmp_path, monkeypatch):
    root = tmp_path / "research-brain"
    code, out, err = sb(root, "init", str(root))
    assert code == 0 and "initialised vault" in out, err
    assert all((root / d).is_dir() for d in ("notes", "archive", ".sb")) and (root / "sb.toml").is_file()
    assert ".sb/index.sqlite" in (root / ".gitignore").read_text(encoding="utf-8")
    assert sb(root, "init", str(root))[0] == 2  # a second init refuses

    # step 7 of the README: the empty vault reports its counts, a new session starts silent
    code, out, err = sb(root, "stats")
    assert code == 0 and "notes: 0, archive: 0" in out and "inbox: 0 open" in out and "last sleep: never" in out, out
    assert "sleep due" not in out and "note: sb.toml sets" not in out
    assert sb(root, "hook", "session-start") == (0, "", "")
    assert sb(root, "map") == (0, "(no notes)\n", "")

    # the user adapts sb.toml: projects, a working folder, a source root
    toml = (root / "sb.toml").read_text(encoding="utf-8")
    for key, line in (("# projects =", 'projects = ["heat", "_general"]'), ("# folder_map =", 'folder_map = { "work/heat" = "heat" }'),
                      ("# source_roots =", 'source_roots = ["srcroot"]')):
        toml = "\n".join(line if ln.startswith(key) else ln for ln in toml.split("\n"))
    (root / "sb.toml").write_text(toml, encoding="utf-8")
    (root / "srcroot" / "runs").mkdir(parents=True)
    (root / "srcroot" / "runs" / "sweep.csv").write_text("r,max\n0.52,inf\n", encoding="utf-8")
    code, out, err = sb(root, "stats")
    assert code == 0 and "note: sb.toml sets" not in out and "sleep due" not in out, out + err
    assert sb(root, "hook", "session-start") == (0, "", "")

    # capture: the project follows the working folder; an unknown project and the wrong folder are told apart
    (root / "work" / "heat").mkdir(parents=True)
    monkeypatch.chdir(root / "work" / "heat")
    code, out, err = sb(root, "capture", "FTCS diverges at r = 0.52", "-s", "runs/sweep.csv")
    line_id = out.strip()
    assert code == 0 and re.fullmatch(r"[0-9a-f]{6}", line_id), err
    monkeypatch.chdir(tmp_path)
    code, out, _ = sb(root, "capture", "a line from nowhere")
    assert code == 0
    assert sb(root, "capture", "x", "-p", "unlisted")[0] == 2
    code, out, _ = sb(root, "inbox")
    assert f"{line_id}  0d  heat  FTCS diverges at r = 0.52  (runs/sweep.csv)" in out and "_unsorted  a line from nowhere" in out

    # new: skeleton, red check, red hook (the agent is told what is missing), filled, green hook and check
    code, out, err = sb(root, "new", "finding", "ftcs-stability-limit", "-p", "heat", "--why", "own measurement")
    note = root / "notes" / "heat" / "ftcs-stability-limit.md"
    assert code == 0 and "no similar notes" in out and "created notes/heat/ftcs-stability-limit.md" in out and note.is_file(), err
    code, out, _ = sb(root, "check")
    assert code == 1 and "unfilled TODO placeholder" in out and "checked 1 notes: 4 errors" in out, out
    code, out, err = edit_hook(monkeypatch, root, note)
    assert code == 2 and "unfilled TODO placeholder" in err and out == ""
    fill(note, title="FTCS stability limit", cue="FTCS blows up above r = 0.5", gist="Gist: the explicit scheme diverges at r = 0.52.",
         sources="runs/sweep.csv")
    assert edit_hook(monkeypatch, root, note) == (0, "", "")
    assert sb(root, "check")[0:2] == (0, "checked 1 notes: 0 errors, 0 warnings\n")

    # recall finds it and counts as use; stats and the session start now see one note and a first sleep to come
    code, out, _ = sb(root, "recall", "ftcs", "stability")
    assert code == 0 and "ftcs-stability-limit  [finding, heat]" in out and "cue: FTCS blows up above r = 0.5" in out
    assert "+1 inbox matches" in out  # the captured line speaks of FTCS too
    code, out, _ = sb(root, "stats")
    assert "notes: 1, archive: 0" in out and "by type: finding 1" in out and "by project: heat 1" in out and "sleep due: never slept" in out
    code, out, _ = sb(root, "hook", "session-start")
    assert code == 0 and out == "Scientific Brain: sleep due: never slept. Use the sleep skill.\n"
    assert ["recall", "ftcs-stability-limit"] in [r[2:4] for r in usage_rows(root)]

    # the line is promoted to the note: settled, counted in the promotion rate
    assert sb(root, "inbox", "resolve", line_id, "--note", "ftcs-stability-limit")[0] == 0
    code, out, _ = sb(root, "stats")
    assert "promotion rate: 100% (1 of 1 settled)" in out and "inbox: 1 open" in out


# ---- sb new for all six types ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("typ", sorted(TYPES))
def test_new_fill_hook_check_recall_for_every_type(tmp_path, monkeypatch, typ):
    v = init_vault(tmp_path)
    slug, mark = f"{typ}-chain", f"zebra{typ}"
    why = 'own: result [x] with # and "quotes"'
    code, out, err = sb(v, "new", typ, slug, "-p", "heat", "--why", why)
    p = v / "notes" / "heat" / f"{slug}.md"
    assert code == 0 and p.is_file(), err
    assert f"type: {typ}" in p.read_text(encoding="utf-8")
    code, out, _ = sb(v, "check")
    assert code == 1 and "unfilled TODO placeholder" in out
    assert edit_hook(monkeypatch, v, p)[0] == 2
    fill(p, title=f"A {mark} title", cue=f"{mark} cue", gist=f"Gist of the {mark} note.")
    assert edit_hook(monkeypatch, v, p) == (0, "", "")
    assert sb(v, "check") == (0, "checked 1 notes: 0 errors, 0 warnings\n", "")
    assert sb(v, "check", "--changed") == (0, "checked 0 notes: 0 errors, 0 warnings\n", "")  # the hook marked it checked
    code, out, _ = sb(v, "recall", mark)
    assert code == 0 and f"{slug}  [{typ}, heat]" in out
    code, out, _ = sb(v, "show", slug)
    assert code == 0 and "backlinks: none" in out and "result [x]" in out
    code, out, _ = sb(v, "map")
    assert f"  {typ}\n    {slug} - A {mark} title" in out
    assert sb(v, "check", "--rebuild")[0] == 0 and slug in sb(v, "recall", mark)[1]  # the index agrees with the disk


# ---- rename with links ----------------------------------------------------------------------------------------------

def test_rename_chain_rewrites_links_keeps_use_and_check_green(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    make(v, "concept", "old-name", title="The renamed hub", cue="hub cue", gist="The hub.")
    make(v, "finding", "user-one", title="First user", cue="first cue", gist="First.", tail="\nSee [[old-name]] for the setting.\n")
    make(v, "decision", "user-two", title="Second user", cue="second cue", gist="Second.", tail="\nAlso [[ old-name ]] holds.\n")
    make(v, "question", "old-question", title="An old question", cue="old cue", gist="Old.")
    assert sb(v, "archive", "old-question", "--why", "superseded", "--superseded-by", "old-name")[0] == 0
    sibling = make(v, "concept", "untouched-sibling", title="Sibling", cue="sibling cue", gist="Sibling.")
    assert sb(v, "check") == (0, "checked 5 notes: 0 errors, 0 warnings\n", "")
    code, out, _ = sb(v, "show", "old-name")
    assert code == 0 and "backlinks: user-one (First user); user-two (Second user)" in out

    clock(monkeypatch, 60)
    assert sb(v, "show", "old-name")[0] == 0  # use under the old name

    # failures change nothing
    before = bytes_of(v)
    for args in (("old-name", "user-one"), ("old-name", "Bad Name"), ("no-such-note", "fresh-name"), ("old-name", "old-name")):
        assert sb(v, "rename", *args)[0] == 2, args
    assert bytes_of(v) == before

    clock(monkeypatch, 70)
    code, out, err = sb(v, "rename", "old-name", "new-name")
    assert code == 0 and out == "renamed old-name -> new-name; links rewritten in 3 other notes\n", err
    assert (v / "notes/heat/new-name.md").is_file() and not (v / "notes/heat/old-name.md").exists()
    one = (v / "notes/heat/user-one.md").read_text(encoding="utf-8")
    two = (v / "notes/heat/user-two.md").read_text(encoding="utf-8")
    arch = (v / "archive/heat/old-question.md").read_text(encoding="utf-8")
    assert "[[new-name]]" in one and "[[new-name]]" in two and "old-name" not in one + two
    assert "superseded_by: [new-name]" in arch and "old-name" not in arch
    assert (v / ".sb" / "renames.tsv").read_text(encoding="utf-8").startswith("old-name\tnew-name\t2026-03-12\t")
    assert sb(v, "check") == (0, "checked 5 notes: 0 errors, 0 warnings\n", "")
    assert sb(v, "show", "old-name")[0] == 2
    code, out, _ = sb(v, "show", "new-name")
    assert "backlinks: user-one (First user); user-two (Second user)" in out
    assert edit_hook(monkeypatch, v, v / "notes/heat/new-name.md") == (0, "", "")
    assert edit_hook(monkeypatch, v, v / "notes/heat/user-one.md", "Edit") == (0, "", "")

    # the use under the old name follows the note: at day 120 the sibling has faded, the renamed note has not (use at day 60)
    clock(monkeypatch, 120)
    fading = forget_list(v)
    assert "new-name" not in fading and "untouched-sibling" in fading
    assert sibling.is_file()

    # rename back: links return, check stays green
    code, out, err = sb(v, "rename", "new-name", "old-name")
    assert code == 0 and "links rewritten in 3 other notes" in out, err
    assert "[[old-name]]" in (v / "notes/heat/user-one.md").read_text(encoding="utf-8")
    assert "superseded_by: [old-name]" in (v / "archive/heat/old-question.md").read_text(encoding="utf-8")
    assert sb(v, "check")[0] == 0 and "old-name" not in forget_list(v)


# ---- L3: a note file in the wrong place ------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["notes/loose.md", "notes/p/sub/x.md"])
def test_a_note_file_in_the_wrong_place_is_not_silently_ignored(tmp_path, monkeypatch, rel):
    v = init_vault(tmp_path)
    p = v / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ntitle: X\n---\n\nbroken, and where sb will not see it\n", encoding="utf-8")
    code, out, err = edit_hook(monkeypatch, v, p)
    assert code == 2 and p.stem in err  # intended: the hook says the note is broken or misplaced
    code, out, _ = sb(v, "check")
    assert p.stem in out  # intended: check names the file, as an error or a warning


@pytest.mark.parametrize("name,flaw", [("Big", "slug 'Big' must be lower-case"), ("ok-one", "missing field 'type'")])
def test_a_note_file_in_the_right_place_is_checked_by_hook_and_check(tmp_path, monkeypatch, name, flaw):
    v = init_vault(tmp_path)
    p = v / "notes" / "p" / f"{name}.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntitle: X\n---\n\nbroken\n", encoding="utf-8")
    code, out, err = edit_hook(monkeypatch, v, p)
    assert code == 2 and flaw in err
    code, out, _ = sb(v, "check")
    assert code == 1 and flaw in out


# ---- L6: the real hook chain -----------------------------------------------------------------------------------------

def hooks():
    spec = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    return {ev: entry for ev, (entry, *_) in spec.items()}


def hook_env(tmp_path, v, path_extra=True):
    """The environment Claude Code gives a hook: the plugin root, SB_VAULT from settings.json, and a PATH on which `sb` is found
    (a wrapper around this checkout, as pipx would install it)."""
    bin_dir = tmp_path / "hookbin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "sb"
    wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m scientific_brain.cli \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin" if path_extra else str(tmp_path / "empty"), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
            "SB_VAULT": str(v), "SB_NO_UPDATE": "1", "SB_TODAY": BASE.isoformat(), "PYTHONPATH": str(SRC), "HOME": str(tmp_path)}


def fire(event, env, payload=None):
    """Run the hooks.json command of an event the way Claude Code does: through the shell, JSON on stdin; (rc, stdout, stderr)."""
    cmd = hooks()[event]["hooks"][0]["command"]
    t0 = time.monotonic()
    r = subprocess.run(cmd, shell=True, env=env, input=json.dumps(payload or {}), capture_output=True, text=True,
                       timeout=hooks()[event]["hooks"][0]["timeout"])
    assert time.monotonic() - t0 < hooks()[event]["hooks"][0]["timeout"]
    return r.returncode, r.stdout, r.stderr


def test_hook_script_is_executable_unix_text_and_named_by_hooks_json():
    script = PLUGIN / "scripts" / "sb-hook.sh"
    assert os.access(script, os.X_OK) and stat.S_IMODE(script.stat().st_mode) & 0o111 == 0o111
    raw = script.read_bytes()
    assert raw.startswith(b"#!/bin/sh\n") and b"\r" not in raw
    for event, entry in hooks().items():
        cmd = entry["hooks"][0]["command"]
        assert cmd.startswith('"${CLAUDE_PLUGIN_ROOT}"/scripts/sb-hook.sh ') and entry["hooks"][0]["type"] == "command"
        assert (PLUGIN / cmd.split("}\"/", 1)[1].split()[0]).is_file()


def test_hook_matchers_cover_the_writing_tools_and_the_session_starts():
    after, start = hooks()["PostToolUse"], hooks()["SessionStart"]
    for tool in ("Write", "Edit", "MultiEdit"):
        assert re.fullmatch(after["matcher"], tool)
    for tool in ("Read", "Bash", "NotebookEdit", "Grep"):
        assert not re.fullmatch(after["matcher"], tool)
    assert all(re.fullmatch(start["matcher"], s) for s in ("startup", "resume"))
    assert not any(re.fullmatch(start["matcher"], s) for s in ("clear", "compact"))
    assert after["hooks"][0]["command"].endswith(" after-edit") and start["hooks"][0]["command"].endswith(" session-start")


def test_session_start_through_hooks_json(tmp_path):
    v = init_vault(tmp_path)
    env = hook_env(tmp_path, v)
    assert fire("SessionStart", env) == (0, "", "")  # empty vault: silent
    add_note(v, "first-note", project="heat", type="concept", title="First", cue="first cue")
    code, out, err = fire("SessionStart", env)
    assert code == 0 and out == "Scientific Brain: sleep due: never slept. Use the sleep skill.\n" and err == ""
    assert sb(v, "sleep", "begin")[0] == 0
    assert fire("SessionStart", env) == (0, "", "")  # a running sleep is not announced again
    assert sb(v, "sleep", "done", "first")[0] == 0
    assert fire("SessionStart", env) == (0, "", "")
    # a vault that is not one is said so, and the session still starts
    bad = tmp_path / "no-vault-here"
    bad.mkdir()
    code, out, err = fire("SessionStart", {**env, "SB_VAULT": str(bad)})
    assert code == 0 and "has no sb.toml" in out and err == ""


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_after_edit_through_hooks_json_exit_2_with_message_then_0(tmp_path, tool):
    v = init_vault(tmp_path)
    env = hook_env(tmp_path, v)
    sb(v, "new", "concept", "chain-note", "-p", "heat", "--why", "chain")
    p = v / "notes" / "heat" / "chain-note.md"

    def event():
        extra = {"edits": [{"old_string": "a", "new_string": "b"}]} if tool == "MultiEdit" else {}
        return {"tool_name": tool, "tool_input": {"file_path": str(p), **extra}}
    code, out, err = fire("PostToolUse", env, event())
    assert code == 2 and out == "" and "sb check found errors" in err and "chain-note.md" in err and "unfilled TODO" in err
    fill(p)
    assert fire("PostToolUse", env, event()) == (0, "", "")
    assert sb(v, "check", "--changed")[1] == "checked 0 notes: 0 errors, 0 warnings\n"  # the subprocess marked it, as in-process
    # a file that is no note of the vault, a missing path and plain garbage never fail an edit
    other = tmp_path / "elsewhere.md"
    other.write_text("not a note", encoding="utf-8")
    assert fire("PostToolUse", env, {"tool_name": tool, "tool_input": {"file_path": str(other)}}) == (0, "", "")
    assert fire("PostToolUse", env, {"tool_name": tool, "tool_input": {}}) == (0, "", "")
    r = subprocess.run(hooks()["PostToolUse"]["hooks"][0]["command"], shell=True, env=env, input="not json", capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_hooks_without_sb_on_the_path(tmp_path):
    v = init_vault(tmp_path)
    (tmp_path / "empty").mkdir()
    env = hook_env(tmp_path, v, path_extra=False)
    code, out, err = fire("SessionStart", env)
    assert code == 0 and "'sb' is not on PATH" in out and err == ""
    assert fire("PostToolUse", env, {"tool_name": "Write", "tool_input": {"file_path": str(v / "notes" / "x" / "y.md")}}) == (0, "", "")


# ---- L8: forgetting over time with real calls -----------------------------------------------------------------------

def test_forgetting_clock_runs_on_real_recall_show_veto_link_and_restore(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    for slug, word in (("note-a", "apricot"), ("note-b", "banana"), ("note-c", "cherry"), ("note-d", "damson"), ("note-e", "elder"),
                       ("note-f", "fig")):
        make(v, "concept", slug, title=f"The {word} topic", cue=f"{word} cue", gist=f"Gist about {word}.",
             tail="\nSee [[note-a]].\n" if slug == "note-c" else "")
    assert sb(v, "check")[1].startswith("checked 6 notes: 0 errors")

    # real use on later days: b by recall (day 30, 70) and show (day 100); c by show (day 60)
    for n, args in ((30, ("recall", "banana")), (60, ("show", "note-c")), (70, ("recall", "banana")), (100, ("show", "note-b"))):
        clock(monkeypatch, n)
        code, out, err = sb(v, *args)
        assert code == 0 and ("note-b" if "banana" in args or "note-b" in args else "note-c") in out, err
    days = {s: sorted({r[0] for r in usage_rows(v) if r[1] == "work" and r[3] == s and r[2] in ("show", "recall")}) for s in ("note-b", "note-c")}
    assert days == {"note-b": ["2026-01-31", "2026-03-12", "2026-04-11"], "note-c": ["2026-03-02"]}

    clock(monkeypatch, 90)  # a note rests 90 days: at the boundary nothing has faded
    assert forget_list(v) == set() and "forget candidates: 0" in sb(v, "stats")[1]
    clock(monkeypatch, 91)
    # a, linked from the active c, is protected; b and c are used; d, e and f are not
    assert forget_list(v) == {"note-d", "note-e", "note-f"} and "forget candidates: 3" in sb(v, "stats")[1]

    assert sb(v, "archive", "note-f", "--why", "faded")[0] == 0
    clock(monkeypatch, 95)
    assert sb(v, "sleep", "veto", "note-e", "still needed for the sweep")[0] == 0  # a veto counts as a review
    assert forget_list(v) == {"note-d"}

    clock(monkeypatch, 150)
    assert sb(v, "restore", "note-f")[0] == 0  # taking a note back is a full review
    assert forget_list(v) == {"note-d"}
    clock(monkeypatch, 200)  # c rests since day 60: it has faded and with it the protection of a
    assert forget_list(v) == {"note-a", "note-c", "note-d"} and "forget candidates: 3" in sb(v, "stats")[1]
    clock(monkeypatch, 240)  # e was kept at day 95: faded again; b (used at day 100) and f (restored at day 150) still hold
    assert forget_list(v) == {"note-a", "note-c", "note-d", "note-e"}
    clock(monkeypatch, 260)
    assert forget_list(v) == {"note-a", "note-c", "note-d", "note-e", "note-b"}
    clock(monkeypatch, 320)
    assert forget_list(v) == {"note-a", "note-b", "note-c", "note-d", "note-e", "note-f"}

    # the plan's verdicts are executable: archive the candidates, nothing is lost, the archive holds every text
    texts = {s: (v / "notes" / "heat" / f"{s}.md").read_text(encoding="utf-8") for s in ("note-a", "note-d")}
    for s in texts:
        assert sb(v, "archive", s, "--why", "faded")[0] == 0
        assert not (v / "notes" / "heat" / f"{s}.md").exists()
        body = (v / "archive" / "heat" / f"{s}.md").read_text(encoding="utf-8")
        assert all(line in body for line in texts[s].splitlines() if line.strip())
    code, out, _ = sb(v, "check")  # note-c still links to the archived note-a: a warning, no error
    assert code == 0 and "link to archived note [[note-a]]" in out


# ---- L9: the inbox over 30 days with real captures ------------------------------------------------------------------

def done_rows(v):
    """id -> list of outcomes in booking order."""
    out = {}
    for ln in (v / ".sb" / "inbox-done.tsv").read_text(encoding="utf-8").splitlines():
        i, d, o = ln.split("\t")
        out.setdefault(i, []).append((d, o))
    return out


def test_inbox_thirty_days_capture_wait_expire_find_and_settle(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    clock(monkeypatch, 0, date(2026, 3, 1))
    first = sb(v, "capture", "alpha behaves oddly near the wall", "-s", "runs/a.csv")[1].strip()
    clock(monkeypatch, 10, date(2026, 3, 1))
    second = sb(v, "capture", "beta needs a finer mesh")[1].strip()
    assert first != second
    clock(monkeypatch, 12, date(2026, 3, 1))
    assert sb(v, "inbox", "resolve", first, "--wait") == (0, f"{first}: wait\n", "")
    assert first in sb(v, "inbox")[1] and second in sb(v, "inbox")[1]

    clock(monkeypatch, 31, date(2026, 3, 1))  # the first line is 31 days old: due; the second is 21
    code, out, _ = sb(v, "inbox", "--due")
    assert first in out and second not in out
    assert sb(v, "hook", "session-start") == (0, "", "")  # inbox lines alone, without a note, make no sleep due
    assert sb(v, "sleep", "begin")[0] == 0
    code, out, _ = sb(v, "sleep", "plan")
    assert re.search(rf"`{first}` 31d _unsorted DUE: alpha", out) and re.search(rf"`{second}` 21d _unsorted: beta", out)
    code, out, err = sb(v, "inbox", "resolve", first, "--wait")  # a due line cannot wait again
    assert code == 2 and "is due" in err
    code, out, _ = sb(v, "sleep", "done", "nothing settled")
    assert code == 0 and "0 inbox lines settled (0 promoted), 1 expired" in out and f"expired {first}: alpha" in out
    assert second in sb(v, "inbox")[1] and first not in sb(v, "inbox")[1]
    code, out, _ = sb(v, "inbox", "--expired")
    assert first in out and "expired 2026-04-01" in out and second not in out
    code, out, _ = sb(v, "recall", "--inbox", "alpha")
    assert f"{first}  [inbox expired, _unsorted, 2026-03-01]" in out
    code, out, _ = sb(v, "recall", "alpha")
    assert "no match" in out and "+1 inbox matches (use --inbox)" in out
    assert "promotion rate: n/a, 1 expired" in sb(v, "stats")[1]

    # an expired line can still be settled; it then counts as settled, not as expired
    clock(monkeypatch, 32, date(2026, 3, 1))
    make(v, "concept", "alpha-note", project="heat", title="Alpha near the wall", cue="alpha cue", gist="Alpha.")
    assert sb(v, "inbox", "resolve", first, "--note", "alpha-note") == (0, f"{first}: note:alpha-note\n", "")
    assert sb(v, "inbox", "--expired")[1] == "(no expired inbox lines)\n"
    assert "promotion rate: 100% (1 of 1 settled)" in sb(v, "stats")[1]
    code, out, err = sb(v, "inbox", "resolve", first, "--drop")
    assert code == 2 and "already settled" in err

    clock(monkeypatch, 41, date(2026, 3, 1))  # the second line is now 31 days old
    assert second in sb(v, "inbox", "--due")[1]
    assert sb(v, "inbox", "resolve", second, "--wait")[0] == 2
    assert sb(v, "sleep", "begin")[0] == 0
    assert re.search(rf"`{second}` 31d _unsorted DUE", sb(v, "sleep", "plan")[1])
    assert "1 expired" in sb(v, "sleep", "done", "second round")[1]
    assert sb(v, "inbox", "resolve", second, "--drop") == (0, f"{second}: dropped\n", "")

    # every line ends with exactly one last outcome; the log keeps the whole history
    rows = done_rows(v)
    assert [o for _, o in rows[first]] == ["wait", "expired", "note:alpha-note"]
    assert [o for _, o in rows[second]] == ["expired", "dropped"]
    code, out, _ = sb(v, "stats")
    assert "inbox: 0 open" in out and "promotion rate: 50% (1 of 2 settled)" in out and "expired" not in out.split("promotion rate")[1].split("\n")[0]
    assert sb(v, "inbox") == (0, "(no open inbox lines)\n", "")
    assert len((v / ".sb" / "sleep.tsv").read_text(encoding="utf-8").splitlines()) == 2


# ---- L10: side effects of the after-edit hook -----------------------------------------------------------------------

def test_hook_marks_a_valid_note_checked_and_leaves_a_broken_one_unchecked(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    p = add_note(v, "hooked", project="heat", type="concept", title="Hooked", cue="hooked cue")
    assert edit_hook(monkeypatch, v, p) == (0, "", "")
    assert sb(v, "check", "--changed") == (0, "checked 0 notes: 0 errors, 0 warnings\n", "")
    p.write_text(p.read_text(encoding="utf-8").replace("cue: hooked cue\n", ""), encoding="utf-8")  # an edit that breaks it
    code, out, err = edit_hook(monkeypatch, v, p, "Edit")
    assert code == 2 and "missing field 'cue'" in err
    code, out, _ = sb(v, "check", "--changed")
    assert code == 1 and "checked 1 notes: 1 errors" in out  # still unchecked: the hook did not bless it
    p.write_text(p.read_text(encoding="utf-8").replace("type: concept\n", "type: concept\ncue: hooked cue again\n"), encoding="utf-8")
    assert edit_hook(monkeypatch, v, p, "Edit") == (0, "", "")
    assert sb(v, "check", "--changed")[1] == "checked 0 notes: 0 errors, 0 warnings\n"


def test_hook_in_a_sleep_logs_the_edit_as_sleep_use_and_gives_it_back_at_done(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    add_note(v, "planned-one", project="heat", type="concept", title="Planned", cue="planned cue")
    assert sb(v, "sleep", "begin")[0] == 0
    assert "planned-one" in sb(v, "sleep", "plan")[1]
    late = add_note(v, "late-note", project="heat", type="concept", title="Late arrival", cue="late cue")  # not in the plan
    assert sb(v, "check")[0] == 0  # the index sees the new file (a new file is no edit) ...
    late.write_text(late.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8")  # ... the edit follows
    assert edit_hook(monkeypatch, v, late) == (0, "", "")
    mine = [r for r in usage_rows(v) if r[3] == "late-note"]
    assert mine and all(r[1] == "sleep" for r in mine)  # during a sleep the edit is booked to the sleep, not to the work
    assert sb(v, "sleep", "verified", "planned-one")[0] == 0
    code, out, err = sb(v, "sleep", "done", "hook in a sleep")
    assert code == 0, err
    back = [r for r in usage_rows(v) if r[3] == "late-note" and r[1] == "sync" and r[2] == "edit"]
    assert back  # an edit of a note the plan did not name is the main session's use: given back at done
    assert re.search(r"verify (\d+)", sb(v, "stats")[1])[1] == "1"  # late-note was never verified by a sleep
    assert "late-note" in sb(v, "sleep", "begin")[1] + sb(v, "sleep", "plan")[1]


def test_hook_with_notes_over_the_limits_names_the_setting(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    toml = (v / "sb.toml").read_text(encoding="utf-8")
    (v / "sb.toml").write_text(toml.replace("body_words = 600", "body_words = 20").replace("title_words = 16", "title_words = 4")
                               .replace("links = 8", "links = 1"), encoding="utf-8")
    add_note(v, "target-a", project="heat", type="concept", title="Target a", cue="a")
    add_note(v, "target-b", project="heat", type="concept", title="Target b", cue="b")
    p = add_note(v, "long-one", project="heat", type="concept", title="One two three four five six", cue="c",
                 gist="[[target-a]] [[target-b]] " + "word " * 25)
    code, out, err = edit_hook(monkeypatch, v, p)
    assert code == 2 and "title_words" in err and "body_words" in err and "more than 1 links" in err
    add_note(v, "long-one", project="heat", type="concept", title="Short", cue="c", gist="[[target-a]] fine")
    assert edit_hook(monkeypatch, v, p) == (0, "", "")


# ---- L15: texts that begin with a dash -------------------------------------------------------------------------------

def dash_vault(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    add_note(v, "keeper", project="heat", type="concept", title="Keeper", cue="keeper cue")
    add_note(v, "partner", project="heat", type="concept", title="Partner", cue="partner cue")
    return v


DASH_CASES = {
    "capture": lambda v: (("capture", "-minus"), lambda: "-minus" in sb(v, "inbox")[1]),
    "capture-number": lambda v: (("capture", "-1e-8"), lambda: "-1e-8" in sb(v, "inbox")[1]),
    "veto": lambda v: (("sleep", "veto", "keeper", "-bad"), lambda: "\tkeeper\t-bad" in (v / ".sb" / "vetoes.tsv").read_text(encoding="utf-8")),
    "unrelated": lambda v: (("sleep", "unrelated", "keeper", "partner", "-bad"),
                            lambda: "-bad" in (v / ".sb" / "unrelated.tsv").read_text(encoding="utf-8")),
    "new-why": lambda v: (("new", "concept", "fresh-one", "-p", "heat", "--why", "-bad"), lambda: (v / "notes/heat/fresh-one.md").is_file()),
    "archive-reason": lambda v: (("archive", "keeper", "--why", "wrong", "--reason", "-bad"), lambda: (v / "archive/heat/keeper.md").is_file()),
}


@pytest.mark.parametrize("case", sorted(DASH_CASES))
def test_a_text_that_starts_with_a_dash_is_taken_as_text(tmp_path, monkeypatch, case):
    v = dash_vault(tmp_path, monkeypatch)
    args, effect = DASH_CASES[case](v)
    code, out, err = run(v, *args)
    assert code == 0, err
    assert effect()


def test_dash_texts_work_behind_double_dash_or_joined_to_the_option(tmp_path, monkeypatch):
    v = dash_vault(tmp_path, monkeypatch)
    assert run(v, "capture", "--", "-minus")[0] == 0 and "-minus" in sb(v, "inbox")[1]
    assert run(v, "capture", "-1e-8 diverges")[0] == 0 and "-1e-8 diverges" in sb(v, "inbox")[1]  # a space makes it text
    assert run(v, "sleep", "veto", "keeper", "--", "-bad")[0] == 0
    assert run(v, "sleep", "unrelated", "keeper", "partner", "--", "-bad")[0] == 0
    assert run(v, "new", "concept", "fresh-one", "-p", "heat", "--why=-bad")[0] == 0
    assert run(v, "archive", "keeper", "--why", "wrong", "--reason=-bad")[0] == 0
    assert run(v, "recall", "-minus")[0] == 0  # recall alone handles it without help


def test_unverifiable_takes_a_text_behind_double_dash(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    add_note(v, "no-source", project="heat", type="concept", title="No source", cue="no source cue")
    assert sb(v, "sleep", "begin")[0] == 0
    sb(v, "sleep", "plan")
    code, out, err = run(v, "sleep", "unverifiable", "no-source", "--", "-bad")
    assert code == 0, err
    assert "no-source" in (v / ".sb" / "unverifiable.tsv").read_text(encoding="utf-8")


def test_unverifiable_takes_a_text_that_starts_with_a_dash(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    add_note(v, "no-source", project="heat", type="concept", title="No source", cue="no source cue")
    assert sb(v, "sleep", "begin")[0] == 0
    sb(v, "sleep", "plan")
    code, out, err = run(v, "sleep", "unverifiable", "no-source", "-bad")
    assert code == 0, err


# ---- L17: sb new by the main session during a sleep ------------------------------------------------------------------

def test_new_during_a_sleep_is_booked_to_the_sleep_and_stays_out_of_the_daily_count(tmp_path, monkeypatch):
    """Documents the present behaviour, whether intended or not: the creation of a note by another session while a
    sleep runs cannot be told from the sleeper's own, so it counts as 'created' by the sleep and not for new_per_day."""
    v = init_vault(tmp_path)
    add_note(v, "existing", project="heat", type="concept", title="Existing", cue="existing cue")
    assert sb(v, "sleep", "begin")[0] == 0
    sb(v, "sleep", "plan")
    for i in range(4):  # four new notes while the sleep runs: no warning about the daily limit (3) at any of them
        code, out, err = sb(v, "new", "concept", f"midsleep-{i}", "-p", "heat", "--why", "created meanwhile", "--anyway")
        assert code == 0 and "were created today already" not in out, err
        fill(v / "notes" / "heat" / f"midsleep-{i}.md", title=f"Midsleep {i}", cue=f"midsleep cue {i}", gist=f"Midsleep gist {i}.")
    rows = [r for r in usage_rows(v) if r[2] == "create"]
    assert [r[1] for r in rows] == ["sleep"] * 4  # none as work
    code, out, err = sb(v, "sleep", "done", "meanwhile")
    assert code == 0 and "4 created" in out, err
    # after the sleep the daily count still starts at zero: the fourth work creation of the day is the one that warns
    for i in range(3):
        code, out, _ = sb(v, "new", "concept", f"after-{i}", "-p", "heat", "--why", "after the sleep", "--anyway")
        assert code == 0 and "created today already" not in out
    code, out, _ = sb(v, "new", "concept", "after-3", "-p", "heat", "--why", "after the sleep", "--anyway")
    assert code == 0 and "3 notes were created today already" in out
    # the notes of the main session were not verified by the sleep: they wait for the next one
    code, out, _ = sb(v, "stats")
    assert int(re.search(r"verify (\d+)", out)[1]) >= 4


@pytest.mark.parametrize("args", [("capture", "a finding", "--sorce", "x.py"), ("capture", "--projekt", "heat", "a finding"),
                                  ("archive", "keeper", "--why", "faded", "--fixlinks"), ("sleep", "veto", "keeper", "--resaon")])
def test_a_mistyped_long_option_is_refused_not_taken_as_text(tmp_path, monkeypatch, args):
    """A text may start with '-', but a mistyped option of a text command must not slip into the inbox or a log as text."""
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", "2026-11-08")
    add_note(v, "keeper", title="Keeper note", cue="kept for the dash tests")
    before = {p: p.read_bytes() for p in v.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as e:  # argparse refuses it before sb runs
        sb(v, *args)
    assert e.value.code == 2
    assert {p: p.read_bytes() for p in v.rglob("*") if p.is_file()} == before  # nothing written


def test_a_prefix_of_a_real_option_stays_that_option(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", "2026-11-08")
    assert sb(v, "capture", "-minus first", "--proj", "heat", "--sour", "src/ftcs.py")[0] == 0
    out = sb(v, "inbox")[1]
    assert "-minus first" in out and "heat" in out


@pytest.mark.parametrize("extra", [("-p", "heat"), ("-n", "3"), ("-n3",), ("--archive",), ("--inbox",)])
def test_recall_takes_options_between_its_terms(tmp_path, monkeypatch, extra):
    """sb <= 0.3.5 moved the options of recall in front of its terms; `recall ftcs -p heat stability` must still work."""
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", "2026-11-08")
    code, out, err = sb(v, "recall", "ftcs", *extra, "stability")
    assert code == 0, err
    assert sb(v, "recall", "ftcs", "stability", *extra)[1] == out  # the same as with the options behind the terms


def test_capture_takes_a_text_that_starts_with_s_or_dash_s_as_text(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", "2026-11-08")
    assert sb(v, "capture", "-sigma", "rises", "-p", "heat")[0] == 0
    assert sb(v, "capture", "-stress", "-1e-8", "here", "-p", "heat")[0] == 0
    rows = [ln.split("\t") for ln in (v / ".sb" / "inbox.tsv").read_text(encoding="utf-8").splitlines()][-2:]
    assert [r[3] for r in rows] == ["", ""]  # no source
    assert [r[-1] for r in rows] == ["-sigma rises", "-stress -1e-8 here"]
