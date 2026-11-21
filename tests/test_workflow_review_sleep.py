"""Workflow: `sb review` and `sb stats` report the same verify backlog through import, edit, sleep and parking."""
import re
from datetime import date

import pytest
from helpers import add_note, make_vault, sb

TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setenv("SB_TODAY", TODAY.isoformat())


def stats_verify(v):
    out = sb(v, "stats")[1]
    return int(re.search(r"waiting for a sleep: \d+ \(.*verify (\d+)", out).group(1))


def upkeep(v, *args):
    """(waiting, parked, never verified, accepted by baseline) from the review's Upkeep section."""
    code, out, err = sb(v, "review", *args)
    assert code == 0, err
    m = re.search(r"- verify: (\d+) wait for a sleep(?:, (\d+) parked as unverifiable)?", out)
    n = re.search(r"- never verified by a sleep: (\d+)(?:, of these (\d+) accepted unchanged by `sb baseline`)?", out)
    assert m and n, out
    return int(m.group(1)), int(m.group(2) or 0), int(n.group(1)), int(n.group(2) or 0)


def append(p, text="\nOne more sentence.\n"):
    p.write_text(p.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_review_and_stats_agree_through_import_edit_sleep_and_parking(tmp_path):
    v = make_vault(tmp_path)
    waiting, parked, never, accepted = upkeep(v)
    assert waiting == stats_verify(v) and parked == 0 and accepted == 0 and never >= waiting

    # import: 40 notes without `verified:`, accepted as checked by baseline
    for i in range(40):
        add_note(v, f"imported-{i:02d}", project="imp", type="concept", title=f"Imported concept {i}", cue=f"cue {i}")
    assert stats_verify(v) == waiting + 40 and upkeep(v)[0] == waiting + 40
    assert sb(v, "sleep", "baseline")[0] == 0
    waiting, parked, never, accepted = upkeep(v)
    assert waiting == stats_verify(v) == 0
    assert never >= 40 and accepted == never  # nothing in this vault is unverified and unchecked any more

    # an edit after the import puts exactly that note back into verify, in both reports
    append(v / "notes" / "imp" / "imported-03.md")
    waiting, parked, never, accepted2 = upkeep(v)
    assert waiting == stats_verify(v) == 1 and accepted2 == accepted - 1

    # a sleep verifies it: both reports drop it, and it leaves "never verified"
    assert sb(v, "sleep", "begin")[0] == 0
    assert "imported-03" in sb(v, "sleep", "plan")[1]
    assert sb(v, "sleep", "verified", "imported-03")[0] == 0
    assert sb(v, "sleep", "done", "checked the edit")[0] == 0
    waiting, parked, never2, _ = upkeep(v)
    assert waiting == stats_verify(v) == 0 and never2 == never - 1

    # a note parked as unverifiable leaves the backlog of both reports but is shown apart in the review
    append(v / "notes" / "imp" / "imported-07.md")
    assert sb(v, "sleep", "begin")[0] == 0
    sb(v, "sleep", "plan")
    code, _, err = sb(v, "sleep", "unverifiable", "imported-07", "No source or neighbour states this.")
    assert code == 0, err
    assert sb(v, "sleep", "done", "parked one")[0] == 0
    waiting, parked, *_ = upkeep(v)
    assert waiting == stats_verify(v) == 0 and parked == 1


def test_review_per_project_adds_up_to_the_whole(tmp_path):
    v = make_vault(tmp_path)
    for i in range(6):
        add_note(v, f"other-{i}", project="other", type="concept", title=f"Other concept {i}", cue=f"other cue {i}")
    sb(v, "sleep", "baseline")
    append(v / "notes" / "other" / "other-2.md")
    add_note(v, "fresh-heat", title="Fresh heat note", cue="fresh cue")
    total = upkeep(v)
    parts = [upkeep(v, p) for p in ("heat", "_general", "other")]
    assert [sum(x[i] for x in parts) for i in range(4)] == list(total)
    assert total[0] == stats_verify(v) == 2


def test_real_size_import_keeps_both_counts_equal(tmp_path):
    """The case of the 2026-11-19 review: an import of 1165 notes looked unverified while the sleep saw 6 to verify."""
    v = make_vault(tmp_path)
    for i in range(1165):
        add_note(v, f"bulk-{i:04d}", project=f"p{i % 7}", type="concept", title=f"Bulk concept {i}", cue=f"bulk {i}")
    sb(v, "sleep", "baseline")
    for i in range(6):
        append(v / "notes" / f"p{i % 7}" / f"bulk-{i:04d}.md")
    waiting, _, never, accepted = upkeep(v)
    assert waiting == stats_verify(v) == 6
    assert never - accepted == 6  # the six edited imports: unverified and not accepted any more
