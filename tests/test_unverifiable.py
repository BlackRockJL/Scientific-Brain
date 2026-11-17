"""`sb sleep unverifiable`: parking notes that no source or neighbour can check, and what parking does to backlog, plan,
leftover and budget."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from helpers import add_note, init_vault, sb, write_log

from scientific_brain import sleep, usage
from scientific_brain.cli import main
from scientific_brain.vault import SbError, Vault

TODAY = date(2026, 11, 8)
OUT = "{} parked as unverifiable; it returns when changed or after its rest"
OFF_LIST = "{} is not on this sleep's verify list"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def at(monkeypatch, day):
    monkeypatch.setenv("SB_TODAY", day.isoformat())


def mk(root, slug, n=0, **kw):
    """A note in project p whose title, cue and gist words no other note shares (n numbers them), so no near-duplicate pair arises."""
    kw.setdefault("title", f"alpha{n}x beta{n}x")
    kw.setdefault("cue", f"gamma{n}x delta{n}x")
    kw.setdefault("gist", f"Gist: epsilon{n}x.")
    return add_note(root, slug, project="p", **kw)


def vault_with(tmp_path, *slugs, **kw):
    """An empty vault with one note per slug (default sources: a path that does not exist), no sleep running."""
    root = init_vault(tmp_path)
    for i, s in enumerate(slugs):
        mk(root, s, i, **kw)
    return root


def begin(root):
    assert sb(root, "sleep", "begin")[0] == 0


def plan_begin(root):
    """Begin a sleep and print its plan: unchecked notes now stand on the sleep's verify list."""
    begin(root)
    assert sb(root, "sleep", "plan")[0] == 0


def listed(root):
    """Slugs the running sleep's first plan put under verify (work in the marker)."""
    return {x[7:] for x in sleep.remembered(Vault(root), "work") if x.startswith("verify:")}


def mark(root, slug, reason="nothing to check it against"):
    return sb(root, "sleep", "unverifiable", slug, reason)


def note(root, slug):
    v = Vault(root)
    return v.read(v.find(slug, ("notes",)))


def path(root, slug):
    return Path(root) / "notes" / "p" / f"{slug}.md"


def edit(root, slug, extra="\nA new sentence.\n"):
    p = path(root, slug)
    p.write_text(p.read_text(encoding="utf-8") + extra, encoding="utf-8")


def parked(root, day=TODAY, **kw):
    return sleep.parked(Vault(root), day, **kw)


def park_rows(root, *marks, reason="no source"):
    """Write .sb/unverifiable.tsv directly: marks are (day, slug), (day, slug, renames) or (day, slug, renames, digest);
    the digest is that of the note as it is now unless given."""
    rows = []
    for day, slug, *rest in marks:
        ren, dig = (rest + [0, None])[:2] if len(rest) < 2 else rest
        rows.append((day.isoformat(), slug, reason, ren, sleep.digest(note(root, slug)) if dig is None else dig))
    write_log(root, "unverifiable.tsv", *rows)


def verify_section(root):
    return sb(root, "sleep", "plan")[1].split("## 4.")[1].split("## 5.")[0]


def verify_slugs(section):
    """Slugs named on the list lines of the verify section (new or changed, then most active)."""
    return [ln[2:].split(" ")[0] for ln in section.splitlines() if ln.startswith("- ")]


def left(root):
    """(size, left, parked) of the running sleep."""
    v = Vault(root)
    return tuple(sleep.leftover(v, TODAY, sleep.started(v)))


def sleep_rows(root):
    return [ln.split("\t") for ln in (root / ".sb" / "sleep.tsv").read_text().splitlines()]


def source_file(root, rel="data/x.csv"):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("1,2\n")


# ---- 1. refusals and what is allowed --------------------------------------------------------------------------------

def test_the_file_name_constant():
    """The marks live in .sb/unverifiable.tsv."""
    assert sleep.UNVERIFIABLE == "unverifiable.tsv"


def test_refused_without_a_running_sleep(tmp_path):
    """No sleep, no parking: the message names `sb sleep begin`, nothing is written, and the check comes before the slug check."""
    root = vault_with(tmp_path, "n1")
    msg = "sleep unverifiable works only during a sleep (sb sleep begin)"
    code, out, err = mark(root, "n1")
    assert code == 2 and msg in err and out == ""
    assert msg in mark(root, "no-such-note")[2]  # the missing sleep is reported first
    assert not (root / ".sb" / "unverifiable.tsv").exists()
    with pytest.raises(SbError, match="works only during a sleep"):
        sleep.unverifiable(Vault(root), "n1", "r", TODAY)


def test_refused_after_the_sleep_ended(tmp_path):
    """The marker is gone after `sleep done`, so parking is refused again."""
    root = vault_with(tmp_path, "n1")
    plan_begin(root)
    assert sb(root, "sleep", "done", "nothing")[0] == 0
    assert mark(root, "n1")[0] == 2 and not (root / ".sb" / "unverifiable.tsv").exists()


def test_refused_for_an_unknown_slug_and_for_an_archived_note(tmp_path):
    """Only notes in notes/ can be parked."""
    root = vault_with(tmp_path, "n1", "n2")
    begin(root)
    code, _, err = mark(root, "ghost")
    assert code == 2 and "no note 'ghost' in notes/" in err
    assert sb(root, "archive", "n2", "--why", "faded")[0] == 0
    code, _, err = mark(root, "n2")
    assert code == 2 and "no note 'n2' in notes/" in err
    assert not (root / ".sb" / "unverifiable.tsv").exists()


@pytest.mark.parametrize("reason", ["", " ", " \t \n "])
def test_refused_for_an_empty_reason(tmp_path, reason):
    """A reason that is empty after cleaning (blanks, tabs, line breaks) is refused; nothing is written."""
    root = vault_with(tmp_path, "n1")
    plan_begin(root)
    code, _, err = mark(root, "n1", reason)
    assert code == 2 and "unverifiable needs a reason of one sentence" in err
    assert not (root / ".sb" / "unverifiable.tsv").exists()


def test_refused_when_a_source_file_exists_in_the_vault_root(tmp_path):
    """Without source_roots, a source is resolved against the vault root; an existing file means: check against that."""
    root = vault_with(tmp_path, "n1", extra={"sources": "[data/x.csv]"})
    source_file(root)
    plan_begin(root)
    code, out, err = mark(root, "n1")
    assert code == 2 and "n1 has a source at data/x.csv; check it against that" in err and out == ""
    assert not (root / ".sb" / "unverifiable.tsv").exists()


def test_refused_when_any_one_of_several_sources_exists(tmp_path):
    """TODO and a URL beside one real file do not help; the message names the file."""
    root = vault_with(tmp_path, "n1", extra={"sources": "[TODO, https://example.org/a.pdf, gone/b.py, data/x.csv]"})
    source_file(root)
    plan_begin(root)
    code, _, err = mark(root, "n1")
    assert code == 2 and "n1 has a source at data/x.csv; check it against that" in err


def test_source_roots_decide_where_a_source_is_looked_for(tmp_path):
    """With source_roots the file is looked for in each root (relative to the vault root), as `sb check` does."""
    root = init_vault(tmp_path)
    toml = root / "sb.toml"
    toml.write_text('source_roots = ["../proj", "../other"]\n' + toml.read_text(), encoding="utf-8")
    assert Vault(root).cfg["source_roots"] == ["../proj", "../other"]
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "o.py").write_text("x = 1\n")
    mk(root, "n1", 1, extra={"sources": "[src/p.py]"})
    mk(root, "n2", 2, extra={"sources": "[o.py]"})
    mk(root, "n3", 3, extra={"sources": "[src/missing.py]"})
    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "proj" / "src" / "p.py").write_text("x = 1\n")
    plan_begin(root)
    code, _, err = mark(root, "n1")
    assert code == 2 and "n1 has a source at src/p.py; check it against that" in err  # in the first root
    code, _, err = mark(root, "n2")
    assert code == 2 and "n2 has a source at o.py; check it against that" in err  # in the second root
    assert mark(root, "n3")[0] == 0 and parked(root) == {"n3"}


def test_with_source_roots_the_vault_root_is_not_searched(tmp_path):
    """Spec reading: source_roots replace the vault root; a file only in the vault root is no source then."""
    root = init_vault(tmp_path)
    toml = root / "sb.toml"
    toml.write_text('source_roots = ["../proj"]\n' + toml.read_text(), encoding="utf-8")
    (tmp_path / "proj").mkdir()
    mk(root, "n1", 1, extra={"sources": "[data/x.csv]"})
    source_file(root)
    plan_begin(root)
    assert mark(root, "n1")[0] == 0


@pytest.mark.parametrize("sources", ["[TODO]", "[https://example.org/paper.pdf]", "[no/such/file.py]", "[]",
                                     "[TODO, https://example.org/a, no/such/file.py]"])
def test_allowed_without_a_source_file(tmp_path, sources):
    """TODO, a URL, a path that does not exist and an empty list are no source file."""
    root = vault_with(tmp_path, "n1", extra={"sources": sources})
    plan_begin(root)
    code, out, err = mark(root, "n1")
    assert code == 0 and out.strip() == OUT.format("n1"), err
    assert parked(root) == {"n1"}


def test_allowed_for_a_note_without_a_sources_field(tmp_path):
    """No `sources:` line at all (a concept) is allowed."""
    root = init_vault(tmp_path)
    mk(root, "c1", 1, type="concept", drop=("sources",))
    plan_begin(root)
    assert mark(root, "c1")[0] == 0 and parked(root) == {"c1"}


def test_a_file_named_todo_is_still_the_placeholder(tmp_path):
    """TODO never counts as a present source, even if a file of that name lies in the vault root."""
    root = vault_with(tmp_path, "n1", extra={"sources": "[TODO]"})
    (root / "TODO").write_text("x\n")
    plan_begin(root)
    assert mark(root, "n1")[0] == 0


# ---- N1 the source check: whole entry or a word of it ---------------------------------------------------------------

def found(root, slug="n1"):
    return sleep.source_found(Vault(root), note(root, slug))


def test_source_found_takes_the_whole_entry_or_a_word_of_it(tmp_path):
    """The entry as a whole, a word with a slash (edge punctuation stripped), the first existing one in order."""
    root = vault_with(tmp_path, "n1", "n2", "n3", "n4")
    source_file(root, "data/a.csv")
    source_file(root, "data/b.csv")
    source_file(root, "my data/c d.csv")
    for slug, srcs in (("n1", "[data/a.csv]"), ("n2", "[see the table in data/a.csv (column 2).]"),
                       ("n3", "[my data/c d.csv]"), ("n4", "[data/b.csv, data/a.csv]")):
        p = path(root, slug)
        p.write_text(p.read_text(encoding="utf-8").replace("sources: [src/ftcs.py]", f"sources: {srcs}"), encoding="utf-8")
    assert [found(root, s) for s in ("n1", "n2", "n3", "n4")] == ["data/a.csv", "data/a.csv", "my data/c d.csv", "data/b.csv"]


@pytest.mark.parametrize("sources", ["[TODO]", "[]", "[no/such/file.py]", "[see https://example.org/data/x.csv]",
                                     "[the file x.csv has no slash]", "[TODO and no/such/file.py]"])
def test_source_found_ignores_what_is_no_local_file(tmp_path, sources):
    """TODO, a word with ://, a word without a slash (even if the file exists) and a missing path find nothing."""
    root = vault_with(tmp_path, "n1", extra={"sources": sources})
    source_file(root)
    (root / "x.csv").write_text("1\n")
    assert found(root) is None


def test_source_found_follows_source_roots(tmp_path):
    """Roots replace the vault root, for a word of a free-text entry too."""
    root = init_vault(tmp_path)
    toml = root / "sb.toml"
    toml.write_text('source_roots = ["../proj"]\n' + toml.read_text(), encoding="utf-8")
    mk(root, "n1", 1, extra={"sources": "[see the log in src/p.py for this]"})
    assert found(root) is None
    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "proj" / "src" / "p.py").write_text("x = 1\n")
    assert found(root) == "src/p.py"


def test_a_path_inside_a_free_text_source_blocks_parking(tmp_path):
    """E-probe: 'see data/x.csv, table 2'-style sources name a file; the check finds it and refuses."""
    root = vault_with(tmp_path, "n1", extra={"sources": "[the numbers are in data/x.csv (table 2).]"})
    source_file(root)
    plan_begin(root)
    code, _, err = mark(root, "n1")
    assert code == 2 and "n1 has a source at data/x.csv; check it against that" in err


def test_ref_is_not_a_source(tmp_path):
    """Only `sources:` counts; a ref: that names an existing file does not block parking."""
    root = vault_with(tmp_path, "n1", extra={"sources": "[TODO]", "ref": "data/x.csv"})
    source_file(root)
    plan_begin(root)
    assert mark(root, "n1")[0] == 0


def test_a_source_that_turns_up_later_takes_the_note_out_of_the_parking(tmp_path):
    """E4: parked, then the source file appears: the note is no longer parked (and is back in the backlog); gone again, it rests again."""
    root = vault_with(tmp_path, "n1", extra={"sources": "[later.py]"})
    plan_begin(root)
    assert mark(root, "n1")[0] == 0
    sb(root, "sleep", "done", "x")
    assert parked(root) == {"n1"} and sleep.backlog(Vault(root), TODAY)["verify"] == 0
    (root / "later.py").write_text("x = 1\n")
    assert parked(root) == set() and sleep.backlog(Vault(root), TODAY)["verify"] == 1
    (root / "later.py").unlink()
    assert parked(root) == {"n1"}


def test_a_source_in_a_root_takes_the_note_out_of_the_parking_too(tmp_path):
    """The same through source_roots."""
    root = init_vault(tmp_path)
    toml = root / "sb.toml"
    toml.write_text('source_roots = ["../proj"]\n' + toml.read_text(), encoding="utf-8")
    (tmp_path / "proj").mkdir()
    mk(root, "n1", 1, extra={"sources": "[src/p.py]"})
    park_rows(root, (TODAY, "n1"))
    assert parked(root) == {"n1"}
    (tmp_path / "proj" / "src").mkdir()
    (tmp_path / "proj" / "src" / "p.py").write_text("x = 1\n")
    assert parked(root) == set()


# ---- N2 only notes of this sleep's verify list, and only unchecked ones --------------------------------------------

def test_refused_before_any_plan(tmp_path):
    """E1: without a plan no note is on the verify list; nothing is parked and the backlog stays."""
    root = vault_with(tmp_path, "n0", "n1", "n2")
    begin(root)
    code, out, err = mark(root, "n2")
    assert code == 2 and OFF_LIST.format("n2") in err and out == ""
    assert not (root / ".sb" / "unverifiable.tsv").exists() and sleep.backlog(Vault(root), TODAY)["verify"] == 3


def test_refused_for_a_note_created_after_the_plan(tmp_path):
    """A note the plan never saw is not on its list."""
    root = vault_with(tmp_path, "n0")
    plan_begin(root)
    mk(root, "late", 5)
    code, _, err = mark(root, "late")
    assert code == 2 and OFF_LIST.format("late") in err
    assert mark(root, "n0")[0] == 0


def test_refused_for_a_note_the_budget_left_out_of_the_plan(tmp_path, monkeypatch):
    """Two unchecked notes, room for one: only the listed one can be parked."""
    monkeypatch.setattr(usage, "B0", 1)
    monkeypatch.setattr(usage, "B_MIN", 1)
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    (one,) = listed(root)
    other = ({"n1", "n2"} - {one}).pop()
    code, _, err = mark(root, other)
    assert code == 2 and OFF_LIST.format(other) in err
    assert mark(root, one)[0] == 0


def test_a_checked_note_of_verify_b_is_not_on_the_list(tmp_path):
    """E2: after a baseline the notes are checked; the plan shows them as 'most active' but they are no work items."""
    root = vault_with(tmp_path, "n0", "n1", "n2")
    assert sb(root, "sleep", "baseline")[0] == 0
    plan_begin(root)
    assert verify_slugs(verify_section(root)) and listed(root) == set()
    code, _, err = mark(root, "n0")
    assert code == 2 and OFF_LIST.format("n0") in err and parked(root) == set()


def test_refused_for_a_note_listed_and_checked(tmp_path):
    """On the list but checked by the snapshot (it caught up meanwhile): nothing to park."""
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    write_log(root, "snapshot.tsv", ("n1", sleep.digest(note(root, "n1")), len(Vault(root).rows("renames.tsv", 5)), sleep.DIGEST))
    code, _, err = mark(root, "n1")
    assert code == 2 and "n1 is checked; nothing to park" in err
    assert not (root / ".sb" / "unverifiable.tsv").exists() and mark(root, "n2")[0] == 0


def test_the_order_of_the_refusals(tmp_path):
    """Sleep, slug, reason, list, checked, source: each is reported before the later ones."""
    root = vault_with(tmp_path, "ok", "chk", "src", extra={"sources": "[data/x.csv]"})
    source_file(root)
    plan_begin(root)
    mk(root, "late", 8, extra={"sources": "[data/x.csv]"})
    write_log(root, "snapshot.tsv", ("chk", sleep.digest(note(root, "chk")), 0, sleep.DIGEST))
    assert "no note 'ghost'" in mark(root, "ghost", "")[2]  # slug before reason
    assert "needs a reason" in mark(root, "late", "")[2]  # reason before list
    assert OFF_LIST.format("late") in mark(root, "late")[2]  # list before source
    assert "chk is checked" in mark(root, "chk")[2]  # checked before source
    assert "src has a source at data/x.csv" in mark(root, "src")[2]


def test_a_parked_note_cannot_be_parked_again_while_it_rests(tmp_path, monkeypatch):
    """A resting note is not planned, so it is off the list in the next sleep (until its rest is over)."""
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    mark(root, "n1")
    sb(root, "sleep", "done", "first")
    at(monkeypatch, TODAY + timedelta(30))
    plan_begin(root)
    assert OFF_LIST.format("n1") in mark(root, "n1")[2]


def test_a_rename_after_the_plan_keeps_the_note_on_the_list(tmp_path):
    """The list follows renames as `leftover` does: the new name can be parked."""
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    assert sb(root, "rename", "n1", "n1b")[0] == 0
    assert mark(root, "n1b")[0] == 0 and parked(root) == {"n1b"}


# ---- 2. the row and the output ---------------------------------------------------------------------------------

def test_the_row_holds_day_slug_reason_renames_and_digest(tmp_path):
    """One tab-separated line: date, slug, cleaned reason, rename count, digest of the note; the note itself is untouched."""
    root = vault_with(tmp_path, "n1", "n2")
    assert sb(root, "rename", "n2", "n2b")[0] == 0  # one rename so far
    before = path(root, "n1").read_text(encoding="utf-8")
    plan_begin(root)
    code, out, err = mark(root, "n1", "a  reason\twith   blanks")
    assert code == 0 and out == OUT.format("n1") + "\n" and err == ""
    rows = [ln.split("\t") for ln in (root / ".sb" / "unverifiable.tsv").read_text(encoding="utf-8").splitlines()]
    assert rows == [[TODAY.isoformat(), "n1", "a reason with blanks", "1", sleep.digest(note(root, "n1"))]]
    assert len(rows[0][4]) == 40  # a sha1
    assert path(root, "n1").read_text(encoding="utf-8") == before  # parking is no edit and no verification
    assert not note(root, "n1").get("verified")


def test_marks_are_appended_not_replaced(tmp_path):
    """A second note and a second mark of the same note (still on the list) add lines."""
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    mark(root, "n1", "first")
    mark(root, "n2", "second")
    mark(root, "n1", "again")
    rows = [ln.split("\t") for ln in (root / ".sb" / "unverifiable.tsv").read_text(encoding="utf-8").splitlines()]
    assert [(r[1], r[2]) for r in rows] == [("n1", "first"), ("n2", "second"), ("n1", "again")]


def test_the_function_logs_the_given_day(tmp_path):
    """sleep.unverifiable(v, slug, reason, day) writes that day, not today."""
    root = vault_with(tmp_path, "n1")
    plan_begin(root)
    sleep.unverifiable(Vault(root), "n1", "r", TODAY - timedelta(3))
    assert (root / ".sb" / "unverifiable.tsv").read_text(encoding="utf-8").startswith("2026-11-05\tn1\tr\t0\t")


def test_the_help_names_the_subcommand(capsys):
    """`sb sleep` lists unverifiable in its help line; the subcommand has its own help."""
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "run a sleep: begin, plan, veto, unrelated, verified, unverifiable, done; baseline" in " ".join(capsys.readouterr().out.split())
    with pytest.raises(SystemExit):
        main(["sleep", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "park a note no source or neighbour can check; it returns when changed or after a rest" in text


# ---- 3. parked --------------------------------------------------------------------------------------------------

def test_a_fresh_vault_rests_a_marked_note_for_ninety_days(tmp_path):
    """The rest is the learned vault base, 90 days in a vault without history: parked on day 90, back on day 91."""
    root = vault_with(tmp_path, "n1", "n2")
    assert usage.learned(Vault(root), TODAY)[""] == 90
    plan_begin(root)
    mark(root, "n1")
    assert parked(root) == {"n1"}  # n2 was not marked
    assert parked(root, TODAY + timedelta(90)) == {"n1"}  # t = S: R = 1/2 still rests
    assert parked(root, TODAY + timedelta(91)) == set()


def test_a_marked_note_is_parked_after_the_sleep_ended(tmp_path):
    """The parking outlives the sleep: `sleep done` does not bless or release the note."""
    root = vault_with(tmp_path, "n1")
    plan_begin(root)
    mark(root, "n1")
    assert sb(root, "sleep", "done", "parked one")[0] == 0
    assert parked(root) == {"n1"}
    assert not sleep.checked(note(root, "n1"), sleep.snapshot(Vault(root)))  # still unchecked, only resting


def test_a_changed_note_leaves_the_parking(tmp_path):
    """The digest of the last mark must equal the note's digest today; an edit returns it, restoring the text parks it again."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"), (TODAY, "n2"))
    assert parked(root) == {"n1", "n2"}
    original = path(root, "n1").read_text(encoding="utf-8")
    edit(root, "n1")
    assert parked(root) == {"n2"}
    path(root, "n1").write_text(original, encoding="utf-8")
    assert parked(root) == {"n1", "n2"}  # the digest says nothing about when the text was changed


def test_edits_that_a_digest_ignores_leave_the_note_parked(tmp_path):
    """A verified: field and the ## Links section are no content change (they are not part of the digest)."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"))
    sleep.mark_verified(Vault(root), ["n1"], TODAY)
    assert note(root, "n1").get("verified") and parked(root) == {"n1"}
    edit(root, "n1", "\n## Links\n- [[n2]] a half-sentence why\n")
    assert parked(root) == {"n1"}


def test_the_digest_of_the_last_mark_counts(tmp_path):
    """Marked, changed, marked again: the second digest is the one that must match; a last mark with a stale digest parks nothing."""
    root = vault_with(tmp_path, "n1")
    old = sleep.digest(note(root, "n1"))
    edit(root, "n1")
    park_rows(root, (TODAY - timedelta(5), "n1", 0, old), (TODAY, "n1"))
    assert parked(root) == {"n1"}
    park_rows(root, (TODAY - timedelta(5), "n1"), (TODAY, "n1", 0, old))  # last mark carries the stale digest
    assert parked(root) == set()


def test_only_marks_of_the_present_content_set_the_rest(tmp_path):
    """E6/N5: a mark made before a change does not count towards the rest: 90 days from the second mark, not 112.5."""
    root = vault_with(tmp_path, "n1")
    old = sleep.digest(note(root, "n1"))
    edit(root, "n1")
    park_rows(root, (TODAY - timedelta(30), "n1", 0, old), (TODAY, "n1"))
    assert parked(root, TODAY + timedelta(90)) == {"n1"} and parked(root, TODAY + timedelta(91)) == set()


def test_the_rest_runs_from_the_mark_not_from_today(tmp_path):
    """A mark older than the rest is over: 91 days ago the note is back, 90 days ago it still rests."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY - timedelta(90), "n1"))
    assert parked(root) == {"n1"}
    park_rows(root, (TODAY - timedelta(91), "n1"))
    assert parked(root) == set()


def test_a_mark_dated_after_today_is_skipped(tmp_path):
    """E5/N6: a row from a clock that ran ahead does not park; it counts from its own day on."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY + timedelta(400), "n1"))
    assert parked(root) == set() and parked(root, TODAY + timedelta(399)) == set()
    assert parked(root, TODAY + timedelta(400)) == {"n1"} and parked(root, TODAY + timedelta(490)) == {"n1"}
    assert parked(root, TODAY + timedelta(491)) == set()


def test_a_future_row_does_not_extend_an_older_mark(tmp_path):
    """A mark 100 days old rests no more; a later row dated in the future must not revive it."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY - timedelta(100), "n1"), (TODAY + timedelta(10), "n1"))
    assert parked(root) == set()
    park_rows(root, (TODAY - timedelta(50), "n1"), (TODAY + timedelta(10), "n1"))
    assert parked(root) == {"n1"}  # the first one alone: 50 of 90 days


def test_a_second_mark_after_the_rest_rests_longer(tmp_path):
    """Marked again right after the first rest ran out: S = 90 (1 + 1 - 90/181) = 135.25 from the second mark."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY - timedelta(91), "n1"), (TODAY, "n1"))
    assert parked(root, TODAY + timedelta(135)) == {"n1"}
    assert parked(root, TODAY + timedelta(136)) == set()
    park_rows(root, (TODAY, "n1"))  # the first mark alone: 90 days
    assert parked(root, TODAY + timedelta(91)) == set()


def test_a_second_mark_while_parked_extends_the_rest_beyond_the_first(tmp_path):
    """Marked again after 30 days: S = 90 (1 + 30/120) = 112.5 from day 30, so parked on day 142 and past the first mark's day 90."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY, "n1"), (TODAY + timedelta(30), "n1"))
    assert parked(root, TODAY + timedelta(100)) == {"n1"}  # the first mark alone would be over
    assert parked(root, TODAY + timedelta(30 + 112)) == {"n1"}
    assert parked(root, TODAY + timedelta(30 + 113)) == set()


def test_marking_twice_on_one_day_adds_nothing(tmp_path):
    """Two marks on one day are one review day: the rest stays 90 days."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY, "n1"), (TODAY, "n1"))
    assert parked(root, TODAY + timedelta(90)) == {"n1"} and parked(root, TODAY + timedelta(91)) == set()


def test_a_second_mark_through_the_cli_after_the_rest(tmp_path, monkeypatch):
    """End to end: parked on day 0, planned again on day 91, parked again: S = 135.25 from day 91."""
    root = vault_with(tmp_path, "n1")
    plan_begin(root)
    mark(root, "n1")
    sb(root, "sleep", "done", "first")
    at(monkeypatch, TODAY + timedelta(91))
    plan_begin(root)
    assert listed(root) == {"n1"} and mark(root, "n1")[0] == 0
    sb(root, "sleep", "done", "second")
    assert usage.learned(Vault(root), TODAY)[""] == 90
    assert parked(root, TODAY + timedelta(91 + 135)) == {"n1"} and parked(root, TODAY + timedelta(91 + 136)) == set()


def test_the_rest_uses_the_given_base_stability(tmp_path):
    """s0 replaces the learned base (as in `locked`): with 30 days the note rests until day 30."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY, "n1"))
    s0 = {"": 30.0}
    assert parked(root, TODAY + timedelta(30), s0=s0) == {"n1"} and parked(root, TODAY + timedelta(31), s0=s0) == set()
    assert parked(root, TODAY + timedelta(31)) == {"n1"}  # without s0 the learned 90 days


def test_the_rest_follows_the_learned_vault_stability(tmp_path, monkeypatch):
    """With one log gap of 300 days the vault base is 195: the note rests 195 days."""
    monkeypatch.setattr(usage, "PRIOR_N", 1)
    root = init_vault(tmp_path)
    mk(root, "n1", 1, extra={"created": (TODAY - timedelta(1000)).isoformat(), "updated": (TODAY - timedelta(1000)).isoformat()})
    write_log(root, "usage.tsv", ((TODAY - timedelta(700)).isoformat(), "work", "show", "n1"))
    assert usage.learned(Vault(root), TODAY)[""] == pytest.approx(195)
    park_rows(root, (TODAY, "n1"))
    assert parked(root, TODAY + timedelta(195)) == {"n1"} and parked(root, TODAY + timedelta(196)) == set()


def test_parked_asks_for_the_learned_base_only_when_there_are_rows(tmp_path, monkeypatch):
    """N7: no file or no rows means no call of usage.learned (it reads the whole usage log)."""
    root = vault_with(tmp_path, "n1")

    def boom(*a, **k):
        raise AssertionError("usage.learned was called")
    monkeypatch.setattr(usage, "learned", boom)
    assert parked(root) == set()
    write_log(root, "unverifiable.tsv")
    assert parked(root) == set()
    write_log(root, "unverifiable.tsv", "", "\t\t")
    assert parked(root) == set()


def test_the_notes_argument_limits_the_result(tmp_path):
    """Notes passed in are the ones considered; one that is not among them falls out."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"), (TODAY, "n2"))
    notes = sleep.load(Vault(root))
    assert parked(root, notes=notes) == {"n1", "n2"}
    assert parked(root, notes={"n1": notes["n1"]}) == {"n1"} and parked(root, notes={}) == set()


def test_parked_follows_a_rename(tmp_path):
    """After `sb rename` the new slug rests; a note created later under the freed name does not inherit the mark."""
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    mark(root, "n1")
    assert sb(root, "rename", "n1", "n1b")[0] == 0
    assert parked(root) == {"n1b"}
    assert sb(root, "rename", "n1b", "n1c")[0] == 0  # a chain
    assert parked(root) == {"n1c"}
    mk(root, "n1", 9)  # a different note under the first name
    assert parked(root) == {"n1c"}


def test_a_rename_before_the_mark_is_not_followed_again(tmp_path):
    """The row stores the rename count at marking: a name freed and reused earlier belongs to the marked note."""
    root = vault_with(tmp_path, "n1")
    assert sb(root, "rename", "n1", "n1b")[0] == 0
    mk(root, "n1", 7)  # a new note named like the old one
    plan_begin(root)
    mark(root, "n1")
    assert parked(root) == {"n1"}


def test_an_archived_note_is_not_parked(tmp_path):
    """A note that is no longer in notes/ falls out of the set."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"), (TODAY, "n2"))
    assert sb(root, "archive", "n1", "--why", "faded")[0] == 0
    assert parked(root) == {"n2"}


def test_a_row_for_a_note_that_does_not_exist_is_ignored(tmp_path):
    """A slug without a note (never there, or deleted by hand) does not appear and does not crash."""
    root = vault_with(tmp_path, "n1")
    park_rows(root, (TODAY, "n1"))
    with open(root / ".sb" / "unverifiable.tsv", "a", encoding="utf-8") as f:
        f.write(f"{TODAY}\tghost\twhy\t0\tabcdef\n")
    assert parked(root) == {"n1"}


def test_broken_rows_are_skipped(tmp_path):
    """An unreadable date, short rows, blank lines and a row without a rename count do not crash `parked`, the plan or stats."""
    root = vault_with(tmp_path, "n1", "n2")
    dig = sleep.digest(note(root, "n1"))
    write_log(root, "unverifiable.tsv", f"not-a-date\tn1\twhy\t0\t{dig}", "short", "", "\t\t\t\t", f"{TODAY}", f"{TODAY}\tn1")
    assert parked(root) == set()  # no row with a readable date and a digest that fits
    write_log(root, "unverifiable.tsv", f"not-a-date\tn1\twhy\t0\t{dig}", "short", "", f"{TODAY}\tn1\twhy\tx\t{dig}")  # unreadable rename count
    assert parked(root) == {"n1"}
    assert sb(root, "sleep", "plan")[0] == 0 and sb(root, "stats")[0] == 0


# ---- N4 restore --force -----------------------------------------------------------------------------------------

def test_unverify_takes_a_parked_note_out_of_the_parking(tmp_path):
    """A snapshot row of '-' (the note must be checked again) beats the parking, though the text is unchanged."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"), (TODAY, "n2"))
    sleep.unverify(Vault(root), "n1")
    assert parked(root) == {"n2"} and sleep.backlog(Vault(root), TODAY)["verify"] == 1


def test_a_note_archived_as_wrong_and_restored_with_force_is_checked_again(tmp_path):
    """E3: parked, archived as wrong, restored with --force: it is no longer parked and counts in the backlog."""
    root = vault_with(tmp_path, "n0", "n1")
    plan_begin(root)
    assert mark(root, "n0")[0] == 0
    sb(root, "sleep", "done", "x")
    assert sb(root, "archive", "n0", "--why", "wrong", "--reason", "it is wrong")[0] == 0
    assert sb(root, "restore", "n0", "--force")[0] == 0
    v = Vault(root)
    assert "n0" not in sleep.parked(v, TODAY) and sleep.backlog(v, TODAY)["verify"] == 2


# ---- 4. backlog and stats ---------------------------------------------------------------------------------------

def test_backlog_and_stats_do_not_count_parked_notes(tmp_path):
    """Three unchecked notes, one parked: verify 2 in backlog, in stats and in the waiting total."""
    root = vault_with(tmp_path, "n1", "n2", "n3")
    assert sleep.backlog(Vault(root), TODAY)["verify"] == 3
    plan_begin(root)
    mark(root, "n1")
    assert sleep.backlog(Vault(root), TODAY)["verify"] == 2
    sb(root, "sleep", "done", "parked one")
    assert sleep.backlog(Vault(root), TODAY)["verify"] == 2
    line = next(ln for ln in sb(root, "stats")[1].splitlines() if ln.startswith("waiting for a sleep"))
    assert line.startswith("waiting for a sleep: 2 (") and "verify 2" in line


def test_a_note_counts_again_when_changed_or_after_its_rest(tmp_path):
    """Back in the backlog after an edit and after day 91, not before."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"), (TODAY, "n2"))
    v = Vault(root)
    assert sleep.backlog(v, TODAY)["verify"] == 0 and sleep.backlog(v, TODAY + timedelta(90))["verify"] == 0
    assert sleep.backlog(v, TODAY + timedelta(91))["verify"] == 2
    edit(root, "n1")
    assert sleep.backlog(v, TODAY)["verify"] == 1


def test_parked_notes_do_not_make_a_sleep_due(tmp_path, monkeypatch):
    """The 'sleep due' line counts the verify queue: parked notes stay out of it, so they cannot keep a sleep due."""
    monkeypatch.setattr(usage, "B0", 2)
    root = vault_with(tmp_path, "n1", "n2", "n3")
    write_log(root, "sleep.tsv", ["2026-11-07T12:00", 0, 0, 0, 0, 0, 0, "s"])  # a day ago, no plan size: the budget stays 2
    assert "sleep due: 3 items waiting" in sb(root, "stats")[1]
    park_rows(root, (TODAY, "n1"), (TODAY, "n2"))  # one open note is below the budget of 2
    assert "sleep due" not in sb(root, "stats")[1]
    assert sleep.due(Vault(root), 3, TODAY) is None


# ---- 5. plan ----------------------------------------------------------------------------------------------------

def test_the_plan_lists_a_parked_note_neither_as_new_nor_as_active(tmp_path):
    """Unchecked and parked: not under verify (a), not among the most active either; the header counts one item less."""
    root = vault_with(tmp_path, "open-a", "open-b", "parked-note")
    assert "touched: 3 of 40" in sb(root, "sleep", "plan")[1]
    park_rows(root, (TODAY, "parked-note"))
    out = sb(root, "sleep", "plan")[1]
    section = out.split("## 4.")[1].split("## 5.")[0]
    assert section.startswith(" Verify: 2 new or changed, 0 most active")
    assert sorted(verify_slugs(section)) == ["open-a", "open-b"] and "touched: 2 of 40" in out


def test_a_checked_parked_note_is_not_among_the_most_active_either(tmp_path):
    """Verify (b) draws on checked notes: a parked one, though checked and used, is left out of it."""
    root = vault_with(tmp_path, "n1", "n2", "n3")
    assert sb(root, "sleep", "baseline")[0] == 0
    assert sorted(verify_slugs(verify_section(root))) == ["n1", "n2", "n3"]  # all three are the most active
    park_rows(root, (TODAY, "n2"))
    section = verify_section(root)
    assert section.startswith(" Verify: 0 new or changed, 2 most active") and sorted(verify_slugs(section)) == ["n1", "n3"]


def test_a_changed_parked_note_is_planned_again(tmp_path):
    """After an edit the note is back under verify (a)."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"))
    assert verify_slugs(verify_section(root)) == ["n2"]
    edit(root, "n1")
    section = verify_section(root)
    assert section.startswith(" Verify: 2 new or changed") and sorted(verify_slugs(section)) == ["n1", "n2"]


def test_a_note_whose_rest_is_over_is_planned_again(tmp_path, monkeypatch):
    """Day 90 still rests, day 91 plans it."""
    root = vault_with(tmp_path, "n1", "n2")
    park_rows(root, (TODAY, "n1"))
    at(monkeypatch, TODAY + timedelta(90))
    assert verify_slugs(verify_section(root)) == ["n2"]
    at(monkeypatch, TODAY + timedelta(91))
    assert sorted(verify_slugs(verify_section(root))) == ["n1", "n2"]


def test_a_second_plan_after_parking_leaves_the_parked_note_out(tmp_path):
    """Parked after the first plan: a second plan no longer lists it, while the sleep's work and size stay those of the first plan."""
    root = vault_with(tmp_path, "n1", "n2")
    plan_begin(root)
    assert listed(root) == {"n1", "n2"}
    mark(root, "n1")
    assert verify_slugs(verify_section(root)) == ["n2"]
    v = Vault(root)
    assert sleep.remembered(v, "work") == {"verify:n1", "verify:n2"} and sleep.remembered(v, "size") == {"2"}
    assert left(root) == (1, 1, 1)  # the plan of 2 shrinks by the parked one; n2 is open


# ---- 6. leftover and sleep done (N3) ----------------------------------------------------------------------------

def planned(tmp_path, *slugs):
    root = vault_with(tmp_path, *slugs)
    plan_begin(root)
    return root


def test_a_planned_note_marked_unverifiable_is_no_rest_and_no_size(tmp_path):
    """Plan of one verify note, agent parks it: size 0, left 0, parked 1; sleep.tsv gets 0, 0, 1 and the closing line says so."""
    root = planned(tmp_path, "n1")
    assert sleep.remembered(Vault(root), "work") == {"verify:n1"} and left(root) == (1, 1, 0)
    assert mark(root, "n1")[0] == 0 and left(root) == (0, 0, 1)
    code, out, _ = sb(root, "sleep", "done", "parked it")
    assert code == 0 and "left undone" not in out and "1 parked as unverifiable" in out
    assert sleep_rows(root)[-1][8:] == ["0", "0", "1"]


def test_without_marking_the_note_is_left(tmp_path):
    """Control: the same plan without a mark leaves 1 of 1, parks nothing, and says nothing about parking."""
    root = planned(tmp_path, "n1")
    code, out, _ = sb(root, "sleep", "done", "did nothing")
    assert code == 0 and out.splitlines()[0].endswith("; 1 of 1 planned items left undone")
    assert "parked as unverifiable" not in out
    assert sleep_rows(root)[-1][8:] == ["1", "1", "0"]


def test_a_note_marked_and_then_changed_is_left(tmp_path):
    """Changed after the mark it is no longer parked: size and left are back, parked is 0."""
    root = planned(tmp_path, "n1")
    mark(root, "n1")
    assert left(root) == (0, 0, 1)
    edit(root, "n1")
    assert left(root) == (1, 1, 0)
    code, out, _ = sb(root, "sleep", "done", "changed after")
    assert code == 0 and "1 of 1 planned items left undone" in out and "parked as unverifiable" not in out
    assert sleep_rows(root)[-1][8:] == ["1", "1", "0"]


def test_only_the_marked_ones_leave_size_and_count(tmp_path):
    """Two planned notes, one parked, one simply not reached: size 1, left 1, parked 1."""
    root = planned(tmp_path, "n1", "n2")
    mark(root, "n1")
    assert left(root) == (1, 1, 1)
    out = sb(root, "sleep", "done", "one parked")[1]
    assert "1 of 1 planned items left undone" in out and "1 parked as unverifiable" in out


def test_a_parked_and_a_verified_note(tmp_path):
    """One note parked, another verified: the plan of 2 is 1 item big and nothing is left."""
    root = planned(tmp_path, "n1", "n2")
    mark(root, "n1")
    assert sb(root, "sleep", "verified", "n2")[0] == 0
    assert left(root) == (1, 0, 1)


def test_a_note_parked_and_then_verified_counts_as_verified(tmp_path):
    """Parked is only counted while the note is not verified: a verified one is a normal item."""
    root = planned(tmp_path, "n1")
    mark(root, "n1")
    assert sb(root, "sleep", "verified", "n1")[0] == 0
    assert left(root) == (1, 0, 0)


def test_leftover_follows_a_rename_of_a_parked_note(tmp_path):
    """Parked, then renamed during the sleep: still parked, not left."""
    root = planned(tmp_path, "n1")
    mark(root, "n1")
    assert sb(root, "rename", "n1", "n1b")[0] == 0
    assert left(root) == (0, 0, 1)


def test_a_sleep_without_a_plan_logs_three_zeros(tmp_path):
    """Nothing planned, nothing parked: the row still has the eleventh column."""
    root = vault_with(tmp_path, "n1")
    begin(root)
    code, out, _ = sb(root, "sleep", "done", "nothing planned")
    assert code == 0 and "parked as unverifiable" not in out and "left undone" not in out
    assert sleep_rows(root)[-1][8:] == ["0", "0", "0"]


def test_a_note_parked_in_an_earlier_sleep_is_no_part_of_the_next(tmp_path, monkeypatch):
    """The next sleep does not plan it, so its size is 0 and nothing is left or parked."""
    root = planned(tmp_path, "n1")
    mark(root, "n1")
    sb(root, "sleep", "done", "first")
    at(monkeypatch, TODAY + timedelta(1))
    begin(root)
    sb(root, "sleep", "plan")
    assert left(root) == (0, 0, 0)
    code, out, _ = sb(root, "sleep", "done", "second")
    assert code == 0 and "left undone" not in out and "parked as unverifiable" not in out
    assert sleep_rows(root)[-1][8:] == ["0", "0", "0"]


def test_a_mixed_sleep_parked_verified_and_open(tmp_path, monkeypatch):
    """Plan of four: one parked, one verified, two open: size 3, left 2, parked 1 in the row and the closing line."""
    monkeypatch.setattr(usage, "B0", 4)
    monkeypatch.setattr(usage, "B_MIN", 1)
    root = planned(tmp_path, "m1", "m2", "m3", "m4")
    assert listed(root) == {"m1", "m2", "m3", "m4"} and left(root) == (4, 4, 0)
    assert mark(root, "m1")[0] == 0 and sb(root, "sleep", "verified", "m2")[0] == 0
    assert left(root) == (3, 2, 1)
    code, out, _ = sb(root, "sleep", "done", "mixed")
    assert code == 0 and "2 of 3 planned items left undone" in out and "1 parked as unverifiable" in out
    assert sleep_rows(root)[-1][8:] == ["3", "2", "1"]
    assert usage.budget(Vault(root)) == 1  # the parked note still filled the plan of 4: one of 3 done sets the budget
    assert parked(root) == {"m1"}


# ---- 7. the budget ----------------------------------------------------------------------------------------------

@pytest.fixture
def small_budget(monkeypatch):
    """A budget of 3 that a plan of three notes fills, so the plan size says something about the budget."""
    monkeypatch.setattr(usage, "B0", 3)
    monkeypatch.setattr(usage, "B_MIN", 1)


def one_sleep(root, monkeypatch, day, parks):
    """A sleep on a day: begin, plan, park the given slugs (or all listed ones with 'all'), done; returns what `sleep done` printed."""
    at(monkeypatch, day)
    plan_begin(root)
    for s in sorted(listed(root)) if parks == "all" else parks:
        assert mark(root, s)[0] == 0
    code, out, _ = sb(root, "sleep", "done", "s")
    assert code == 0
    return out


def test_five_sleeps_that_only_park_leave_the_budget_where_it_is(tmp_path, monkeypatch, small_budget):
    """Each sleep plans three new notes nobody can check and parks all: size 0, left 0, parked 3; the budget stays 3 and does not grow."""
    root = vault_with(tmp_path, "w0", "w1", "w2")
    v = Vault(root)
    for k in range(5):
        if k:
            for i in range(3):
                mk(root, f"w{3 * k + i}", 3 * k + i)
        out = one_sleep(root, monkeypatch, TODAY + timedelta(k), "all")
        assert sleep_rows(root)[-1][8:] == ["0", "0", "3"] and "3 parked as unverifiable" in out and "left undone" not in out, k
        assert usage.budget(v) == 3, k


def test_the_same_sleeps_without_parking_pull_the_budget_down(tmp_path, monkeypatch, small_budget):
    """Control: the open notes count as left and the budget falls to its floor."""
    root = vault_with(tmp_path, "u1", "u2", "u3")
    one_sleep(root, monkeypatch, TODAY, [])
    assert sleep_rows(root)[-1][8:] == ["3", "3", "0"] and usage.budget(Vault(root)) == 1


def test_a_note_the_agent_lacked_time_for_still_counts_as_left(tmp_path, monkeypatch, small_budget):
    """Two parked, one simply left open: size 1, left 1, parked 2; the parked notes filled the plan, so the open one
    counts and the budget falls to its floor."""
    root = vault_with(tmp_path, "u1", "u2", "u3")
    out = one_sleep(root, monkeypatch, TODAY, ["u1", "u2"])
    assert "1 of 1 planned items left undone" in out and "2 parked as unverifiable" in out
    assert sleep_rows(root)[-1][8:] == ["1", "1", "2"]
    assert usage.budget(Vault(root)) == 1


def test_parking_cannot_hide_an_unfinished_full_plan(tmp_path, monkeypatch):
    """A plan of 15 with 3 parked and 9 of 12 left open is a full plan left unfinished: the budget falls to what was
    done (3, floored at B_MIN), as a plan of 12 below a budget of 15 would otherwise say nothing."""
    monkeypatch.setattr(usage, "B0", 15)
    root = vault_with(tmp_path)
    write_log(root, "sleep.tsv", ["2026-11-01T12:00", 0, 0, 0, 0, 0, 0, "s", 12, 9, 3])
    assert usage.budget(Vault(root)) == usage.B_MIN
    write_log(root, "sleep.tsv", ["2026-11-01T12:00", 0, 0, 0, 0, 0, 0, "s", 12, 0, 3])
    assert usage.budget(Vault(root)) == round(usage.B0 * 1.25)


def test_parked_notes_stay_out_of_the_next_plans_size(tmp_path, monkeypatch, small_budget):
    """After parking, the next sleep plans only what is not resting: three notes, two parked, a plan of one."""
    root = vault_with(tmp_path, "u1", "u2", "u3")
    one_sleep(root, monkeypatch, TODAY, ["u1", "u2"])
    at(monkeypatch, TODAY + timedelta(1))
    begin(root)
    out = sb(root, "sleep", "plan")[1]
    assert "touched: 1 of" in out and sorted(verify_slugs(out.split("## 4.")[1].split("## 5.")[0])) == ["u3"]
