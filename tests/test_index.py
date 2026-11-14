import os

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain.index import fold, match_query


@pytest.mark.parametrize("a,b", [
    ("Größe", "grosse"), ("Fläche", "Flaeche"), ("Übergang", "Uebergang"), ("Öl", "oel"), ("STRASSE", "straße"),
    ("Zeitschrittweite", "zeitschrittweite"), ("Café", "cafe"),
])
def test_fold_equates(a, b):
    assert fold(a) == fold(b)


def test_fold_keeps_ue_after_q_and_vowels():
    assert fold("queue") == "queue" and fold("reuse") == "reuse"  # no ae/oe/ue fold after a vowel or q


def test_query_quotes_every_term():
    q, ignored = match_query(["FFT-Solver", "C++", "1e-8", "AND", "a", "ab", "+-", "x.y"])
    assert q == '"fft solver" OR "c++" OR "1e 8" OR "ab" OR "x.y"'  # stop words drop out, short words stay; hyphens match spaces
    assert ignored == ["+-"]
    assert match_query(["the", "und"]) == (None, [])


def test_awkward_terms_are_searchable(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "fft-note", title="FFT-Solver on a C++ grid", gist="Gist: the 1e-8 tolerance AND the FFT-Solver agree.")
    for term in ("FFT-Solver", "C++", "1e-8", "AND", "NOT", "(", "col:x", "*"):
        code, out, err = sb(v, "recall", term)
        assert code in (0, 2), (term, err)  # never a SQL or FTS syntax error
        if term in ("FFT-Solver", "C++", "1e-8"):
            assert "fft-note" in out, term


def test_recall_german_umlauts_and_compounds(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "de-note", title="Crank-Nicolson schwingt bei großer Zeitschrittweite",
             cue="wichtig, wenn die Zeitschrittweite groß gewählt wird",
             gist="Gist: Über der Fläche des Fehlers zeigen sich Schwingungen; die Konvergenzordnung sinkt.")
    for term in ("grosse", "große", "Größe", "Zeitschrittweite", "schritt", "ZEITSCHRITT"):
        assert "de-note" in sb(v, "recall", term)[1], term
    for term in ("Konvergenzordnung", "Fläche", "Flaeche", "Schwingungen"):
        assert "de-note" in sb(v, "recall", term)[1], term


def test_recall_english_and_scope(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "recall", "Crank-Nicolson", "start-up")
    assert code == 0 and "scheme-choice" in out
    assert "observed-convergence-order" in sb(v, "recall", "convergence", "order")[1]
    assert "observed-convergence-order" not in sb(v, "recall", "convergence", "order", "-p", "heat")[1]
    assert out.count("cue:") <= 5
    assert len(sb(v, "recall", "stability", "-n", "1")[1].split("cue:")) == 2  # one result


def test_recall_no_match(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "recall", "tokamak")[1].startswith("no match")
    assert sb(v, "recall", "++", "tokamak")[1].startswith("ignored (not searchable): ++")
    code, _, err = sb(v, "recall", "the", "und", "++")
    assert code == 2 and "no usable search terms" in err


def test_recall_requires_min_coverage_of_terms(tmp_path):
    v = make_vault(tmp_path)
    # one of four terms is not enough (min_coverage 0.5), two of four is
    assert sb(v, "recall", "scheme", "tokamak", "plasma", "divertor")[1].startswith("no match")
    assert "scheme-choice" in sb(v, "recall", "rannacher", "implicit", "plasma", "divertor")[1]


def test_recall_counts_archived_matches(tmp_path):
    v = make_vault(tmp_path)
    out = sb(v, "recall", "ftcs")[1]
    assert "ftcs-cfl-guess" not in out and "+1 archived matches" in out
    out = sb(v, "recall", "ftcs", "--archive")[1]
    assert "ftcs-cfl-guess  [finding, heat] (archived 2026-11-02: superseded; superseded by: ftcs-stability-limit)" in out and "archived matches" not in out


def test_archived_only_match_still_reports_count(tmp_path):
    v = make_vault(tmp_path)
    out = sb(v, "recall", "assumed")[1]
    assert out.startswith("no match") and "+1 archived matches" in out


def test_incremental_index_follows_edits_deletes_and_new_files(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "recall", "xylophone")[1].startswith("no match")
    p = add_note(v, "music-note", gist="Gist: a xylophone appears.")
    assert "music-note" in sb(v, "recall", "xylophone")[1]  # new file
    p.write_text(p.read_text().replace("xylophone", "marimba"), encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert sb(v, "recall", "xylophone")[1].startswith("no match")  # changed file
    assert "music-note" in sb(v, "recall", "marimba")[1]
    p.unlink()
    assert sb(v, "recall", "marimba")[1].startswith("no match")  # deleted file


def test_archive_files_added_by_hand_are_picked_up(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "recall", "ftcs")  # builds the index
    p = add_note(v, "late-archive", loc="archive", gist="Gist: a zeppelin was archived.")
    assert "+1 archived matches" in sb(v, "recall", "zeppelin")[1]
    assert "archive: 2" in sb(v, "stats")[1]
    p.unlink()
    assert "archived matches" not in sb(v, "recall", "zeppelin")[1]


def test_unparsable_note_is_skipped_with_a_message(tmp_path):
    v = make_vault(tmp_path)
    (v / "notes" / "heat" / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    code, out, err = sb(v, "recall", "stability")
    assert code == 0 and "broken.md" in err and "ftcs-stability-limit" in out
