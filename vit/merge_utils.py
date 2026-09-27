"""Helpers for deterministic merge policies on timeline domain files."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class OverlayMergePlan:
    """Bookkeeping for title-overlay remaps created during merge."""

    id_remaps: Dict[str, str] = field(default_factory=dict)
    generator_renames: Dict[str, str] = field(default_factory=dict)
    grade_renames: Dict[str, str] = field(default_factory=dict)
    grade_restore_ours: set[str] = field(default_factory=set)


def _is_generator_item(item: dict) -> bool:
    return item.get("item_type") in ("generator", "title") or str(
        item.get("media_ref", "")
    ).startswith("generator:")


def _collect_track_items(cuts: dict) -> Dict[int, list]:
    return {
        int(track.get("index", 0)): copy.deepcopy(track.get("items", []))
        for track in cuts.get("video_tracks", [])
    }


def _collect_existing_ids(track_map: Dict[int, list]) -> set:
    return {
        item.get("id")
        for items in track_map.values()
        for item in items
        if item.get("id")
    }


def _find_item(track_map: Dict[int, list], item_id: str) -> tuple[int, dict] | None:
    for track_index, items in track_map.items():
        for item in items:
            if item.get("id") == item_id:
                return track_index, item
    return None


def _find_overlay_item(track_map: Dict[int, list], item_id: str) -> tuple[int, dict] | None:
    prefix = f"{item_id}_overlay"
    for track_index, items in track_map.items():
        for item in items:
            current_id = str(item.get("id", ""))
            if current_id.startswith(prefix) and _is_generator_item(item):
                return track_index, item
    return None


def _remove_item(track_map: Dict[int, list], item_id: str) -> None:
    for track_index, items in list(track_map.items()):
        filtered = [item for item in items if item.get("id") != item_id]
        track_map[track_index] = filtered


def _ranges_overlap(a: dict, b: dict) -> bool:
    return not (
        a.get("record_end_frame", 0) <= b.get("record_start_frame", 0)
        or b.get("record_end_frame", 0) <= a.get("record_start_frame", 0)
    )


def _first_overlay_track(track_map: Dict[int, list], item: dict) -> int:
    track_index = 2
    while True:
        items = track_map.get(track_index, [])
        if all(not _ranges_overlap(existing, item) for existing in items):
            return track_index
        track_index += 1


def _unique_overlay_id(existing_ids: set, base_id: str) -> str:
    candidate = f"{base_id}_overlay"
    suffix = 1
    while candidate in existing_ids:
        suffix += 1
        candidate = f"{base_id}_overlay_{suffix}"
    return candidate


def _remap_generator_fields(item: dict, old_id: str, new_id: str, plan: OverlayMergePlan) -> None:
    media_ref = str(item.get("media_ref", ""))
    if media_ref == f"generator:{old_id}":
        item["media_ref"] = f"generator:{new_id}"

    comp_file = item.get("fusion_comp_file")
    if comp_file == f"{old_id}.comp":
        new_comp = f"{new_id}.comp"
        item["fusion_comp_file"] = new_comp
        plan.generator_renames[comp_file] = new_comp


def merge_timeline_domains_for_overlays(
    merged_files: Dict[str, dict],
    ours_files: Dict[str, dict],
    theirs_files: Dict[str, dict],
) -> Tuple[Dict[str, dict], OverlayMergePlan]:
    """Normalize title/media ID collisions so titles become overlays."""
    merged = copy.deepcopy(merged_files)
    plan = OverlayMergePlan()

    ours_cuts = ours_files.get("cuts", {})
    theirs_cuts = theirs_files.get("cuts", {})
    track_map = _collect_track_items(merged.get("cuts", {}))
    existing_ids = _collect_existing_ids(track_map)

    for their_track in theirs_cuts.get("video_tracks", []):
        for their_item in their_track.get("items", []):
            their_id = their_item.get("id")
            if not their_id:
                continue

            ours_match = _find_item(_collect_track_items(ours_cuts), their_id)
            if ours_match is None:
                continue

            _, ours_item = ours_match
            if (
                not _is_generator_item(ours_item)
                and _is_generator_item(their_item)
            ):
                current_match = _find_item(track_map, their_id)
                overlay_match = _find_overlay_item(track_map, their_id)

                if overlay_match:
                    _, existing_overlay = overlay_match
                    new_id = existing_overlay["id"]
                else:
                    new_id = _unique_overlay_id(existing_ids, their_id)
                    existing_ids.add(new_id)

                if current_match and _is_generator_item(current_match[1]):
                    _remove_item(track_map, their_id)

                current_after = _find_item(track_map, their_id)
                if current_after is None:
                    restored_media = copy.deepcopy(ours_item)
                    restored_media["track_index"] = ours_item.get("track_index", 1)
                    track_map.setdefault(restored_media["track_index"], []).append(restored_media)

                if overlay_match:
                    overlay_track, overlay_item = overlay_match
                    overlay_item["track_index"] = overlay_track
                else:
                    overlay_item = copy.deepcopy(their_item)
                    overlay_track = _first_overlay_track(track_map, overlay_item)
                    overlay_item["track_index"] = overlay_track

                if overlay_item.get("id") != new_id:
                    plan.id_remaps[their_id] = new_id
                overlay_item["id"] = new_id
                _remap_generator_fields(overlay_item, their_id, new_id, plan)
                if not overlay_match:
                    track_map.setdefault(overlay_track, []).append(overlay_item)

    merged["cuts"] = {
        "video_tracks": [
            {
                "index": idx,
                "items": sorted(
                    items,
                    key=lambda item: (
                        item.get("record_start_frame", 0),
                        item.get("record_end_frame", 0),
                        item.get("id", ""),
                    ),
                ),
            }
            for idx, items in sorted(track_map.items())
        ]
    }

    merged.setdefault("effects", {})
    merged.setdefault("color", {})
    merged["effects"].setdefault("clip_effects", {})
    merged["color"].setdefault("grades", {})

    for original_id, overlay_id in plan.id_remaps.items():
        ours_effect = ours_files.get("effects", {}).get("clip_effects", {}).get(original_id)
        theirs_effect = theirs_files.get("effects", {}).get("clip_effects", {}).get(original_id)
        if ours_effect is not None:
            merged["effects"]["clip_effects"][original_id] = copy.deepcopy(ours_effect)
        else:
            merged["effects"]["clip_effects"].pop(original_id, None)
        if theirs_effect is not None:
            merged["effects"]["clip_effects"][overlay_id] = copy.deepcopy(theirs_effect)

        ours_grade = ours_files.get("color", {}).get("grades", {}).get(original_id)
        theirs_grade = theirs_files.get("color", {}).get("grades", {}).get(original_id)
        if ours_grade is not None:
            merged["color"]["grades"][original_id] = copy.deepcopy(ours_grade)
        else:
            merged["color"]["grades"].pop(original_id, None)

        if theirs_grade is not None:
            grade_copy = copy.deepcopy(theirs_grade)
            drx_file = grade_copy.get("drx_file")
            if drx_file:
                if str(drx_file).startswith(f"{original_id}_"):
                    new_drx = str(drx_file).replace(f"{original_id}_", f"{overlay_id}_", 1)
                elif str(drx_file).startswith(original_id):
                    new_drx = str(drx_file).replace(original_id, overlay_id, 1)
                else:
                    new_drx = f"{overlay_id}.drx"
                if new_drx != drx_file:
                    plan.grade_renames[str(drx_file)] = new_drx
                    if ours_grade is not None and ours_grade.get("drx_file") == drx_file:
                        plan.grade_restore_ours.add(str(drx_file))
                    grade_copy["drx_file"] = new_drx

            lut_file = grade_copy.get("lut_file")
            if lut_file:
                if str(lut_file).startswith(f"{original_id}_"):
                    new_lut = str(lut_file).replace(f"{original_id}_", f"{overlay_id}_", 1)
                elif str(lut_file).startswith(original_id):
                    new_lut = str(lut_file).replace(original_id, overlay_id, 1)
                else:
                    new_lut = f"{overlay_id}.cube"
                if new_lut != lut_file:
                    plan.grade_renames[str(lut_file)] = new_lut
                    if ours_grade is not None and ours_grade.get("lut_file") == lut_file:
                        plan.grade_restore_ours.add(str(lut_file))
                    grade_copy["lut_file"] = new_lut
            merged["color"]["grades"][overlay_id] = grade_copy

    merged["metadata"] = copy.deepcopy(merged.get("metadata", {}))
    merged["metadata"].setdefault("track_count", {})
    highest_video_track = max(
        (track["index"] for track in merged["cuts"].get("video_tracks", [])),
        default=1,
    )
    merged["metadata"]["track_count"]["video"] = max(
        int(merged_files.get("metadata", {}).get("track_count", {}).get("video", 0) or 0),
        highest_video_track,
    )

    if "audio_tracks" in merged.get("audio", {}):
        merged["metadata"]["track_count"]["audio"] = max(
            int(merged_files.get("metadata", {}).get("track_count", {}).get("audio", 0) or 0),
            len(merged["audio"].get("audio_tracks", [])),
        )

    return merged, plan


def referenced_sidecars(domain_files: Dict[str, dict]) -> Tuple[set, set]:
    """Return referenced generator and grade sidecar paths."""
    generator_files = set()
    grade_files = set()

    for track in domain_files.get("cuts", {}).get("video_tracks", []):
        for item in track.get("items", []):
            comp_file = item.get("fusion_comp_file")
            if comp_file:
                generator_files.add(f"timeline/generators/{comp_file}")

    for grade in domain_files.get("color", {}).get("grades", {}).values():
        drx_file = grade.get("drx_file")
        if drx_file:
            grade_files.add(f"timeline/grades/{drx_file}")
        lut_file = grade.get("lut_file")
        if lut_file:
            grade_files.add(f"timeline/grades/{lut_file}")

    return generator_files, grade_files


_MISSING = object()


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
            "items": sorted(
                by_track[idx], key=lambda i: (i.get(start_key, 0), str(i.get("id", "")))
            ),
        }
        for idx in sorted(by_track)
    ]


def _normalize_domain(domain: str, data: dict) -> dict:
    """Turn a domain file into nested dicts keyed by stable ids.

    Lists of clips/markers become dicts so a clip-level 3-way merge can
    combine independent additions that a line-based text merge would
    report as conflicts.
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


def _merge_value(base, ours, theirs, path: tuple, conflicts: list, resolutions: dict):
    """Recursive 3-way merge. Returns merged value or _MISSING (deleted)."""
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    if all(isinstance(v, dict) for v in (ours, theirs)) and (
        base is _MISSING or isinstance(base, dict)
    ):
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


def three_way_merge_domains(
    base_files: Dict[str, dict],
    ours_files: Dict[str, dict],
    theirs_files: Dict[str, dict],
    resolutions: Dict[str, str] = None,
) -> Tuple[Dict[str, dict], list]:
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
    merged_files: Dict[str, dict] = {}
    conflicts: list = []
    for domain in sorted(set(base_files) | set(ours_files) | set(theirs_files)):
        base = _normalize_domain(domain, base_files.get(domain, {}))
        ours = _normalize_domain(domain, ours_files.get(domain, {}))
        theirs = _normalize_domain(domain, theirs_files.get(domain, {}))
        merged = _merge_value(base, ours, theirs, (domain,), conflicts, resolutions)
        if merged is _MISSING:
            merged = {}
        reference = {}
        for side in (ours_files.get(domain, {}), theirs_files.get(domain, {})):
            for key in ("video_tracks", "audio_tracks"):
                if len(side.get(key, [])) > len(reference.get(key, [])):
                    reference[key] = side[key]
        merged_files[domain] = _denormalize_domain(domain, merged, reference)
    return merged_files, conflicts


def domain_file_map() -> Dict[str, str]:
    return {
        "cuts": "timeline/cuts.json",
        "color": "timeline/color.json",
        "audio": "timeline/audio.json",
        "effects": "timeline/effects.json",
        "markers": "timeline/markers.json",
        "metadata": "timeline/metadata.json",
        "manifest": "assets/manifest.json",
    }


# ── Merge orchestration (shared by the CLI, the Resolve panels and the demo) ──

@dataclass
class MergeOutcome:
    """Result of perform_merge.

    status is "merged", "conflicts" (nothing was changed — ask the user and call
    again with resolutions) or "up_to_date".
    """

    status: str
    conflicts: list = field(default_factory=list)
    commit: str = ""
    git_text_conflicts: List[str] = field(default_factory=list)
    base_files: Dict[str, dict] = field(default_factory=dict)
    ours_files: Dict[str, dict] = field(default_factory=dict)
    theirs_files: Dict[str, dict] = field(default_factory=dict)
    merged_files: Dict[str, dict] = field(default_factory=dict)


def load_domain_files(project_dir: str, ref: Optional[str]) -> Dict[str, dict]:
    """Domain files at a git ref, keyed by domain. Files absent at ref are omitted."""
    from .core import git_show_file

    files = {}
    if not ref:
        return files
    for domain, relpath in domain_file_map().items():
        raw = git_show_file(project_dir, ref, relpath)
        if raw is None:
            continue
        try:
            files[domain] = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"{relpath} at {ref} is not valid JSON: {e}") from e
    return files


def write_domain_files(project_dir: str, files: Dict[str, dict]) -> None:
    """Write domain files (only the domains present in `files`)."""
    from .json_writer import _write_json

    paths = domain_file_map()
    for domain, data in files.items():
        if domain in paths:
            _write_json(os.path.join(project_dir, paths[domain]), data)


def sidecar_side(relpath: str, merged: Dict[str, dict], theirs: Dict[str, dict]) -> str:
    """Pick the side of a conflicted binary sidecar that matches the merged JSON."""
    name = relpath.rsplit("/", 1)[-1]
    their_grades = theirs.get("color", {}).get("grades", {})
    for clip_id, grade in merged.get("color", {}).get("grades", {}).items():
        if name in (grade.get("drx_file"), grade.get("lut_file")):
            return "theirs" if their_grades.get(clip_id) == grade else "ours"
    their_items = {
        item.get("id"): item
        for track in theirs.get("cuts", {}).get("video_tracks", [])
        for item in track.get("items", [])
    }
    for track in merged.get("cuts", {}).get("video_tracks", []):
        for item in track.get("items", []):
            if item.get("fusion_comp_file") == name:
                return "theirs" if their_items.get(item.get("id")) == item else "ours"
    return "ours"


def format_conflicts(conflicts: list, ours_label: str = "ours", theirs_label: str = "theirs") -> str:
    """Human-readable list of conflicts for terminal or dialog output."""
    def show(value):
        return "(deleted)" if value is None else json.dumps(value, sort_keys=True)

    lines = []
    for c in conflicts:
        lines.append(f"  {c['path']}")
        lines.append(f"    {ours_label}: {show(c['ours'])}")
        lines.append(f"    {theirs_label}: {show(c['theirs'])}")
    return "\n".join(lines)


def perform_merge(
    project_dir: str,
    branch: str,
    resolutions: Optional[Dict[str, str]] = None,
    message: Optional[str] = None,
) -> MergeOutcome:
    """Merge `branch` into the current branch at clip/field granularity.

    git records the merge (a real two-parent commit); the content comes from
    three_way_merge_domains, so independent edits to the same JSON file combine
    instead of one side overwriting the other. If the same property changed
    differently on both sides, returns status "conflicts" without touching the
    repo — pass the user's choices back as `resolutions`.

    The working tree must be clean (auto-save first).
    """
    from .core import (
        GitError, git_add, git_checkout_ours, git_checkout_theirs, git_commit,
        git_current_branch, git_list_conflicted_files, git_merge, git_merge_abort,
        git_merge_base, git_merge_tree_conflicts, git_rev_parse, git_rm,
    )

    theirs_hash = git_rev_parse(project_dir, branch)
    if theirs_hash is None:
        raise GitError(f"Unknown branch '{branch}'")
    base = git_merge_base(project_dir, "HEAD", branch)
    if base == theirs_hash:
        return MergeOutcome(status="up_to_date")

    base_f = load_domain_files(project_dir, base)
    ours_f = load_domain_files(project_dir, "HEAD")
    theirs_f = load_domain_files(project_dir, branch)
    merged, conflicts = three_way_merge_domains(base_f, ours_f, theirs_f, resolutions)
    outcome = MergeOutcome(
        status="conflicts", conflicts=conflicts,
        base_files=base_f, ours_files=ours_f, theirs_files=theirs_f, merged_files=merged,
    )
    if conflicts:
        return outcome

    outcome.git_text_conflicts = git_merge_tree_conflicts(project_dir, "HEAD", branch) or []
    current = git_current_branch(project_dir)
    _, output = git_merge(project_dir, branch, no_commit=True)
    if git_rev_parse(project_dir, "MERGE_HEAD") is None:
        raise GitError(f"git could not start merging '{branch}': {output.strip()}")

    try:
        write_domain_files(project_dir, merged)
        domain_paths = {domain_file_map()[d] for d in merged if d in domain_file_map()}
        to_stage = set(domain_paths)
        for relpath in git_list_conflicted_files(project_dir):
            if relpath in domain_paths:
                continue
            if not relpath.startswith("timeline/"):
                raise GitError(f"Conflict outside the timeline needs manual resolution: {relpath}")
            side = sidecar_side(relpath, merged, theirs_f)
            try:
                (git_checkout_theirs if side == "theirs" else git_checkout_ours)(project_dir, [relpath])
                to_stage.add(relpath)
            except GitError:
                git_rm(project_dir, [relpath])  # that side deleted the sidecar
        git_add(project_dir, sorted(to_stage))
        remaining = git_list_conflicted_files(project_dir)
        if remaining:
            raise GitError(f"Unresolved conflicts: {', '.join(remaining)}")
        outcome.commit = git_commit(project_dir, message or f"vit: merge '{branch}' into '{current}'")
    except Exception:
        git_merge_abort(project_dir)
        raise

    outcome.status = "merged"
    return outcome
