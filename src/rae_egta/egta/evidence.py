"""Frozen evidence-package loading, validation, and snapshot manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


EVIDENCE_SCHEMA_VERSION = "egta_evidence.v1"
EVIDENCE_MANIFEST_VERSION = "egta_evidence_manifest.v1"
SOURCE_FIELDS = {
    "evidence_id",
    "url",
    "canonical_url",
    "title",
    "publisher",
    "source_tier",
    "publication_or_update_date",
    "access_date",
    "content_sha256",
    "spans",
}
SOURCE_TIERS = {
    "official_primary",
    "authoritative_database",
    "versioned_encyclopedia",
    "reliable_secondary",
    "other",
}
PACKAGE_FIELDS = {
    "schema_version",
    "item_id",
    "evidence_package_id",
    "aliases",
    "sources",
    "source_contents",
    "package_sha256",
}


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize JSON deterministically for protocol hashes."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def source_content_sha256(content: str) -> str:
    """Hash the exact UTF-8 source snapshot stored beside source metadata."""

    return sha256_bytes(content.encode("utf-8"))


def _package_digest(package: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in package.items()
        if key not in {"evidence_package_id", "package_sha256"}
    }
    return sha256_bytes(canonical_json_bytes(unsigned))


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        from datetime import date

        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _validate_source(source: Any, content: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(source, dict):
        return [f"{path} must be an object"]
    missing = SOURCE_FIELDS - set(source)
    extra = set(source) - SOURCE_FIELDS
    errors.extend(f"{path}.{key} is required" for key in sorted(missing))
    errors.extend(f"{path}.{key} is not allowed" for key in sorted(extra))
    if missing or extra:
        return errors
    for key in ("evidence_id", "url", "canonical_url", "title", "publisher"):
        if not isinstance(source[key], str) or not source[key].strip():
            errors.append(f"{path}.{key} must be a non-empty string")
    if source["source_tier"] not in SOURCE_TIERS:
        errors.append(f"{path}.source_tier is invalid")
    if not _valid_iso_date(source["access_date"]):
        errors.append(f"{path}.access_date must be an ISO date")
    published = source["publication_or_update_date"]
    if published is not None and not _valid_iso_date(published):
        errors.append(f"{path}.publication_or_update_date must be an ISO date or null")
    if not isinstance(content, str):
        errors.append(f"source_contents[{source['evidence_id']!r}] must be a string")
    elif source_content_sha256(content) != source["content_sha256"]:
        errors.append(f"{path}.content_sha256 does not match its frozen source content")
    spans = source["spans"]
    if not isinstance(spans, list):
        errors.append(f"{path}.spans must be an array")
        return errors
    seen: set[str] = set()
    for index, span in enumerate(spans):
        span_path = f"{path}.spans[{index}]"
        if not isinstance(span, dict) or set(span) != {"span_id", "text", "locator", "supports"}:
            errors.append(f"{span_path} has an invalid shape")
            continue
        if not all(isinstance(span[key], str) and span[key].strip() for key in ("span_id", "text", "locator")):
            errors.append(f"{span_path} requires non-empty span_id, text, and locator")
        if not isinstance(span["supports"], list) or not all(
            isinstance(value, str) and value.strip() for value in span["supports"]
        ):
            errors.append(f"{span_path}.supports must be a string array")
        if span["span_id"] in seen:
            errors.append(f"{path}.spans contains duplicate span_id values")
        seen.add(span["span_id"])
        if isinstance(content, str) and span["text"] not in content:
            errors.append(f"{span_path}.text is absent from its frozen source content")
    return errors


def validate_evidence_package(package: Any, *, expected_item_id: str | None = None) -> list[str]:
    """Return every evidence-package contract violation."""

    if not isinstance(package, dict):
        return ["package must be an object"]
    errors: list[str] = []
    missing = PACKAGE_FIELDS - set(package)
    extra = set(package) - PACKAGE_FIELDS
    errors.extend(f"package.{key} is required" for key in sorted(missing))
    errors.extend(f"package.{key} is not allowed" for key in sorted(extra))
    if missing or extra:
        return errors
    if package["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {EVIDENCE_SCHEMA_VERSION!r}")
    if not isinstance(package["item_id"], str) or not package["item_id"].startswith("egta_"):
        errors.append("item_id must be an EGTA item ID")
    if expected_item_id is not None and package["item_id"] != expected_item_id:
        errors.append("item_id does not match the requested item")
    aliases = package["aliases"]
    if not isinstance(aliases, list) or not all(
        isinstance(group, list)
        and len(group) >= 2
        and all(isinstance(value, str) and value.strip() for value in group)
        for group in aliases
    ):
        errors.append("aliases must be an array of string groups, each with at least two values")
    sources = package["sources"]
    contents = package["source_contents"]
    if not isinstance(sources, list):
        errors.append("sources must be an array")
        sources = []
    if not isinstance(contents, dict):
        errors.append("source_contents must be an object")
        contents = {}
    evidence_ids: list[str] = []
    for index, source in enumerate(sources):
        evidence_id = source.get("evidence_id", "") if isinstance(source, dict) else ""
        evidence_ids.append(evidence_id)
        errors.extend(_validate_source(source, contents.get(evidence_id), f"sources[{index}]"))
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("sources contains duplicate evidence_id values")
    if set(contents) != set(evidence_ids):
        errors.append("source_contents keys must exactly match source evidence_id values")
    digest = _package_digest(package)
    if package["evidence_package_id"] != f"egta_evidence_{digest}":
        errors.append("evidence_package_id does not match package content")
    if package["package_sha256"] != digest:
        errors.append("package_sha256 does not match package content")
    return errors


def build_evidence_package(
    item_id: str,
    sources: Iterable[dict[str, Any]],
    *,
    aliases: Iterable[Iterable[str]] = (),
    source_contents: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a valid package, deriving source and package hashes."""

    built_sources = [json.loads(json.dumps(source)) for source in sources]
    contents = dict(source_contents or {})
    for source in built_sources:
        evidence_id = source["evidence_id"]
        if evidence_id not in contents:
            contents[evidence_id] = "\n".join(span["text"] for span in source.get("spans", []))
        source["content_sha256"] = source_content_sha256(contents[evidence_id])
    package: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "item_id": item_id,
        "evidence_package_id": "",
        "aliases": [list(group) for group in aliases],
        "sources": built_sources,
        "source_contents": contents,
        "package_sha256": "",
    }
    digest = _package_digest(package)
    package["evidence_package_id"] = f"egta_evidence_{digest}"
    package["package_sha256"] = digest
    errors = validate_evidence_package(package, expected_item_id=item_id)
    if errors:
        raise ValueError("invalid evidence package: " + "; ".join(errors))
    return package


def write_evidence_package(path: Path, package: dict[str, Any]) -> None:
    errors = validate_evidence_package(package)
    if errors:
        raise ValueError("invalid evidence package: " + "; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def load_evidence_package(path: Path, *, expected_item_id: str | None = None) -> dict[str, Any]:
    package = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_evidence_package(package, expected_item_id=expected_item_id)
    if errors:
        raise ValueError(f"invalid evidence package {path}: " + "; ".join(errors))
    return package


def load_evidence_for_item(evidence_root: Path, item_id: str) -> dict[str, Any]:
    return load_evidence_package(evidence_root / f"{item_id}.json", expected_item_id=item_id)


def snapshot_evidence_root(evidence_root: Path, *, write: bool = True) -> dict[str, Any]:
    """Validate all item files and produce one order-stable evidence snapshot."""

    packages: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(evidence_root.glob("egta_*.json")):
        if path.name == "evidence_manifest.json":
            continue
        packages.append((path, load_evidence_package(path)))
    item_ids = [package["item_id"] for _, package in packages]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("evidence root contains duplicate item_id values")
    items = [
        {
            "item_id": package["item_id"],
            "path": path.name,
            "file_sha256": sha256_path(path),
            "package_sha256": package["package_sha256"],
            "evidence_package_id": package["evidence_package_id"],
            "source_count": len(package["sources"]),
        }
        for path, package in packages
    ]
    manifest = {
        "schema_version": EVIDENCE_MANIFEST_VERSION,
        "item_count": len(items),
        "items": items,
        "snapshot_sha256": sha256_bytes(canonical_json_bytes(items)),
    }
    if write:
        (evidence_root / "evidence_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return manifest
