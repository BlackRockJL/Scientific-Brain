import pytest
from helpers import add_note, make_vault, sb

HEAT = "notes/heat/"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", "2026-11-08")


def read(v, rel):
    return (v / rel).read_text(encoding="utf-8")


def test_archive_with_successor_without_fix_lists_incoming_links(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "ftcs-limit-v2")
    code, out, _ = sb(v, "archive", "ftcs-stability-limit", "--why", "superseded", "--superseded-by", "ftcs-limit-v2")
    assert code == 0 and "archived ftcs-stability-limit (superseded) -> archive/heat/ftcs-stability-limit.md" in out
    assert "incoming links from: cn-oscillations-large-time-step, morton-mayers-assessment, scheme-choice" in out
    assert not (v / HEAT / "ftcs-stability-limit.md").exists()
    assert "superseded_by: [ftcs-limit-v2]" in read(v, "archive/heat/ftcs-stability-limit.md")
    assert "[[ftcs-stability-limit]]" in read(v, HEAT + "scheme-choice.md")  # not repaired
    code, out, _ = sb(v, "check")
    assert code == 0 and "link to archived note [[ftcs-stability-limit]]" in out
    assert "(archived 2026-11-08: superseded; superseded by: ftcs-limit-v2)" in sb(v, "recall", "--archive", "stability", "limit")[1]  # index follows without a rebuild
    assert "ftcs-stability-limit" not in sb(v, "recall", "stability")[1].split("+")[0]
    assert "(archived 2026-11-08: superseded; superseded by: ftcs-limit-v2)" in sb(v, "show", "ftcs-stability-limit")[1]


def test_archive_fix_links_points_to_the_first_successor(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "ftcs-limit-v2")
    add_note(v, "ftcs-limit-v3")
    code, out, _ = sb(v, "archive", "ftcs-stability-limit", "--why", "superseded", "--superseded-by", "ftcs-limit-v2", "ftcs-limit-v3", "--fix-links")
    assert code == 0 and "links repaired in 3 notes" in out
    assert "superseded_by: [ftcs-limit-v2, ftcs-limit-v3]" in read(v, "archive/heat/ftcs-stability-limit.md")
    for name in ("scheme-choice", "cn-oscillations-large-time-step", "morton-mayers-assessment"):
        t = read(v, HEAT + f"{name}.md")
        assert "[[ftcs-limit-v2]]" in t and "ftcs-stability-limit" not in t
    assert sb(v, "check")[1].strip().endswith("0 errors, 0 warnings")


def test_archive_fix_links_without_successor_drops_bullets_and_unlinks_text(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "archive", "ftcs-stability-limit", "--why", "faded", "--fix-links")
    assert code == 0 and "links repaired in 3 notes" in out
    t = read(v, HEAT + "scheme-choice.md")
    assert "- [[ftcs-stability-limit]]" not in t and "see ftcs-stability-limit;" in t  # bullet gone, inline text stays
    assert "- [[cn-oscillations-large-time-step]]" in t  # other bullets stay
    t = read(v, HEAT + "morton-mayers-assessment.md")
    assert "limit in ftcs-stability-limit." in t and "[[" in t  # inline mention became text, other links stay
    assert "- [[ftcs-stability-limit]]" not in read(v, HEAT + "cn-oscillations-large-time-step.md")
    assert "superseded_by" not in read(v, "archive/heat/ftcs-stability-limit.md")
    assert sb(v, "check")[1].strip().endswith("0 errors, 0 warnings")


def test_archive_failures_leave_the_note_in_place(tmp_path):
    v = make_vault(tmp_path)
    code, _, err = sb(v, "archive", "scheme-choice", "--why", "superseded", "--superseded-by", "no-such-note")
    assert code == 2 and "target 'no-such-note'" in err and (v / HEAT / "scheme-choice.md").is_file()
    code, _, err = sb(v, "archive", "scheme-choice", "--why", "superseded", "--superseded-by", "ftcs-cfl-guess")  # archived targets do not count
    assert code == 2 and (v / HEAT / "scheme-choice.md").is_file()
    assert sb(v, "archive", "scheme-choice", "--why", "superseded", "--superseded-by", "scheme-choice")[0] == 2
    code, _, err = sb(v, "archive", "ftcs-cfl-guess", "--why", "faded")
    assert code == 2 and "already archived" in err
    assert sb(v, "archive", "missing", "--why", "faded")[0] == 2


def test_restore_round_trip_and_collision(tmp_path):
    v = make_vault(tmp_path)
    code, out, _ = sb(v, "restore", "ftcs-cfl-guess")
    assert code == 0 and "restored ftcs-cfl-guess -> notes/heat/ftcs-cfl-guess.md" in out
    assert not (v / "archive" / "heat" / "ftcs-cfl-guess.md").exists()
    assert "superseded_by" not in read(v, HEAT + "ftcs-cfl-guess.md")
    assert "ftcs-cfl-guess" in sb(v, "recall", "cfl", "guess")[1]  # back in the default search
    assert sb(v, "check")[0] == 0
    assert sb(v, "restore", "ftcs-cfl-guess")[0] == 2  # not archived any more
    assert sb(v, "restore", "missing")[0] == 2
    # a slug present in both places is an error, not a silent overwrite
    add_note(v, "twin", loc="archive")
    add_note(v, "twin")
    before = read(v, "archive/heat/twin.md")
    code, _, err = sb(v, "restore", "twin")
    assert code == 2 and "already exists at notes/heat/twin.md" in err and read(v, "archive/heat/twin.md") == before


def test_archive_then_restore_keeps_the_text(tmp_path):
    v = make_vault(tmp_path)
    original = read(v, HEAT + "open-nonuniform-mesh-order.md")
    assert sb(v, "archive", "open-nonuniform-mesh-order", "--why", "faded")[0] == 0
    assert sb(v, "restore", "open-nonuniform-mesh-order")[0] == 0
    assert read(v, HEAT + "open-nonuniform-mesh-order.md") == original


def test_fix_links_never_creates_a_self_link(tmp_path):
    v = make_vault(tmp_path)  # scheme-choice links to ftcs-stability-limit and is named as its successor
    assert sb(v, "archive", "ftcs-stability-limit", "--why", "superseded", "--superseded-by", "scheme-choice", "--fix-links")[0] == 0
    t = read(v, HEAT + "scheme-choice.md")
    assert "[[scheme-choice]]" not in t and "- [[ftcs-stability-limit]]" not in t
    assert "[[scheme-choice]]" in read(v, HEAT + "morton-mayers-assessment.md")  # others point to the successor
    assert sb(v, "check")[1].strip().endswith("0 errors, 0 warnings")


def meta_of(v, rel):
    return dict(ln.split(": ", 1) for ln in read(v, rel).split("---")[1].strip().splitlines())


def test_archive_sets_the_three_fields_and_check_requires_them(tmp_path):
    v = make_vault(tmp_path)
    assert sb(v, "archive", "open-nonuniform-mesh-order", "--why", "faded")[0] == 0
    m = meta_of(v, "archive/heat/open-nonuniform-mesh-order.md")
    assert m["archived"] == "2026-11-08" and m["archived_why"] == "faded" and "archived_reason" not in m
    code, out, _ = sb(v, "archive", "couple-time-step-to-mesh", "--why", "wrong", "--reason", "the factor c is 0.4,\nnot 0.5")
    assert code == 0 and "(wrong)" in out
    assert meta_of(v, "archive/heat/couple-time-step-to-mesh.md")["archived_reason"] == "the factor c is 0.4, not 0.5"
    assert sb(v, "check")[0] == 0
    for drop in ("archived", "archived_why"):  # both are required in archive/
        p = v / "archive" / "heat" / "open-nonuniform-mesh-order.md"
        text = p.read_text()
        p.write_text("\n".join(ln for ln in text.split("\n") if not ln.startswith(drop + ":")), encoding="utf-8")
        code, out, _ = sb(v, "check")
        assert code == 1 and f"archived notes require '{drop}'" in out
        p.write_text(text, encoding="utf-8")
    p.write_text(text.replace("archived_why: faded", "archived_why: tired"), encoding="utf-8")
    assert "archived_why must be" in sb(v, "check")[1]


def test_archive_why_and_reason_rules(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "successor")
    for args in (("--why", "wrong"), ("--why", "wrong", "--reason", " "), ("--why", "superseded"),
                 ("--why", "faded", "--superseded-by", "successor")):
        code, _, err = sb(v, "archive", "scheme-choice", *args)
        assert code == 2 and (v / HEAT / "scheme-choice.md").is_file(), args
    assert sb(v, "archive", "scheme-choice", "--why", "superseded", "--reason", "merged into successor")[0] == 0
    assert sb(v, "archive", "ftcs-stability-limit", "--why", "superseded", "--superseded-by", "successor")[0] == 0  # no reason needed
    assert sb(v, "archive", "richardson-error-estimate", "--why", "wrong", "--reason", "w", "--superseded-by", "successor")[0] == 0
    assert "superseded_by: [successor]" in read(v, "archive/heat/richardson-error-estimate.md")
    with pytest.raises(SystemExit):  # --why is always explicit
        sb(v, "archive", "open-nonuniform-mesh-order", "--superseded-by", "successor")
    with pytest.raises(SystemExit):
        sb(v, "archive", "open-nonuniform-mesh-order", "--why", "boring")


def test_archive_note_function_for_the_forgetting_phase(tmp_path):
    from datetime import date

    from scientific_brain.links import archive_note
    from scientific_brain.vault import Vault
    v = make_vault(tmp_path)
    dst, inc, fixed = archive_note(Vault(v), "open-nonuniform-mesh-order", "faded", today=date(2026, 12, 1))
    assert dst == "archive/heat/open-nonuniform-mesh-order.md" and inc == [] and fixed == []
    assert meta_of(v, dst)["archived"] == "2026-12-01"
    assert "open-nonuniform-mesh-order" not in sb(v, "map")[1]  # the index was updated


def test_show_and_recall_archive_display_the_reason(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "archive", "couple-time-step-to-mesh", "--why", "wrong", "--reason", "c is 0.4")
    out = sb(v, "show", "couple-time-step-to-mesh")[1]
    assert "== couple-time-step-to-mesh (archived 2026-11-08: wrong \u2014 c is 0.4) ==" in out
    out = sb(v, "recall", "--archive", "couple", "mesh")[1]
    assert "WRONG: couple-time-step-to-mesh  [decision, heat] (archived 2026-11-08: wrong \u2014 c is 0.4)" in out
    assert "WRONG: ftcs-cfl-guess" not in sb(v, "recall", "--archive", "cfl", "guess")[1]


def test_restore_of_wrong_needs_force_and_clears_the_fields(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "archive", "scheme-choice", "--why", "wrong", "--reason", "c is 0.4")
    code, _, err = sb(v, "restore", "scheme-choice")
    assert code == 2 and "archived as wrong (c is 0.4)" in err and "--force" in err
    assert not (v / HEAT / "scheme-choice.md").exists()
    assert sb(v, "restore", "scheme-choice", "--force")[0] == 0
    assert all(k not in read(v, HEAT + "scheme-choice.md") for k in ("archived", "superseded_by"))
    assert sb(v, "check")[0] == 0
    sb(v, "archive", "open-nonuniform-mesh-order", "--why", "superseded", "--reason", "x")
    assert sb(v, "restore", "open-nonuniform-mesh-order")[0] == 0  # only wrong needs force
    assert "archived" not in read(v, HEAT + "open-nonuniform-mesh-order.md")


def test_archive_fields_in_notes_are_a_check_error(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "misplaced", extra={"archived": "2026-11-01", "archived_why": "faded", "archived_reason": "x"})
    code, out, _ = sb(v, "check")
    assert code == 1 and out.count("is only allowed in archive/") == 3


def snapshot(v):
    return {p.relative_to(v).as_posix(): p.read_bytes() for p in v.rglob("*.md")}


def test_rename_with_broken_superseded_by_changes_nothing(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "x-old")
    add_note(v, "y-link", body="\nSee [[x-old]].\n")
    add_note(v, "z-arch", loc="archive", extra={"superseded_by": "[x-old"})
    before = snapshot(v)
    code, out, err = sb(v, "rename", "x-old", "x-new")
    assert code == 2 and "z-arch.md" in err and "nothing was renamed" in err and "Traceback" not in err
    assert snapshot(v) == before


def test_rename_with_unreadable_file_names_it_and_changes_nothing(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "x-old")
    add_note(v, "y-link", body="\nSee [[x-old]].\n")
    (v / "archive" / "heat" / "bad.md").write_bytes(b"---\ntitle: \xff\xfe\n---\n")
    before = snapshot(v)
    code, out, err = sb(v, "rename", "x-old", "x-new")
    assert code == 2 and "bad.md" in err and "UnicodeDecodeError" not in err
    assert snapshot(v) == before


def test_rename_write_failure_restores_the_texts(tmp_path, monkeypatch):
    from pathlib import Path
    v = make_vault(tmp_path)
    add_note(v, "x-old")
    add_note(v, "y-link", body="\nSee [[x-old]].\n")
    add_note(v, "w-link", body="\nSee [[x-old]].\n")
    before = snapshot(v)
    real = Path.rename

    def boom(self, target):
        raise OSError("disk full") if self.name == "x-old.md" else real(self, target)

    monkeypatch.setattr(Path, "rename", boom)
    code, out, err = sb(v, "rename", "x-old", "x-new")
    assert code == 2 and "left as they were" in err
    assert snapshot(v) == before
