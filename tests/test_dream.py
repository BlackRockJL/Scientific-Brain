from datetime import date, timedelta

import pytest
from helpers import add_note, sb

from scientific_brain import sleep, usage, vault as vault_mod
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)
WORDS = ("quasar nebula photon plasma vortex cobalt helium zircon argon borax tundra mirage lichen cipher drizzle saffron "
         "garnet ripple walnut falcon marble ember flint gravel harbor jasper kestrel lantern meadow nectar orchid pebble").split()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def vault(tmp_path, toml=""):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "sb.toml").write_text(toml)
    return v


def word(text) -> str:
    """A letters-only token unique to text (the index splits digits off words)."""
    return text.translate(str.maketrans("0123456789-", "abcdefghijk")) + "x"


def five(base, tag) -> str:
    return " ".join(w + word(tag) for w in base.split())


def pair(v, i, a, b, pa="pa", pb="pb", body="", **kw):
    """Two notes sharing four rare words (group i) plus one rare word of their own each; no title or cue words.
    The body goes to the first note only."""
    shared = " ".join(WORDS[4 * i:4 * i + 4])
    for slug, proj, text in ((a, pa, body), (b, pb, "")):
        add_note(v, slug, project=proj, gist=f"Gist: {shared} {word(slug)}.", body=text, **kw)


def lonely(v, slug, i, proj="pa", **kw):
    """A note without overlap with any other (five words of its own); decisions do not enter the far pair."""
    add_note(v, slug, project=proj, type="decision", gist="Gist: " + five("zeta eta theta iota kappa", str(i)) + ".", **kw)


def fill(v, n=4):
    for i in range(n):
        lonely(v, f"filler-{i}", i)


def dream(v, day=None):
    out = sb(v, "sleep", "plan")[1]
    return [ln for ln in out.split("## 5.")[1].splitlines() if ln.startswith("- ")], out


def at(monkeypatch, day):
    monkeypatch.setenv("SB_TODAY", day.isoformat())


def test_plan_has_phase_five_with_at_most_dream_pairs(tmp_path, monkeypatch):
    v = vault(tmp_path)
    for i in range(4):
        pair(v, i, f"across-{i}-a", f"across-{i}-b")
        pair(v, i + 4, f"same-{i}-a", f"same-{i}-b", pb="pa")
    pairs, out = dream(v)
    assert "## 5. Dream: 5 candidate pairs" in out and len(pairs) == 5
    assert sum("across projects" in p for p in pairs) == 3 and sum("same project" in p for p in pairs) == 2  # 2 and 2, one filled up
    assert "`sb sleep unrelated <a> <b>" in out and "only when they share words, not substance" in out
    assert f"  {pairs[0][2:].split(' ')[0]} [finding]: " in out  # each pair shows the gist of both notes
    monkeypatch.setitem(vault_mod.LIFECYCLE, "dream_pairs", 2)  # a constant, not an sb.toml setting
    assert len(dream(v)[0]) == 2


def test_dream_pairs_zero_switches_the_phase_off(tmp_path, monkeypatch):
    monkeypatch.setitem(vault_mod.LIFECYCLE, "dream_pairs", 0)
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    fill(v)
    pairs, out = dream(v)
    assert pairs == [] and "## 5. Dream: 0 candidate pairs" in out


def test_pair_is_offered_with_both_gists_and_folders(tmp_path):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    pair(v, 1, "two-a", "two-b", pb="pa")
    fill(v)
    pairs, out = dream(v)
    assert "- one-a ~ one-b (across projects; pa, pb)" in pairs and "- two-a ~ two-b (same project; pa, pa)" in pairs
    assert "  one-b [finding]: Gist: quasar nebula photon plasma" not in out  # the gist prefix is not part of the text shown
    assert "  one-b [finding]: quasar nebula photon plasma " in out


def test_linked_pairs_in_either_direction_are_left_out(tmp_path):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b", body="\n## Links\n- [[one-b]] - same mechanism\n")
    pair(v, 1, "two-a", "two-b", body="\n## Links\n- [[two-a]] - own\n")  # a note linking itself says nothing
    pair(v, 2, "three-a", "three-b")
    p3 = v / "notes" / "pb" / "three-b.md"
    p3.write_text(p3.read_text() + "\n## Links\n- [[three-a]] - reverse direction\n")
    fill(v)
    pairs, _ = dream(v)
    assert not any("one-a" in p or "three-a" in p for p in pairs) and any("two-a ~ two-b" in p for p in pairs)


def test_condense_pairs_are_not_dream_pairs(tmp_path):
    v = vault(tmp_path)
    for s in ("implicit-euler-damping", "backward-euler-damping"):
        add_note(v, s, title="Implicit Euler damps stiff modes strongly", cue="choosing a solver for stiff modes",
                 gist="Gist: Implicit Euler damps stiff modes strongly.")
    pair(v, 0, "one-a", "one-b")
    fill(v)
    pairs, out = dream(v)
    assert "- backward-euler-damping ~ implicit-euler-damping (coverage" in out.split("## 3.")[0]
    assert not any("euler" in p for p in pairs) and any("one-a ~ one-b" in p for p in pairs)


def test_title_overlap_that_looks_like_a_duplicate_stays_with_condense(tmp_path):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b", title="Quasar nebula photon scaling", cue="plasma vortex")
    fill(v)
    pairs, out = dream(v)  # coverage of the title words is 1.0 in both ways, whether condense or the check catches it
    assert not any("one-a" in p for p in pairs)


def test_a_note_at_the_link_limit_is_left_out(tmp_path):
    v = vault(tmp_path)
    pair(v, 0, "full-a", "full-b", body="\n## Links\n" + "".join(f"- [[x{i}]] - why\n" for i in range(8)))
    pair(v, 1, "two-a", "two-b")
    fill(v)
    pairs, _ = dream(v)
    assert not any("full-a" in p for p in pairs) and any("two-a ~ two-b" in p for p in pairs)
    p = v / "notes" / "pa" / "full-a.md"
    p.write_text(p.read_text().replace("- [[x7]] - why\n", ""))  # seven links: room for one more
    assert any("full-a ~ full-b" in p for p in dream(v)[0])


def test_every_note_appears_at_most_once(tmp_path):
    v = vault(tmp_path)
    shared = " ".join(WORDS[:4])
    add_note(v, "hub", project="pa", gist=f"Gist: {shared} hubx.")
    for i, proj in enumerate(("pb", "pa", "pa")):
        add_note(v, f"spoke-{i}", project=proj, gist=f"Gist: {shared} {word('spoke' + str(i))}.")
    fill(v)
    pairs, out = dream(v)
    seen = [s for p in pairs for s in p[2:].split(" (")[0].split(" ~ ")]
    assert len(seen) == len(set(seen)) and sum("hub" in p for p in pairs) == 1


def test_unrelated_errors_and_log(tmp_path):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    fill(v)
    assert sb(v, "sleep", "unrelated", "one-a", "no-such-note", "why")[0] == 2
    assert sb(v, "sleep", "unrelated", "one-a", "one-a", "why")[0] == 2
    assert sb(v, "sleep", "unrelated", "one-a", "one-b", "  ")[0] == 2
    assert not (v / ".sb" / "unrelated.tsv").exists()
    code, out, _ = sb(v, "sleep", "unrelated", "one-b", "one-a", "different mechanisms")  # works outside a sleep
    assert code == 0 and out == "pair one-b ~ one-a logged as unrelated; it returns after the cooldown\n"
    assert (v / ".sb" / "unrelated.tsv").read_text() == "2026-11-08\tone-a\tone-b\tdifferent mechanisms\n"  # sorted
    assert not any("one-a" in p for p in dream(v)[0])


def test_lock_fades_and_lengthens_with_repeated_rejection(tmp_path, monkeypatch):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    fill(v)
    shown = lambda: any("one-a ~ one-b" in p for p in dream(v)[0])
    assert shown()
    sb(v, "sleep", "unrelated", "one-a", "one-b", "no link")
    for d, expect in ((1, False), (89, False), (90, False), (91, True)):
        at(monkeypatch, TODAY + timedelta(days=d))
        assert shown() is expect, d
    at(monkeypatch, TODAY + timedelta(days=91))
    sb(v, "sleep", "unrelated", "one-a", "one-b", "still no link")  # rejected again after the lock ran out
    for d, expect in ((92, False), (181, False)):
        at(monkeypatch, TODAY + timedelta(days=d))
        assert shown() is expect, d
    assert (v / ".sb" / "unrelated.tsv").read_text().count("\n") == 2


def test_lock_follows_the_vault_stability(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PRIOR", 10.0)  # the vault's initial stability is learned; without a log it is the prior
    monkeypatch.setattr(usage, "FLOOR", 5.0)
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    fill(v)
    sb(v, "sleep", "unrelated", "one-a", "one-b", "no link")
    for d, expect in ((10, False), (11, True)):
        at(monkeypatch, TODAY + timedelta(days=d))
        assert any("one-a ~ one-b" in p for p in dream(v)[0]) is expect


def test_lock_survives_a_rename(tmp_path, monkeypatch):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    fill(v)
    sb(v, "sleep", "unrelated", "one-a", "one-b", "no link")
    assert sb(v, "rename", "one-a", "zed-a")[0] == 0
    at(monkeypatch, TODAY + timedelta(days=5))
    assert not any("zed-a" in p for p in dream(v)[0])
    assert frozenset(("zed-a", "one-b")) in sleep.rejected(Vault(v))
    at(monkeypatch, TODAY + timedelta(days=91))
    assert any("one-b ~ zed-a" in p for p in dream(v)[0])


def far_vault(tmp_path, title_b):
    v = vault(tmp_path)
    add_note(v, "scaling-concept", project="pa", type="concept", title="Plasma ember flint gravel harbor jasper",
             gist="Gist: " + five("quasar nebula photon plasma vortex", "c") + ".")
    add_note(v, "scaling-finding", project="pb", type="finding", title=title_b,
             gist="Gist: " + five("cobalt helium zircon argon borax", "f") + ".")
    fill(v)
    return v


def test_far_pair_takes_a_concept_against_a_finding_of_another_project(tmp_path):
    v = far_vault(tmp_path, "Plasma kestrel lantern meadow nectar orchid")  # one shared title word
    pairs, _ = dream(v)
    assert ["- scaling-concept ~ scaling-finding (far; pa, pb)"] == [p for p in pairs if "far" in p]
    (tmp_path / "again").mkdir()
    v2 = far_vault(tmp_path / "again", "Plasma kestrel lantern meadow nectar orchid")
    assert dream(v2)[0] == pairs  # same date, same pick


def test_far_pair_needs_a_different_project_and_type(tmp_path):
    v = vault(tmp_path)
    add_note(v, "same-concept", project="pa", type="concept", gist="Gist: " + five("quasar nebula photon plasma vortex", "c") + ".")
    add_note(v, "same-finding", project="pa", type="finding", gist="Gist: " + five("cobalt helium zircon argon borax", "f") + ".")
    add_note(v, "other-decision", project="pb", type="decision", gist="Gist: " + five("tundra mirage lichen cipher drizzle", "d") + ".")
    fill(v)
    assert not any("far" in p for p in dream(v)[0])


def test_far_pair_with_two_shared_keys_is_no_far_pair(tmp_path):
    v = far_vault(tmp_path, "Plasma ember kestrel lantern meadow nectar")
    assert not any("(far" in p for p in dream(v)[0])


def test_far_pair_choice_depends_on_the_day(tmp_path, monkeypatch):
    v = vault(tmp_path)
    for i, proj in enumerate(("pa", "pa", "pb", "pb")):
        add_note(v, f"c{i}" if i < 2 else f"f{i}", project=proj, type="concept" if i < 2 else "finding",
                 gist="Gist: " + five("alpha beta gamma delta epsilon", "n" + str(i)) + ".")
    fill(v)
    picks = set()
    for d in range(12):
        at(monkeypatch, TODAY + timedelta(days=d))
        picks |= {p for p in dream(v)[0] if "(far" in p}
    assert 1 < len(picks) <= 4  # the four combinations rotate, one per day


def test_done_reports_linked_unrelated_and_open_pairs(tmp_path):
    v = vault(tmp_path)
    for i in range(3):
        pair(v, i, f"p{i}-a", f"p{i}-b")
    fill(v)
    assert sb(v, "sleep", "begin")[0] == 0
    pairs, _ = dream(v)
    assert len(pairs) == 3
    sb(v, "sleep", "unrelated", "p0-a", "p0-b", "no shared mechanism")
    p = v / "notes" / "pa" / "p1-a.md"
    p.write_text(p.read_text() + "\n## Links\n- [[p1-b]] - same quantity\n")
    code, out, _ = sb(v, "sleep", "done", "dreamed")
    assert code == 0 and "dream: 1 linked, 1 unrelated, 1 open of 3" in out.splitlines()[-1]


def test_done_without_dream_pairs_prints_no_dream_line(tmp_path, monkeypatch):
    monkeypatch.setitem(vault_mod.LIFECYCLE, "dream_pairs", 0)
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    sb(v, "sleep", "begin")
    sb(v, "sleep", "plan")
    assert "dream:" not in sb(v, "sleep", "done", "x")[1]


def test_dream_costs_no_budget_and_does_not_make_a_sleep_due(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "B0", 5)
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    fill(v)
    assert "touched: " in dream(v)[1] and dream(v)[1].count(" ~ ") >= 1
    work = sleep.backlog(Vault(v), TODAY)
    assert set(work) == {"inbox", "verify", "forget"}
    assert usage.sleep_due(Vault(v), 6, 0, TODAY) == "sleep due: never slept"


def test_a_second_plan_in_one_sleep_repeats_the_open_pairs(tmp_path):
    v = vault(tmp_path)
    for i in range(3):
        pair(v, i, f"p{i}-a", f"p{i}-b")
    fill(v)
    sb(v, "sleep", "begin")
    first, _ = dream(v)
    assert len(first) == 3
    pair(v, 5, "late-a", "late-b")  # new notes after the first plan bring no new pairs in this sleep
    sb(v, "sleep", "unrelated", "p0-a", "p0-b", "no shared mechanism")
    again, out = dream(v)
    assert sorted(again) == sorted(p for p in first if "p0-a" not in p) and "## 5. Dream: 2 candidate pairs" in out
    assert not any("late-" in p for p in again)
    assert "dream: 0 linked, 1 unrelated, 2 open of 3" in sb(v, "sleep", "done", "x")[1].splitlines()[-1]
    sb(v, "sleep", "begin")  # the next sleep draws afresh
    assert any("late-" in p for p in dream(v)[0])


def test_verify_asks_to_mark_only_what_was_checked(tmp_path):
    v = vault(tmp_path)
    pair(v, 0, "one-a", "one-b")
    out = sb(v, "sleep", "plan")[1]
    assert "Mark only what you checked" in out and "came from an inbox line is no check" in out


def test_a_sleep_whose_first_plan_found_no_pair_draws_none_later(tmp_path):
    v = vault(tmp_path)
    fill(v)
    sb(v, "sleep", "begin")
    assert dream(v)[0] == []
    pair(v, 0, "late-a", "late-b")
    pairs, out = dream(v)
    assert pairs == [] and "(still open from this sleep's first plan)" in out
