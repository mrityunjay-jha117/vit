"""Vit: Merge — Resolve Workspace > Scripts menu item.

Merges a branch into the current branch and restores the timeline.
The `resolve` variable is injected by DaVinci Resolve.
"""
import os
import subprocess
import sys
import traceback

try:
    _real = os.path.realpath(__file__)
except NameError:
    _real = None
if _real:
    _root = os.path.dirname(os.path.dirname(_real))
    if os.path.isdir(os.path.join(_root, "vit")) and _root not in sys.path:
        sys.path.insert(0, _root)
else:
    _pf = os.path.expanduser("~/.vit/package_path")
    if os.path.exists(_pf):
        with open(_pf) as _f:
            _root = _f.read().strip()
        if _root and os.path.isdir(os.path.join(_root, "vit")) and _root not in sys.path:
            sys.path.insert(0, _root)


def main():
    import json

    from resolve_plugin.plugin_utils import (
        auto_save_current_timeline, check_resolve, get_project_dir, ask_choice,
        show_error, show_message, _log,
    )
    from vit.core import (
        git_add, git_commit, git_current_branch,
        git_is_clean, git_list_branches, git_list_conflicted_files,
        git_merge, git_merge_abort, git_merge_base, git_rev_parse, GitError,
    )
    from vit.deserializer import (
        capture_restore_state,
        deserialize_timeline,
        restore_timeline_overlays,
        should_restore_overlays_only,
    )
    from vit.merge_utils import (
        domain_file_map,
        format_conflicts,
        sidecar_side,
        load_domain_files,
        merge_timeline_domains_for_overlays,
        referenced_sidecars,
        three_way_merge_domains,
    )
    from vit.validator import validate_project, format_issues

    def _load_domain_files_at_ref(ref_name):
        files = load_domain_files(project_dir, ref_name)
        return {domain: files.get(domain, {}) for domain in domain_file_map()}

    def _write_binary_from_ref(ref_name, relpath, dest_relpath):
        result = subprocess.run(
            ["git", "show", f"{ref_name}:{relpath}"],
            cwd=project_dir,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return False

        dest_path = os.path.join(project_dir, dest_relpath)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(result.stdout)
        return True

    def _write_domain_files(domain_files):
        for domain, relpath in domain_file_map().items():
            filepath = os.path.join(project_dir, relpath)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(domain_files.get(domain, {}), f, indent=2, sort_keys=True)
                f.write("\n")

    def _remove_path(relpath):
        path = os.path.join(project_dir, relpath)
        if not os.path.exists(path):
            return
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relpath],
            cwd=project_dir,
            capture_output=True,
            check=False,
        )
        if tracked.returncode == 0:
            subprocess.run(
                ["git", "rm", "-f", relpath],
                cwd=project_dir,
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.remove(path)
            except OSError:
                pass

    def _apply_overlay_sidecars(branch_name, ours_ref, merged_files, merge_plan):
        referenced_generators, referenced_grades = referenced_sidecars(merged_files)
        staged_paths = set(domain_file_map().values())

        for old_name, new_name in merge_plan.generator_renames.items():
            if _write_binary_from_ref(
                branch_name,
                f"timeline/generators/{old_name}",
                f"timeline/generators/{new_name}",
            ):
                staged_paths.add(f"timeline/generators/{new_name}")
            old_relpath = f"timeline/generators/{old_name}"
            if old_relpath not in referenced_generators:
                _remove_path(old_relpath)

        for old_name, new_name in merge_plan.grade_renames.items():
            if _write_binary_from_ref(
                branch_name,
                f"timeline/grades/{old_name}",
                f"timeline/grades/{new_name}",
            ):
                staged_paths.add(f"timeline/grades/{new_name}")
            old_relpath = f"timeline/grades/{old_name}"
            if old_name in merge_plan.grade_restore_ours:
                if _write_binary_from_ref(ours_ref, old_relpath, old_relpath):
                    staged_paths.add(old_relpath)
            elif old_relpath not in referenced_grades:
                _remove_path(old_relpath)

        for relpath in sorted(referenced_generators | referenced_grades):
            if os.path.exists(os.path.join(project_dir, relpath)):
                staged_paths.add(relpath)

        return staged_paths, referenced_generators, referenced_grades

    def _normalize_overlay_merge(branch_name):
        theirs_files = _load_domain_files_at_ref(branch_name)
        current_files = capture_restore_state(project_dir)["domains"]
        normalized_files, merge_plan = merge_timeline_domains_for_overlays(
            current_files,
            pre_merge_state["domains"],
            theirs_files,
        )

        if normalized_files == current_files and not (
            merge_plan.id_remaps or merge_plan.generator_renames or merge_plan.grade_renames
        ):
            return False

        _write_domain_files(normalized_files)
        staged_paths, _, _ = _apply_overlay_sidecars(
            branch_name, "ORIG_HEAD", normalized_files, merge_plan
        )
        git_add(project_dir, sorted(staged_paths))
        git_commit(project_dir, f"vit: normalized '{branch_name}' merge as overlay")
        return True

    try:
        _resolve = resolve  # noqa: F821 — injected by DaVinci Resolve
    except NameError:
        _resolve = None
    if not check_resolve(_resolve):
        return

    project_dir = get_project_dir()
    if not project_dir:
        show_error("Vit", "No vit project found.\nRun 'vit init <path>' from terminal.")
        return

    current = git_current_branch(project_dir)
    branches = git_list_branches(project_dir)
    other_branches = [b for b in branches if b != current]

    if not other_branches:
        show_message("Vit", "No other branches to merge.")
        return

    _log(f"Current branch: {current}")
    _log(f"Merge candidates: {', '.join(other_branches)}")

    branch = ask_choice(
        "Vit: Merge Branch",
        f"Current: {current}\nSelect branch to merge into '{current}':",
        other_branches,
    )
    if not branch:
        _log("No branch selected — cancelled.")
        _log("To merge from CLI: vit merge <branch>")
        return

    # Always serialize the active timeline before merge. Resolve changes exist
    # in memory until saved, so git status cannot reliably detect them.
    if not auto_save_current_timeline(
        _resolve, project_dir, f"merging '{branch}' into '{current}'"
    ):
        return

    pre_merge_state = capture_restore_state(project_dir)

    # Keep the existing safeguard for already-dirty project files on disk.
    if not git_is_clean(project_dir):
        _log("Working directory still has uncommitted vit files — committing them...")
        git_add(project_dir, ["timeline/", "assets/", ".vit/", ".gitignore"])
        try:
            git_commit(project_dir, f"vit: auto-save before merging '{branch}'")
        except GitError as e:
            if "nothing to commit" not in str(e):
                show_error("Vit", f"Auto-save failed:\n{e}")
                return

    _log(f"Merging '{branch}' into '{current}'...")
    success, output = git_merge(project_dir, branch)

    if not success:
        conflicted = git_list_conflicted_files(project_dir)
        _log(f"Conflicts in: {', '.join(conflicted)}")

        auto_resolvable = [f for f in conflicted
                           if f.endswith(".drx") or f.startswith("timeline/")]
        non_resolvable = [f for f in conflicted if f not in auto_resolvable]

        if auto_resolvable and not non_resolvable:
            _log(
                f"Auto-resolving {len(auto_resolvable)} timeline conflict(s) "
                f"with overlay-aware merge rules..."
            )
            try:
                theirs_files = _load_domain_files_at_ref(branch)
                base_ref = git_merge_base(project_dir, "HEAD", branch)
                base_files = _load_domain_files_at_ref(base_ref) if base_ref else {}
                structural, conflicts = three_way_merge_domains(
                    base_files, pre_merge_state["domains"], theirs_files,
                )
                if conflicts:
                    raise GitError(
                        "Both branches changed the same thing:\n"
                        + format_conflicts(conflicts, current, branch)
                    )
                merged_files, merge_plan = merge_timeline_domains_for_overlays(
                    structural,
                    pre_merge_state["domains"],
                    theirs_files,
                )
                _write_domain_files(merged_files)
                staged_paths, referenced_generators, referenced_grades = _apply_overlay_sidecars(
                    branch, "HEAD", merged_files, merge_plan
                )

                for path in auto_resolvable:
                    if path.endswith(".json"):
                        staged_paths.add(path)
                        continue
                    if not path.endswith((".comp", ".drx", ".cube")):
                        raise GitError(f"Don't know how to merge {path}")
                    if path not in referenced_generators and path not in referenced_grades:
                        subprocess.run(
                            ["git", "rm", "-f", path],
                            cwd=project_dir,
                            capture_output=True,
                            check=False,
                        )
                        continue
                    # Take the sidecar from the side whose grade/title the
                    # merged JSON kept, so the binary matches the metadata.
                    filename = path.rsplit("/", 1)[-1]
                    if filename in merge_plan.grade_restore_ours:
                        source = "HEAD"
                    elif sidecar_side(path, merged_files, theirs_files) == "theirs":
                        source = branch
                    else:
                        source = "HEAD"
                    if not _write_binary_from_ref(source, path, path):
                        raise GitError(f"Could not restore {path} from {source}")
                    staged_paths.add(path)

                git_add(project_dir, sorted(staged_paths))
                remaining = git_list_conflicted_files(project_dir)
                if remaining:
                    raise GitError(f"Unresolved conflicts: {', '.join(remaining)}")
                git_commit(project_dir,
                           f"vit: merged '{branch}' into '{current}' (auto-resolved)")
                success = True
                output = "Auto-resolved timeline conflicts with overlay-aware merge."
            except (GitError, ValueError) as e:
                _log(f"Auto-resolve failed: {e}")
                output = str(e)

    if success:
        normalized_overlay = False
        try:
            normalized_overlay = _normalize_overlay_merge(branch)
        except GitError as e:
            _log(f"Overlay normalization skipped: {e}")

        issues = validate_project(project_dir)
        if issues:
            msg = f"Merge succeeded with issues:\n{format_issues(issues)}"
        else:
            msg = f"Merged '{branch}' into '{current}' cleanly."
        if normalized_overlay:
            msg += "\n\nTitle clips were normalized as overlays."

        project = _resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline()
        if timeline:
            merged_state = capture_restore_state(project_dir)
            overlays_only = should_restore_overlays_only(
                pre_merge_state, merged_state
            )

            if overlays_only:
                restore_timeline_overlays(timeline, project_dir, resolve_app=_resolve)
                msg += "\n\nTimeline overlays restored."
            else:
                deserialize_timeline(timeline, project, project_dir, resolve_app=_resolve)
                msg += "\n\nTimeline restored."

        show_message("Vit", msg)
    else:
        if git_rev_parse(project_dir, "MERGE_HEAD"):
            git_merge_abort(project_dir)
        show_error(
            "Vit",
            f"Merge was not applied — nothing changed.\n\n{output}\n\n"
            f"Run 'vit merge {branch}' in a terminal to choose which version wins.",
        )


try:
    main()
except Exception:
    print(f"[vit] SCRIPT ERROR:\n{traceback.format_exc()}")
