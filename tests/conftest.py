import pytest

from airiti_mcp import harvest


@pytest.fixture(autouse=True)
def _fresh_run_locks():
    """harvest keeps one asyncio.Lock per run folder in a module-level dict; every test gets a clean one."""
    harvest._locks.clear()
    yield
    harvest._locks.clear()
