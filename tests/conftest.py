import pytest


@pytest.fixture(autouse=True)
def no_self_update(monkeypatch):
    """No test may reach pipx, git or claude; the update tests lift this and fake the calls."""
    monkeypatch.setenv("SB_NO_UPDATE", "1")


@pytest.fixture(autouse=True)
def no_real_vault(monkeypatch, tmp_path_factory):
    """No test may reach the user's vault through SB_VAULT: it points to an empty folder unless a test sets its own."""
    monkeypatch.setenv("SB_VAULT", str(tmp_path_factory.mktemp("no-vault")))


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    """No test may run git, pipx or claude for real, even with SB_NO_UPDATE lifted; update tests set their own fake."""
    from scientific_brain import update
    monkeypatch.setattr(update, "_run", lambda args, timeout: pytest.fail(f"external call without a fake: {args}"))
