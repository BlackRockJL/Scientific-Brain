"""Audit group C (index.py, review.py): coverage vs. limit, prefix, sync log, check --changed, NFC, review counts,
ranking, short terms, FTS syntax, review sections."""
import os
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import pytest
from helpers import add_note, init_vault, make_vault, sb

from scientific_brain import review
from scientific_brain.index import Index, prepare
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SB_NO_UPDATE", "1")
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def usage_rows(v):
    p = Path(v) / ".sb" / "usage.tsv"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def slugs(out):
    """Slugs of the hits in recall output, in order."""
    return [ln.split()[0] for ln in out.splitlines() if ln and not ln.startswith(" ") and "[" in ln and "]" in ln]


# --- index.py: findings c01-c05 ------------------------------------------------------------------

def test_c01_coverage_filter_does_not_lose_hits_to_the_limit(tmp_path):
    v = init_vault(tmp_path)
    for i in range(120):
        add_note(v, f"decoy-{i:03d}", cue="zeppelin zeppelin zeppelin", gist="Gist: short.")
    for i in range(300):
        add_note(v, f"fill-{i:03d}", cue="c", gist="Gist: walrus filler.")
    add_note(v, "target", cue="nothing", gist="Gist: short.", body="\n" + " ".join(f"w{j}x" for j in range(500)) + " zeppelin walrus\n")
    assert "target" in sb(v, "recall", "zeppelin", "walrus", "xyzzyq", "-n", "500")[1]
    assert "target" in sb(v, "recall", "zeppelin", "walrus", "xyzzyq")[1]  # also with the default limit 5
    assert "target" in sb(v, "recall", "zeppelin", "walrus", "xyzzyq", "-n", "1")[1]  # the only note with 2 of 3 terms


def test_c02_field_prefix_is_stripped_only_at_the_start_of_a_term():
    assert prepare(["somebody:said"])[0] == ["somebody:said"]
    assert prepare(["rescue:x"])[0] == ["rescue:x"]
    assert prepare(["body:Fatigue"])[0] == ["fatigue"] and prepare(["cue:Fatigue"])[0] == ["fatigue"]


def test_c03_unparsable_note_is_not_logged_as_edited(tmp_path):
    v = make_vault(tmp_path)
    (v / "notes" / "heat" / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    for _ in range(3):
        sb(v, "recall", "stability")
    assert [r for r in usage_rows(v) if r.endswith("broken")] == []


def test_c03_refresh_returns_the_indexed_changed_slugs(tmp_path):
    v = make_vault(tmp_path)
    idx = Index(Vault(v))
    idx.refresh()
    add_note(v, "fresh-one")
    (v / "notes" / "heat" / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    assert idx.refresh() == ["fresh-one"]


def test_c04_check_changed_reports_an_unindexed_note(tmp_path):
    v = make_vault(tmp_path)
    sb(v, "check")
    (v / "notes" / "heat" / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    assert sb(v, "check")[0] == 1
    code, out, _ = sb(v, "check", "--changed")
    assert code == 1 and "broken.md" in out


def test_c05_project_filter_is_blind_to_nfc_nfd(tmp_path):
    nfd, nfc = (unicodedata.normalize(f, "Übung") for f in ("NFD", "NFC"))
    v = init_vault(tmp_path)
    add_note(v, "un-note", project=nfd, extra={"project": nfd}, gist="Gist: zeppelin text.")
    assert "un-note" in sb(v, "recall", "zeppelin", "-p", nfd)[1]
    assert "un-note" in sb(v, "recall", "zeppelin", "-p", nfc)[1]
    idx = Index(Vault(v))
    assert [r[2] for r in idx.listing(nfc)] == ["un-note"] and [r[2] for r in idx.listing(nfd)] == ["un-note"]


@pytest.mark.xfail(reason="design limit: the index tests mtime and size only", strict=True)
def test_c06_same_size_edit_with_unchanged_mtime_is_missed(tmp_path):
    v = make_vault(tmp_path)
    p = v / "notes" / "heat" / "ftcs-stability-limit.md"
    sb(v, "recall", "stability")
    st, txt = p.stat(), p.read_text(encoding="utf-8")
    p.write_text(txt.replace("FTCS", "ZXQV"), encoding="utf-8")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert "ftcs-stability-limit" in sb(v, "recall", "zxqv")[1]


@pytest.mark.xfail(reason="design limit: an in-place edit in archive/ changes no directory mtime", strict=True)
def test_c07_archived_note_edited_in_place_is_stale(tmp_path):
    v = make_vault(tmp_path)
    p = add_note(v, "arch-x", loc="archive", gist="Gist: zeppelin old text.")
    assert "arch-x" in sb(v, "recall", "zeppelin", "--archive")[1]
    p.write_text(p.read_text(encoding="utf-8").replace("zeppelin old text", "quokka new text"), encoding="utf-8")
    assert "arch-x" in sb(v, "recall", "quokka", "--archive")[1]


# --- review.py: findings c08-c11 -----------------------------------------------------------------

def vault_with_usage(tmp_path, notes, rows):
    v = init_vault(tmp_path)
    for slug, proj in notes:
        add_note(v, slug, project=proj)
    (v / ".sb" / "usage.tsv").write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    return v


def test_c08_use_dated_after_today_is_not_counted(tmp_path):
    v = vault_with_usage(tmp_path, [("fut", "p")], [("2027-01-01", "work", "show", "fut")])
    inv = review.collect(Vault(v), TODAY)
    assert inv["total"]["fut"] == 0 and "fut" not in inv["last"]


def test_c09_use_counts_one_event_per_note_and_day(tmp_path):
    d = "2026-11-07"
    rows = [(d, "work", "show", "na")] * 3 + [(d, "work", "recall", "na")]
    v = vault_with_usage(tmp_path, [("na", "p")], rows)
    assert review.collect(Vault(v), TODAY)["total"]["na"] == 1.0
    rows += [("2026-11-06", "work", "recall", "na")]  # another day counts again, with its own strength
    v2 = vault_with_usage(tmp_path / "x", [("na", "p")], rows)
    assert review.collect(Vault(v2), TODAY)["total"]["na"] == pytest.approx(1.0 + review.usage.STRENGTH["recall"])


def test_c10_project_review_narrows_vetoes_faded_and_inbox(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "na", project="a")
    add_note(v, "nb", project="b")
    add_note(v, "ab", project="b", loc="archive", extra={"archived": "2026-11-01", "archived_why": "faded"})
    sb(v, "capture", "line for b", "-p", "b", "-s", "x")
    (v / ".sb" / "vetoes.tsv").write_text("2026-11-05\tnb\tkept\n", encoding="utf-8")
    inv = review.collect(Vault(v), TODAY, "a")
    assert (inv["faded"], inv["vetoes"], inv["inbox_open"], inv["inbox_due"]) == (0, 0, 0, 0)
    inv = review.collect(Vault(v), TODAY, "b")
    assert (inv["faded"], inv["vetoes"], inv["inbox_open"]) == (1, 1, 1)
    inv = review.collect(Vault(v), TODAY)
    assert (inv["faded"], inv["vetoes"], inv["inbox_open"]) == (1, 1, 1)


def test_c11_self_link_is_no_incoming_link(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "solo", project="p", body="\nSee [[solo]].\n")
    add_note(v, "other", project="p", body="\nSee [[solo]].\n")
    inv = review.collect(Vault(v), TODAY)
    assert inv["inbound"]["solo"] == 1 and inv["inbound"]["other"] == 0
    assert "need" not in inv


# --- ranking, limit, short terms -----------------------------------------------------------------

def test_rank_cue_over_title_over_gist_over_body(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "in-body", title="t", cue="c", gist="Gist: g.", body="\nzeppelin\n")
    add_note(v, "in-gist", title="t", cue="c", gist="Gist: zeppelin.")
    add_note(v, "in-title", title="zeppelin", cue="c", gist="Gist: g.")
    add_note(v, "in-cue", title="t", cue="zeppelin", gist="Gist: g.")
    code, out, _ = sb(v, "recall", "zeppelin", "-n", "10")
    assert code == 0 and slugs(out) == ["in-cue", "in-title", "in-gist", "in-body"]


def test_search_limit_cuts_and_keeps_the_best(tmp_path):
    v = init_vault(tmp_path)
    for i in range(8):
        add_note(v, f"n{i}", cue="zeppelin" if i == 5 else "c", gist="Gist: zeppelin." if i != 5 else "Gist: g.")
    idx = Index(Vault(v))
    idx.refresh()
    assert len(idx.search(["zeppelin"], limit=3)[0]) == 3
    assert idx.search(["zeppelin"], limit=1)[0][0][0] == "n5"  # the cue outranks the gists
    assert len(idx.search(["zeppelin"], limit=50)[0]) == 8


def test_search_coverage_with_limit_and_cover_argument(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "both", gist="Gist: zeppelin walrus.")
    for i in range(6):
        add_note(v, f"one{i}", cue="zeppelin zeppelin", gist="Gist: g.")
    idx = Index(Vault(v))
    idx.refresh()
    assert [r[0] for r in idx.search(["zeppelin", "walrus", "quokka"], limit=2)[0]] == ["both"]
    assert len(idx.search(["zeppelin", "walrus", "quokka"], limit=10, cover=0.3)[0]) == 7
    assert [r[0] for r in idx.search(["zeppelin", "walrus"], limit=10, cover=1.0)[0]] == ["both"]


def short_vault(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "cg-solver", project="a", cue="CG iteration", gist="Gist: g.")
    add_note(v, "cg-other", project="b", cue="CG again", gist="Gist: g.")
    add_note(v, "plain", project="a", cue="none", gist="Gist: g.")
    return v


def test_short_terms_in_search_count_and_project(tmp_path):
    v = short_vault(tmp_path)
    idx = Index(Vault(v))
    idx.refresh()
    assert {r[0] for r in idx.search(["CG"], limit=5)[0]} == {"cg-solver", "cg-other"}
    assert [r[0] for r in idx.search(["CG"], project="a")[0]] == ["cg-solver"]
    assert idx.count(["CG"], ("notes",)) == 2 and idx.count(["CG"], ("notes",), "b") == 1
    out = sb(v, "recall", "CG", "-p", "b")[1]
    assert slugs(out) == ["cg-other"]


@pytest.mark.parametrize("terms,locs", [(["CG"], ("notes",)), (["zeppelin"], ("notes",)), (["zeppelin", "walrus", "x1"], ("notes",)),
                                        (["zeppelin"], ("archive",)), (["zeppelin", "CG"], ("notes", "archive"))])
def test_count_agrees_with_search(tmp_path, terms, locs):
    v = short_vault(tmp_path)
    add_note(v, "zw", project="a", gist="Gist: zeppelin walrus.", cue="CG zeppelin")
    add_note(v, "z1", project="b", gist="Gist: zeppelin.", cue="c")
    add_note(v, "za", project="a", loc="archive", gist="Gist: zeppelin.", cue="CG")
    idx = Index(Vault(v))
    idx.refresh()
    for proj in (None, "a", "b"):
        assert idx.count(terms, locs, proj) == len(idx.search(terms, locs, proj, limit=1000)[0]), (terms, locs, proj)


def test_inbox_short_terms_and_hyphens(tmp_path):
    v = init_vault(tmp_path)
    sb(v, "capture", "CG converges slowly here", "-p", "p", "-s", "x")
    sb(v, "capture", "the Crank-Nicolson scheme rings", "-p", "p", "-s", "x")
    assert "CG converges" in sb(v, "recall", "--inbox", "CG")[1]
    for t in ("Crank-Nicolson", "Crank Nicolson"):
        assert "Crank-Nicolson scheme" in sb(v, "recall", "--inbox", *t.split(" ", 0 if "-" in t else 1))[1], t


def test_hyphenated_and_spaced_forms_meet_in_notes(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "cn-a", title="Crank-Nicolson rings", gist="Gist: g.")
    add_note(v, "cn-b", title="Crank Nicolson damps", gist="Gist: g.")
    for args in (["Crank-Nicolson"], ["Crank", "Nicolson"]):
        assert set(slugs(sb(v, "recall", *args)[1])) == {"cn-a", "cn-b"}, args
    assert "cn-a" in sb(v, "recall", "cn-a")[1]  # a slug with hyphens still matches itself


# --- Index.mentions / update / listing ------------------------------------------------------------

def test_mentions_update_and_listing_with_project(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "target-n", project="a")
    add_note(v, "src-a", project="a", body="\nSee [[target-n]].\n")
    add_note(v, "src-b", project="b", body="\nSee [[target-n]] and [[other-n]].\n")
    idx = Index(Vault(v))
    idx.refresh()
    assert sorted(p for p, _ in idx.mentions("target-n")) == ["notes/a/src-a.md", "notes/b/src-b.md"]
    assert [p for p, _ in idx.mentions("other-n")] == ["notes/b/src-b.md"]
    assert idx.mentions("nobody") == []
    assert [r[2] for r in idx.listing("b")] == ["src-b"] and [r[2] for r in idx.listing("a")] == ["src-a", "target-n"]
    assert len(idx.listing()) == 3
    p = v / "notes" / "b" / "src-b.md"
    p.write_text(p.read_text(encoding="utf-8").replace("[[target-n]]", "[[renamed-n]]"), encoding="utf-8")
    idx.update(["notes/b/src-b.md"])
    assert [p for p, _ in idx.mentions("target-n")] == ["notes/a/src-a.md"]
    assert [p for p, _ in idx.mentions("renamed-n")] == ["notes/b/src-b.md"]
    p.unlink()
    idx.update(["notes/b/src-b.md"])
    assert idx.listing("b") == [] and idx.mentions("renamed-n") == []


# --- FTS5 syntax in terms --------------------------------------------------------------------------

def test_fts_syntax_in_terms_is_literal(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "plain-a", gist="Gist: alpha beta gamma.")
    add_note(v, "plain-b", gist="Gist: delta epsilon.")
    add_note(v, "star-n", gist="Gist: the wild* card and a NEAR marker.")
    add_note(v, "quote-n", gist='Gist: a "quoted" phrase.')
    # NEAR(, OR, NOT and * are plain words here (OR and NOT are stop words); `NEAR(alpha beta)` is the terms near, alpha, beta
    cases = {("NEAR(alpha", "beta)"): ["plain-a"], ("alpha", "OR", "delta"): ["plain-a", "plain-b"], ("alpha*",): ["plain-a"],
             ("wild*",): ["star-n"], ('"quoted"',): ["quote-n"], ('qu"oted',): ["quote-n"], ("NEAR",): ["star-n"],
             ("alpha", "NOT", "beta"): ["plain-a"], ("col:alpha",): [], ("body:alpha",): ["plain-a"], ("zzz^",): [], ("alpha", "AND", "zzz"): ["plain-a"]}
    for terms, want in cases.items():
        code, out, err = sb(v, "recall", *terms)
        assert code in (0, 2), (terms, err)
        assert set(slugs(out)) == set(want), (terms, out)


# --- review: sections, warnings, tables ------------------------------------------------------------

def test_review_dangling_links_and_warning(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "ln", project="p", body="\nSee [[ghost]] and [[ghost2]].\n")
    add_note(v, "arch-t", project="p", loc="archive")
    add_note(v, "ln2", project="p", body="\nSee [[arch-t]].\n")  # a link into the archive is not dangling
    inv = review.collect(Vault(v), TODAY)
    assert inv["dangling"] == {"ln": 2}
    assert "2 links point to no note in 1 notes" in inv["warnings"]
    assert "2 links point to no note." in review.report(inv)


def test_review_warnings_inbox_and_prefix(tmp_path):
    v = init_vault(tmp_path)
    for i in range(6):
        add_note(v, f"res-{i}", project="p", type="finding")
    add_note(v, "res-odd", project="p", type="concept")
    (v / ".sb" / "inbox.tsv").write_text(f"old1\t{(TODAY - timedelta(days=60)).isoformat()}\tp\t\tan old line\n", encoding="utf-8")
    inv = review.collect(Vault(v), TODAY)
    wait = inv["inbox_due"]
    assert wait == 1 and any("1 inbox lines are older than" in w for w in inv["warnings"])
    assert "1 notes carry the slug prefix of another type" in inv["warnings"]
    text = review.report(inv)
    assert "## Warnings" in text and "res- as concept 1" in text


def test_review_since_line_and_project_table(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "na", project="a")
    add_note(v, "nb", project="b", type="concept")
    code, out, _ = sb(v, "review", "--since", "2026-10-01")
    assert code == 0 and "Use counts only events from 2026-10-01 on." in out
    assert "Use counts only" not in sb(v, "review")[1]
    assert "| project | finding | concept | all |" in out and "| a | 1 | 0 | 1 |" in out and "| **all** | 1 | 1 | 2 |" in out
    code, out, _ = sb(v, "review", "a")
    assert code == 0 and "# Review of the brain – a" in out and "| type | notes |" in out and "\n1 notes, " in out and "projects" not in out


def test_uses_follows_renames(tmp_path):
    v = vault_with_usage(tmp_path, [("new-name", "p")], [("2026-11-05", "work", "show", "old-name")])
    (v / ".sb" / "renames.tsv").write_text("old-name\tnew-name\t2026-11-06\t1\t0\n", encoding="utf-8")
    u = review.uses(Vault(v), None, TODAY)
    assert list(u) == ["new-name"] and u["new-name"][0][0] == date(2026, 11, 5)
    assert review.uses(Vault(v), date(2026, 11, 6), TODAY) == {}


def test_collect_with_project_restricts_every_figure(tmp_path):
    v = init_vault(tmp_path)
    add_note(v, "na", project="a", body="\nSee [[nb]].\n")
    add_note(v, "nb", project="b", type="question")
    add_note(v, "na2", project="a", type="concept")
    (v / ".sb" / "usage.tsv").write_text("2026-11-07\twork\tshow\tna\n2026-11-07\twork\tshow\tnb\n", encoding="utf-8")
    inv = review.collect(Vault(v), TODAY, "a")
    assert inv["notes"] == 2 and set(inv["total"]) == {"na", "na2"} and inv["total"]["na"] == 1.0
    assert inv["questions"] == 0 and inv["cross"] == {} and inv["by_type"] == {"finding": 1, "concept": 1}
    assert [g["name"] for g in inv["groups"]] == ["finding", "concept"]
    whole = review.collect(Vault(v), TODAY)
    assert whole["questions"] == 1 and sum(whole["cross"].values()) == 1
