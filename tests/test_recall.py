"""recall: punctuation, stop words, short terms, term limits, skipped sections, usage logging."""
import pytest
from helpers import add_note, make_vault, sb

from scientific_brain.index import MAX_LEN, MAX_TERMS, Index, fold, prepare
from scientific_brain.vault import Vault


@pytest.mark.parametrize("raw", ["Fatigue,", "(Fatigue", "Fatigue;", "Fatigue.)", '"Fatigue"', "title:Fatigue", "Fatigue's", "–Fatigue—"])
def test_punctuation_is_stripped(raw):
    assert prepare([raw]) == (["fatigue"], [])


def test_dashes_and_possessive():
    assert prepare(["Navier–Stokes's"]) == (["navier-stokes"], [])
    assert prepare(["Navier—Stokes,"]) == (["navier-stokes"], [])
    assert prepare(["Navier-Stokes"]) == (["navier-stokes"], [])
    assert prepare(["Crack,Fatigue"]) == (["crack", "fatigue"], [])


def test_recall_finds_through_punctuation(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "ns-note", title="Navier-Stokes channel", gist="Gist: the fatigue grows along the channel wall.")
    for term in ("Fatigue,", "(Fatigue", "Fatigue;", '"Navier-Stokes"', "title:Fatigue", "Navier-Stokes's", "Navier–Stokes"):
        assert "ns-note" in sb(v, "recall", term)[1], term


def test_stop_words_are_dropped(tmp_path):
    v = make_vault(tmp_path)
    assert prepare(["the", "and", "of", "der", "und", "mit"]) == ([], [])
    assert prepare(["the", "avalanche", "and", "the", "anchor"])[0] == ["avalanche", "anchor"]
    add_note(v, "stop-note", gist="Gist: the avalanche and the anchor.")
    add_note(v, "noise-note", gist="Gist: the plain and the simple.")
    out = sb(v, "recall", "the", "avalanche", "and", "the", "anchor")[1]
    assert "stop-note" in out and "noise-note" not in out
    code, _, err = sb(v, "recall", "the", "and", "der")
    assert code == 2 and "no usable search terms" in err


def test_fold_keeps_words_ending_in_ue():
    assert fold("due") == "due" and fold("Value") == "value" and fold("blue") == "blue"
    assert fold("Zeitschrittweite") == "zeitschrittweite" and fold("fuer") == "fur"
    assert prepare(["due"]) == (["due"], [])


def test_short_terms_match_slug_and_cue_words(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "ns-model", title="Channel flow", cue="check the CG iteration count")
    add_note(v, "other-note", title="Unrelated", cue="nothing about it", gist="Gist: a sense of cgs units.")
    out = sb(v, "recall", "NS")[1]
    assert "ns-model" in out and "other-note" not in out  # exact word, not a substring ("sense")
    assert "ns-model" in sb(v, "recall", "cg")[1]
    assert "ns-model" in sb(v, "recall", "ns", "CG")[1]
    assert "ns-model" in sb(v, "recall", "ns", "channel")[1]  # mixed with a long term, min_coverage 0.5
    assert "ns-model" in sb(v, "recall", "ns", "cg", "tokamak", "plasma")[1]  # 2 of 4 terms
    assert sb(v, "recall", "ns", "tokamak", "plasma")[1].startswith("no match")  # 1 of 3
    assert sb(v, "recall", "ns", "-p", "heat")[1].count("ns-model") == 1
    assert sb(v, "recall", "ns", "-p", "other")[1].startswith("no match")


def test_short_terms_follow_edits(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "v2-note", cue="the v2 variant")
    assert "v2-note" in sb(v, "recall", "v2")[1]
    p.unlink()
    assert sb(v, "recall", "v2")[1].startswith("no match")


def test_terms_are_limited():
    long = "x" * 500
    assert prepare([long])[0] == ["x" * MAX_LEN]
    assert len(prepare([f"word{i}" for i in range(100)])[0]) == MAX_TERMS


def test_skip_sections_stay_out_of_the_index(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "old-note", body="\n## Altbestand\nzeppelin legacy text\n\n## Rest\nkept words here\n")
    assert "old-note" in sb(v, "recall", "zeppelin")[1]
    toml = v / "sb.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + '\n[index]\nskip_sections = ["Altbestand"]\n', encoding="utf-8")
    assert sb(v, "recall", "zeppelin")[1].startswith("no match")  # config change rebuilds the index
    assert "old-note" in sb(v, "recall", "kept", "words")[1]
    assert Index(Vault(v)).skip == ("altbestand",)


def test_only_the_first_hit_is_logged_as_used(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "first-hit", cue="zeppelin zeppelin zeppelin")
    add_note(v, "second-hit", cue="zeppelin")
    out = sb(v, "recall", "zeppelin")[1]
    assert "first-hit" in out and "second-hit" in out
    log = (v / ".sb" / "usage.tsv").read_text(encoding="utf-8")
    assert [x.split("\t")[2:] for x in log.splitlines() if x.endswith(("first-hit", "second-hit"))] == [["recall", "first-hit"]]


def test_rebuild_vacuums_and_keeps_results(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "recall", "stability")
    assert sb(v, "check", "--rebuild")[0] in (0, 1)
    assert "ftcs-stability-limit" in sb(v, "recall", "stability")[1]
