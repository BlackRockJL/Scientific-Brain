import sys
from collections import Counter
from datetime import date, timedelta
from statistics import median

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain import review, sleep, usage
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def empty(tmp_path, notes=(), usage=(), vetoes=()):
    """Empty vault with (slug, type, project) notes, usage rows (days_ago, caller, kind, slug) and veto rows (days_ago, slug)."""
    root = tmp_path / "v"
    assert sb(root, "init", str(root))[0] == 0
    for slug, typ, proj in notes:
        add_note(root, slug, project=proj, type=typ)
    (root / ".sb" / "usage.tsv").write_text("".join(f"{ago(d)}\t{c}\t{k}\t{s}\n" for d, c, k, s in usage), encoding="utf-8")
    if vetoes:
        (root / ".sb" / "vetoes.tsv").write_text("".join(f"{ago(d)}\t{s}\tkept\n" for d, s in vetoes), encoding="utf-8")
    return root


def test_review_prints_every_section_and_logs_nothing(tmp_path):
    v = make_vault(tmp_path)
    log = v / ".sb" / "usage.tsv"
    before = log.read_text(encoding="utf-8") if log.exists() else None
    code, out, err = sb(v, "review")
    assert code == 0, err
    for head in ("# Review of the brain", "## Inventory", "## Use", "## Fading", "## Structure", "## Upkeep", "```mermaid"):
        assert head in out
    assert (log.read_text(encoding="utf-8") if log.exists() else None) == before


def test_since_counts_only_later_use(tmp_path):
    v = empty(tmp_path, [("a", "finding", "p")], [(20, "work", "show", "a"), (2, "work", "show", "a"), (1, "sleep", "show", "a")])
    assert review.collect(Vault(v), TODAY)["total"]["a"] == 2
    assert review.collect(Vault(v), TODAY, since=TODAY - timedelta(5))["total"]["a"] == 1


def test_unknown_project_is_an_error(tmp_path):
    code, _, err = sb(make_vault(tmp_path), "review", "nope")
    assert code == 2 and "no notes in project 'nope'" in err


def test_fade_day_is_one_day_past_the_stability():
    assert usage.fade_day(90, TODAY - timedelta(80), TODAY) == TODAY + timedelta(11)
    assert usage.fade_day(90.0, TODAY - timedelta(200), TODAY) == TODAY
    assert usage.fade_day(94.74, TODAY, TODAY) == TODAY + timedelta(95)  # t = 94 is still below S, t = 95 is past it


def test_prefix_of_another_type_is_flagged(tmp_path):
    notes = [(f"result-{i}", "finding", "p") for i in range(5)] + [("result-x", "concept", "p"), ("idea-y", "concept", "p")]
    v = empty(tmp_path, notes)
    assert review.prefix_mismatches(sleep.load(Vault(v))) == {("result", "concept"): 1}


def test_many_vetoes_raise_no_warning_about_a_setting(tmp_path):
    v = empty(tmp_path, [(f"n{i}", "finding", "p") for i in range(10)], vetoes=[(3, f"n{i}") for i in range(10)])
    inv = review.collect(Vault(v), TODAY)
    assert inv["vetoes"] == 10 and not any("veto" in w for w in inv["warnings"])  # the veto weight is no knob to raise any more


def test_many_forget_candidates_warn_when_sleeps_decide_few(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "B0", 3)
    v = empty(tmp_path, [(f"n{i}", "finding", "p") for i in range(5)])
    for i in range(5):  # created long ago, never used: all five are candidates, more than a sleep takes
        add_note(v, f"n{i}", project="p", extra={"created": "2025-01-01", "updated": "2025-01-01"})
    inv = review.collect(Vault(v), TODAY)
    assert inv["candidates"] == 5 and inv["budget"] == 3
    assert any("5 forget candidates wait" in w for w in inv["warnings"])


def test_fading_section_names_the_learned_values_and_the_budget(tmp_path):
    v = make_vault(tmp_path)
    vv = Vault(v)
    lr, b = usage.learned(vv, TODAY), usage.budget(vv)
    text = sb(v, "review")[1].split("## Fading")[1].split("## Structure")[0]
    assert f"a new note rests {lr['']:.0f} days (" in text and f"a sleep takes {b} items" in text
    assert all(f"{t} {x:.0f}" in text for t, x in lr.items() if t)
    st = sorted(x[4] for x in usage.scores(vv, TODAY))
    assert f"median {median(st):.0f} days, at most {max(st):.0f}." in text
    assert "horizon" not in text.lower() and "veto (weight" not in text


def test_the_outlook_follows_fade_day_and_agrees_with_the_forget_list(tmp_path):
    v = empty(tmp_path, [("a", "finding", "p"), ("b", "finding", "p"), ("c", "concept", "p"), ("d", "decision", "p")],
              [(40, "work", "show", "a"), (20, "work", "show", "a"), (100, "work", "show", "b"), (5, "work", "recall", "c")])
    vv = Vault(v)
    sc = {x[0]: x for x in usage.scores(vv, TODAY)}
    cands = {s for s, *_ in usage.forget_candidates(vv, TODAY)}
    want = Counter(usage.fade_day(x[4], x[5], TODAY).strftime("%Y-%m") for s, x in sc.items() if s not in cands)
    inv = review.collect(vv, TODAY)
    assert want and inv["outlook"] == dict(sorted(want.items()))
    for s, x in sc.items():  # the day it names is the first day the note is a forget candidate
        f = usage.fade_day(x[4], x[5], TODAY)
        assert (s in {c for c, *_ in usage.forget_candidates(vv, f)}) and (
            f == TODAY or s not in {c for c, *_ in usage.forget_candidates(vv, f - timedelta(1))}), s


@pytest.mark.parametrize("name", ["map.png", "map.svg"])
def test_map_is_drawn_and_linked(tmp_path, name):
    pytest.importorskip("numpy")
    pytest.importorskip("matplotlib")
    v = make_vault(tmp_path)
    code, out, err = sb(v, "review", "-o", str(tmp_path / "r.md"), "--map", str(tmp_path / name))
    assert code == 0, err
    assert (tmp_path / name).stat().st_size > 10_000
    assert f"![mind map]({name})" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_map_needs_a_picture_suffix(tmp_path):
    pytest.importorskip("matplotlib")
    code, _, err = sb(make_vault(tmp_path), "review", "--map", str(tmp_path / "map.pdf"))
    assert code == 2 and ".png or .svg" in err


def test_map_without_matplotlib_names_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    code, _, err = sb(make_vault(tmp_path), "review", "--map", str(tmp_path / "map.png"))
    assert code == 2 and "scientific-brain[review]" in err
