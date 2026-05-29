from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT / "pyproject.toml"


def read_project_version(path: Path = PYPROJECT_PATH) -> str:
    with path.open("rb") as file:
        pyproject = tomllib.load(file)
    return pyproject["project"]["version"]


def normalize_tag(tag: str) -> str:
    return tag.removeprefix("v")


def validate_versions(tag: str | None = None) -> list[str]:
    project_version = read_project_version()
    errors = []

    if tag is not None and normalize_tag(tag) != project_version:
        errors.append(
            f"release tag {tag} does not match pyproject.toml version {project_version}."
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that the release tag matches pyproject.toml."
    )
    parser.add_argument(
        "--tag",
        help="Release tag to compare with pyproject.toml, with or without a leading v.",
    )
    args = parser.parse_args()

    errors = validate_versions(args.tag)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    version = read_project_version()
    print(f"Version OK: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
