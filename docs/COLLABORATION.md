# Collaborating with Vit

Vit shares the **edit** — timeline cuts, color grades, audio levels. It does **not** upload video files. Share footage the way you already do (shared drive, Dropbox, server); `assets/manifest.json` lists every file the timeline references, with its checksum.

**Prerequisites:** Vit and Git installed. See the main [README](../README.md) if any command is not found.

---

## Before anyone starts

1. On **GitHub** (or GitLab, etc.), create a **new empty repository** — no README, no license file.
2. Copy the **SSH clone URL** (e.g. `git@github.com:yourname/your-repo.git`). HTTPS works too, but GitHub no longer accepts passwords over HTTPS.

---

## Person who starts the project (once)

```bash
cd ~/Documents
vit init my-project
cd my-project
vit collab setup        # paste the empty repo URL when prompted
```

Send the `vit clone …` line that `vit collab setup` prints to your collaborators.

---

## Collaborators joining (once)

```bash
cd ~/Documents
vit clone git@github.com:yourname/your-repo.git
cd your-repo
vit branch your-name    # agree on a naming convention with your team
```

Copy the team's footage onto your machine so the paths in `assets/manifest.json` resolve.

---

## Every work session

```bash
vit pull                         # fetch the team's latest work
vit checkout your-name           # work on your branch
# ...edit the timeline...
vit commit -m "what you changed"
vit push
```

---

## Merging work (lead / editor)

```bash
vit pull
vit checkout main
vit merge other-persons-branch
vit push
```

`vit merge` combines both branches clip by clip. If both branches changed the *same* property of the same clip (e.g. two different saturation values), it shows both values and asks which one to keep — nothing is changed until you answer. After merging it validates the timeline (orphaned grades, overlapping clips, audio/video sync) and lists anything that needs fixing.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Not a vit project" | Make sure you're in the folder that contains `.vit`. |
| `vit collab setup` fails | Make sure the GitHub repo is **empty** (no files), then try again. For auth errors, follow the SSH instructions it prints. |
| "unresolved merge conflicts" on commit | Run `git merge --abort`, then `vit merge <branch>` to merge clip by clip. |
| Validation reports overlapping clips after a merge | Two people placed clips in the same spot — move one to another track or time, then `vit commit`. |

For anything else, see the main [README](../README.md).
