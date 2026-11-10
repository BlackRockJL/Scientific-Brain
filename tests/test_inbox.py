import hashlib
from datetime import date, timedelta

import pytest
from helpers import make_vault, sb

from scientific_brain import inbox
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def lines(v):
    return [ln.split("\t") for ln in (v / ".sb" / "inbox.tsv").read_text(encoding="utf-8").splitlines()]


def put(v, id, days_ago, project, text, source=""):
    d = (TODAY - timedelta(days=days_ago)).isoformat()
    with open(v / ".sb" / "inbox.tsv", "a", encoding="utf-8") as f:
        f.write(f"{id}\t{d}\t{project}\t{source}\t{text}\n")


def empty_inbox(v):
    (v / ".sb" / "inbox.tsv").write_text("", encoding="utf-8")


def test_capture_default_project_id_and_cleaning(tmp_path):
    v = make_vault(tmp_path)
    empty_inbox(v)
    code, out, _ = sb(v, "capture", "order drops\tto 1.7\nat dt = 0.1", "-s", "runs/x")
    (i, d, proj, src, text), = lines(v)
    assert code == 0 and out.strip() == i == hashlib.sha1((d + text).encode()).hexdigest()[:6]
    assert (d, proj, src, text) == ("2026-11-08", "_unsorted", "runs/x", "order drops to 1.7 at dt = 0.1")
    assert sb(v, "capture", "x", "-p", "nowhere")[0] == 2 and sb(v, "capture", " \t")[0] == 2
    assert sb(v, "capture", "a thought", "-p", "heat")[0] == 0 and lines(v)[-1][2] == "heat"
    assert sb(v, "capture", "unsorted on purpose", "-p", "_unsorted")[0] == 0


def test_capture_same_text_twice_gets_distinct_ids(tmp_path):
    v = make_vault(tmp_path)
    ids = {sb(v, "capture", "the same line")[1].strip() for _ in range(3)}
    assert len(ids) == 3 and all(len(i) >= 6 for i in ids)


def test_capture_id_is_lengthened_on_prefix_collision(tmp_path):
    v = make_vault(tmp_path)
    full = hashlib.sha1((TODAY.isoformat() + "collide").encode()).hexdigest()
    put(v, full[:6], 1, "heat", "another line")
    assert inbox.capture(Vault(v), "collide", "heat", "", TODAY) == full[:7]


def test_capture_uses_folder_map_relative_and_absolute(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    solver, other = tmp_path / "work" / "solver", tmp_path / "elsewhere"
    (solver / "deep").mkdir(parents=True)
    other.mkdir()
    fm = f'folder_map = {{ "../work" = "_general", "{solver.as_posix()}" = "heat" }}\n'
    (v / "sb.toml").write_text(fm + (v / "sb.toml").read_text(), encoding="utf-8")
    for cwd, proj in ((solver / "deep", "heat"), (tmp_path / "work", "_general"), (other, "_unsorted")):
        monkeypatch.chdir(cwd)
        i = sb(v, "capture", f"from {cwd.name}")[1].strip()
        assert [r for r in lines(v) if r[0] == i][0][2] == proj
    monkeypatch.chdir(solver)
    sb(v, "capture", "explicit wins", "-p", "_general")
    assert lines(v)[-1][2] == "_general"


def test_inbox_lists_age_project_text_and_filters(tmp_path):
    v = make_vault(tmp_path)
    empty_inbox(v)
    put(v, "aaaaaa", 5, "heat", "young line", "src/x.py")
    put(v, "bbbbbb", 31, "_general", "old line")
    out = sb(v, "inbox")[1]
    assert "aaaaaa  5d  heat  young line  (src/x.py)" in out and "bbbbbb  31d  _general  old line" in out
    assert "young" not in sb(v, "inbox", "--due")[1] and "old line" in sb(v, "inbox", "--due")[1]
    assert "old line" not in sb(v, "inbox", "-p", "heat")[1]
    put(v, "cccccc", 30, "heat", "edge line")
    assert "edge line" not in sb(v, "inbox", "--due")[1]  # older than 30 days means more than 30
    assert sb(v, "inbox", "-p", "nowhere")[1].strip() == "(no open inbox lines)"


def test_resolve_note_drop_wait(tmp_path):
    v = make_vault(tmp_path)
    empty_inbox(v)
    for i in ("aaaaaa", "bbbbbb", "cccccc"):
        put(v, i, 3, "heat", f"line {i}")
    assert sb(v, "inbox", "resolve", "aaaaaa", "--note", "scheme-choice")[0] == 0
    assert sb(v, "inbox", "resolve", "bbbbbb", "--drop")[0] == 0
    assert sb(v, "inbox", "resolve", "cccccc", "--wait")[0] == 0
    done = [ln.split("\t") for ln in (v / ".sb" / "inbox-done.tsv").read_text().splitlines()]
    assert done == [["aaaaaa", "2026-11-08", "note:scheme-choice"], ["bbbbbb", "2026-11-08", "dropped"],
                    ["cccccc", "2026-11-08", "wait"]]
    out = sb(v, "inbox")[1]
    assert "cccccc" in out and "aaaaaa" not in out and "bbbbbb" not in out  # wait stays open
    assert sb(v, "inbox", "resolve", "cccccc", "--drop")[0] == 0  # a waiting line can still be settled
    code, _, err = sb(v, "inbox", "resolve", "aaaaaa", "--drop")
    assert code == 2 and "already settled (note:scheme-choice)" in err


def test_resolve_failures_write_nothing(tmp_path):
    v = make_vault(tmp_path)
    empty_inbox(v)
    put(v, "aaaaaa", 3, "heat", "a line")
    code, _, err = sb(v, "inbox", "resolve", "zzzzzz", "--drop")
    assert code == 2 and "no inbox line 'zzzzzz'" in err
    code, _, err = sb(v, "inbox", "resolve", "aaaaaa", "--note", "no-such-note")
    assert code == 2 and "no note 'no-such-note' in notes/" in err
    assert sb(v, "inbox", "resolve", "aaaaaa", "--note", "ftcs-cfl-guess")[0] == 2  # archived is not notes/
    for args in (("--drop", "--wait"), ()):  # argparse rejects these before the command runs
        with pytest.raises(SystemExit):
            sb(v, "inbox", "resolve", "aaaaaa", *args)
    assert not (v / ".sb" / "inbox-done.tsv").exists()


def test_expire_inbox_books_only_due_open_lines(tmp_path):
    v = make_vault(tmp_path)
    empty_inbox(v)
    put(v, "aaaaaa", 31, "heat", "due and open")
    put(v, "bbbbbb", 30, "heat", "not yet due")
    put(v, "cccccc", 45, "heat", "waited too long")
    put(v, "dddddd", 50, "heat", "already dropped")
    vault = Vault(v)
    inbox.resolve(vault, "cccccc", "wait", TODAY)
    inbox.resolve(vault, "dddddd", "dropped", TODAY)
    assert sorted(inbox.expire_inbox(vault, TODAY)) == ["aaaaaa", "cccccc"]
    assert inbox.done(vault)["aaaaaa"] == ("2026-11-08", "expired")
    assert inbox.done(vault)["dddddd"][1] == "dropped"
    assert [r[0] for r in inbox.open_lines(vault)] == ["bbbbbb"]
    assert inbox.expire_inbox(vault, TODAY) == []
    assert inbox.expire_inbox(vault, TODAY + timedelta(days=1)) == ["bbbbbb"]  # injected date
    assert inbox.promotion(vault) == (0, 1)  # expired lines are not settled


def test_recall_inbox_and_hint(tmp_path):
    v = make_vault(tmp_path)
    empty_inbox(v)
    put(v, "aaaaaa", 3, "heat", "Die Schrittweite wird bei Oszillationen zu groß", "runs/a")
    put(v, "bbbbbb", 2, "heat", "the Schrittweite matters for the stability limit")
    out = sb(v, "recall", "stability")[1]
    assert "+1 inbox matches (use --inbox)" in out and "inbox:bbbbbb" not in out
    out = sb(v, "recall", "--inbox", "stability")[1]
    assert "inbox:bbbbbb  [inbox, heat, 2026-11-06]" in out and "ftcs-stability-limit" not in out
    assert "inbox matches" not in out and "archived matches" not in out
    out = sb(v, "recall", "--inbox", "Groesse", "Oszillationen")[1]  # folded like notes
    assert "inbox:aaaaaa" in out and "source: runs/a" in out
    assert "no match" in sb(v, "recall", "--inbox", "xylophone")[1]
    assert "inbox:bbbbbb" not in sb(v, "recall", "--inbox", "-p", "_general", "stability")[1]
    sb(v, "inbox", "resolve", "bbbbbb", "--drop")  # settled lines leave the index
    assert "inbox:bbbbbb" not in sb(v, "recall", "--inbox", "stability")[1]
    put(v, "cccccc", 1, "heat", "a brand new stability remark")  # new lines are picked up
    assert "inbox:cccccc" in sb(v, "recall", "--inbox", "stability")[1]


def test_inbox_hits_are_not_logged_as_usage(tmp_path):
    v = make_vault(tmp_path)
    put(v, "aaaaaa", 3, "heat", "a remark about stability")
    before = (v / ".sb" / "usage.tsv").read_text()
    sb(v, "recall", "--inbox", "remark")
    assert (v / ".sb" / "usage.tsv").read_text() == before
