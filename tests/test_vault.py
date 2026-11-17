import pytest

from helpers import add_note, make_vault, sb
from scientific_brain.vault import ParseError, SbError, Vault, find_vault, parse_frontmatter

OK = "---\ntitle: A: b\ntype: finding\nsources: [a/b.py, \"c, d\"]\ncreated: 2026-11-08\nwhy: \"quoted\"\nempty:\n---\n\nGist: one.\nstill one.\n\nSecond.\n"


def test_parse_values_and_body():
    meta, lines, body, bl = parse_frontmatter(OK, "x.md")
    assert meta["title"] == "A: b"
    assert meta["sources"] == ["a/b.py", "c, d"]
    assert meta["created"] == "2026-11-08"  # dates stay strings
    assert meta["why"] == "quoted" and meta["empty"] == ""
    assert lines["sources"] == 4 and bl == 9
    assert body.startswith("\nGist: one.")


@pytest.mark.parametrize("text,where,msg", [
    ("title: x\n", "x.md:1", "must start with"),
    ("---\ntitle: x\n", "x.md:2", "not closed"),
    ("---\ntitle x\n---\n", "x.md:2", "expected 'key: value'"),
    ("---\ntitle: a\ntitle: b\n---\n", "x.md:3", "duplicate key"),
    ("---\nsources: a.py\n---\n", "x.md:2", "expects a list"),
    ("---\ntitle: [a, b]\n---\n", "x.md:2", "single value"),
    ("---\nsources: [a, b\n---\n", "x.md:2", "unclosed list"),
    ("---\n  title: x\n---\n", "x.md:2", "expected 'key: value'"),
])
def test_parse_errors_name_file_and_line(text, where, msg):
    with pytest.raises(ParseError) as e:
        parse_frontmatter(text, "x.md")
    assert str(e.value).startswith(where) and msg in str(e.value)


def test_gist_strips_prefix_and_takes_first_paragraph():
    from scientific_brain.vault import Note
    meta, lines, body, bl = parse_frontmatter(OK, "x.md")
    assert Note("notes/p/x.md", meta, lines, body, bl, OK).gist == "one. still one."


def test_find_vault_env_and_upwards(tmp_path):
    import os
    (tmp_path / "sb.toml").write_text("", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    old = os.environ.pop("SB_VAULT", None)
    try:
        assert find_vault(deep).root == tmp_path.resolve()
        os.environ["SB_VAULT"] = str(tmp_path)
        assert find_vault(deep).root == tmp_path.resolve()
        os.environ["SB_VAULT"] = str(deep)
        with pytest.raises(SbError):
            find_vault()
    finally:
        os.environ.pop("SB_VAULT", None)
        if old is not None:
            os.environ["SB_VAULT"] = old


def test_no_vault_is_an_error(tmp_path):
    import os
    old = os.environ.pop("SB_VAULT", None)
    try:
        with pytest.raises(SbError):
            find_vault(tmp_path)
    finally:
        if old is not None:
            os.environ["SB_VAULT"] = old


def test_slug_rejects_trailing_newline():
    from scientific_brain.vault import SLUG
    assert SLUG.match("t4") and not SLUG.match("t4\n")


@pytest.mark.parametrize("toml", ["folder_map = []", 'projects = "heat"', "index = 1", "[update]\nauto = 1"])
def test_bad_config_types_give_a_clean_error(tmp_path, toml):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text(toml + "\n", encoding="utf-8")
    code, _, err = sb(v, "stats")
    assert code == 2 and "sb.toml" in err and "Traceback" not in err


def test_skip_sections_must_be_strings_and_retired_values_are_not_checked(tmp_path):
    from scientific_brain.vault import SbError, Vault
    for extra in ("[index]\nskip_sections = 5\n", "[index]\nskip_sections = [1]\n"):
        v = make_vault(tmp_path / str(abs(hash(extra))))
        (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n" + extra)
        with pytest.raises(SbError):
            Vault(v)
    v = make_vault(tmp_path / "ok")  # values of retired sections are not read, so their types cannot be wrong
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[index]\nskip_sections = [\"Old\"]\n[lifecycle]\nforget_budget = 2.5\n"
                               "sleep_budget = -1\ncondense_coverage = 0.7\n")
    assert Vault(v).cfg["index"]["skip_sections"] == ["Old"] and Vault(v).cfg["lifecycle"]["condense_coverage"] == 0.6


def test_limits_and_lifecycle_stay_in_cfg_filled_from_the_constants(tmp_path, monkeypatch):
    from scientific_brain import vault as vm
    cfg = Vault(make_vault(tmp_path)).cfg
    assert cfg["limits"] == vm.LIMITS == {"title_words": 16, "body_words": 600, "links": 8, "min_coverage": 0.5}
    assert cfg["lifecycle"] == vm.LIFECYCLE == {"inbox_wait_days": 30, "new_per_day": 3, "condense_coverage": 0.6,
                                                 "max_promotion_rate": 0.25, "dream_pairs": 5}
    assert not Vault(make_vault(tmp_path / "b")).ignored
    monkeypatch.setitem(vm.LIMITS, "links", 2)
    assert Vault(make_vault(tmp_path / "c")).cfg["limits"]["links"] == 2  # a constant is read when the vault is opened
    cfg["limits"]["links"] = 99
    assert vm.LIMITS["links"] == 2  # cfg holds a copy: changing it does not touch the constant


def test_self_update_is_on_by_default(tmp_path):
    from scientific_brain.vault import DEFAULTS, Vault
    assert DEFAULTS["update"] == {"auto": True} and Vault(make_vault(tmp_path)).cfg["update"] == {"auto": True}


def test_toml_reader_falls_back_to_tomli_on_python_310(monkeypatch):
    import importlib
    import sys
    import types
    import scientific_brain.vault as vm
    fake = types.ModuleType("tomli")
    monkeypatch.setitem(sys.modules, "tomli", fake)
    monkeypatch.setitem(sys.modules, "tomllib", None)  # import raises ModuleNotFoundError
    try:
        assert importlib.reload(vm).tomllib is fake
    finally:
        monkeypatch.undo()
        importlib.reload(vm)


def limits_vault(tmp_path, toml):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + toml, encoding="utf-8")
    return v


def test_body_words_is_read_from_limits_and_defaults_to_600(tmp_path):
    assert Vault(make_vault(tmp_path / "a")).cfg["limits"]["body_words"] == 600
    assert Vault(limits_vault(tmp_path / "b", "\n[limits]\nbody_words = 40\n")).cfg["limits"]["body_words"] == 40
    assert not Vault(limits_vault(tmp_path / "c", "\n[limits]\nbody_words = 40\n")).ignored


@pytest.mark.parametrize("key", ["title_words", "body_words", "links"])
@pytest.mark.parametrize("val", ['"40"', "true", "0", "-5", "40.5"])
def test_a_limit_of_the_wrong_type_or_range_is_an_error(tmp_path, key, val):
    from scientific_brain.vault import SbError, Vault  # a reload above may have replaced the classes
    with pytest.raises(SbError, match=key):
        Vault(limits_vault(tmp_path, f"\n[limits]\n{key} = {val}\n"))


def test_title_words_and_links_are_read_and_other_limits_ignored(tmp_path):
    v = Vault(limits_vault(tmp_path, "\n[limits]\ntitle_words = 40\nbody_words = 40\nlinks = 30\nmin_coverage = 0.9\n"))
    assert v.ignored == ["[limits] min_coverage"]
    assert (v.cfg["limits"]["title_words"], v.cfg["limits"]["body_words"], v.cfg["limits"]["links"]) == (40, 40, 30)
    assert v.cfg["limits"]["min_coverage"] == 0.5


def test_check_flags_a_body_over_the_vaults_limit_and_names_the_setting(tmp_path):
    v = limits_vault(tmp_path, "\n[limits]\nbody_words = 20\n")
    add_note(v, "wordy-note", body="word " * 25)
    code, out, _ = sb(v, "check")
    assert code != 0 and "body has more than 20 words (sb.toml [limits] body_words)" in out
    (v / "sb.toml").write_text((v / "sb.toml").read_text().replace("= 20", "= 30"), encoding="utf-8")
    assert "wordy-note" not in sb(v, "check")[1]


def test_check_follows_the_vaults_title_and_link_limits(tmp_path):
    v = limits_vault(tmp_path, "\n[limits]\ntitle_words = 3\nlinks = 1\n")
    add_note(v, "long-title-note", title="one two three four five")
    code, out, _ = sb(v, "check")
    assert code != 0 and "title has more than 3 words (sb.toml [limits] title_words)" in out
    (v / "sb.toml").write_text((v / "sb.toml").read_text().replace("title_words = 3", "title_words = 5"), encoding="utf-8")
    assert "long-title-note" not in sb(v, "check")[1]
