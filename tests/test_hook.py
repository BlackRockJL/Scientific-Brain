import io
import json

import pytest
from helpers import add_note, make_vault, sb


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", "2026-11-08")


def edit_event(monkeypatch, path):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})))


def test_after_edit_valid_note_is_silent(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    edit_event(monkeypatch, add_note(v, "fine-note"))
    assert sb(v, "hook", "after-edit") == (0, "", "")


def test_after_edit_broken_note_reports_with_exit_2(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    edit_event(monkeypatch, add_note(v, "broken-note", drop=("cue",)))
    code, _, err = sb(v, "hook", "after-edit")
    assert code == 2 and "broken-note" in err


@pytest.mark.parametrize("rel", ["archive/heat/x.md", "sources/src/ftcs.py", "sb.toml"])
def test_after_edit_ignores_files_outside_notes(tmp_path, monkeypatch, rel):
    v = make_vault(tmp_path)
    edit_event(monkeypatch, v / rel)
    assert sb(v, "hook", "after-edit")[0] == 0


def test_after_edit_ignores_files_outside_the_vault_and_bad_input(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    edit_event(monkeypatch, tmp_path / "elsewhere.md")
    assert sb(v, "hook", "after-edit") == (0, "", "")
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert sb(v, "hook", "after-edit") == (0, "", "")


def test_session_start_reports_missing_vault(tmp_path, monkeypatch, capsys):
    from scientific_brain.cli import main
    monkeypatch.delenv("SB_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["hook", "session-start"]) == 0 and "no vault found" in capsys.readouterr().out
    monkeypatch.setenv("SB_VAULT", str(tmp_path))
    assert main(["hook", "session-start"]) == 0 and "has no sb.toml" in capsys.readouterr().out


def test_session_start_reports_due_sleep_then_silent(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "hook", "session-start")
    assert code == 0 and "sleep due" in out
    sb(v, "sleep", "begin")
    sb(v, "sleep", "done", "test")
    assert sb(v, "hook", "session-start") == (0, "", "")


def test_after_edit_ignores_errors_of_other_notes(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    add_note(v, "other-broken", drop=("cue",))
    edit_event(monkeypatch, add_note(v, "fine-note"))
    assert sb(v, "hook", "after-edit") == (0, "", "")


@pytest.mark.parametrize("fp", ["missing", "null", "no-input", "gone"])
def test_after_edit_missing_or_null_file_path_is_silent(tmp_path, monkeypatch, fp):
    v = make_vault(tmp_path)
    ti = {"missing": {}, "null": {"file_path": None}, "no-input": None, "gone": {"file_path": str(tmp_path / "gone.md")}}[fp]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": ti})))
    assert sb(v, "hook", "after-edit") == (0, "", "")
