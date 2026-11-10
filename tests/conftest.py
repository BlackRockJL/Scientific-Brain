import pytest


@pytest.fixture(autouse=True)
def no_self_update(monkeypatch):
    """No test may reach pipx, git or claude; the update tests lift this and fake the calls."""
    monkeypatch.setenv("SB_NO_UPDATE", "1")
