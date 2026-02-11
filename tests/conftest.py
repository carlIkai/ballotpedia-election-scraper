from pathlib import Path
from typing import Callable
import pytest

@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"

@pytest.fixture(scope="session")
def html_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "html"

@pytest.fixture()
def load_html(html_dir: Path) -> Callable[[str], str]:
    def _load(name: str) -> str:
        path = html_dir / name
        return path.read_text(encoding="utf-8")
    return _load
