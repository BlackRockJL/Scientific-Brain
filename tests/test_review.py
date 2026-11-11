import sys
from datetime import date, timedelta

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain import review, sleep
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


def test_fade_day_is_one_day_past_the_horizon():
    assert review.fade_day({TODAY - timedelta(80): 1.0}, 90, TODAY) == TODAY + timedelta(11)
    assert review.fade_day({TODAY - timedelta(200): 1.0}, 90, TODAY) == TODAY


def test_prefix_of_another_type_is_flagged(tmp_path):
    notes = [(f"result-{i}", "finding", "p") for i in range(5)] + [("result-x", "concept", "p"), ("idea-y", "concept", "p")]
    v = empty(tmp_path, notes)
    assert review.prefix_mismatches(sleep.load(Vault(v))) == {("result", "concept"): 1}


def test_short_veto_rest_with_many_vetoes_warns(tmp_path):
    v = empty(tmp_path, [(f"n{i}", "finding", "p") for i in range(10)], vetoes=[(3, f"n{i}") for i in range(10)])
    warn = " ".join(review.collect(Vault(v), TODAY)["warnings"])
    assert "vetoed note returns after 22 days" in warn
    (v / "sb.toml").write_text("[activation]\nveto = 2.0\n", encoding="utf-8")
    assert "vetoed note" not in " ".join(review.collect(Vault(v), TODAY)["warnings"])


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
