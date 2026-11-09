import pytest
from helpers import add_note, make_vault, sb

from scientific_brain.schema import check
from scientific_brain.vault import Vault


def run_check(vault):
    return check(Vault(vault))[0]


def messages(vault, level="error"):
    return [str(i) for i in run_check(vault) if i.level == level]


def test_demo_vault_is_clean(tmp_path):
    v = make_vault(tmp_path)
    issues, n = check(Vault(v))
    assert issues == [] and n == 9


@pytest.mark.parametrize("kwargs,msg", [
    ({"drop": ("cue",)}, "missing field 'cue'"),
    ({"drop": ("sources",)}, "requires 'sources'"),
    ({"type": "nonsense"}, "unknown type 'nonsense'"),
    ({"extra": {"project": "_general"}}, "does not match folder 'heat'"),
    ({"extra": {"colour": "red"}}, "unknown field 'colour'"),
    ({"extra": {"created": "2026-13-45"}}, "'created' must be a date"),
    ({"extra": {"superseded_by": "[scheme-choice]"}}, "only allowed in archive/"),
    ({"title": "one two three four five six seven eight nine ten eleven twelve thirteen"}, "title has more than 12"),
    ({"body": "word " * 301}, "body has more than 300"),
    ({"body": "\n" + " ".join(f"[[{s}]]" for s in ("scheme-choice", "ftcs-stability-limit", "richardson-error-estimate",
                                                      "morton-mayers-assessment", "open-nonuniform-mesh-order",
                                                      "couple-time-step-to-mesh"))}, "more than 5 links"),
    ({"body": "\nsee [[no-such-note]]"}, "link to unknown note [[no-such-note]]"),
    ({"gist": "## Heading first"}, "missing gist"),
    ({"title": "TODO"}, "unfilled TODO"),
    ({"extra": {"project": "other"}}, "unknown project 'other'"),
])
def test_note_errors(tmp_path, kwargs, msg):
    v = make_vault(tmp_path)
    add_note(v, "extra-note", **kwargs)
    errs = [m for m in messages(v) if "extra-note" in m]
    assert any(msg in m for m in errs), errs
    assert all(":" in m.split(" error")[0] for m in errs)  # file:line prefix


def test_unsorted_project_is_an_error(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "x-note", project="_unsorted")
    assert any("'_unsorted' is for the inbox only" in m for m in messages(v))


def test_words_containing_todo_are_no_placeholder(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "x-note", body="\nThe open point is noted in TODOs.md, in TODO.md and as an open TODO item.\n"
                               "TODO-list entries stay prose.")
    assert not [m for m in messages(v) if "x-note" in m]


@pytest.mark.parametrize("body", ["\n- TODO", "\nTODO: fill in", "\nTODO"])
def test_skeleton_placeholders_in_the_body_are_errors(tmp_path, body):
    v = make_vault(tmp_path)
    add_note(v, "x-note", body=body)
    assert any("unfilled TODO" in m for m in messages(v) if "x-note" in m)


def test_required_sections(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "a-decision", type="decision")
    p.write_text(p.read_text().replace("## Rejected", "## Other"), encoding="utf-8")
    q = add_note(v, "a-question", type="question")
    q.write_text(q.read_text().replace("## Depends on", "## Other"), encoding="utf-8")
    msgs = messages(v)
    assert any("a-decision" in m and "'## Rejected'" in m for m in msgs)
    assert any("a-question" in m and "'## Depends on'" in m for m in msgs)


def test_limits_apply_only_in_notes_not_archive(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "long-old", loc="archive", body="word " * 400)
    assert not [m for m in messages(v) if "long-old" in m]


def test_slug_unique_across_notes_and_archive(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "scheme-choice", loc="archive")
    assert any("slug 'scheme-choice' is also used by" in m for m in messages(v))


def test_ref_checks(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "src-doi", type="source", extra={"ref": "10.1000/xyz123"})
    add_note(v, "src-bad", type="source", extra={"ref": "nobody-1999"})
    add_note(v, "src-dup", type="source")  # same ref as the demo source
    msgs = messages(v)
    assert not [m for m in msgs if "src-doi" in m]
    assert any("src-bad" in m and "unknown citation key" in m for m in msgs)
    assert any("src-dup" in m and "already used by" in m for m in msgs)


def test_missing_source_path_is_only_a_warning(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "gone-src", extra={"sources": "[runs/nothing/here.csv]"})
    assert not messages(v)
    assert any("source path not found" in m for m in messages(v, "warning"))
    assert sb(v, "check")[0] == 0


def test_superseded_by_target_and_cycle(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "old-a", loc="archive", extra={"superseded_by": "[nowhere]"})
    add_note(v, "old-b", loc="archive", extra={"superseded_by": "[old-c]"})
    add_note(v, "old-c", loc="archive", extra={"superseded_by": "[old-b]"})
    msgs = messages(v)
    assert any("old-a" in m and "target 'nowhere' does not exist" in m for m in msgs)
    assert any("superseded_by cycle" in m for m in msgs)


def test_link_to_archived_note_warns(tmp_path):
    v = make_vault(tmp_path)
    add_note(v, "pointer", body="\nsee [[ftcs-cfl-guess]]")
    assert any("link to archived note" in m for m in messages(v, "warning"))


def test_parse_error_is_reported_with_line(tmp_path):
    v = make_vault(tmp_path)
    (v / "notes" / "heat" / "broken.md").write_text("---\ntitle x\n---\n", encoding="utf-8")
    assert any(m.startswith("notes/heat/broken.md:2: error") for m in messages(v))
