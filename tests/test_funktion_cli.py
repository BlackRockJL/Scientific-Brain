"""Regression tests for the CLI layer: `sb new` two-step, restore as use, error handling, recall terms, hook, review."""
import errno
import io
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from helpers import add_note, init_vault, make_vault, sb

from scientific_brain import cli, usage
from scientific_brain.vault import Vault


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", "2026-11-08")


def rows(v):
    return [ln.split("\t") for ln in (v / ".sb" / "usage.tsv").read_text(encoding="utf-8").splitlines()]


# ---------- sb new: similar notes stop the write until --anyway ----------

def test_new_with_similar_notes_writes_nothing_and_says_so(tmp_path):
    v = make_vault(tmp_path)
    before = rows(v)
    code, out, err = sb(v, "new", "finding", "ftcs-stability-recheck", "-p", "heat", "--why", "ftcs stability limit measured again")
    assert code != 0 and "ftcs-stability-limit" in out and "created" not in out
    assert "not written" in err and "--anyway" in err
    assert not (v / "notes" / "heat" / "ftcs-stability-recheck.md").exists()
    assert rows(v) == before  # no create event either


def test_new_anyway_writes_after_the_similar_notes_were_shown(tmp_path):
    v = make_vault(tmp_path)
    code, out, err = sb(v, "new", "finding", "ftcs-stability-recheck", "-p", "heat", "--why", "ftcs stability limit measured again",
                        "--anyway")
    assert code == 0 and "ftcs-stability-limit" in out and "created notes/heat/ftcs-stability-recheck.md" in out
    assert (v / "notes" / "heat" / "ftcs-stability-recheck.md").is_file()


def test_new_without_similar_notes_needs_no_flag(tmp_path):
    v = init_vault(tmp_path)
    code, out, _ = sb(v, "new", "finding", "ftcs-stability-limit", "-p", "heat", "--why", "own measurement")
    assert code == 0 and "no similar notes" in out and "created" in out


def test_new_for_a_slug_taken_meanwhile_is_an_error_not_a_traceback(tmp_path, monkeypatch):
    v = init_vault(tmp_path)
    real = cli.Index.search

    def search(self, *a, **k):  # a parallel session writes the note right after the existence check
        add_note(v, "race-note")
        return real(self, *a, **k)

    monkeypatch.setattr(cli.Index, "search", search)
    code, _, err = sb(v, "new", "finding", "race-note", "-p", "heat", "--why", "own measurement", "--anyway")
    assert code == 2 and "already exists" in err and "Traceback" not in err


# ---------- restore counts as use ----------

def test_restore_is_a_full_review_so_the_note_is_no_candidate_at_once(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", "2027-06-01")
    day = date(2027, 6, 1)
    slug = usage.forget_candidates(Vault(v), day)[0][0]
    assert sb(v, "archive", slug, "--why", "faded")[0] == 0
    assert sb(v, "restore", slug)[0] == 0
    assert ["2027-06-01", "work", "show", slug] in rows(v)
    assert slug not in [x[0] for x in usage.forget_candidates(Vault(v), day)]


# ---------- main turns I/O and database errors into one line, exit 2 ----------

@pytest.mark.parametrize("exc", [OSError(errno.EACCES, "Permission denied", "notes/heat/x.md"),
                                 sqlite3.OperationalError("database is locked"),
                                 UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
                                 UnicodeEncodeError("utf-8", "\udcff", 0, 1, "surrogates not allowed")])
def test_main_reports_os_sqlite_and_unicode_errors_as_a_line(tmp_path, monkeypatch, exc):
    v = make_vault(tmp_path)

    def boom(a):
        raise exc

    monkeypatch.setattr(cli, "cmd_map", boom)
    code, out, err = sb(v, "map")
    assert code == 2 and err.startswith("sb: error: ") and err.count("\n") == 1 and "Traceback" not in err


def test_a_note_that_is_not_utf8_gives_an_error_line_from_archive(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "latin-note")
    p.write_bytes(p.read_bytes() + "\nM\xfcller\n".encode("latin-1"))
    code, _, err = sb(v, "archive", "latin-note", "--why", "faded")
    assert code == 2 and err.splitlines()[-1].startswith("sb: error: ") and "Traceback" not in err


# ---------- a skeleton's ref: TODO is one error ----------

def test_ref_todo_is_reported_once_and_two_skeletons_do_not_collide(tmp_path):
    v = init_vault(tmp_path)
    for slug in ("first-paper", "second-paper"):
        assert sb(v, "new", "source", slug, "-p", "proj", "--why", "own assessment")[0] == 0
    code, out, _ = sb(v, "check")
    assert code == 1
    assert "unknown citation key" not in out and "already used" not in out
    for slug in ("first-paper", "second-paper"):
        text = (v / "notes" / "proj" / f"{slug}.md").read_text(encoding="utf-8")
        todo = [i + 1 for i, ln in enumerate(text.split("\n")) if "TODO" in ln]
        named = [int(m[1]) for m in re.finditer(rf"{slug}\.md:(\d+): error", out)]
        assert named == todo  # one error per placeholder line, none else


# ---------- recall: a term with a leading '-' is a term ----------

def hits(out):
    return re.findall(r"^(\S+)  \[(?!inbox)", out, flags=re.M)


@pytest.mark.parametrize("term", ["-1e-8", "-minus", "-0.5", "-C++"])
def test_recall_takes_terms_with_a_leading_dash(tmp_path, term):
    v = init_vault(tmp_path)
    add_note(v, "dashed", gist=f"Gist: the value {term} was observed with a large margin.")
    code, out, err = sb(v, "recall", term)
    assert code == 0, err
    assert "dashed" in hits(out) or term == "-C++"  # C++ has too few letters to match, but it is no usage error


def test_recall_a_word_with_only_dashes_is_ignored_like_punctuation(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "dashed", gist="Gist: error of the margin observed.")
    code, out, err = sb(v, "recall", "error", "---", "margin")
    assert code == 0 and "dashed" in hits(out), err
    code, _, err = sb(v, "recall", "---")
    assert code == 2 and err.startswith("sb: error: no usable search terms")


def test_recall_dash_terms_combine_with_words_and_options(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "dashed", gist="Gist: error of -1e-8 observed in the margin.")
    add_note(v, "other", project="solver", gist="Gist: error of -1e-8 observed again.")
    for argv in (("recall", "error", "-1e-8"), ("recall", "-1e-8", "error", "-n", "5"), ("recall", "-n", "1", "-1e-8", "-p", "heat"),
                 ("recall", "-p", "heat", "--", "-1e-8", "error")):
        code, out, err = sb(v, *argv)
        assert code == 0 and "dashed" in hits(out), (argv, err)
    assert hits(sb(v, "recall", "-p", "heat", "-1e-8")[1]) == ["dashed"]  # options still work


# ---------- after-edit hook with an unusable index ----------

def edit_event(monkeypatch, path):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})))


def break_index(v):
    for f in (v / ".sb").glob("index.sqlite*"):
        f.unlink()
    (v / ".sb" / "index.sqlite").write_bytes(b"not a database " * 100)


def test_after_edit_hook_still_checks_the_note_when_the_index_is_broken(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    assert sb(v, "check")[0] == 0
    break_index(v)
    edit_event(monkeypatch, add_note(v, "broken-note", drop=("cue",)))
    code, _, err = sb(v, "hook", "after-edit")
    assert code == 2 and "broken-note" in err and "index error" not in err
    edit_event(monkeypatch, add_note(v, "fine-note"))
    assert sb(v, "hook", "after-edit")[0] == 0


# ---------- stats: a note created in the future is not new ----------

def test_stats_does_not_count_notes_created_after_today_as_new(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "future-note", extra={"created": "2026-12-01", "updated": "2026-12-01"})
    add_note(v, "recent-note", extra={"created": "2026-11-05", "updated": "2026-11-05"})
    assert "new notes in the last 7 days: 1" in sb(v, "stats")[1]


# ---------- review ----------

def test_review_counts_only_decided_candidates_not_merged_duplicates(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "sleep", "begin")[0] == 0
    assert sb(v, "archive", "cn-oscillations-large-time-step", "--why", "superseded", "--superseded-by", "scheme-choice")[0] == 0
    assert sb(v, "sleep", "done", "merged")[0] == 0
    assert "deciding 0 candidates" in sb(v, "review")[1]


def test_review_counts_faded_archives_and_vetoes_as_decided(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "sleep", "begin")[0] == 0
    assert sb(v, "archive", "cn-oscillations-large-time-step", "--why", "faded")[0] == 0
    assert sb(v, "sleep", "veto", "scheme-choice", "still needed")[0] == 0
    assert sb(v, "sleep", "done", "forgot one, kept one")[0] == 0
    assert "deciding 2 candidates" in sb(v, "review")[1]


@pytest.mark.parametrize("today", ["0001-01-01", "0001-01-20", "9999-12-01", "9999-12-31"])
def test_review_at_the_ends_of_the_calendar_gives_no_traceback(tmp_path, monkeypatch, today):
    v = make_vault(tmp_path)
    monkeypatch.setenv("SB_TODAY", today)
    code, out, err = sb(v, "review")
    assert code == 0 and "## Fading" in out, err


# ---------- wiring of the sleep, lock and schema helpers ----------

def test_no_sleep_due_from_stats_recall_or_hook_while_a_sleep_runs(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    assert "sleep due" in sb(v, "stats")[1]  # never slept: due before the sleep starts
    assert sb(v, "sleep", "begin")[0] == 0
    out = sb(v, "stats")[1]
    assert "sleep due" not in out and "a sleep has been running since 2026-11-08" in out
    assert "sleep due" not in sb(v, "recall", "ftcs", "stability")[1]
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert sb(v, "hook", "session-start")[1] == ""
    monkeypatch.setenv("SB_TODAY", "2026-11-11")  # a marker left behind runs nothing
    out = sb(v, "stats")[1]
    assert "left behind" in out and "sleep due" in out and "has been running" not in out


def verify_list(v):
    return sb(v, "sleep", "plan")[1].split("## 4.")[1].split("## 5.")[0]


@pytest.mark.parametrize("why, checked_again", [("wrong", True), ("faded", False)])
def test_restore_checks_a_note_archived_as_wrong_again(tmp_path, why, checked_again):
    v = make_vault(tmp_path)
    assert sb(v, "sleep", "baseline")[0] == 0
    args = ("--reason", "the scheme was not chosen that way") if why == "wrong" else ()
    assert sb(v, "archive", "scheme-choice", "--why", why, *args)[0] == 0
    assert sb(v, "restore", "scheme-choice", *(("--force",) if why == "wrong" else ()))[0] == 0
    sb(v, "sleep", "begin")
    assert ("- scheme-choice (changed)" in verify_list(v)) is checked_again


def test_rename_logs_its_row_under_the_same_lock(tmp_path, monkeypatch):
    from scientific_brain import vault as vault_module
    v = make_vault(tmp_path)
    held, log_rename = [], usage.log_rename
    monkeypatch.setattr(usage, "log_rename", lambda vv, a, b: (held.append(vault_module._HELD.roots.get(str(vv.root), 0)),
                                                              log_rename(vv, a, b)))
    assert sb(v, "rename", "scheme-choice", "scheme-decision")[0] == 0
    assert held == [1]  # a parallel session cannot log under the old name in between


def test_init_ignores_the_lock_file(tmp_path):
    root = init_vault(tmp_path)
    assert ".sb/lock" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_check_accepts_a_project_field_in_nfd(tmp_path):
    root = init_vault(tmp_path)
    add_note(root, "heat-note", project="wärme", extra={"project": "wärme"})
    code, out, _ = sb(root, "check")
    assert code == 0 and "does not match folder" not in out


def test_the_sleeper_reads_notes_as_files():
    # show and recall during a sleep count as the main session's use, so the sleeper must not call them
    text = " ".join((Path(__file__).resolve().parents[1] / "plugin" / "agents" / "sleeper.md").read_text(encoding="utf-8").split())
    assert "do not call `sb show` or `sb recall`" in text


def test_show_and_recall_during_a_sleep_say_on_stderr_that_they_count_as_use(tmp_path):
    v = make_vault(tmp_path)
    assert "sleep is running" not in sb(v, "show", "scheme-choice")[2]
    assert sb(v, "sleep", "begin")[0] == 0
    for args in (("show", "scheme-choice"), ("recall", "ftcs", "stability")):
        code, out, err = sb(v, *args)
        assert code == 0 and "sleep is running" in err and "counts as use" in err and "sleep is running" not in out
