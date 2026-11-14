"""Index and search: folding, white space in phrases, a damaged or locked index, ranking ties, edits found by a refresh."""
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest
from helpers import add_note, init_vault, make_vault, sb
from scientific_brain import index as ix
from scientific_brain.index import Index, fold, fts_text, quote_term
from scientific_brain.vault import Vault


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SB_NO_UPDATE", "1")
    monkeypatch.setenv("SB_TODAY", "2026-11-14")


def hits(out):
    return [ln.split()[0] for ln in out.splitlines() if ln and not ln.startswith(" ") and "[" in ln and "]" in ln]


def usage(v):
    p = Path(v) / ".sb" / "usage.tsv"
    return [r.split("\t") for r in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def edits(v):
    return [r[3] for r in usage(v) if r[1:3] == ["sync", "edit"]]


def bump(p):
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def index_file(v):
    return Path(v) / ".sb" / "index.sqlite"


# ---- I1: folding -------------------------------------------------------------------------------------

# English words whose e belongs to the stem (final ue, or ue/oe/ae before a final s or d) stay as they are
ENGLISH = ["value", "values", "valued", "issue", "issues", "issued", "queue", "queues", "queued", "continue", "continues",
           "continued", "due", "dues", "does", "shoes", "shoe", "true", "blue", "blues", "statue", "statues", "venue", "venues",
           "residue", "residues", "eigenvalue", "eigenvalues", "argue", "argues", "argued", "rescue", "rescues", "rescued",
           "tissue", "tissues", "clue", "clues", "glue", "glued", "toes", "foes", "goes", "hoes", "roes", "canoes", "vaes"]

# transliterated German words fold like their umlaut spelling
GERMAN = [("Groesse", "Größe"), ("Mueller", "Müller"), ("Fruehling", "Frühling"), ("Gruesse", "Grüße"), ("Fuesse", "Füße"),
          ("Stuecke", "Stücke"), ("Uebung", "Übung"), ("Aerger", "Ärger"), ("Haeuser", "Häuser"), ("Schoen", "Schön"),
          ("Oel", "Öl"), ("Koeln", "Köln"), ("Duesseldorf", "Düsseldorf"), ("Fuehrung", "Führung"), ("Muedigkeit", "Müdigkeit"),
          ("Maedchen", "Mädchen"), ("Staedte", "Städte"), ("Aepfel", "Äpfel"), ("Waerme", "Wärme"), ("Hoehe", "Höhe"),
          ("Gebaeude", "Gebäude"), ("Aktuelles", "Aktuelles"), ("Zuerst", "Zuerst")]


@pytest.mark.parametrize("word", ENGLISH)
def test_i01_english_words_with_e_in_the_stem_are_not_folded(word):
    assert fold(word) == word and fold(word.upper()) == word


@pytest.mark.parametrize("translit,umlaut", GERMAN)
def test_i01_transliterated_german_folds_like_the_umlaut_spelling(translit, umlaut):
    assert fold(translit) == fold(umlaut) == fold(unicodedata.normalize("NFD", umlaut))


@pytest.mark.parametrize("single,plural", [("value", "values"), ("issue", "issues"), ("continue", "continues"),
                                           ("eigenvalue", "eigenvalues"), ("residue", "residues"), ("venue", "venues"),
                                           ("queue", "queues"), ("statue", "statues"), ("shoe", "shoes")])
def test_i01_the_singular_finds_the_plural(tmp_path, single, plural):
    v = init_vault(tmp_path)
    add_note(v, "only-single", gist=f"Gist: the {single} is small.")
    add_note(v, "only-plural", gist=f"Gist: the {plural} are small.")
    assert "only-plural" in hits(sb(v, "recall", single)[1]), f"{single} must find {plural}"  # as for any word: no stemming
    assert "only-single" in hits(sb(v, "recall", single)[1])


@pytest.mark.parametrize("query,text", [("Groesse", "Größe"), ("Größe", "Groesse"), ("Mueller", "Müller"), ("Fruehling", "Frühling"),
                                        ("Frühling", "Fruehling")])
def test_i01_transliteration_and_umlaut_find_each_other(tmp_path, query, text):
    v = init_vault(tmp_path)
    add_note(v, "german", gist=f"Gist: {text} gemessen.")
    assert "german" in hits(sb(v, "recall", query)[1])


def test_i01_an_index_of_the_old_folding_is_rebuilt(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "only-plural", gist="Gist: the values are small.")
    sb(v, "recall", "values")
    db = sqlite3.connect(index_file(v))
    db.execute(f"PRAGMA user_version={ix.VERSION - 1}")  # an index built by the previous release
    db.close()
    assert "only-plural" in hits(sb(v, "recall", "value")[1])  # the old index folded `values` to `valus`


# ---- I2: white space in hyphenated phrases -------------------------------------------------------------

@pytest.mark.parametrize("sep", ["\n", "-\n", "\t", "  ", " - ", " – ", " —\n", " "])
def test_i02_hyphenated_term_meets_text_with_other_white_space(tmp_path, sep):
    v = init_vault(tmp_path)
    add_note(v, "wrapped", body=f"\nThe Crank{sep}Nicolson scheme is stable.\n")
    assert "wrapped" in hits(sb(v, "recall", "Crank-Nicolson")[1])


def test_i02_text_and_query_use_one_spelling():
    assert fts_text("Crank -\n Nicolson\t scheme") == "crank nicolson scheme"
    assert quote_term("crank--nicolson") == '"crank nicolson"'
    assert fts_text("  - lead and trail -  ") == "lead and trail"


# ---- I3: a damaged index is a cache ------------------------------------------------------------------------

def damage(v, how):
    f = index_file(v)
    db = sqlite3.connect(f)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the pages live in the main file, not only in the log
    db.close()
    data = f.read_bytes()
    for g in f.parent.glob("index.sqlite*"):
        g.unlink()
    if how == "garbage":
        data = b"not a database at all" * 100
    elif how == "header":
        data = data[:100]
    elif how == "half":
        data = data[:len(data) // 2]
    elif how == "flipped":
        data = bytearray(data)
        for i in range(4096, len(data), 997):
            data[i] ^= 0xFF
        data = bytes(data)
    f.write_bytes(data)


@pytest.mark.parametrize("how", ["garbage", "header", "half", "flipped"])
def test_i03_a_damaged_index_is_set_aside_and_rebuilt_once(tmp_path, how):
    v = make_vault(tmp_path)
    assert sb(v, "recall", "stability")[0] == 0
    damage(v, how)
    code, out, err = sb(v, "recall", "stability")
    assert code == 0 and "ftcs-stability-limit" in out and "Traceback" not in err, err
    assert "index.sqlite.broken" in err and "rebuilding" in err
    assert (index_file(v).parent / "index.sqlite.broken").exists()
    code, out, err = sb(v, "recall", "stability")  # healthy again, no second message
    assert code == 0 and err == "" and "ftcs-stability-limit" in out


def test_i03_check_rebuild_heals_too_and_a_new_damage_replaces_the_old_copy(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    damage(v, "garbage")
    assert sb(v, "check", "--rebuild")[0] == 0
    first = (index_file(v).parent / "index.sqlite.broken").read_bytes()
    damage(v, "header")
    assert sb(v, "recall", "stability")[0] == 0
    assert (index_file(v).parent / "index.sqlite.broken").read_bytes() != first


def test_i03_a_stale_wal_is_not_replayed_into_the_new_index(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    f = index_file(v)
    wal = f.with_name("index.sqlite-wal")
    wal.write_bytes(b"STALE" * 1000)  # a log left over from the damaged file
    f.write_bytes(b"garbage" * 500)
    code, out, err = sb(v, "recall", "stability")
    assert code == 0 and "ftcs-stability-limit" in out, err
    assert not wal.exists() or b"STALE" not in wal.read_bytes()


def test_i03_a_damaged_index_that_cannot_be_set_aside_is_an_error_not_a_traceback(tmp_path, monkeypatch):
    v = make_vault(tmp_path)
    sb(v, "map")
    damage(v, "garbage")

    def refuse(*a):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(os, "replace", refuse)
    code, out, err = sb(v, "recall", "stability")
    assert code == 2 and "index error" in err and "Traceback" not in err


# ---- I4: ranking ties --------------------------------------------------------------------------------------

def test_i04_equal_hits_come_in_slug_order_whatever_was_indexed_first(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "bb-two", cue="nu rest", gist="Gist: identical walrus text.")
    sb(v, "recall", "walrus")  # index the first note
    add_note(v, "aa-one", cue="nu rest", gist="Gist: identical walrus text.")
    add_note(v, "cc-three", cue="nu rest", gist="Gist: identical walrus text.")
    inc = [hits(sb(v, "recall", q, "-n", "50")[1]) for q in ("walrus", "nu")]
    for f in index_file(v).parent.glob("index.sqlite*"):
        f.unlink()
    reb = [hits(sb(v, "recall", q, "-n", "50")[1]) for q in ("walrus", "nu")]
    assert inc == reb == [["aa-one", "bb-two", "cc-three"]] * 2


def test_i04_many_short_term_hits_are_ranked_by_coverage_then_slug(tmp_path):
    v = init_vault(tmp_path)
    for i in range(700):  # more candidates than one SQL batch
        add_note(v, f"n{i:03d}", cue="nu rest" if i % 2 else "nu", gist="Gist: filler.")
    out = hits(sb(v, "recall", "nu", "rest", "-n", "4")[1])
    assert out == ["n001", "n003", "n005", "n007"]


# ---- I5: a locked index -------------------------------------------------------------------------------------

def holding_lock(v):
    lock = sqlite3.connect(index_file(v), isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    return lock


def test_i05_a_locked_index_is_read_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "LOCK_WAIT", 0.2)
    v = make_vault(tmp_path)
    assert sb(v, "recall", "stability")[0] == 0
    add_note(v, "late-note", gist="Gist: zeppelin late.")  # forces a refresh that needs the write lock
    lock = holding_lock(v)
    try:
        code, out, err = sb(v, "recall", "stability")
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    assert code == 0 and "ftcs-stability-limit" in out and "locked" in err and "Traceback" not in err
    assert "late-note" in hits(sb(v, "recall", "zeppelin")[1])  # the next call catches up


def test_i05_an_index_not_built_yet_and_locked_is_a_clean_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "LOCK_WAIT", 0.2)
    v = make_vault(tmp_path)
    assert sb(v, "recall", "stability")[0] == 0
    for f in index_file(v).parent.glob("index.sqlite*"):
        f.unlink()
    sqlite3.connect(index_file(v)).close()
    db = sqlite3.connect(index_file(v), isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE x(a)")  # a half-written first build
    try:
        code, out, err = sb(v, "recall", "stability")
    finally:
        db.execute("ROLLBACK")
        db.close()
    assert code == 2 and ("locked" in err or "busy" in err) and "Traceback" not in err


WORKER = """
import sys, time
from scientific_brain.cli import main
time.sleep(max(0.0, float(sys.argv[1]) - time.time()))  # all processes start together
sys.exit(main(sys.argv[2:]))
"""


@pytest.mark.parametrize("round", range(3))
def test_i05_parallel_first_calls_build_one_sound_index(tmp_path, round):
    """Several sessions open a vault without an index at the same instant: every call succeeds, the index is whole."""
    v = make_vault(tmp_path)
    for f in index_file(v).parent.glob("index.sqlite*"):
        f.unlink()
    env = {**os.environ, "SB_VAULT": str(v), "SB_NO_UPDATE": "1", "SB_TODAY": "2026-11-14",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    start = str(time.time() + 2.0)
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, start, "recall", "ftcs", "scheme"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
    res = [(p.wait(timeout=120), p.stderr.read()) for p in procs]
    assert all(code == 0 for code, _ in res), res
    db = sqlite3.connect(index_file(v))
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT count(*) FROM files").fetchone()[0] == db.execute("SELECT count(*) FROM fts WHERE rowid>0").fetchone()[0]


def test_i05_update_and_mark_checked_skip_a_locked_index(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "LOCK_WAIT", 0.2)
    v = make_vault(tmp_path)
    idx = Index(Vault(v))
    idx.refresh()
    lock = holding_lock(v)
    try:
        idx.update(["notes/heat/scheme-choice.md"])
        idx.mark_checked(["notes/heat/scheme-choice.md"])
    finally:
        lock.execute("ROLLBACK")
        lock.close()


# ---- I6, I7: which edits a refresh reports ----------------------------------------------------------------------

def test_i06_files_new_to_an_indexed_vault_are_imports_not_edits(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    add_note(v, "imported-one")
    add_note(v, "imported-two")
    sb(v, "map")
    assert edits(v) == []
    p = v / "notes" / "heat" / "imported-one.md"
    p.write_text(p.read_text().replace("a gist", "another gist"), encoding="utf-8")
    bump(p)
    sb(v, "map")
    assert edits(v) == ["imported-one"]  # once they are known, a change is an edit


def test_i07_a_rebuild_reports_the_edits_it_finds(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("second order", "2nd order"), encoding="utf-8")
    bump(p)
    assert sb(v, "check", "--rebuild")[0] == 0
    assert edits(v) == ["scheme-choice"]
    sb(v, "check", "--rebuild")
    assert edits(v) == ["scheme-choice"]  # reported once


def test_i07_a_version_change_does_not_swallow_edits_and_imports_are_still_no_edits(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("second order", "2nd order"), encoding="utf-8")
    bump(p)
    add_note(v, "imported-one")
    db = sqlite3.connect(index_file(v))
    db.execute(f"PRAGMA user_version={ix.VERSION - 1}")
    db.close()
    sb(v, "map")
    assert edits(v) == ["scheme-choice"]


def test_i07_a_process_ended_between_the_schema_change_and_the_rebuild_loses_no_edits(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("second order", "2nd order"), encoding="utf-8")
    bump(p)
    db = sqlite3.connect(index_file(v))
    db.execute(f"PRAGMA user_version={ix.VERSION - 1}")
    db.close()
    Index(Vault(v)).db.close()  # the new schema is committed, then the process ends before its refresh
    sb(v, "map")
    assert edits(v) == ["scheme-choice"]
    sb(v, "map")
    assert edits(v) == ["scheme-choice"]  # the old state is used once
    assert not sqlite3.connect(index_file(v)).execute("SELECT name FROM sqlite_master WHERE name='prev'").fetchall()


def test_i07_a_new_skip_sections_setting_does_not_swallow_edits(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("second order", "2nd order"), encoding="utf-8")
    bump(p)
    with open(v / "sb.toml", "a", encoding="utf-8") as f:
        f.write('\n[index]\nskip_sections = ["Evidence"]\n')
    sb(v, "map")
    assert edits(v) == ["scheme-choice"]


def test_i07_a_damaged_old_index_reports_nothing(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "map")
    p = v / "notes" / "heat" / "scheme-choice.md"
    p.write_text(p.read_text().replace("second order", "2nd order"), encoding="utf-8")
    bump(p)
    damage(v, "garbage")
    assert sb(v, "map")[0] == 0
    assert edits(v) == []
