# Vit — Git for Video Editing

[![Vit Demo](https://img.youtube.com/vi/phS28hhJSP8/maxresdefault.jpg)](https://www.youtube.com/watch?v=phS28hhJSP8)

Vit brings git-style version control to video editing. Instead of versioning raw media files, Vit tracks **timeline metadata** — clip placements, color grades, audio levels, effects, and markers — as lightweight JSON, using Git as the backend.

Collaborators (editors, colorists, sound designers) work in parallel on branches and merge changes cleanly, just like developers with code.

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

A clip is a *reference* to frames of a media file (`source_start_frame` … `source_end_frame`) placed on the timeline (`record_start_frame` … `record_end_frame`). Media never enters git — `assets/manifest.json` records only paths and checksums.

Merging is **clip-level**: clips are matched by id and merged field by field, so two editors adding clips to the same timeline combine cleanly even where line-based git would report a conflict. Only the same property changed differently on both branches needs a human decision. A rule-based validator then checks for cross-domain problems (e.g. a deleted clip still referenced in `color.json`).

## Installation

**Requirements:** Python 3.8+, Git 2.38+

### One-Line Install (macOS/Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/LucasHJin/vit/main/install.sh | bash
```

### Manual Install

```bash
git clone https://github.com/LucasHJin/vit.git
cd vit
pip install -e .
```

## Try It: Vit Studio (browser demo)

A small browser-based video editor that runs on top of vit-core and real git — no NLE needed:

```bash
python demo/server.py
```

Branch a timeline, edit on two branches in parallel, merge them, inspect history, and see the JSON on disk. See [`demo/README.md`](demo/README.md) for a 5-minute walkthrough.

## Quick Start (CLI)

```bash
vit init my-project             # initialize a project (required once)
cd my-project
vit commit -m "rough cut done"  # save a version
vit branch color-grade          # create a branch and switch to it
vit commit -m "first color pass"
vit checkout main
vit merge color-grade           # clip-level merge; asks only about true conflicts
vit diff                        # see what changed
vit log                         # version history
```

### Sharing with a team

```bash
vit collab setup                # connect an empty GitHub repo and push
vit clone <url>                 # collaborators: get the project
vit push / vit pull             # share and fetch work
```

Footage is shared separately (shared drive, Dropbox, server); `assets/manifest.json` lists what each clip needs. See [`docs/COLLABORATION.md`](docs/COLLABORATION.md).

## Commands

| Command | Description |
|---------|-------------|
| `vit init` | Initialize a new vit project |
| `vit add` | Stage the timeline JSON |
| `vit commit -m "msg"` | Stage + commit (refuses unresolved conflicts or invalid JSON) |
| `vit branch <name>` | Create a new branch |
| `vit checkout <name>` | Switch branches |
| `vit merge <branch>` | Clip-level merge; `--no-ai` skips the optional AI analysis / fix-up |
| `vit diff` | Human-readable timeline diff |
| `vit log` | Formatted version history |
| `vit revert` | Undo the last commit |
| `vit push` / `vit pull` | Sync with a remote |
| `vit status` | Show project status |
| `vit validate` | Check the timeline for orphaned refs, overlaps, sync issues |

## Project Structure

```
vit/                      # Core library
  cli.py                  # CLI entry point
  core.py                 # Git operations wrapper
  merge_utils.py          # Clip-level 3-way merge (perform_merge)
  differ.py               # Human-readable diff formatting
  validator.py            # Post-merge validation
  models.py               # Data models
  json_writer.py          # Domain-split JSON I/O
  ai_merge.py             # Optional AI assistance (Gemini)
  serializer.py           # DaVinci Resolve timeline -> JSON (not used by the MVP)
  deserializer.py         # JSON -> DaVinci Resolve timeline (not used by the MVP)

demo/                     # Vit Studio — browser editor + local server
tests/                    # Test suite
docs/                     # Reference docs
  COLLABORATION.md        # Multi-user setup
  JSON_SCHEMAS.md         # Full schema for all domain JSON files
  AI_MERGE_DETAILS.md     # Merge flow and AI prompts
  RESOLVE_API_LIMITATIONS.md  # Notes for a future Resolve integration
```

## AI Features (optional)

Vit can use the **Gemini API** (`gemini-2.5-flash`). Merging never depends on it — the merge result is deterministic.

- **Post-merge fix-up** — if validation finds errors after a merge (e.g. orphaned references, overlapping clips), `vit merge` can ask the AI to propose a fix, shows you the diff, and commits only if you confirm
- **Pre-merge summary** — `vit merge` prints an AI summary of what each branch changed
- **Commit message suggestions** — `vit commit` without `-m` can suggest a message from the timeline diff
- **Log summaries** — `vit log --summary`

Set `GEMINI_API_KEY` in your environment or project `.env` file to enable them. Everything works without a key.

## Testing

```bash
python -m pytest tests/
```

## Contributions

We are not accepting external pull requests at this time.

Please feel free to fork the project and build on it independently!

## License

MIT
