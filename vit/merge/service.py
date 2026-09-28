"""Merge orchestration: git records the merge, vit supplies the merged timeline."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..git import GitError
from ..project import VitProject
from ..timeline.store import DOMAIN_FILES, DomainFiles
from .three_way import Conflict, three_way_merge_domains

MERGED = "merged"
CONFLICTS = "conflicts"
UP_TO_DATE = "up_to_date"


@dataclass
class MergeOutcome:
    """Result of perform_merge.

    status is MERGED, CONFLICTS (nothing was changed — ask the user and call
    again with resolutions) or UP_TO_DATE.
    """

    status: str
    conflicts: List[Conflict] = field(default_factory=list)
    commit: str = ""
    git_text_conflicts: List[str] = field(default_factory=list)
    base_files: DomainFiles = field(default_factory=dict)
    ours_files: DomainFiles = field(default_factory=dict)
    theirs_files: DomainFiles = field(default_factory=dict)
    merged_files: DomainFiles = field(default_factory=dict)


@dataclass
class MergePreview:
    """What a merge of `branch` would do, computed without touching the repo."""

    base: Optional[str]
    base_files: DomainFiles
    ours_files: DomainFiles
    theirs_files: DomainFiles
    conflicts: List[Conflict]
    git_text_conflicts: List[str]
    up_to_date: bool


def preview_merge(project: VitProject, branch: str) -> MergePreview:
    """Compare HEAD with `branch` against their merge base."""
    theirs_hash = project.repo.rev_parse(branch)
    if theirs_hash is None:
        raise ValueError(f"Unknown branch '{branch}'")
    base = project.repo.merge_base("HEAD", branch)
    base_f, ours_f, theirs_f = project.files_at(base), project.files_at("HEAD"), project.files_at(branch)
    _, conflicts = three_way_merge_domains(base_f, ours_f, theirs_f)
    return MergePreview(
        base=base,
        base_files=base_f,
        ours_files=ours_f,
        theirs_files=theirs_f,
        conflicts=conflicts,
        git_text_conflicts=project.repo.merge_tree_conflicts("HEAD", branch) or [],
        up_to_date=base == theirs_hash,
    )


def perform_merge(
    project: VitProject,
    branch: str,
    resolutions: Optional[Dict[str, str]] = None,
    message: Optional[str] = None,
) -> MergeOutcome:
    """Merge `branch` into the current branch at clip/field granularity.

    git records the merge (a real two-parent commit); the content comes from
    three_way_merge_domains, so independent edits to the same JSON file combine
    instead of one side overwriting the other. If the same property changed
    differently on both sides, returns CONFLICTS without touching the repo —
    pass the user's choices back as `resolutions`.

    The working tree must be clean (auto-save first).
    """
    repo = project.repo
    theirs_hash = repo.rev_parse(branch)
    if theirs_hash is None:
        raise GitError(f"Unknown branch '{branch}'")
    base = repo.merge_base("HEAD", branch)
    if base == theirs_hash:
        return MergeOutcome(status=UP_TO_DATE)

    base_f, ours_f, theirs_f = project.files_at(base), project.files_at("HEAD"), project.files_at(branch)
    merged, conflicts = three_way_merge_domains(base_f, ours_f, theirs_f, resolutions)
    outcome = MergeOutcome(
        status=CONFLICTS, conflicts=conflicts,
        base_files=base_f, ours_files=ours_f, theirs_files=theirs_f, merged_files=merged,
    )
    if conflicts:
        return outcome

    outcome.git_text_conflicts = repo.merge_tree_conflicts("HEAD", branch) or []
    current = repo.current_branch()
    _, output = repo.merge_no_commit(branch)
    if repo.rev_parse("MERGE_HEAD") is None:
        raise GitError(f"git could not start merging '{branch}': {output.strip()}")

    try:
        _write_merge_result(project, merged)
        outcome.commit = repo.commit(message or f"vit: merge '{branch}' into '{current}'")
    except Exception:
        repo.merge_abort()
        raise

    outcome.status = MERGED
    return outcome


def _write_merge_result(project: VitProject, merged: DomainFiles) -> None:
    """Replace git's (possibly conflicted) domain files with vit's merge and stage them."""
    project.store.write_files(merged)
    domain_paths = {DOMAIN_FILES[d] for d in merged if d in DOMAIN_FILES}
    outside = [p for p in project.repo.conflicted_files() if p not in domain_paths]
    if outside:
        raise GitError(f"Conflict outside the timeline needs manual resolution: {', '.join(outside)}")
    project.repo.add(sorted(domain_paths))
    remaining = project.repo.conflicted_files()
    if remaining:
        raise GitError(f"Unresolved conflicts: {', '.join(remaining)}")
