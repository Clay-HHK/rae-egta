#!/usr/bin/env python3
"""Shared provenance helpers for evidence-producing experiment runs."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def stable_json_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _status_path(line: str) -> str:
    """Extract the path component from one `git status --short` line."""
    return line[3:] if len(line) > 2 and line[2] == " " else line[2:].lstrip()


def git_metadata() -> dict[str, Any]:
    def run_git(args: list[str]) -> str | None:
        try:
            return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    status = run_git(["status", "--short"])
    ignored_paths = {
        "data",
    }
    ignored_prefixes = (
        "data/",
        "experiments/kg_external_validation/data/eval_panels/",
        "experiments/kg_external_validation/data/retired_provenance_audit/",
        "experiments/kg_external_validation/runs/",
        "experiments/kg_external_validation/go/runs/",
        "experiments/primekg_mvp/outputs/",
        "raw_runs/",
    )
    filtered_lines = []
    for line in (status or "").splitlines():
        path = _status_path(line)
        if path in ignored_paths or any(path.startswith(prefix) for prefix in ignored_prefixes):
            continue
        filtered_lines.append(line)
    filtered_status = "\n".join(filtered_lines)
    return {
        "git_sha": run_git(["rev-parse", "HEAD"]),
        "git_dirty": bool(filtered_status),
        "git_status_short": filtered_status,
        "git_status_short_raw": status or "",
        "git_dirty_ignored_paths": sorted(ignored_paths),
        "git_dirty_ignored_prefixes": list(ignored_prefixes),
    }


def optional_version(module_name: str) -> str | None:
    try:
        module = __import__(module_name)
    except Exception:
        return None
    return str(getattr(module, "__version__", "unknown"))


def hash_path_tree(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        return None
    for item in files:
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        try:
            with item.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
    return digest.hexdigest()


def dataset_loader_hint(dataset_name: str) -> dict[str, str]:
    if dataset_name == "synthetic" or dataset_name.startswith("Synthetic:"):
        return {"dataset_kind": "synthetic", "loader": "make_synthetic_dataset", "dataset_id": dataset_name}
    if dataset_name in {"Cora", "CiteSeer", "PubMed"}:
        return {"dataset_kind": "pyg", "loader": "torch_geometric.datasets.Planetoid", "dataset_id": dataset_name}
    if dataset_name in {"Amazon-Photo", "Photo"}:
        return {"dataset_kind": "pyg", "loader": "torch_geometric.datasets.Amazon", "dataset_id": "Photo"}
    if dataset_name in {"Amazon-Computers", "Computers"}:
        return {"dataset_kind": "pyg", "loader": "torch_geometric.datasets.Amazon", "dataset_id": "Computers"}
    if dataset_name == "ogbn-arxiv":
        return {"dataset_kind": "ogb", "loader": "ogb.nodeproppred.PygNodePropPredDataset", "dataset_id": "ogbn-arxiv"}
    return {"dataset_kind": "unknown", "loader": "load_pyg_dataset", "dataset_id": dataset_name}


def dataset_storage_path(data_root: Path, dataset_name: str, hint: dict[str, str]) -> Path:
    if hint["dataset_kind"] == "synthetic":
        return data_root / dataset_name
    if hint["loader"] == "torch_geometric.datasets.Planetoid":
        return data_root / "Planetoid" / hint["dataset_id"]
    if hint["loader"] == "torch_geometric.datasets.Amazon":
        return data_root / "Amazon" / hint["dataset_id"]
    if hint["loader"] == "ogb.nodeproppred.PygNodePropPredDataset":
        return data_root / hint["dataset_id"]
    return data_root / dataset_name


def dataset_provenance(data_root: Path, datasets: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        hint = dataset_loader_hint(dataset)
        dataset_path = dataset_storage_path(data_root, dataset, hint)
        raw_path = dataset_path / "raw"
        processed_path = dataset_path / "processed"
        if hint["dataset_kind"] == "synthetic":
            tree_hash = stable_json_hash(hint)
            hash_status = "generated_from_spec"
            download_status = "generated"
        else:
            tree_hash = hash_path_tree(dataset_path)
            hash_status = "present" if tree_hash else "missing_or_unavailable"
            if processed_path.exists():
                download_status = "processed_cache_present"
            elif raw_path.exists():
                download_status = "raw_cache_present"
            elif dataset_path.exists():
                download_status = "dataset_dir_present_without_raw_or_processed"
            else:
                download_status = "not_present_loader_may_download"
        out[dataset] = {
            **hint,
            "data_root": str(data_root),
            "dataset_path": str(dataset_path),
            "dataset_path_exists": dataset_path.exists(),
            "raw_path": str(raw_path),
            "raw_path_exists": raw_path.exists(),
            "processed_path": str(processed_path),
            "processed_path_exists": processed_path.exists(),
            "dataset_tree_hash": tree_hash,
            "dataset_hash_status": hash_status,
            "download_status": download_status,
        }
    return out


def environment_metadata(device: str) -> dict[str, Any]:
    import torch

    return {
        "command": " ".join([sys.executable, *sys.argv]),
        "device": device,
        "python_version": sys.version.split()[0],
        "torch_version": torch.__version__,
        "pyg_version": optional_version("torch_geometric"),
        "numpy_version": optional_version("numpy"),
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda,
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }
