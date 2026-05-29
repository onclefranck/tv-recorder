import tomllib
from pathlib import Path

import tv_recorder


ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project_version = tomllib.load(file)["project"]["version"]

    assert tv_recorder.__version__ == project_version
