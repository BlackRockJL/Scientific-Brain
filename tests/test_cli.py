import os

import pytest
from helpers import add_note, make_vault, sb

from scientific_brain.schema import TYPES


def test_init_creates_vault_and_refuses_second_run(tmp_path):
    root = tmp_path / "new-vault"
    code, out, _ = sb(root, "init", str(root))
    assert code == 0
    assert (root / "sb.toml").is_file() and (root / "notes").is_dir() and (root / "archive").is_dir()
    assert (root / ".sb").is_dir()
    assert ".sb/index.sqlite*" in (root / ".gitignore").read_text() and ".sb/sleeping" in (root / ".gitignore").read_text()
    assert sb(root, "init", str(root))[0] == 2
    assert sb(root, "check")[0] == 0 and sb(root, "map")[1].strip() == "(no notes)"
    assert sb(root, "recall", "anything")[1].strip() == "no match"


def test_new_creates_skeleton_per_type_and_check_demands_filling(tmp_path):
    v = make_vault(tmp_path)
    for t in TYPES:
        code, out, _ = sb(v, "new", t, f"fresh-{t}", "-p", "heat", "--why", "own measurement")
        assert code == 0 and f"created notes/heat/fresh-{t}.md" in out
    text = (v / "notes" / "heat" / "fresh-decision.md").read_text()
    assert "## Rejected" in text and "why: own measurement" in text and "type: decision" in text
    assert "ref: TODO" in (v / "notes" / "heat" / "fresh-source.md").read_text()
    code, out, _ = sb(v, "check")
    assert code == 1 and all(f"fresh-{t}.md" in out for t in TYPES) and out.count("unfilled TODO placeholder") > len(TYPES)  # every placeholder


def test_new_shows_similar_notes_and_prevents_collisions(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "new", "finding", "ftcs-step-size", "-p", "heat", "--why", "stability of the explicit scheme")
    assert code == 0 and "similar notes" in out and "ftcs-stability-limit" in out
    before = (v / "notes" / "heat" / "ftcs-step-size.md").read_text()
    code, _, err = sb(v, "new", "finding", "ftcs-step-size", "-p", "heat", "--why", "again")
    assert code == 2 and "already exists" in err
    code, _, err = sb(v, "new", "finding", "ftcs-cfl-guess", "-p", "heat", "--why", "archived slug")
    assert code == 2 and "archive/heat/ftcs-cfl-guess.md" in err  # unique over notes and archive
    assert (v / "notes" / "heat" / "ftcs-step-size.md").read_text() == before


def test_new_rejects_bad_input(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "new", "essay", "x-note", "-p", "heat", "--why", "w")[0] == 2
    assert sb(v, "new", "finding", "Bad Slug", "-p", "heat", "--why", "w")[0] == 2
    assert sb(v, "new", "finding", "x-note", "-p", "nowhere", "--why", "w")[0] == 2
    assert sb(v, "new", "finding", "x-note", "-p", "heat", "--why", " ")[0] == 2
    assert not (v / "notes" / "nowhere").exists()


def test_rename_rewrites_links_and_superseded_by(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "rename", "ftcs-stability-limit", "ftcs-limit")
    assert code == 0 and "links rewritten in 4 other notes" in out
    assert not (v / "notes" / "heat" / "ftcs-stability-limit.md").exists()
    assert (v / "notes" / "heat" / "ftcs-limit.md").is_file()
    heat = v / "notes" / "heat"
    for name in ("scheme-choice", "cn-oscillations-large-time-step", "morton-mayers-assessment"):
        t = (heat / f"{name}.md").read_text()
        assert "[[ftcs-limit]]" in t and "ftcs-stability-limit" not in t
    assert "superseded_by: [ftcs-limit]" in (v / "archive" / "heat" / "ftcs-cfl-guess.md").read_text()
    assert sb(v, "check")[0] == 0
    assert "ftcs-limit" in sb(v, "recall", "ftcs")[1]  # index follows the rename
    assert "ftcs-stability-limit" not in sb(v, "recall", "ftcs")[1]


def test_rename_of_archived_note_and_failures(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "rename", "ftcs-cfl-guess", "ftcs-old-guess")[0] == 0
    assert (v / "archive" / "heat" / "ftcs-old-guess.md").is_file()
    assert sb(v, "rename", "nope", "other")[0] == 2
    assert sb(v, "rename", "scheme-choice", "ftcs-limit-x")[0] == 0
    assert sb(v, "rename", "ftcs-limit-x", "ftcs-stability-limit")[0] == 2  # collision
    assert sb(v, "rename", "ftcs-limit-x", "Not A Slug")[0] == 2
    assert sb(v, "check")[0] == 0


def test_show_note_backlinks_and_reference(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "show", "scheme-choice")
    assert code == 0 and "## Rejected" in out
    assert "backlinks: " in out and "ftcs-stability-limit (FTCS blows up" in out
    out = sb(v, "show", "morton-mayers-assessment")[1]
    assert "reference: Numerical Solution of Partial Differential Equations | Morton, K. W. and Mayers, D. F. | 2005" in out
    out = sb(v, "show", "ftcs-cfl-guess")[1]
    assert "(archived 2026-11-02: superseded; superseded by: ftcs-stability-limit)" in out
    assert sb(v, "show", "missing")[0] == 2


def test_map_groups_by_project_and_type(tmp_path):
    v = make_vault(tmp_path)
    out = sb(v, "map")[1]
    assert out.index("[_general]") < out.index("[heat]") and "    scheme-choice - Use Crank-Nicolson" in out
    assert out.index("  decision") < out.index("  derivation") < out.index("  finding")
    assert "ftcs-cfl-guess" not in out
    only = sb(v, "map", "_general")[1]
    assert "[heat]" not in only and "observed-convergence-order" in only


def test_check_exit_codes_and_summary(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "check")
    assert code == 0 and "checked 9 notes: 0 errors, 0 warnings" in out
    add_note(v, "warn-only", extra={"sources": "[nowhere.csv]"})
    code, out, _ = sb(v, "check")
    assert code == 0 and "0 errors, 1 warnings" in out
    add_note(v, "bad-note", drop=("cue",))
    code, out, _ = sb(v, "check")
    assert code == 1 and "notes/heat/bad-note.md:1: error: missing field 'cue'" in out


def test_check_changed_looks_only_at_unchecked_notes(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "check")[0] == 0
    assert "checked 0 notes" in sb(v, "check", "--changed")[1]
    p = add_note(v, "bad-note", drop=("cue",))
    code, out, _ = sb(v, "check", "--changed")
    assert code == 1 and "checked 1 notes" in out
    assert sb(v, "check", "--changed")[0] == 1  # still unchecked: it failed
    p.write_text(p.read_text().replace("title:", "cue: now present\ntitle:"), encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    code, out, _ = sb(v, "check", "--changed")
    assert code == 0 and "checked 1 notes" in out
    assert "checked 0 notes" in sb(v, "check", "--changed")[1]
    assert sb(v, "check", "--rebuild")[0] == 0


def test_rules_prints_the_contract():
    code, out, _ = sb("unused", "rules")
    assert code == 0 and "Scientific Brain" in out and len(out.split()) <= 1200


def test_rules_fill_in_the_constants_and_ignore_a_retired_sb_toml(tmp_path):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text("[limits]\ntitle_words = 20\nbody_words = 700\nlinks = 9\n", encoding="utf-8")  # sb <= 0.2.1: not read
    out = sb(v, "rules")[1]
    assert "at most 16 words, body at most 600 words, at most 8 links" in out and "{" not in out
    assert "at most 16 words" in sb("unused", "rules")[1]


def test_rules_have_no_open_placeholder_and_stay_within_1200_words(tmp_path):
    import re
    from importlib.resources import files
    raw = files("scientific_brain").joinpath("RULES.md").read_text(encoding="utf-8")
    assert re.search(r"\{\w+\}", raw)  # the template has placeholders ...
    for where in (sb("unused", "rules"), sb(make_vault(tmp_path), "rules")):
        code, out, _ = where
        assert code == 0 and not re.search(r"\{\w*\}", out) and len(out.split()) <= 1200
        assert "a week since the last sleep" in out and "at most 40 notes and inbox lines" in out


def test_rules_show_the_learned_sleep_budget(tmp_path):
    v = make_vault(tmp_path)
    (v / ".sb" / "sleep.tsv").write_text("2026-11-01T12:00\t0\t0\t0\t0\t0\t0\tx\t40\t0\n", encoding="utf-8")  # a full plan, all done
    out = sb(v, "rules")[1]
    assert "at most 50 notes and inbox lines" in out and "{" not in out


@pytest.mark.parametrize("n", ["0", "-1", "x"])
def test_recall_n_below_one_is_rejected(tmp_path, n):
    with pytest.raises(SystemExit) as e:
        sb(make_vault(tmp_path), "recall", "heat", "-n", n)
    assert e.value.code == 2


def test_bad_sb_today_gives_a_clean_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_TODAY", "tomorrow")
    code, _, err = sb(make_vault(tmp_path), "stats")
    assert code == 2 and "SB_TODAY" in err


def test_new_rejects_slug_with_newline(tmp_path):
    code, _, err = sb(make_vault(tmp_path), "new", "finding", "t4\n", "-p", "heat", "--why", "x")
    assert code == 2 and "valid slug" in err


def test_init_template_describes_the_update_default(tmp_path):
    root = tmp_path / "t"
    sb(root, "init", str(root))
    toml = (root / "sb.toml").read_text()
    assert "# auto = true" in toml and "latest release tag" in toml and "# auto = false" not in toml


def test_the_help_of_stats_names_what_it_shows(capsys):
    from scientific_brain.cli import main
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "counts, stability, learned values, inbox, last sleep" in " ".join(capsys.readouterr().out.split())



def test_recall_is_silent_about_a_due_sleep_while_one_runs(tmp_path):
    """gutachten 2026-11-14: the sleeper's own recall must not report "sleep due" (the hook is silent too)."""
    from scientific_brain import usage
    v = make_vault(tmp_path)
    for i in range(45):
        sb(v, "capture", f"line {i} about quasar nebula", "-s", "x")
    assert "sleep due" in sb(v, "recall", "quasar")[1]
    sb(v, "sleep", "begin")
    assert "sleep due" not in sb(v, "recall", "quasar")[1]
