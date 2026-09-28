"""Clip-level three-way merge of domain files. Pure functions — no git, no disk.

A line-based text merge of cuts.json reports a conflict whenever both
branches touch nearby lines, e.g. two editors each adding a clip. Here lists of
clips and markers are first keyed by a stable id, then merged field by field,
so only the *same field changed differently on both sides* is a conflict.
"""

import copy
from typing import Dict, List, Optional, Tuple

from ..timeline.store import DomainFiles

Conflict = Dict[str, object]  # {path, domain, base, ours, theirs}

# Sentinel for "key absent on this side" (distinct from a JSON null).
_MISSING = object()


# ── Normalisation: track lists ⇄ dicts keyed by stable ids ──────────────────

def _keyed_items(tracks: list, id_key: str, track_field: str) -> Dict[str, dict]:
    """Flatten track lists into {item_id: item}, remembering the track index."""
    items = {}
    for track in tracks:
        for item in track.get("items", []):
            entry = copy.deepcopy(item)
            entry[track_field] = track.get("index", 1)
            items[str(entry[id_key])] = entry
    return items


def _unkeyed_tracks(items: Dict[str, dict], track_field: str, start_key: str,
                    keep_field: bool, min_tracks: int) -> list:
    """Rebuild a sorted track list from {item_id: item}."""
    by_track: Dict[int, list] = {i: [] for i in range(1, min_tracks + 1)}
    for item in items.values():
        entry = copy.deepcopy(item)
        idx = int(entry.get(track_field, 1))
        if not keep_field:
            entry.pop(track_field, None)
        by_track.setdefault(idx, []).append(entry)
    return [
        {
            "index": idx,
            "items": sorted(by_track[idx], key=lambda i: (i.get(start_key, 0), str(i.get("id", "")))),
        }
        for idx in sorted(by_track)
    ]


def _normalize_domain(domain: str, data: dict) -> dict:
    """Turn a domain file into nested dicts keyed by stable ids.

    Clips are keyed by id, markers by frame; color grades and effects are
    already dicts keyed by clip id.
    """
    data = copy.deepcopy(data or {})
    if domain == "cuts":
        data["video_tracks"] = _keyed_items(data.get("video_tracks", []), "id", "track_index")
    elif domain == "audio":
        data["audio_tracks"] = _keyed_items(data.get("audio_tracks", []), "id", "_track")
    elif domain == "markers":
        data["markers"] = {str(m["frame"]): m for m in data.get("markers", [])}
    return data


def _denormalize_domain(domain: str, data: dict, reference: dict) -> dict:
    """Inverse of _normalize_domain. `reference` supplies minimum track counts."""
    data = copy.deepcopy(data)
    if domain == "cuts":
        min_tracks = len(reference.get("video_tracks", []))
        data["video_tracks"] = _unkeyed_tracks(
            data.get("video_tracks", {}), "track_index", "record_start_frame", True, min_tracks
        )
    elif domain == "audio":
        min_tracks = len(reference.get("audio_tracks", []))
        data["audio_tracks"] = _unkeyed_tracks(
            data.get("audio_tracks", {}), "_track", "start_frame", False, min_tracks
        )
    elif domain == "markers":
        data["markers"] = sorted(data.get("markers", {}).values(), key=lambda m: m["frame"])
    return data


# ── Recursive value merge ───────────────────────────────────────────────────

def _merge_value(base, ours, theirs, path: tuple, conflicts: List[Conflict],
                 resolutions: Dict[str, str]):
    """Recursive 3-way merge. Returns the merged value, or _MISSING (deleted)."""
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    if all(isinstance(v, dict) for v in (ours, theirs)) and (base is _MISSING or isinstance(base, dict)):
        base_d = base if isinstance(base, dict) else {}
        merged = {}
        for key in sorted(set(base_d) | set(ours) | set(theirs)):
            value = _merge_value(
                base_d.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING),
                path + (key,), conflicts, resolutions,
            )
            if value is not _MISSING:
                merged[key] = value
        return merged

    path_key = "/".join(str(p) for p in path)
    choice = resolutions.get(path_key)
    if choice == "ours":
        return ours
    if choice == "theirs":
        return theirs
    conflicts.append({
        "path": path_key,
        "domain": path[0],
        "base": None if base is _MISSING else base,
        "ours": None if ours is _MISSING else ours,
        "theirs": None if theirs is _MISSING else theirs,
    })
    return ours


def _track_reference(ours: dict, theirs: dict) -> dict:
    """Whichever side has more tracks, per track kind (empty tracks survive the merge)."""
    reference: dict = {}
    for side in (ours, theirs):
        for key in ("video_tracks", "audio_tracks"):
            if len(side.get(key, [])) > len(reference.get(key, [])):
                reference[key] = side[key]
    return reference


def three_way_merge_domains(
    base_files: DomainFiles,
    ours_files: DomainFiles,
    theirs_files: DomainFiles,
    resolutions: Optional[Dict[str, str]] = None,
) -> Tuple[DomainFiles, List[Conflict]]:
    """Merge domain files at clip/field granularity.

    Clips are matched by id, markers by frame, grades by clip id. Changes to
    different clips — or different fields of the same clip — combine cleanly.
    Only the same field changed differently on both sides is a conflict.

    Args:
        resolutions: {conflict_path: "ours" | "theirs"} for previously
            reported conflicts.

    Returns:
        (merged_files, conflicts). Each conflict is a dict with path, domain,
        base, ours, theirs. Unresolved conflicts keep "ours" in merged_files.
    """
    resolutions = resolutions or {}
    merged_files: DomainFiles = {}
    conflicts: List[Conflict] = []
    for domain in sorted(set(base_files) | set(ours_files) | set(theirs_files)):
        base = _normalize_domain(domain, base_files.get(domain, {}))
        ours = _normalize_domain(domain, ours_files.get(domain, {}))
        theirs = _normalize_domain(domain, theirs_files.get(domain, {}))
        merged = _merge_value(base, ours, theirs, (domain,), conflicts, resolutions)
        if merged is _MISSING:
            merged = {}
        reference = _track_reference(ours_files.get(domain, {}), theirs_files.get(domain, {}))
        merged_files[domain] = _denormalize_domain(domain, merged, reference)
    return merged_files, conflicts
