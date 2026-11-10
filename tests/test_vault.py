import pytest

from helpers import make_vault, sb
from scientific_brain.vault import ParseError, SbError, find_vault, parse_frontmatter

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


@pytest.mark.parametrize("toml", ["limits = 1", "folder_map = []", 'projects = "heat"', '[limits]\nmin_coverage = "x"',
                                  "[lifecycle]\nsleep_days = true"])
def test_bad_config_types_give_a_clean_error(tmp_path, toml):
    v = make_vault(tmp_path)
    (v / "sb.toml").write_text(toml + "\n", encoding="utf-8")
    code, _, err = sb(v, "stats")
    assert code == 2 and "sb.toml" in err and "Traceback" not in err


def test_counts_must_be_whole_numbers_and_skip_sections_strings(tmp_path):
    import pytest
    from scientific_brain.vault import SbError, Vault
    for extra in ("[lifecycle]\nforget_budget = 2.5\n", "[lifecycle]\nsleep_budget = -1\n", "[index]\nskip_sections = 5\n",
                  "[index]\nskip_sections = [1]\n"):
        v = make_vault(tmp_path / str(abs(hash(extra))))
        (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n" + extra)
        with pytest.raises(SbError):
            Vault(v)
    v = make_vault(tmp_path / "ok")
    (v / "sb.toml").write_text((v / "sb.toml").read_text() + "\n[index]\nskip_sections = [\"Old\"]\n[lifecycle]\ncondense_coverage = 0.7\n")
    assert Vault(v).cfg["index"]["skip_sections"] == ["Old"]


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
