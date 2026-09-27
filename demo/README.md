# Vit Studio — standalone demo

A browser-based mini video editor that shows Vit's idea end to end, with no
DaVinci Resolve or plugin: branch a timeline, edit in parallel, merge.

The browser plays the part of the NLE. The small Python server
(`server.py`, stdlib only) is the part that is real Vit: it writes the timeline
with `vit.models` + `vit.json_writer`, diffs with `vit.differ`, validates with
`vit.validator`, merges with `vit.merge_utils`, and runs every version-control
step through `vit.core` (the system `git` binary).

## Run

```bash
python demo/server.py            # opens http://127.0.0.1:8765
python demo/server.py --reset    # start from a fresh project
```

Needs Python 3.8+ and git 2.38+. No `pip install` required.

The demo repo lives in `demo/workspace/project/`. It is a plain git repo of
JSON files, so you can open it in a terminal and run `git log --graph` or read
`timeline/cuts.json` while presenting. Imported videos go to
`demo/workspace/media/` and never enter git. The manifest records only their
checksum and path.

## What's on screen

| Area | What it shows |
|------|---------------|
| Media | Six built-in procedural clips (frames are generated from the frame number, so no media files are needed), plus **Import video** for your own `.mp4`/`.webm`. |
| Viewer | The composited timeline. The burn-in and the line under the viewer show the core idea: *timeline frame 245 → Sunset_Beach.mov source frame 149*. |
| Timeline | Drag clips from the bin, move them, trim the edges, split with **S**, add markers with **M**. Each video clip has a linked audio clip. |
| Inspector | Every control group is labelled with the JSON file it edits (`cuts.json`, `color.json`, `effects.json`, `audio.json`). |
| JSON on disk | The real files in the repo, plus `git diff` against the last saved version. |
| Right panel | Branch switcher, live human-readable diff of unsaved changes, post-merge validation with one-click fixes, and a commit graph. Click any version to inspect, preview or restore it. |

## A 5-minute demo script

1. **Checkpoint A.** The project opens on `main` with two clips. Type your
   name in *You are*.
2. **User A's branch.** Click *New branch*, then create `aditi-edit`. Drag
   *City_Night* onto V1 after the last clip, then grade it with the *Cool night*
   preset. Watch *Unsaved changes* list the edit in plain English. Save the
   version as "city b-roll".
3. **User B's branch.** Switch to `main` and the timeline rebuilds from its
   JSON. Create `kabir-edit`. Drag *Forest_Drone* into the same slot, set the
   interview clip to *Warm film*, and add a marker. Save the version.
4. **Merge.** Switch to `aditi-edit` and click *Merge…*, then pick `kabir-edit`.
   The dialog shows what each branch changed, and that **plain git would
   conflict** in `cuts.json` while **Vit's clip-level merge doesn't**. Click
   merge.
5. **Validation.** Both users put a clip in the same slot, so validation
   flags an overlap. Click *Move up a track* or *Place after*, then save.
6. **A real conflict.** On two branches, set the *same* clip's saturation to
   different values and merge. Vit asks which value wins; everything else still
   merges automatically.
7. **Time travel.** Click any old version in History, then *Preview in viewer*
   (read-only) or *Restore this version* (adds a new version, so no history is
   lost).
8. **Proof it's just JSON.** Open the *JSON on disk* tab, or run
   `git -C demo/workspace/project log --graph --oneline --all`.

## How merging works here

1. Unsaved edits are auto-saved as a version first.
2. The merge base, ours and theirs are loaded from git.
3. `three_way_merge_domains` matches clips by id, markers by frame and grades
   by clip id, then merges field by field. Only the same field changed
   differently on both sides becomes a conflict for the user.
4. `git merge --no-commit` starts the merge. Vit writes the merged JSON and
   commits it, so history has a real two-parent merge commit.
5. `validate_project` checks for orphaned grades, overlaps and audio/video
   sync.

AI-assisted resolution (`vit merge` in the CLI) is not used by the demo, so it
works without an API key.
