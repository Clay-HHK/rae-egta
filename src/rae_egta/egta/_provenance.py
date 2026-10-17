"""EGTA-specific provenance wrapper; shared provenance remains unchanged."""

from __future__ import annotations

from typing import Any

from rae_egta.common.provenance import git_metadata


def git_metadata_ignoring_egta() -> dict[str, Any]:
    metadata = git_metadata()
    filtered = []
    for line in str(metadata.get("git_status_short", "")).splitlines():
        path = line[3:] if len(line) > 2 and line[2] == " " else line[2:].lstrip()
        if path == "audit" or path.startswith("audit/"):
            continue
        filtered.append(line)
    metadata["git_status_short"] = "\n".join(filtered)
    metadata["git_dirty"] = bool(filtered)
    metadata["git_dirty_ignored_prefixes"] = [
        *metadata.get("git_dirty_ignored_prefixes", []),
        "audit/",
    ]
    return metadata
