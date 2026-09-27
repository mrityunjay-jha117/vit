"""End-to-end merge tests against a real git repo (perform_merge + callers)."""

import os
import tempfile

import pytest

from vit.cli import _default_clone_dir, _require_committable
from vit.core import (
    git_add, git_branch, git_checkout, git_commit, git_config_set,
    git_current_branch, git_init, git_merge, git_rev_parse,
)
from vit.json_writer import _write_json, read_json
from vit.merge_utils import perform_merge


def _clip(clip_id, start):
    return {
        "id": clip_id, "name": clip_id, "media_ref": "m",
        "record_start_frame": start, "record_end_frame": start + 10,
        "source_start_frame": 0, "source_end_frame": 10,
        "track_index": 1, "transform": {},
    }


def _write(project, items, grades=None):
    _write_json(os.path.join(project, "timeline", "cuts.json"),
                {"video_tracks": [{"index": 1, "items": items}]})
    _write_json(os.path.join(project, "timeline", "color.json"), {"grades": grades or {}})


def _commit(project, msg):
    git_add(project, ["."])
    git_commit(project, msg)


def _clip_ids(project):
    cuts = read_json(os.path.join(project, "timeline", "cuts.json"))
    return [i["id"] for t in cuts["video_tracks"] for i in t["items"]]


@pytest.fixture
def project():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "proj")
        git_init(d)
        git_config_set(d, "user.name", "Test")
        git_config_set(d, "user.email", "test@test")
        _write(d, [_clip("a", 0)])
        _commit(d, "base")
        yield d


def _diverge(project, ours_items, theirs_items, ours_grades=None, theirs_grades=None):
    git_branch(project, "feature")
    _write(project, theirs_items, theirs_grades)
    _commit(project, "feature edit")
    git_checkout(project, "main")
    _write(project, ours_items, ours_grades)
    _commit(project, "main edit")


def test_git_init_starts_on_main(project):
    assert git_current_branch(project) == "main"


def test_git_init_keeps_existing_history():
    """Regression: re-pointing HEAD to 'main' orphaned work in existing repos."""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q", "-b", "master", tmp], check=True)
        git_config_set(tmp, "user.name", "Test")
        git_config_set(tmp, "user.email", "test@test")
        with open(os.path.join(tmp, "notes.txt"), "w") as f:
            f.write("old work")
        _commit(tmp, "old work")
        before = git_rev_parse(tmp, "HEAD")

        git_init(tmp)

        assert git_current_branch(tmp) == "master"
        assert git_rev_parse(tmp, "HEAD") == before


def test_merge_works_when_git_lacks_merge_tree(project, monkeypatch):
    """git < 2.38 has no `merge-tree --write-tree`; merging must not depend on it."""
    import vit.core
    monkeypatch.setattr(vit.core, "git_merge_tree_conflicts", lambda *a: None)
    _diverge(project, [_clip("a", 0), _clip("ours", 10)], [_clip("a", 0), _clip("theirs", 20)])

    outcome = perform_merge(project, "feature")

    assert outcome.status == "merged"
    assert outcome.git_text_conflicts == []
    assert _clip_ids(project) == ["a", "ours", "theirs"]


def test_merge_keeps_clips_added_on_both_branches(project):
    _diverge(project, [_clip("a", 0), _clip("ours", 10)], [_clip("a", 0), _clip("theirs", 20)])

    outcome = perform_merge(project, "feature")

    assert outcome.status == "merged"
    assert _clip_ids(project) == ["a", "ours", "theirs"]
    assert "timeline/cuts.json" in outcome.git_text_conflicts
    assert git_rev_parse(project, "HEAD^2") == git_rev_parse(project, "feature")


def test_conflict_leaves_repo_untouched_until_resolved(project):
    grade = lambda sat: {"a": {"nodes": [{"saturation": sat}]}}  # noqa: E731
    _diverge(project, [_clip("a", 0)], [_clip("a", 0)], grade(1.5), grade(0.2))
    head = git_rev_parse(project, "HEAD")

    outcome = perform_merge(project, "feature")
    assert outcome.status == "conflicts"
    assert git_rev_parse(project, "HEAD") == head
    assert git_rev_parse(project, "MERGE_HEAD") is None

    outcome = perform_merge(project, "feature", {c["path"]: "theirs" for c in outcome.conflicts})
    assert outcome.status == "merged"
    color = read_json(os.path.join(project, "timeline", "color.json"))
    assert color["grades"]["a"]["nodes"][0]["saturation"] == 0.2


def test_merge_up_to_date(project):
    git_branch(project, "feature")
    git_checkout(project, "main")
    assert perform_merge(project, "feature").status == "up_to_date"


def test_panel_merge_keeps_current_branch_work(project):
    """Regression: the Resolve panel used `git checkout --theirs` and dropped ours."""
    from resolve_plugin.vit_panel_launcher import handle_request

    class _Project:
        def GetCurrentTimeline(self):
            return None

    class _Resolve:
        def GetProjectManager(self):
            return self

        def GetCurrentProject(self):
            return _Project()

    _diverge(project, [_clip("a", 0), _clip("ours", 10)], [_clip("a", 0), _clip("theirs", 20)])

    result = handle_request({"action": "merge", "branch": "feature"}, _Resolve(), project)

    assert result["ok"], result
    assert _clip_ids(project) == ["a", "ours", "theirs"]


def test_commit_refuses_conflict_markers(project):
    _diverge(project, [_clip("a", 0), _clip("ours", 10)], [_clip("a", 0), _clip("theirs", 20)])
    ok, _ = git_merge(project, "feature")
    assert not ok

    with pytest.raises(SystemExit):
        _require_committable(project)


def test_commit_refuses_invalid_json(project):
    with open(os.path.join(project, "timeline", "cuts.json"), "w") as f:
        f.write("{ not json")
    with pytest.raises(SystemExit):
        _require_committable(project)


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/x/widget.git", "widget"),
    ("git@github.com:x/digit.git", "digit"),
    ("https://github.com/x/film/", "film"),
    ("git@github.com:solo.git", "solo"),
    ("/home/me/projects/film/.git", "film"),
    (r"C:\work\film\.git", "film"),
])
def test_default_clone_dir(url, expected):
    assert _default_clone_dir(url) == expected
