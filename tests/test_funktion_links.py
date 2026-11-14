"""Regression tests for note IO: atomic writes, all-or-nothing rename and archive, line endings, identity, logs, the vault lock."""
import builtins
import io
import os
import stat
import threading
import time
import unicodedata
from datetime import date
from pathlib import Path

import pytest
from helpers import add_note, init_vault, make_vault, sb

from scientific_brain import inbox, links, vault
from scientific_brain.vault import Vault

NOTE = "ftcs-stability-limit"  # demo vault: linked from morton-mayers-assessment and cn-oscillations-large-time-step


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", "2026-11-08")


def tree(v):
    return {p.relative_to(v).as_posix(): p.read_bytes() for p in sorted(Path(v).rglob("*.md"))}


def crlf(p):
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))


def fail_writes(monkeypatch, decide):
    """Make opening a file for writing fail like a full disk; decide(path, n) -> 'after' truncates and writes 20 bytes
    first, any other true value fails before writing (n counts the write opens of files whose name holds '.md')."""
    real, count = io.open, {"n": 0}

    def fake(file, mode="r", *a, **k):
        if isinstance(file, (str, Path)) and ("w" in mode or "x" in mode) and ".md" in Path(file).name:
            count["n"] += 1
            how = decide(Path(file), count["n"])
            if how == "after":
                with real(file, mode, *a, **k) as f:
                    f.write("x" * 20)
            if how:
                raise OSError(28, "No space left on device")
        return real(file, mode, *a, **k)

    monkeypatch.setattr(io, "open", fake)
    monkeypatch.setattr(builtins, "open", fake)


# ---- L1 atomic writes ----------------------------------------------------------------------------------------------

def test_write_atomic_keeps_the_mode_and_writes_line_endings_as_given(tmp_path):
    from scientific_brain.vault import write_atomic
    p = tmp_path / "n.md"
    p.write_text("old\n")
    p.chmod(0o640)
    write_atomic(p, "a\r\nb\n")
    assert p.read_bytes() == b"a\r\nb\n" and stat.S_IMODE(p.stat().st_mode) == 0o640
    write_atomic(tmp_path / "new.md", "x", mode=0o600)
    assert stat.S_IMODE((tmp_path / "new.md").stat().st_mode) == 0o600
    assert sorted(f.name for f in tmp_path.iterdir()) == ["n.md", "new.md"]  # no temporary file left


def test_write_atomic_does_not_replace_a_read_only_file(tmp_path):
    from scientific_brain.vault import write_atomic
    p = tmp_path / "n.md"
    p.write_text("old\n")
    p.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            write_atomic(p, "new\n")
    finally:
        p.chmod(0o644)
    assert p.read_text() == "old\n" and [f.name for f in tmp_path.iterdir()] == ["n.md"]


@pytest.mark.parametrize("cmd", ["archive", "restore", "rename"])
def test_a_write_that_fails_after_truncating_does_not_destroy_the_note(tmp_path, monkeypatch, cmd):
    """An in-place write truncates first: a full disk then leaves 20 bytes of the note (archive and restore had moved it already)."""
    v = make_vault(tmp_path)
    p = add_note(v, "w-n", loc="archive" if cmd == "restore" else "notes", body="\nSee [[w-m]].\n" if cmd == "rename" else "")
    add_note(v, "w-m")
    before = tree(v)
    argv = {"archive": ("archive", "w-n", "--why", "faded"), "restore": ("restore", "w-n"), "rename": ("rename", "w-m", "w-k")}[cmd]
    fail_writes(monkeypatch, lambda path, n: "after" if "w-n.md" in path.name else None)
    code, out, err = sb(v, *argv)
    monkeypatch.undo()
    assert code == 2 and "Traceback" not in err
    assert tree(v) == before and p.exists()


# ---- L2 archive never replaces an archived note --------------------------------------------------------------------

def test_archive_refuses_when_the_archive_holds_the_slug_already(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "twin", loc="archive", body="\nThe archived one.\n")
    add_note(v, "twin", body="\nThe live one.\n")
    before = tree(v)
    code, out, err = sb(v, "archive", "twin", "--why", "faded")
    assert code == 2 and "already exists" in err and "nothing was archived" in err
    assert tree(v) == before


# ---- L3 / L4 all or nothing ----------------------------------------------------------------------------------------

def test_rename_with_a_read_only_linking_note_fails_cleanly_and_changes_nothing(tmp_path):
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "morton-mayers-assessment.md"
    before = tree(v)
    p.chmod(0o444)
    try:
        code, out, err = sb(v, "rename", NOTE, "ftcs-limit")
    finally:
        p.chmod(0o644)
    assert code == 2 and "morton-mayers-assessment.md" in err and "nothing was renamed" in err
    assert tree(v) == before


def test_rename_with_a_note_it_cannot_read_fails_cleanly_and_changes_nothing(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "locked-away")
    before = tree(v)
    p.chmod(0)
    try:
        code, out, err = sb(v, "rename", NOTE, "ftcs-limit")
    finally:
        p.chmod(0o644)
    assert code == 2 and "locked-away.md" in err and "nothing was renamed" in err
    assert tree(v) == before


def test_a_failed_rename_puts_back_the_times_of_the_notes_it_had_rewritten(tmp_path, monkeypatch):
    """The index takes a new mtime for an edit, and an edit counts as use: a roll-back must restore the times too."""
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "morton-mayers-assessment.md"
    os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    real = Path.rename

    def boom(self, target):
        if self.name == f"{NOTE}.md":
            raise OSError("disk full")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", boom)
    code, out, err = sb(v, "rename", NOTE, "ftcs-limit")
    assert code == 2 and "left as they were" in err
    assert p.stat().st_mtime_ns == 1_000_000_000_000_000_000 and f"[[{NOTE}]]" in p.read_text()


def test_archive_fix_links_with_a_read_only_linking_note_changes_nothing(tmp_path):
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "morton-mayers-assessment.md"
    before = tree(v)
    p.chmod(0o444)
    try:
        code, out, err = sb(v, "archive", NOTE, "--why", "superseded", "--superseded-by", "scheme-choice", "--fix-links")
    finally:
        p.chmod(0o644)
    assert code == 2 and "nothing was archived" in err
    assert tree(v) == before


def test_archive_fix_links_with_a_failing_second_write_changes_nothing(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    before = tree(v)
    fail_writes(monkeypatch, lambda path, n: n == 2)
    code, out, err = sb(v, "archive", NOTE, "--why", "faded", "--fix-links")
    monkeypatch.undo()
    assert code == 2 and "left as they were" in err
    assert tree(v) == before


@pytest.mark.parametrize("cmd", ["archive", "restore"])
def test_a_note_that_is_not_utf8_is_neither_archived_nor_restored(tmp_path, cmd):
    v = make_vault(tmp_path)
    p = add_note(v, "latin", loc="archive" if cmd == "restore" else "notes")
    p.write_bytes(p.read_bytes() + "\nMüller\n".encode("latin-1"))
    before = tree(v)
    code, out, err = sb(v, *(("archive", "latin", "--why", "faded") if cmd == "archive" else ("restore", "latin")))
    assert code == 2 and "latin.md" in err
    assert tree(v) == before


# ---- L5 line endings -----------------------------------------------------------------------------------------------

def test_rename_archive_and_restore_keep_crlf_line_endings(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "c-old")
    p = add_note(v, "c-linker", body="\nSee [[c-old]].\n")
    crlf(p)
    before = p.read_bytes()
    assert sb(v, "rename", "c-old", "c-new")[0] == 0
    assert p.read_bytes() == before.replace(b"[[c-old]]", b"[[c-new]]")
    before = p.read_bytes()
    assert sb(v, "archive", "c-linker", "--why", "faded")[0] == 0
    archived = (v / "archive" / "heat" / "c-linker.md").read_bytes()
    assert archived.count(b"\n") == archived.count(b"\r\n") and b"archived_why: faded\r\n" in archived
    assert sb(v, "restore", "c-linker")[0] == 0
    assert p.read_bytes() == before


# ---- L6 rename touches only what names the old slug ----------------------------------------------------------------

def test_rename_leaves_a_superseded_by_line_without_the_old_slug_as_it_is(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "r-old")
    add_note(v, "r-a")
    add_note(v, "r-b")
    p = add_note(v, "r-arch", loc="archive", extra={"archived_why": "superseded", "superseded_by": '[ "r-a" ,r-b ]'})
    os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    before = p.read_bytes()
    assert sb(v, "rename", "r-old", "r-new")[0] == 0
    assert p.read_bytes() == before and p.stat().st_mtime_ns == 1_000_000_000_000_000_000


# ---- L7 no duplicate link bullets ----------------------------------------------------------------------------------

def test_fix_links_to_a_successor_already_listed_keeps_one_bullet(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "archive", NOTE, "--why", "superseded", "--superseded-by", "couple-time-step-to-mesh", "--fix-links")[0] == 0
    text = (v / "notes" / "heat" / "cn-oscillations-large-time-step.md").read_text()
    assert text.count("- [[couple-time-step-to-mesh]]") == 1 and f"[[{NOTE}]]" not in text


# ---- L8 / L9 identity ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [("rename", "dup-note", "other-name"), ("archive", "dup-note", "--why", "faded")])
def test_two_notes_with_one_slug_make_rename_and_archive_refuse(tmp_path, argv):
    v = make_vault(tmp_path)
    add_note(v, "dup-note", type="concept")
    add_note(v, "dup-note", project="_general", type="concept")
    before = tree(v)
    code, out, err = sb(v, *argv)
    assert code == 2 and "used by 2 notes" in err
    assert tree(v) == before


def test_rename_finds_a_note_whose_file_name_is_not_nfc(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "x-tmp", type="concept")
    p.rename(p.with_name(unicodedata.normalize("NFD", "grüne-note") + ".md"))
    code, out, err = sb(v, "rename", "grüne-note", "gruene-note")
    assert code == 0, err
    assert (v / "notes" / "heat" / "gruene-note.md").is_file()


# ---- L10 / L13 logs ------------------------------------------------------------------------------------------------

def test_an_append_after_a_row_without_final_newline_starts_a_new_row(tmp_path):
    v = init_vault(tmp_path)
    (v / ".sb" / "t.tsv").write_text("a\tb\tc", encoding="utf-8")  # a row cut off by a crash
    Vault(v).append("t.tsv", "d", "e", "f")
    assert Vault(v).rows("t.tsv", 3) == [["a", "b", "c"], ["d", "e", "f"]]


def test_a_cut_off_inbox_row_without_text_is_not_a_capture(tmp_path):
    v = init_vault(tmp_path)
    (v / ".sb" / "inbox.tsv").write_text("aaaaaa\t2026-11-01\t_unsorted\t\tfirst line\nbbbbbb\t2026-11-0", encoding="utf-8")
    code, out, err = sb(v, "inbox")
    assert "aaaaaa" in out and "bbbbbb" not in out


# ---- L11 the vault lock --------------------------------------------------------------------------------------------

def _waits(root, hold):
    """True if a second session (thread) has to wait for the lock while hold() runs."""
    got = threading.Event()

    def other():
        with Vault(root).locked():
            got.set()

    with hold():
        t = threading.Thread(target=other)
        t.start()
        waited = not got.wait(0.3)
    t.join(5)
    return waited and got.is_set()


def test_the_vault_lock_makes_another_session_wait_and_nests_in_one(tmp_path):
    v = Vault(init_vault(tmp_path))
    assert _waits(v.root, v.locked)

    def nested():
        with v.locked(), Vault(v.root).locked():  # a nested use in one thread must not wait on itself
            pass

    t = threading.Thread(target=nested, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive()
    assert not _waits(v.root, lambda: open(os.devnull))  # released again


def test_without_fcntl_the_vault_lock_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "fcntl", None)
    root = init_vault(tmp_path)
    with Vault(root).locked():
        assert sb(root, "capture", "still works")[0] == 0


def test_parallel_captures_of_the_same_text_each_get_a_line(tmp_path, monkeypatch):
    """The id is chosen from the lines read before the append: a second session in between chose the same id and one
    capture was lost although both printed an id."""
    root = init_vault(tmp_path)
    real, ids, th = inbox.lines, [], []

    def lines(v):
        out = real(v)
        if not th:
            th.append(threading.Thread(target=lambda: ids.append(inbox.capture(Vault(root), "same", "p", "", date(2026, 11, 8)))))
            th[0].start()
            th[0].join(0.3)  # unlocked it is done at once; locked it waits for the first capture
        return out

    monkeypatch.setattr(inbox, "lines", lines)
    ids.append(inbox.capture(Vault(root), "same", "p", "", date(2026, 11, 8)))
    th[0].join(5)
    monkeypatch.undo()
    assert len(set(ids)) == 2 and len(inbox.lines(Vault(root))) == 2


def test_two_renames_that_rewrite_the_same_note_both_survive(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    add_note(v, "c-a")
    add_note(v, "c-b")
    p = add_note(v, "c-c", body="\nSee [[c-a]] and [[c-b]].\n")
    real, th = links.rewrite, []

    def rewrite(text, old, new):
        if old == "c-a" and "[[c-b]]" in text and not th:  # another session renames c-b meanwhile
            th.append(threading.Thread(target=lambda: links.rename(Vault(v), "c-b", "c-b2")))
            th[0].start()
            th[0].join(0.3)
        return real(text, old, new)

    monkeypatch.setattr(links, "rewrite", rewrite)
    assert sb(v, "rename", "c-a", "c-a2")[0] == 0
    th[0].join(5)
    monkeypatch.undo()
    assert "[[c-a2]] and [[c-b2]]" in p.read_text()


def held(root):
    return getattr(vault._HELD, "roots", {}).get(str(Path(root).resolve()), 0)


def test_rename_reads_every_note_once_under_the_lock_and_updates_the_index_after_it(tmp_path, monkeypatch):
    from scientific_brain.index import Index
    v = make_vault(tmp_path)
    reads, updates, rewrite, update = [], [], links.rewrite, Index.update
    monkeypatch.setattr(links, "rewrite", lambda text, old, new: (reads.append(held(v)), rewrite(text, old, new))[1])
    monkeypatch.setattr(Index, "update", lambda self, rels: (updates.append(held(v)), update(self, rels))[1])
    assert sb(v, "rename", NOTE, "ftcs-limit")[0] == 0
    assert len(reads) == sum(len(Vault(v).scan(loc)) for loc in ("notes", "archive")) and all(reads)  # one pass, locked
    assert updates == [0]  # the index is not updated under the lock


def old_gitignore(tmp_path, text):
    root = init_vault(tmp_path)
    (root / ".gitignore").write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize("text", [".sb/index.sqlite*\n.sb/sleeping\n", ".sb/index.sqlite*\n.sb/sleeping"])
def test_the_gitignore_of_an_older_vault_gets_the_lock_file_once(tmp_path, monkeypatch, text):
    root = old_gitignore(tmp_path, text)
    assert sb(root, "capture", "first")[0] == 0
    assert (root / ".gitignore").read_text(encoding="utf-8") == text  # a write command leaves it alone
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert sb(root, "stats")[0] == 0 and sb(root, "hook", "session-start")[0] == 0 and sb(root, "stats")[0] == 0
    assert (root / ".gitignore").read_text(encoding="utf-8") == ".sb/index.sqlite*\n.sb/sleeping\n.sb/lock\n"


def test_a_gitignore_not_written_by_sb_is_left_alone(tmp_path):
    root = old_gitignore(tmp_path, "*.pdf\n")
    assert sb(root, "stats")[0] == 0
    assert (root / ".gitignore").read_text(encoding="utf-8") == "*.pdf\n"
    (root / ".gitignore").unlink()
    assert sb(root, "stats")[0] == 0
    assert not (root / ".gitignore").exists()


def test_check_warns_about_temporary_files_a_killed_write_left_behind(tmp_path):
    v = make_vault(tmp_path)
    left = [v / "notes" / "heat" / ".scheme-choice.md.0123abcd.tmp", v / ".sb" / ".snapshot.tsv.89abcdef.tmp"]
    for p in left:
        p.write_text("half", encoding="utf-8")
    code, out, _ = sb(v, "check")
    assert code == 0
    for rel in ("notes/heat/.scheme-choice.md.0123abcd.tmp:1: warning", ".sb/.snapshot.tsv.89abcdef.tmp:1: warning"):
        assert rel in out, out


# ---- L12 linear-time patterns --------------------------------------------------------------------------------------

def test_a_list_value_with_a_long_blank_run_parses_in_linear_time_and_lists_parse_as_before():
    from scientific_brain.vault import ParseError, parse_value
    t0 = time.time()
    with pytest.raises(ParseError):
        parse_value("sources", "[" + " " * 1500 + '"]', "x:1")
    assert time.time() - t0 < 1
    assert parse_value("sources", '[ a.py ,"b, c.py",  d e ,]', "x:1") == ["a.py", "b, c.py", "d e"]
    assert parse_value("sources", "[]", "x:1") == [] and parse_value("sources", '[ "x" ]', "x:1") == ["x"]


def test_a_line_of_open_wikilinks_is_scanned_in_linear_time():
    t0 = time.time()
    assert vault.WIKI.findall("[[" * 20000) == []
    assert time.time() - t0 < 1


# ---- L14 folder names and BOM --------------------------------------------------------------------------------------

def test_a_project_folder_in_nfd_matches_the_project_in_nfc(tmp_path):
    v = init_vault(tmp_path)
    d = v / "notes" / unicodedata.normalize("NFD", "Übung")
    d.mkdir(parents=True)
    (d / "n.md").write_text("---\ntitle: T\ntype: concept\nproject: Übung\ncreated: 2026-11-01\nupdated: 2026-11-01\n"
                            "cue: c\n---\n\nA gist.\n", encoding="utf-8")
    code, out, err = sb(v, "check")
    assert code == 0, out


def test_a_note_with_a_bom_is_an_error_that_names_the_bom(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "bom")
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
    code, out, err = sb(v, "check")
    assert code == 1 and "bom.md:1" in out and "byte order mark (BOM)" in out
