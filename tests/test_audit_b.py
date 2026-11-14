"""Logic audit, group B (usage, vault, schema, links): each finding as a test of the intended behaviour, plus the
direct tests that were missing for links, Vault.bib/scan/find, schema.check, sleeping, creates_today and stamp."""
from datetime import date, datetime, timedelta

import pytest
from helpers import add_note, init_vault, sb, write_log

from scientific_brain import links, usage
from scientific_brain.index import Index
from scientific_brain.schema import check
from scientific_brain.vault import Note, ParseError, SbError, Vault, parse_value

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SB_NO_UPDATE", "1")
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def ago(n):
    return TODAY - timedelta(days=n)


def put(root, slug, made, typ="finding", loc="notes", project="p", **kw):
    add_note(root, slug, loc=loc, project=project, type=typ,
             extra={"created": ago(made).isoformat(), "updated": ago(made).isoformat(), **kw.pop("extra", {})}, **kw)


def errors(root, only=None):
    return [i for i in check(Vault(root), only)[0] if i.level == "error"]


# ---- usage.py ---------------------------------------------------------------------------------------------------

def test_B1_gap_weights_are_continuous_when_a_second_gap_of_a_note_starts_to_count(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 1000)
    write_log(root, "usage.tsv", (ago(366), "work", "show", "a"), (ago(300), "work", "show", "a"))
    v = Vault(root)
    yesterday = usage.learned(v, TODAY - timedelta(days=1))[""]  # the second gap starts 365 days ago: not yet counted
    today = usage.learned(v, TODAY)[""]  # 366 days: counted with a weight of 1/365
    assert abs(today - yesterday) < 1.0, (yesterday, today)


def test_B1_gap_weights_never_jump_and_a_note_weighs_at_most_one(tmp_path):
    """design.md: each gap phases in, what is learned moves smoothly, each note weighs one (gutachten 2026-11-14)."""
    root = init_vault(tmp_path)
    put(root, "a", 1000)  # days 1000, 900, 365, 2: two full gaps, the third starts to count tomorrow
    put(root, "b", 1000)  # used every day from 899 to 401 days ago: many gaps, still weight 1 in all
    rows = [(ago(d), "work", "show", "a") for d in (900, 365, 2)] + [(ago(d), "work", "show", "b") for d in range(899, 400, -1)]
    write_log(root, "usage.tsv", *rows)
    g = usage.gaps(Vault(root), TODAY)["finding"]
    assert sorted(w for d, w in g if d in (100, 365))[-2:] == pytest.approx([0.5, 0.5])  # a: two full gaps share 1
    assert sum(w for _, w in g) == pytest.approx(2.0)  # a and b weigh 1 each, however often b was used
    nxt = usage.gaps(Vault(root), TODAY + timedelta(1))["finding"]
    assert max(w for d, w in nxt if d == 100) == pytest.approx(0.5, abs=0.01)  # a third gap phasing in moves a's weights by little


def test_B12_a_sleep_row_with_a_utc_offset_does_not_crash_sleep_due(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", (ago(1).isoformat() + "T12:00Z", 0, 0, 0, 0, 0, 0, "s"))
    usage.sleep_due(Vault(root), 5, 0, TODAY)  # must not raise


@pytest.mark.parametrize("zone", ["Z", "+00:00", "+05:30", "-08:00"])
def test_B12_a_zone_is_converted_to_local_naive_time(tmp_path, zone):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", (f"{ago(2).isoformat()}T09:00{zone}", 0, 0, 0))
    at = datetime.fromisoformat(f"{ago(2).isoformat()}T09:00{zone.replace('Z', '+00:00')}")
    got = usage.last_sleep_at(Vault(root))
    assert got.tzinfo is None and got == at.astimezone().replace(tzinfo=None)


def test_B12_a_row_in_the_future_is_a_wrong_clock_and_does_not_count(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", (ago(3).isoformat() + "T12:00", 0, 0, 0), (ago(-5).isoformat() + "T12:00", 0, 0, 0))
    v = Vault(root)
    assert usage.last_sleep_at(v) == datetime(2026, 11, 5, 12, 0) and usage.last_sleep(v) == ago(3)
    write_log(root, "sleep.tsv", (ago(-5).isoformat() + "T12:00", 0, 0, 0))
    assert usage.last_sleep_at(v) is None and "never slept" in usage.sleep_due(v, 4, 0, TODAY)


def test_last_sleep_at_takes_a_date_only_row_from_midnight_and_skips_garbage(tmp_path):
    root = init_vault(tmp_path)
    write_log(root, "sleep.tsv", "2026-11-04\tx\ty", "not a date\tx\ty")
    assert usage.last_sleep_at(Vault(root)) == datetime(2026, 11, 4)


def test_stamp_is_noon_under_sb_today_and_the_clock_otherwise(monkeypatch):
    assert usage.stamp(TODAY) == "2026-11-08T12:00"
    monkeypatch.delenv("SB_TODAY")
    s = datetime.fromisoformat(usage.stamp(date(2026, 1, 2)))
    assert s.date() == date(2026, 1, 2) and s.second == 0 and s.microsecond == 0


def test_creates_today_counts_only_todays_work_creates(tmp_path):
    root = init_vault(tmp_path)
    t = TODAY.isoformat()
    write_log(root, "usage.tsv", (t, "work", "create", "a"), (t, "work", "create", "b"), (t, "work", "show", "a"),
              (t, "sleep", "create", "c"), (ago(1), "work", "create", "d"), "broken line")
    assert usage.creates_today(Vault(root), TODAY) == 2
    assert usage.creates_today(Vault(init_vault(tmp_path, "w")), TODAY) == 0


def test_sleeping_marker_unreadable_empty_stale_or_current(tmp_path):
    root = init_vault(tmp_path)
    v, m = Vault(root), root / ".sb" / "sleeping"
    assert not usage.sleeping(v, TODAY)  # no marker
    m.write_bytes(b"\xff\xfe\x00")  # not UTF-8
    assert not usage.sleeping(v, TODAY)
    m.write_text("", encoding="utf-8")
    assert not usage.sleeping(v, TODAY)
    m.write_text("garbage\n", encoding="utf-8")
    assert not usage.sleeping(v, TODAY)
    m.unlink()
    m.mkdir()  # a directory instead of a file
    assert not usage.sleeping(v, TODAY)
    m.rmdir()
    m.write_text(ago(1).isoformat() + "\nx", encoding="utf-8")
    assert usage.sleeping(v, TODAY)
    m.write_text(ago(2).isoformat() + "\n", encoding="utf-8")
    assert not usage.sleeping(v, TODAY)


# ---- vault.py ---------------------------------------------------------------------------------------------------

def test_B3_a_heading_line_without_text_does_not_take_the_next_line_as_its_heading():
    n = Note("notes/p/x.md", {}, {}, "Gist.\n\n##\nRejected\n\n## Real  \n", 1, "")
    assert n.headings == {"Real"}


def test_B4_unbalanced_quote_in_a_list_item_is_an_error_not_a_dropped_item():
    with pytest.raises(ParseError):
        parse_value("sources", '[a"b, c]', "x")
    with pytest.raises(ParseError):
        parse_value("sources", '["a"b]', "x")


def test_B4_valid_lists_still_parse():
    assert parse_value("sources", '[a, "b, c", d e,]', "x") == ["a", "b, c", "d e"]
    assert parse_value("sources", "[]", "x") == [] and parse_value("sources", "[ , ]", "x") == []


def test_B5_a_dangling_md_symlink_is_skipped_by_scan_and_every_command(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 1)
    (root / "notes" / "p" / "ghost.md").symlink_to(root / "nowhere.md")
    assert list(Vault(root).scan("notes")) == ["notes/p/a.md"]
    assert Vault(root).find("ghost") is None
    assert sb(root, "check")[0] in (0, 1)  # no traceback, whatever the verdict


def test_B6_sb_toml_with_invalid_utf8_gives_an_sb_error(tmp_path):
    root = init_vault(tmp_path)
    (root / "sb.toml").write_bytes(b"# caf\xe9\nprojects = []\n")
    with pytest.raises(SbError):
        Vault(root)


def test_scan_skips_dotfiles_non_md_and_loose_files_follows_valid_symlinks(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 1)
    p = root / "notes" / "p"
    (p / ".hidden.md").write_text("x")
    (p / "a.txt").write_text("x")
    (root / "notes" / "loose.md").write_text("x")  # directly in notes/, no project folder
    (root / "notes" / ".dot").mkdir()
    (root / "notes" / ".dot" / "b.md").write_text("x")
    (p / "sub").mkdir()
    (p / "sub" / "deep.md").write_text("x")  # one level too deep
    (p / "real-target.txt").write_text("---\n---\n")
    (p / "linked.md").symlink_to(p / "real-target.txt")
    got = Vault(root).scan("notes")
    assert sorted(got) == ["notes/p/a.md", "notes/p/linked.md"]
    assert all(isinstance(t, tuple) and len(t) == 2 for t in got.values()) and Vault(root).scan("nothing") == {}


def test_find_matches_whole_slugs_not_suffixes_and_prefers_notes(tmp_path):
    root = init_vault(tmp_path)
    put(root, "ftcs-limit", 1)
    put(root, "limit", 1, project="q")
    put(root, "old", 1, loc="archive")
    v = Vault(root)
    assert v.find("limit") == "notes/q/limit.md"  # not ftcs-limit
    assert v.find("imit") is None and v.find("ftcs") is None
    assert v.find("old") == "archive/p/old.md" and v.find("old", ("notes",)) is None
    put(root, "old", 1, project="z")  # same slug in notes and archive: notes first
    assert v.find("old") == "notes/z/old.md"


def bib_vault(tmp_path, text, names=("r.bib",)):
    root = init_vault(tmp_path)
    (root / "sb.toml").write_text("bibtex = [%s]\n" % ", ".join(f'"{n}"' for n in names), encoding="utf-8")
    if text is not None:
        (root / names[0]).write_text(text, encoding="utf-8")
    return Vault(root)


def test_bib_reads_title_author_year_in_braces_quotes_and_bare_numbers(tmp_path):
    v = bib_vault(tmp_path, '@Article{Key1,\n  title = {A {B} c},\n  Author = "Me, Myself",\n  year = 2020\n}\n'
                            '@book{key2, title={two\n lines}, year={1999}}\n')
    assert v.bib() == {"Key1": {"title": "A {B} c", "author": "Me, Myself", "year": "2020"},
                       "key2": {"title": "two lines", "year": "1999"}}


def test_bib_keys_are_case_sensitive_and_a_missing_or_second_file_is_handled(tmp_path):
    v = bib_vault(tmp_path, "@a{Key,title={x}}\n@a{key,title={y}}\n", names=("r.bib", "missing.bib"))
    assert v.bib() == {"Key": {"title": "x"}, "key": {"title": "y"}}
    assert bib_vault(tmp_path / "n", None).bib() == {}  # no file at all


def test_bib_ignores_string_and_preamble_entries(tmp_path):
    v = bib_vault(tmp_path, '@string{foo = "bar"}\n@Preamble{ "x, y" }\n@a{k, title={t}}\n')
    assert v.bib() == {"k": {"title": "t"}}


def test_bib_reads_a_title_with_brace_depth_three(tmp_path):
    assert bib_vault(tmp_path, "@a{k, title = {A {B {C}} d}, year = 2020}\n").bib()["k"]["title"] == "A {B {C}} d"


def test_bib_ignores_a_comment_entry(tmp_path):
    assert bib_vault(tmp_path, "@comment{x, y = {z}}\n@a{k, title={t}}\n").bib() == {"k": {"title": "t"}}


# ---- schema.py --------------------------------------------------------------------------------------------------

def test_B7_duplicate_ref_is_reported_when_only_the_later_note_is_checked(tmp_path):
    root = init_vault(tmp_path)
    add_note(root, "aaa-src", type="source", project="p", extra={"ref": "10.1000/xyz"})
    add_note(root, "zzz-src", type="source", project="p", extra={"ref": "10.1000/xyz"})
    issues, n = check(Vault(root), {"notes/p/zzz-src.md"})
    assert n == 1 and [i.msg for i in issues if "already used" in i.msg] == ["ref '10.1000/xyz' already used by notes/p/aaa-src.md"]
    first = [i.msg for i in check(Vault(root), {"notes/p/aaa-src.md"})[0] if "already used" in i.msg]
    assert first == ["ref '10.1000/xyz' already used by notes/p/zzz-src.md"]  # the hook flags whichever note it checks


def test_B7_full_check_still_reports_the_duplicate_once(tmp_path):
    root = init_vault(tmp_path)
    add_note(root, "aaa-src", type="source", project="p", extra={"ref": "10.1000/xyz"})
    add_note(root, "zzz-src", type="source", project="p", extra={"ref": "10.1000/xyz"})
    dup = [i for i in check(Vault(root))[0] if "already used" in i.msg]
    assert len(dup) == 1 and "zzz-src" in dup[0].where


def test_check_flags_a_bad_slug_format(tmp_path):
    root = init_vault(tmp_path)
    for slug in ("Bad_Name", "-lead", "trail-", "dou--ble"):
        put(root, slug, 1)
    bad = {i.where.split(":")[0] for i in errors(root) if "must be lower-case" in i.msg}
    assert bad == {f"notes/p/{s}.md" for s in ("Bad_Name", "-lead", "trail-", "dou--ble")}
    put(root, "fine-1", 1)
    assert "notes/p/fine-1.md" not in {i.where.split(":")[0] for i in errors(root)}


def test_check_slug_duplicate_is_global_even_with_only(tmp_path):
    root = init_vault(tmp_path)
    put(root, "same", 1, project="p")
    put(root, "same", 1, project="q", loc="archive")
    msgs = [i.msg for i in check(Vault(root), {"archive/q/same.md"})[0] if "also used" in i.msg]
    assert msgs == ["slug 'same' is also used by notes/p/same.md"]


def test_check_link_to_unknown_archived_and_superseded_cycle(tmp_path):
    root = init_vault(tmp_path)
    put(root, "live", 1, body="\n## Links\n- [[ghost]] x\n- [[gone]] y\n")
    put(root, "gone", 1, loc="archive")
    put(root, "old1", 1, loc="archive", extra={"archived_why": "superseded", "superseded_by": "[old2]"})
    put(root, "old2", 1, loc="archive", extra={"archived_why": "superseded", "superseded_by": "[old1]"})
    iss = check(Vault(root))[0]
    assert any(i.level == "error" and "unknown note [[ghost]]" in i.msg for i in iss)
    assert any(i.level == "warning" and "archived note [[gone]]" in i.msg for i in iss)
    cyc = [i for i in iss if "cycle" in i.msg]
    assert len(cyc) == 1 and ("old1 -> old2 -> old1" in cyc[0].msg or "old2 -> old1 -> old2" in cyc[0].msg)


def test_check_superseded_by_chain_without_cycle_and_missing_target(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 1, loc="archive", extra={"archived_why": "superseded", "superseded_by": "[b]"})
    put(root, "b", 1, loc="archive", extra={"archived_why": "superseded", "superseded_by": "[c]"})
    put(root, "c", 1)
    assert not errors(root)
    put(root, "d", 1, loc="archive", extra={"archived_why": "superseded", "superseded_by": "[nope]"})
    assert any("target 'nope' does not exist" in i.msg for i in errors(root))


# ---- links.py ---------------------------------------------------------------------------------------------------

def _linked_vault(tmp_path, link):
    root = init_vault(tmp_path)
    put(root, "old-name", 5)
    put(root, "other", 5, body=f"\n## Links\n- {link} why\n")
    return root


def test_B8_rename_rewrites_a_link_with_padding_inside_the_brackets(tmp_path):
    root = _linked_vault(tmp_path, "[[ old-name ]]")
    assert sb(root, "rename", "old-name", "new-name")[0] == 0
    assert "[[new-name]]" in (root / "notes" / "p" / "other.md").read_text()
    assert not errors(root)


def test_B9_archive_fix_links_removes_a_padded_link(tmp_path):
    root = _linked_vault(tmp_path, "[[ old-name ]]")
    assert sb(root, "archive", "old-name", "--why", "faded", "--fix-links")[0] == 0
    assert "old-name" not in (root / "notes" / "p" / "other.md").read_text()


@pytest.mark.parametrize("line", ["archived_why:  wrong", 'archived_why: "wrong"', "archived_why: wrong "])
def test_B10_restore_of_a_wrong_note_needs_force_whatever_the_spacing_or_quoting(tmp_path, line):
    root = init_vault(tmp_path)
    add_note(root, "bad", loc="archive", project="p", extra={"archived_why": "wrong", "archived_reason": "false"})
    f = root / "archive" / "p" / "bad.md"
    f.write_text(f.read_text().replace("archived_why: wrong", line), encoding="utf-8")
    assert not errors(root)
    with pytest.raises(SbError):
        links.restore(Vault(root), "bad")
    links.restore(Vault(root), "bad", force=True)
    assert (root / "notes" / "p" / "bad.md").is_file()


def test_rewrite_changes_body_links_and_superseded_by_but_not_the_frontmatter_text():
    text = "---\ntitle: [[old]] in a title\nsuperseded_by: [old, other]\n---\n\nSee [[old]], [[ old ]] and [[older]].\n"
    got = links.rewrite(text, "old", "new")
    assert got == "---\ntitle: [[old]] in a title\nsuperseded_by: [new, other]\n---\n\nSee [[new]], [[new]] and [[older]].\n"
    assert links.rewrite(got, "absent", "x") == got


def test_unlink_drops_link_bullets_in_the_links_section_and_plainifies_elsewhere():
    text = ("---\ntitle: t\n---\n\nSee [[x]] here.\n\n## Links\n- [[x]] why\n  - [[ x ]] padded\n- [[y]] keep\n"
            "\n## Other\n- [[x]] stays as text\n")
    got = links.unlink(text, "x")
    assert got == "---\ntitle: t\n---\n\nSee x here.\n\n## Links\n- [[y]] keep\n\n## Other\n- x stays as text\n"


def test_fm_end_finds_the_closing_line_and_gives_zero_without_one():
    assert links.fm_end(["---", "a: b", "---", "x", "---"]) == 2
    assert links.fm_end(["---", "a: b", "  ---  "]) == 2
    assert links.fm_end(["---", "a: b"]) == 0 and links.fm_end([]) == 0


def test_backlinks_by_location_sorted_and_an_unreadable_file_is_skipped(tmp_path):
    root = init_vault(tmp_path)
    put(root, "target", 5)
    put(root, "zeta", 5, body="\nSee [[target]].\n")
    put(root, "alpha", 5, body="\nSee [[ target ]].\n")
    put(root, "arch", 5, loc="archive", body="\nSee [[target]].\n")
    put(root, "other", 5, body="\nSee [[target-two]].\n")
    v = Vault(root)
    idx = Index(v)
    idx.refresh()
    assert [s for s, _ in links.backlinks(v, idx, "target")] == ["alpha", "arch", "zeta"]
    assert [s for s, _ in links.backlinks(v, idx, "target", "notes")] == ["alpha", "zeta"]
    assert links.backlinks(v, idx, "nobody") == []
    (root / "notes" / "p" / "zeta.md").write_bytes(b"\xff\xfe broken [[target]]")  # unreadable as UTF-8
    assert [s for s, _ in links.backlinks(v, idx, "target")] == ["alpha", "arch"]


def test_rename_rejects_a_bad_slug_a_missing_note_and_a_taken_name(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 5)
    put(root, "b", 5)
    v = Vault(root)
    for old, new in (("a", "Bad Name"), ("zz", "c"), ("a", "b")):
        with pytest.raises(SbError):
            links.rename(v, old, new)
    assert (root / "notes" / "p" / "a.md").is_file()


def test_rename_back_a_to_b_to_a_folds_in_renames_and_keeps_links_valid(tmp_path):
    root = init_vault(tmp_path)
    put(root, "a", 5)
    put(root, "user", 5, body="\n## Links\n- [[a]] why\n")
    assert sb(root, "rename", "a", "b")[0] == 0 and sb(root, "rename", "b", "a")[0] == 0
    assert "[[a]]" in (root / "notes" / "p" / "user.md").read_text() and not errors(root)
    assert usage.renames(Vault(root)) == {"b": "a"}
    write_log(root, "renames.tsv", ("a", "b", "2026-11-01", 0, 0), ("b", "c", "2026-11-02", 0, 0), ("c", "a", "2026-11-03", 0, 0))
    assert usage.renames(Vault(root)) == {"b": "a", "c": "a"}
