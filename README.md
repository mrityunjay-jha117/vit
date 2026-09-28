# Vit — Git for Video Editing

[![Vit Demo](https://img.youtube.com/vi/phS28hhJSP8/maxresdefault.jpg)](https://www.youtube.com/watch?v=phS28hhJSP8)

Vit brings git-style version control to video editing. Instead of versioning raw media files, Vit tracks **timeline metadata** — clip placements, color grades, audio levels, effects, and markers — as lightweight JSON, using Git as the backend.

**Vit Studio** is a browser-based mini video editor that shows the idea end to end: branch a timeline, edit in parallel, merge.

## How It Works

A timeline is stored as **domain-split JSON files**:

| File | Contents | Typical Owner |
|------|----------|---------------|
| `cuts.json` | Clip placements, in/out points, transforms | Editor |
| `color.json` | Color grading per clip | Colorist |
| `audio.json` | Levels, panning | Sound Designer |
| `effects.json` | Effects, transitions | Editor / VFX |
| `markers.json` | Markers, notes | Anyone |
| `metadata.json` | Frame rate, resolution, track counts | Rarely changed |

A clip is a *reference* to frames of a media file (`source_start_frame` … `source_end_frame`) placed on the timeline (`record_start_frame` … `record_end_frame`). Media never enters git — `assets/manifest.json` records only paths and metadata. Full schemas: [`docs/JSON_SCHEMAS.md`](docs/JSON_SCHEMAS.md).

Merging is **clip-level**: clips are matched by id and merged field by field, so two editors adding clips to the same timeline combine cleanly even where line-based git would report a conflict. Only the same property changed differently on both branches needs a human decision. A validator then checks for cross-domain problems (e.g. a deleted clip still referenced in `color.json`).

## Run Vit Studio

### With Docker (any OS, nothing else to install)

```bash
docker compose up --build        # then open http://localhost:8765
```

The demo project and imported media live in the `vit-workspace` volume, so they survive restarts.

| Task | Command |
|------|---------|
| Stop | `docker compose down` |
| Start over (delete the demo repo + media) | `docker compose down -v` |
| Run the tests in the container | `docker build --target test .` |
| Inspect the demo repo | `docker exec vit-studio git -C /data/project log --graph --oneline --all` |

The port is published on `127.0.0.1` only. The Studio has no authentication, so keep it that way. If you reach it under a different host name, add that name to `VIT_ALLOWED_HOSTS` in `docker-compose.yml`.

### With Python

Requires Python 3.8+ and Git 2.38+. No `pip install` needed.

```bash
python demo/server.py            # opens http://localhost:8765
python demo/server.py --reset    # start from a fresh project
```

Options: `--port N`, `--host ADDR` (bind address, default `127.0.0.1`), `--no-browser`. The demo repo lives in `demo/workspace/project/` (override with `VIT_DEMO_WORKSPACE`). It's a plain git repo of JSON files. Imported videos go to `demo/workspace/media/` and never enter git.

## What's on Screen

| Area | What it shows |
|------|---------------|
| Media | Six built-in procedural clips (frames are generated from the frame number, so no media files are needed), plus **Import video** for your own `.mp4`/`.webm`. |
| Viewer | The composited timeline. The burn-in shows the core idea: *timeline frame 245 → Sunset_Beach.mov source frame 149*. |
| Timeline | Drag clips from the bin, move them, trim the edges, split with **S**, add markers with **M**. Each video clip has a linked audio clip. |
| Inspector | Every control group is labelled with the JSON file it edits (`cuts.json`, `color.json`, `effects.json`, `audio.json`). |
| JSON on disk | The real files in the repo, plus `git diff` against the last saved version. |
| Right panel | Branch switcher, live human-readable diff of unsaved changes, post-merge validation with one-click fixes, and a commit graph. Click any version to inspect, preview or restore it. |

## A 5-Minute Walkthrough

1. **Start.** The project opens on `main` with two clips. Type your name in *You are*.
2. **User A's branch.** Click *New branch* and create `aditi-edit`. Drag *City_Night* onto V1 after the last clip, then grade it with the *Cool night* preset. *Unsaved changes* lists the edit in plain English. Save the version as "city b-roll".
3. **User B's branch.** Switch to `main` and the timeline rebuilds from its JSON. Create `kabir-edit`. Drag *Forest_Drone* into the same slot, set the interview clip to *Warm film*, and add a marker. Save the version.
4. **Merge.** Switch to `aditi-edit`, click *Merge…* and pick `kabir-edit`. The dialog shows what each branch changed. It also shows that **plain git would conflict** in `cuts.json` while **Vit's clip-level merge doesn't**. Click merge.
5. **Validation.** Both users put a clip in the same slot, so validation flags an overlap. Click *Move up a track* or *Place after*, then save.
6. **A real conflict.** On two branches, set the *same* clip's saturation to different values and merge. Vit asks which value wins, and everything else still merges automatically.
7. **Time travel.** Click any old version in History, then *Preview in viewer* (read-only) or *Restore this version*. Restoring adds a new version, so no history is lost.
8. **Proof it's just JSON.** Open the *JSON on disk* tab, or run `git -C demo/workspace/project log --graph --oneline --all`.

### How a merge works

1. Unsaved edits are auto-saved as a version first.
2. The merge base, ours and theirs are loaded from git.
3. `three_way_merge_domains` matches clips by id, markers by frame and grades by clip id, then merges field by field. A conflict is only raised when the same field changed differently on both sides.
4. `git merge --no-commit` starts the merge. Vit writes the merged JSON and commits it, so history has a real two-parent merge commit.
5. `validate` checks for orphaned grades, overlaps and audio/video sync.

## Project Structure

```
vit/                      core library — no HTTP, no UI; each layer uses only the ones above it
  git.py                  GitRepository: every git command, via the system binary
  timeline/
    models.py             dataclasses for clips, tracks, grades, markers, assets
    store.py              DOMAIN_FILES layout + canonical JSON read/write (TimelineStore)
  project.py              VitProject: a git repo of timeline JSON (create, files_at, commit)
  diff.py                 human-readable timeline diffs
  validation.py           consistency checks (pure: domain files in, issues out)
  merge/
    three_way.py          clip-level three-way merge (pure)
    service.py            perform_merge / preview_merge: git + three-way merge

demo/                     Vit Studio
  server.py               entry point (argument parsing only)
  studio/                 backend
    config.py             paths, ports, allowed hosts
    media.py              MediaLibrary: procedural sources + imported videos
    starter.py            starter timeline for a fresh project
    serializer.py         browser JSON → vit Timeline
    workspace.py          Workspace: save / commit / auto-save / reset
    api.py                StudioApi: one method per JSON endpoint
    handler.py            HTTP: routing, local-only security, static + media (Range) files
    app.py                composition root (build_studio)
  static/                 frontend (vanilla JS, no build step)
    index.html
    css/style.css
    js/                   sources, core, model, sync, clip-ops, viewer, timeline,
                          media-bin, inspector, version-control, modal, merge, json-view, main

tests/                    pytest suite (core library + Studio API + HTTP security)
docs/JSON_SCHEMAS.md      domain file schemas
Dockerfile, docker-compose.yml
```

## Testing

```bash
python -m pytest tests/          # or: docker build --target test .
```

## Contributions

We are not accepting external pull requests at this time.

Please feel free to fork the project and build on it independently!

## License

MIT
