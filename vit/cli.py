"""Vit CLI — all user interaction goes through here."""

import argparse
import json
import os
import sys

from . import __version__
from .core import (
    GitError,
    find_project_root,
    git_add,
    git_branch,
    git_checkout,
    git_clone,
    git_commit,
    git_config_get,
    git_config_set,
    git_current_branch,
    git_diff,
    git_init,
    git_list_branches,
    git_list_conflicted_files,
    git_log,
    git_pull,
    git_push,
    git_push_set_upstream,
    git_remote_add,
    git_remote_list,
    git_remote_remove,
    git_revert,
    git_status,
    is_git_repo,
)
from .json_writer import write_timeline
from .validator import format_issues, validate_project


def _require_project() -> str:
    """Find the vit project root or exit with error."""
    root = find_project_root()
    if root is None:
        print("Error: Not a vit project. Run 'vit init' first.")
        sys.exit(1)
    return root


def cmd_init(args):
    """Initialize a new vit project."""
    project_dir = args.path or os.getcwd()

    if os.path.isdir(os.path.join(project_dir, ".vit")):
        print(f"Error: '{project_dir}' is already a vit project.")
        sys.exit(1)

    git_init(project_dir)

    # Write empty domain files for initial commit
    from .models import Timeline
    write_timeline(project_dir, Timeline())

    git_add(project_dir, [".vit/", "timeline/", "assets/", ".gitignore"])
    git_commit(project_dir, "vit: initial snapshot")
    print(f"  Initialized vit project in {project_dir}")
    print("  Created: .vit/, timeline/, assets/")
    print("  Initial snapshot committed.")


def cmd_add(args):
    """Serialize current timeline state and stage files."""
    project_dir = _require_project()

    # Stages the domain JSON already on disk (written by an editor front end,
    # e.g. the demo server). The CLI never serializes a timeline itself.
    # CLI 'add' just stages the JSON files that were already written.
    git_add(project_dir, ["timeline/", "assets/"])
    print("  Staged timeline and asset files.")


def _ensure_git_identity(project_dir: str) -> None:
    """Prompt for git user.name/email if not set, so commits are attributed correctly."""
    name = git_config_get(project_dir, "user.name")
    email = git_config_get(project_dir, "user.email")
    if not name or not email:
        print("  Git identity not set. This ensures commits show who made each change.")
        if not name:
            name = input("  Your name: ").strip()
            if name:
                git_config_set(project_dir, "user.name", name)
        if not email:
            email = input("  Your email: ").strip()
            if email:
                git_config_set(project_dir, "user.email", email)


def _require_committable(project_dir: str) -> None:
    """Refuse to commit unresolved conflicts or domain JSON that doesn't parse."""
    from .merge_utils import domain_file_map

    conflicted = git_list_conflicted_files(project_dir)
    if conflicted:
        print(f"  Error: unresolved merge conflicts in {', '.join(conflicted)}.")
        print("  Run 'git merge --abort', then 'vit merge <branch>' to merge clip by clip.")
        sys.exit(1)
    for relpath in domain_file_map().values():
        path = os.path.join(project_dir, relpath)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            print(f"  Error: {relpath} is not valid JSON ({e}). Not committing.")
            sys.exit(1)


def cmd_commit(args):
    """Stage and commit current state."""
    project_dir = _require_project()

    _ensure_git_identity(project_dir)
    _require_committable(project_dir)
    git_add(project_dir, ["timeline/", "assets/", ".vit/", ".gitignore"])

    message = args.message
    if not message:
        # Try AI-suggested commit message
        try:
            from .differ import diff_from_project
            diff_text = diff_from_project(project_dir)
            if diff_text.strip():
                from .ai_merge import suggest_commit_message
                suggestion = suggest_commit_message(diff_text)
                if suggestion:
                    print(f'  AI suggested: "{suggestion}"')
                    response = input("  Use this message? [Y/n/edit]: ").strip()
                    if response.lower() in ("", "y", "yes"):
                        message = suggestion
                    elif response.lower() in ("n", "no"):
                        message = None  # will fall through to default
                    else:
                        # User typed a custom message
                        message = response
        except Exception as e:
            print(f"  (AI commit message unavailable: {e})")
        if not message:
            message = "vit: save version"

    try:
        commit_hash = git_commit(project_dir, message)
        print(f"  Committed: {message}")
        if commit_hash:
            print(f"  Hash: {commit_hash}")
    except GitError as e:
        if "nothing to commit" in str(e):
            print("  Nothing to commit — timeline unchanged.")
        else:
            raise


def cmd_branch(args):
    """Create and switch to a new branch."""
    project_dir = _require_project()

    if args.list:
        branches = git_list_branches(project_dir)
        current = git_current_branch(project_dir)
        for b in branches:
            prefix = "* " if b == current else "  "
            print(f"{prefix}{b}")
        return

    if not args.name:
        # Show current branch
        current = git_current_branch(project_dir)
        print(f"  Current branch: {current}")
        return

    git_branch(project_dir, args.name)
    print(f"  Created and switched to branch '{args.name}'")


def cmd_checkout(args):
    """Switch to a branch or commit."""
    project_dir = _require_project()
    git_checkout(project_dir, args.ref)
    print(f"  Switched to '{args.ref}'")


def _ask_conflict_resolutions(conflicts: list, ours: str, theirs: str):
    """Ask which side wins for each conflicting property. None = user aborted."""
    from .merge_utils import format_conflicts

    print(f"\n  Both branches changed the same thing in {len(conflicts)} place(s).")
    print("  Everything else merges automatically.\n")
    resolutions = {}
    for conflict in conflicts:
        print(format_conflicts([conflict], ours_label=ours, theirs_label=theirs))
        while True:
            try:
                choice = input(f"    Keep [o]urs ({ours}) / take [t]heirs ({theirs}) / [a]bort: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None
            if choice in ("o", "ours"):
                resolutions[conflict["path"]] = "ours"
                break
            if choice in ("t", "theirs"):
                resolutions[conflict["path"]] = "theirs"
                break
            if choice in ("a", "abort"):
                return None
        print()
    return resolutions


def cmd_merge(args):
    """Merge a branch into current branch."""
    from .core import git_is_clean
    from .merge_utils import perform_merge
    project_dir = _require_project()
    branch = args.branch
    current = git_current_branch(project_dir)
    if branch == current:
        print(f"  Error: can't merge '{branch}' into itself.")
        sys.exit(1)

    # Auto-commit any outstanding changes before merge
    if not git_is_clean(project_dir):
        print(f"  Auto-saving uncommitted changes before merge...")
        git_add(project_dir, ["timeline/", "assets/", ".vit/", ".gitignore"])
        try:
            git_commit(project_dir, f"vit: auto-save before merging '{branch}'")
        except GitError as e:
            if "nothing to commit" not in str(e):
                raise

    # Pre-merge AI analysis (enrichment only — never blocks on failure)
    if not args.no_ai:
        try:
            from .differ import get_branch_diff_by_category
            from .ai_merge import analyze_branch_comparison
            changes_ours, changes_theirs = get_branch_diff_by_category(
                project_dir, current, branch
            )
            has_changes = any(changes_ours.values()) or any(changes_theirs.values())
            if has_changes:
                print(f"  Analyzing merge of '{branch}' into '{current}'...")
                analysis = analyze_branch_comparison(
                    current, branch, changes_ours, changes_theirs
                )
                rec = analysis.get("recommendation", "manual_review")
                explanation = analysis.get("explanation", "")
                conflicts = analysis.get("conflicts", [])
                if conflicts:
                    print(f"  ⚠ Potential conflicts: {', '.join(conflicts)}")
                if explanation:
                    print(f"  Analysis: {explanation}")
                # Only a real AI recommendation is worth an extra confirmation;
                # the clip-level merge below asks about actual conflicts anyway.
                if rec == "manual_review" and analysis.get("ai_available", True):
                    response = input("  Proceed with merge? [Y/n]: ").strip().lower()
                    if response in ("n", "no"):
                        print("  Merge cancelled.")
                        return
        except Exception as e:
            print(f"  (AI pre-merge analysis skipped: {e})")

    print(f"  Merging '{branch}' into '{current}'...")
    outcome = perform_merge(project_dir, branch)
    if outcome.status == "up_to_date":
        print(f"  Already up to date — '{current}' contains everything from '{branch}'.")
        return
    if outcome.status == "conflicts":
        resolutions = _ask_conflict_resolutions(outcome.conflicts, current, branch)
        if resolutions is None:
            print("  Merge cancelled. Nothing was changed.")
            sys.exit(1)
        outcome = perform_merge(project_dir, branch, resolutions)

    print(f"  Merged '{branch}' into '{current}' (commit {outcome.commit}).")
    if outcome.git_text_conflicts:
        print(f"  Line-based git would have conflicted in: {', '.join(outcome.git_text_conflicts)}")
        print("  Vit merged those files clip by clip instead.")

    issues = validate_project(project_dir)
    if not issues:
        print("  Post-merge validation passed.")
        return

    print(f"\n  Post-merge validation found issues:")
    print(format_issues(issues))
    errors = [i for i in issues if i.severity == "error"]
    if not errors:
        return
    if args.no_ai:
        print("  Fix these in your editor, then run 'vit commit'.")
        return

    from .ai_merge import merge_with_ai
    resolved = merge_with_ai(
        project_dir, branch,
        outcome.base_files, outcome.merged_files, outcome.theirs_files,
        issues, [],
    )
    if resolved:
        git_add(project_dir, ["timeline/", "assets/"])
        git_commit(project_dir, f"vit: AI fix-up after merging '{branch}'")
        print("  AI fix-up committed.")
    else:
        print("  Merge is committed; the issues above still need fixing.")


def _detect_overlapping_domains(
    base_files: dict,
    ours_files: dict,
    theirs_files: dict,
) -> list:
    """Detect which domain files were modified by both branches.

    Returns list of domain names that both branches modified relative to the base.
    This indicates potential semantic conflicts that git may not catch.
    """
    overlapping = []

    for domain in base_files.keys():
        base_content = base_files.get(domain, {})
        ours_content = ours_files.get(domain, {})
        theirs_content = theirs_files.get(domain, {})

        # Check if both branches modified this domain
        ours_changed = base_content != ours_content
        theirs_changed = base_content != theirs_content

        if ours_changed and theirs_changed:
            overlapping.append(domain)

    return overlapping


def cmd_diff(args):
    """Show human-readable diff of timeline changes."""
    project_dir = _require_project()

    ref = args.ref or "HEAD"

    try:
        from .differ import diff_from_project
        output = diff_from_project(project_dir, ref)
        if output.strip():
            print(output)
        else:
            print("  No changes.")
    except GitError:
        # No commits yet or other git issue — show raw git diff
        raw = git_diff(project_dir)
        if raw:
            print(raw)
        else:
            print("  No changes.")


def cmd_log(args):
    """Show version history."""
    project_dir = _require_project()
    count = args.count or 20
    output = git_log(project_dir, max_count=count)
    if output:
        print(output)
        if args.summary:
            try:
                from .ai_merge import summarize_log
                summary = summarize_log(output)
                if summary:
                    print(f"\n  AI Summary: {summary}")
                else:
                    print("\n  AI summary unavailable (check GEMINI_API_KEY).")
            except Exception:
                print("\n  AI summary unavailable.")
    else:
        print("  No commits yet.")


def cmd_status(args):
    """Show current project status."""
    project_dir = _require_project()
    current = git_current_branch(project_dir)
    print(f"  Branch: {current}")

    status = git_status(project_dir)
    if status:
        print(status)
    else:
        print("  Working tree clean.")


def cmd_revert(args):
    """Revert the last commit."""
    project_dir = _require_project()
    try:
        git_revert(project_dir)
        print("  Reverted last commit.")
    except GitError as e:
        print(f"  Error: {e}")
        sys.exit(1)


def cmd_push(args):
    """Push to remote."""
    project_dir = _require_project()
    remote = args.remote or "origin"
    branch = args.branch
    try:
        output = git_push(project_dir, remote, branch)
        print(f"  Pushed to {remote}")
        if output.strip():
            print(output)
    except GitError as e:
        err = str(e)
        print(f"  Error: {err}")
        if _is_github_auth_error(err):
            print()
            print("  GitHub auth failed. SSH is the recommended fix:")
            print("    1. ssh-keygen -t ed25519 -C \"your@email.com\"")
            print("    2. Add ~/.ssh/id_ed25519.pub at https://github.com/settings/ssh/new")
            print("    3. git remote set-url origin git@github.com:user/repo.git")
            print("  Or re-run 'vit collab setup' for a guided walkthrough.")
        sys.exit(1)


def cmd_pull(args):
    """Pull from remote."""
    project_dir = _require_project()
    remote = args.remote or "origin"
    branch = args.branch
    try:
        output = git_pull(project_dir, remote, branch)
        print(f"  Pulled from {remote}")
        if output.strip():
            print(output)
    except GitError as e:
        print(f"  Error: {e}")
        sys.exit(1)


def cmd_validate(args):
    """Run post-merge validation on current state."""
    project_dir = _require_project()
    issues = validate_project(project_dir)
    if issues:
        print(format_issues(issues))
        sys.exit(1)
    else:
        print("  Validation passed — no issues found.")


def _default_clone_dir(url: str) -> str:
    """Folder name git would pick: last path segment without a '.git' suffix."""
    import re
    path = url.rstrip("/\\")
    if re.search(r"[/\\]\.git$", path):
        path = path[:-5]  # ".../project/.git" -> ".../project"
    name = re.split(r"[/\\:]", path)[-1]
    return name[:-4] if name.endswith(".git") else name


def cmd_clone(args):
    """Clone a remote vit repo to a local directory."""
    url = args.url
    dest = args.directory or _default_clone_dir(url)
    if os.path.exists(dest):
        print(f"  Error: '{dest}' already exists.")
        sys.exit(1)
    print(f"  Cloning {url} into '{dest}'...")
    try:
        git_clone(url, dest)
    except GitError as e:
        print(f"  Error: {e}")
        sys.exit(1)
    print(f"  Cloned into '{dest}'")
    print(f"  Note: Media files are not included — assets/manifest.json lists what each clip needs.")


def cmd_remote(args):
    """Manage remote repositories."""
    project_dir = _require_project()

    if args.remote_cmd == "add":
        git_remote_add(project_dir, args.name, args.url)
        print(f"  Added remote '{args.name}' -> {args.url}")

    elif args.remote_cmd == "list" or args.remote_cmd is None:
        remotes = git_remote_list(project_dir)
        if remotes:
            for r in remotes:
                print(f"  {r['name']}\t{r['url']}")
        else:
            print("  No remotes configured. Run 'vit collab setup' to add one.")

    elif args.remote_cmd == "remove":
        git_remote_remove(project_dir, args.name)
        print(f"  Removed remote '{args.name}'")


def _is_github_auth_error(error_str: str) -> bool:
    """Return True if the error looks like a GitHub HTTPS credential rejection."""
    markers = [
        "invalid username or token",
        "password authentication is not supported",
        "authentication failed",
        "could not read username",
        "could not read password",
        "403",
        "remote: forbidden",
    ]
    lower = error_str.lower()
    return any(m in lower for m in markers)


def _https_to_ssh_url(url: str) -> str:
    """Convert https://github.com/user/repo.git → git@github.com:user/repo.git"""
    import re
    match = re.match(r"https://github\.com/([^/]+)/(.+)", url)
    if match:
        return f"git@github.com:{match.group(1)}/{match.group(2)}"
    return url


def _print_ssh_instructions(url: str, remote_name: str) -> None:
    """Print step-by-step SSH setup guidance."""
    ssh_url = _https_to_ssh_url(url)
    print()
    print("  GitHub no longer accepts passwords over HTTPS.")
    print("  SSH is the recommended way to authenticate — set it up once and it")
    print("  works for every GitHub repo on this machine, with no expiry.")
    print()
    print("  Step 1 — Generate an SSH key (skip if you already have one):")
    print('    ssh-keygen -t ed25519 -C "your@email.com"')
    print("    (press Enter through all prompts to accept defaults)")
    print()
    print("  Step 2 — Add your public key to GitHub:")
    print("    cat ~/.ssh/id_ed25519.pub")
    print("    Copy the output, then go to:")
    print("    https://github.com/settings/ssh/new")
    print("    Paste it in and save.")
    print()
    print("  Step 3 — Use the SSH remote URL instead of HTTPS.")
    if ssh_url != url:
        print(f"  Your URL:     {url}")
        print(f"  SSH version:  {ssh_url}")
        print()
        print(f"  Update it with:")
        print(f"    git remote set-url {remote_name} {ssh_url}")
    else:
        print("  Use the SSH URL from GitHub: git@github.com:username/repo.git")
        print("  (On the repo page, click Code -> SSH to copy it)")
    print()
    print("  Then re-run: vit collab setup")


def cmd_collab_setup(args):
    """Interactive wizard to set up collaboration with a remote repository."""
    project_dir = _require_project()

    print("  Vit Collaboration Setup")
    print("  ─────────────────────────────────────")
    print("  Tip: use the SSH URL from GitHub (git@github.com:user/repo.git),")
    print("  not the HTTPS URL. SSH works without entering credentials every time.")
    print()

    # Check existing remotes
    remotes = git_remote_list(project_dir)
    if remotes:
        print(f"  Existing remotes:")
        for r in remotes:
            print(f"    {r['name']}  {r['url']}")
        print()

    url = input("  Remote URL (e.g. git@github.com:you/film.git): ").strip()
    if not url:
        print("  Cancelled.")
        return

    if url.startswith("https://"):
        ssh_url = _https_to_ssh_url(url)
        print()
        print("  Note: you entered an HTTPS URL. SSH is recommended to avoid auth issues.")
        if ssh_url != url:
            print(f"  SSH equivalent: {ssh_url}")
            choice = input("  Switch to SSH URL? [Y/n]: ").strip().lower()
            if choice != "n":
                url = ssh_url
                print(f"  Using SSH URL: {url}")
        print()

    remote_name = "origin"
    if remotes:
        remote_name = input("  Remote name [origin]: ").strip() or "origin"

    # Set identity if needed
    _ensure_git_identity(project_dir)

    # Add remote if not already present
    existing_names = {r["name"] for r in remotes}
    if remote_name not in existing_names:
        git_remote_add(project_dir, remote_name, url)
        print(f"  Added remote '{remote_name}'")

    # Push
    current_branch = git_current_branch(project_dir)
    print(f"  Pushing '{current_branch}' to {remote_name}...")
    try:
        output = git_push_set_upstream(project_dir, remote_name, current_branch)
        if output.strip():
            print(output)
    except GitError as e:
        err = str(e)
        print(f"  Push failed: {err}")
        if _is_github_auth_error(err):
            _print_ssh_instructions(url, remote_name)
        else:
            print("  Make sure the remote repository exists and is empty, then try again.")
        return

    print()
    print("  Setup complete!")
    print(f"  Share this command with collaborators:")
    print(f"    vit clone {url}")
    print()
    print("  Each collaborator should:")
    print("    1. Run: vit clone <url>")
    print("    2. Make media available at the paths in assets/manifest.json")
    print("    3. Create their own branch: vit branch <name>")


def main():
    parser = argparse.ArgumentParser(
        prog="vit",
        description="Git for Video Editing — version control timeline metadata",
    )
    parser.add_argument("--version", action="version", version=f"vit {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    p_init = subparsers.add_parser("init", help="Initialize a new vit project")
    p_init.add_argument("path", nargs="?", help="Project directory (default: current)")
    p_init.set_defaults(func=cmd_init)

    # add
    p_add = subparsers.add_parser("add", help="Stage timeline files")
    p_add.set_defaults(func=cmd_add)

    # commit
    p_commit = subparsers.add_parser("commit", help="Save a version")
    p_commit.add_argument("-m", "--message", help="Commit message")
    p_commit.set_defaults(func=cmd_commit)

    # branch
    p_branch = subparsers.add_parser("branch", help="Create or list branches")
    p_branch.add_argument("name", nargs="?", help="Branch name to create")
    p_branch.add_argument("-l", "--list", action="store_true", help="List branches")
    p_branch.set_defaults(func=cmd_branch)

    # checkout
    p_checkout = subparsers.add_parser("checkout", help="Switch branch or version")
    p_checkout.add_argument("ref", help="Branch name or commit hash")
    p_checkout.set_defaults(func=cmd_checkout)

    # merge
    p_merge = subparsers.add_parser("merge", help="Merge a branch")
    p_merge.add_argument("branch", help="Branch to merge")
    p_merge.add_argument("--no-ai", action="store_true", help="Skip AI merge resolution")
    p_merge.set_defaults(func=cmd_merge)

    # diff
    p_diff = subparsers.add_parser("diff", help="Show timeline changes")
    p_diff.add_argument("ref", nargs="?", help="Compare against ref (default: HEAD)")
    p_diff.set_defaults(func=cmd_diff)

    # log
    p_log = subparsers.add_parser("log", help="Show version history")
    p_log.add_argument("-n", "--count", type=int, help="Max entries (default: 20)")
    p_log.add_argument("--summary", action="store_true", help="Show AI summary of recent commits")
    p_log.set_defaults(func=cmd_log)

    # status
    p_status = subparsers.add_parser("status", help="Show project status")
    p_status.set_defaults(func=cmd_status)

    # revert
    p_revert = subparsers.add_parser("revert", help="Revert last version")
    p_revert.set_defaults(func=cmd_revert)

    # push
    p_push = subparsers.add_parser("push", help="Push to remote")
    p_push.add_argument("--remote", default="origin", help="Remote name")
    p_push.add_argument("--branch", help="Branch to push")
    p_push.set_defaults(func=cmd_push)

    # pull
    p_pull = subparsers.add_parser("pull", help="Pull from remote")
    p_pull.add_argument("--remote", default="origin", help="Remote name")
    p_pull.add_argument("--branch", help="Branch to pull")
    p_pull.set_defaults(func=cmd_pull)

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate timeline consistency")
    p_validate.set_defaults(func=cmd_validate)

    # clone
    p_clone = subparsers.add_parser("clone", help="Clone a remote vit project")
    p_clone.add_argument("url", help="Remote URL to clone")
    p_clone.add_argument("directory", nargs="?", help="Target directory (default: repo name)")
    p_clone.set_defaults(func=cmd_clone)

    # remote
    p_remote = subparsers.add_parser("remote", help="Manage remote repositories")
    remote_sub = p_remote.add_subparsers(dest="remote_cmd")
    p_remote.set_defaults(func=cmd_remote, remote_cmd=None)

    p_remote_add = remote_sub.add_parser("add", help="Add a remote")
    p_remote_add.add_argument("name", help="Remote name (e.g. origin)")
    p_remote_add.add_argument("url", help="Remote URL")

    p_remote_list = remote_sub.add_parser("list", help="List remotes")

    p_remote_rm = remote_sub.add_parser("remove", help="Remove a remote")
    p_remote_rm.add_argument("name", help="Remote name to remove")

    # collab
    p_collab = subparsers.add_parser("collab", help="Collaboration setup")
    collab_sub = p_collab.add_subparsers(dest="collab_cmd")
    p_collab_setup = collab_sub.add_parser("setup", help="Guided remote setup wizard")
    p_collab_setup.set_defaults(func=cmd_collab_setup)
    p_collab.set_defaults(func=lambda a: p_collab.print_help())

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        args.func(args)
    except GitError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
