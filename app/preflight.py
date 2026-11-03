"""Check the explicitly requested documents without guessing their contents."""

from pathlib import Path


REQUIRED_DOCUMENTS = (
    Path("TEAM_PLAN.md"),
    Path("PARTNER_START_HERE.md"),
    Path("plan.txt"),
    Path("contracts/v1/README.md"),
)

REQUIRED_P0_INPUTS = REQUIRED_DOCUMENTS + (
    Path("contracts/v1/messages.schema.json"),
    Path("contracts/v1/examples.jsonl"),
    Path("scripts/validate_handoff.py"),
)


def missing_documents(root: Path) -> tuple[Path, ...]:
    """Return absent documents in a stable order; directories are not files.

    File presence does not establish contract validity or fixture completeness.
    Fixture names and schemas must come from the supplied documentation.
    """
    return tuple(path for path in REQUIRED_DOCUMENTS if not ((root / path).is_file() or (root / "docs/archive" / path).is_file()))


def missing_p0_inputs(root: Path) -> tuple[Path, ...]:
    return tuple(path for path in REQUIRED_P0_INPUTS if not ((root / path).is_file() or (root / "docs/archive" / path).is_file()))
