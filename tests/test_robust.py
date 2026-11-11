"""sb 0.3 reads no settings but the set-up: old sb.toml sections are reported and ignored, broken log lines never crash."""
from datetime import date, timedelta

import pytest
from helpers import make_vault, sb, write_log

from scientific_brain import sleep, usage, vault as vault_mod
from scientific_brain.vault import Vault

TODAY = date(2026, 11, 8)

OLD_TOML = """
[limits]
title_words = 1
body_words = 5
links = 0
min_coverage = 0.99
[activation]
recall = 5.0
h = 1
h_decision = 2
h_unrelated = 1
veto = 9.0
veto_growth = 3.0
[lifecycle]
inbox_wait_days = 1
sleep_days = 1
sleep_inbox = 1
sleep_gap_hours = 0
new_per_day = 0
sleep_budget = 1
forget_budget = 0
condense_pairs = 0
condense_coverage = 0.1
max_promotion_rate = 0.0
dream_pairs = 0
"""


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def old_vault(tmp_path, extra=OLD_TOML):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + extra, encoding="utf-8")
    return v


# ---- an sb.toml of sb <= 0.2.1 ----------------------------------------------------------------------------------

def test_an_old_sb_toml_loads_and_lists_the_retired_sections(tmp_path):
    v = Vault(old_vault(tmp_path))
    assert [x.split(" ")[0] for x in v.ignored] == ["[limits]", "[activation]", "[lifecycle]"]
    assert v.ignored[0] == "[limits] title_words, body_words, links, min_coverage"
    assert v.cfg["limits"] == vault_mod.LIMITS and v.cfg["lifecycle"] == vault_mod.LIFECYCLE  # nothing of it reaches the values
    assert v.cfg["projects"] == ["heat", "_general"] and v.cfg["source_roots"] == ["sources"]  # the set-up is still read


def test_check_and_stats_point_out_the_old_sections_without_failing(tmp_path):
    v = old_vault(tmp_path)
    for cmd in ("check", "stats"):
        code, out, err = sb(v, cmd)
        assert code == 0 and not err, (cmd, err)
        hint = [ln for ln in out.splitlines() if ln.startswith("note: sb.toml sets")]
        assert len(hint) == 1, cmd
        assert all(x in hint[0] for x in ("[limits]", "[activation]", "[lifecycle]", "no longer read")), hint


def test_a_clean_vault_prints_no_hint(tmp_path):
    v = make_vault(tmp_path)
    assert "no longer read" not in sb(v, "check")[1] + sb(v, "stats")[1] and not Vault(v).ignored


def test_the_old_values_have_no_effect_anywhere(tmp_path):
    old, clean = old_vault(tmp_path / "old"), make_vault(tmp_path / "clean")

    def body(out):
        return "\n".join(ln for ln in out.splitlines() if not ln.startswith("note: sb.toml sets"))
    for args in (("sleep", "plan"), ("stats",), ("check",), ("rules",), ("recall", "stability", "limit"), ("review",)):
        a, b = sb(old, *args), sb(clean, *args)
        assert a[0] == b[0] == 0, args
        assert body(a[1]) == body(b[1]), args  # title_words = 1, links = 0, sleep_budget = 1 ... change nothing
    assert "touched: 11 of 40" in sb(old, "sleep", "plan")[1]
    assert sb(old, "new", "finding", "a-long-slug-name", "-p", "heat", "--why", "own measurement")[0] == 0  # new_per_day = 0 would warn at once


@pytest.mark.parametrize("toml", ["limits = 1", "[lifecycle]\nsleep_days = true", '[limits]\nmin_coverage = "x"', "[activation]\nh = 'abc'",
                                  "[lifecycle]\nsleep_budget = -1\nforget_budget = 2.5", "[activation]\n", "activation = []"])
def test_retired_sections_with_any_content_never_fail(tmp_path, toml):
    v = old_vault(tmp_path, "\n" + toml + "\n")
    for cmd in ("stats", "check", "sleep"):
        code, out, err = sb(v, *((cmd, "plan") if cmd == "sleep" else (cmd,)))
        assert code == 0, (cmd, err)
    assert Vault(v).ignored


def test_a_top_level_dream_pairs_is_not_a_retired_section(tmp_path):
    v = old_vault(tmp_path, "dream_pairs = 3\n")
    assert Vault(v).ignored == [] and Vault(v).cfg["lifecycle"]["dream_pairs"] == 5
    assert "no longer read" not in sb(v, "stats")[1]


def test_the_setup_keys_still_fail_on_a_wrong_type_next_to_old_sections(tmp_path):
    v = old_vault(tmp_path, '\nsource_roots = "x"\n')
    code, _, err = sb(v, "stats")
    assert code == 2 and "sb.toml" in err


# ---- broken lines in the logs -----------------------------------------------------------------------------------

JUNK = ["garbage", "2026-13-45\twork\tshow\tscheme-choice", "2026-11-08\twork", "2026-11-08", "\t\t\t", "2026-11-08\twork\tfly\tscheme-choice",
        "2026-11-08\tdaemon\tshow\tscheme-choice", "2026-11-08\twork\tshow\t", "2026-11-08\twork\tshow\tno-such-note", "x" * 5000,
        "2026-11-08\twork\tshow\tscheme-choice\textra\tfields", "ü\tä\tö\tß"]


def battery(v):
    """Every command that reads the logs; none may raise."""
    for args in (("stats",), ("review",), ("sleep", "plan"), ("check",), ("recall", "stability"), ("show", "scheme-choice"), ("rules",)):
        code, out, err = sb(v, *args)
        assert code == 0 and "Traceback" not in err, (args, err)


def test_broken_lines_in_usage_tsv_are_skipped_and_good_ones_still_count(tmp_path):
    v = make_vault(tmp_path)
    good = [ln for ln in (v / ".sb" / "usage.tsv").read_text().splitlines()]
    before = {x[0]: x[3:] for x in usage.scores(Vault(v), TODAY)}
    write_log(v, "usage.tsv", *JUNK, *good, *JUNK)
    assert {x[0]: x[3:] for x in usage.scores(Vault(v), TODAY)} == before  # the same stability and last use as without the junk
    assert usage.events(Vault(v))["scheme-choice"][date(2026, 11, 5)] == 1.0
    battery(v)


def test_broken_lines_in_seeds_tsv_and_renames_tsv_are_skipped(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "seeds.tsv", "garbage", "2026-11-08", "2026-11-08\tscheme-choice", "2026-11-08\tscheme-choice\tx", "nope\tscheme-choice\t0.5",
              "2026-11-08\t\t0.5", "\t\t", "2026-11-08\tno-such-note\t0.5")
    write_log(v, "renames.tsv", "garbage", "a\t", "\t\t", "a\tb\tnot-a-date", "a\tb\t2026-11-08\tx\ty", "scheme-choice\tscheme-choice\t2026-11-08\t1\t1")
    battery(v)
    assert "scheme-choice" in {x[0] for x in usage.scores(Vault(v), TODAY)}


def test_broken_lines_in_sleep_tsv_are_skipped(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "sleep.tsv", "garbage", "", "\t\t\t", "2026-13-01T12:00\t1", "x\t1\t2\t3\t4\t5\t6\ts\t1\t0", f"{ago(3)}T12:00\t0\t0\t0\t0\t0\t0\tok\t40\t0",
              "2026-11-08T25:99\t1")
    assert usage.last_sleep(Vault(v)) == TODAY - timedelta(3)
    assert usage.budget(Vault(v)) == 50
    battery(v)
    out = sb(v, "stats")[1]
    assert "a sleep takes 50 items" in out and "last sleep: " in out


def test_broken_lines_in_the_other_logs_are_skipped(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "vetoes.tsv", "garbage", "", "2026-11-08", "nope\tscheme-choice\twhy", f"{TODAY}\tscheme-choice")
    write_log(v, "snapshot.tsv", "garbage", "", "x", "\t")
    write_log(v, "inbox-done.tsv", "garbage", "", "5c4c87", "5c4c87\tnot-a-date\tdropped", "\t\t")
    write_log(v, "unrelated.tsv", "garbage", "", f"{TODAY}\ta", f"{TODAY}\t\t\t")
    battery(v)


def test_a_running_sleep_survives_broken_lines_in_every_log(tmp_path):
    v = make_vault(tmp_path)
    for name in ("usage.tsv", "seeds.tsv", "renames.tsv", "sleep.tsv", "vetoes.tsv", "inbox-done.tsv"):  # unrelated.tsv: see its xfail test
        with open(v / ".sb" / name, "a", encoding="utf-8") as f:
            f.write("\n".join(JUNK) + "\n")
    assert sb(v, "sleep", "begin")[0] == 0
    assert sb(v, "sleep", "plan")[0] == 0
    code, out, _ = sb(v, "sleep", "done", "with junk in the logs")
    assert code == 0 and out.startswith("slept ")


def test_scores_and_events_on_empty_logs_and_missing_files(tmp_path):
    v = make_vault(tmp_path)
    for name in ("usage.tsv", "seeds.tsv", "renames.tsv", "sleep.tsv", "unrelated.tsv"):
        (v / ".sb" / name).unlink(missing_ok=True)
    vv = Vault(v)
    assert usage.events(vv) == {} and usage.seeds(vv) == {} and usage.renames(vv) == {} and usage.last_sleep(vv) is None
    assert usage.budget(vv) == 40 and usage.learned(vv, TODAY)[""] == 90.0 and sleep.rejected(vv) == {}
    assert len(usage.scores(vv, TODAY)) == 8  # every note rests its prior from its creation
    battery(v)


# ---- huge seeds and bytes that are not UTF-8 --------------------------------------------------------------------

def test_a_huge_seed_weight_gives_a_bounded_rest_and_no_overflow(tmp_path):
    v = make_vault(tmp_path)
    write_log(v, "seeds.tsv", (ago(1), "scheme-choice", "1e200"))
    S = {x[0]: x[4] for x in usage.scores(Vault(v), TODAY)}["scheme-choice"]
    assert S == 1e6
    for args in (("stats",), ("review",), ("sleep", "plan"), ("check",)):
        assert sb(v, *args)[0] == 0, args


def test_invalid_utf8_in_the_logs_is_read_with_replacement_characters(tmp_path):
    v = make_vault(tmp_path)
    log = v / ".sb" / "usage.tsv"
    log.write_bytes(log.read_bytes() + b"2026-11-20\twork\tshow\tnote\xe4\xff\n2026-11-07\twork\tshow\tscheme-choice\n")
    ev = usage.events(Vault(v))
    assert "note��" in ev and ev["note��"] == {date(2026, 11, 20): 1.0}  # the row counts, for a slug no note has
    assert ev["scheme-choice"][date(2026, 11, 7)] == 1.0  # and the next row is intact
    for args in (("stats",), ("sleep", "plan"), ("recall", "stability", "limit"), ("review",), ("check",)):
        code, out, err = sb(v, *args)
        assert code == 0 and "Traceback" not in err, (args, err)
    assert "note�" not in sb(v, "stats")[1]  # no note carries that slug


def test_invalid_utf8_in_every_log_does_not_stop_a_sleep(tmp_path):
    v = make_vault(tmp_path)
    for name in ("usage.tsv", "seeds.tsv", "renames.tsv", "sleep.tsv", "vetoes.tsv", "inbox-done.tsv", "snapshot.tsv"):
        with open(v / ".sb" / name, "ab") as f:
            f.write(b"\xff\xfe\t\xe4\t2026-11-08\t\xc3\n")
    assert sb(v, "sleep", "begin")[0] == 0 and sb(v, "sleep", "plan")[0] == 0
    code, out, _ = sb(v, "sleep", "done", "bytes")
    assert code == 0 and out.startswith("slept ")
