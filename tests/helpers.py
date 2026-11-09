"""Shared test helpers: demo vault copy, in-process CLI runner, note writer."""
import contextlib
import io
import os
import shutil
from pathlib import Path

from scientific_brain.cli import main

DEMO = Path(__file__).resolve().parents[1] / "examples" / "vault"


def make_vault(tmp_path) -> Path:
    dst = tmp_path / "vault"
    shutil.copytree(DEMO, dst)
    return dst


def sb(vault, *args):
    """Run the CLI in-process; returns (exit code, stdout, stderr)."""
    out, err, old = io.StringIO(), io.StringIO(), os.environ.get("SB_VAULT")
    os.environ["SB_VAULT"] = str(vault)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
    finally:
        if old is None:
            os.environ.pop("SB_VAULT")
        else:
            os.environ["SB_VAULT"] = old
    return code, out.getvalue(), err.getvalue()


def add_note(vault, slug, loc="notes", project="heat", type="finding", title="A title", cue="a cue",
             gist="Gist: a gist.", body="", extra=None, drop=()):
    """Write a valid note (override fields via arguments, remove keys via drop)."""
    fields = {"title": title, "type": type, "project": project, "created": "2026-11-01", "updated": "2026-11-01",
              "cue": cue}
    if type in ("finding", "derivation"):
        fields["sources"] = "[src/ftcs.py]"
    if type == "source":
        fields["ref"] = "morton-mayers-2005"
    if loc == "archive":
        fields.update({"archived": "2026-11-02", "archived_why": "faded"})
    fields.update(extra or {})
    text = "---\n" + "".join(f"{k}: {x}\n" for k, x in fields.items() if k not in drop) + "---\n\n" + gist + "\n"
    if type == "decision":
        text += "\n## Rejected\n- nothing\n"
    if type == "question":
        text += "\n## Depends on\n- nothing\n"
    path = Path(vault) / loc / project / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + body, encoding="utf-8")
    return path
