# AI-Powered Semantic Merge

## Known Edge Cases Git Can't Handle

| Problem | Example | Why git fails |
|---------|---------|---------------|
| Orphaned references | Editor deletes clip `item_003` in `cuts.json`; colorist graded `item_003` in `color.json` | Merge "succeeds" but color grade points at nothing |
| Audio/video sync | Editor trims clip in `cuts.json`; sound designer adjusted audio for old length in `audio.json` | Merge succeeds but audio out of sync |
| Overlapping clips | Two editors add clips to same track at same timecode | Git may merge both → invalid timeline |
| Track count mismatch | One branch adds V3 track, another doesn't | `metadata.json` conflicts, structural issue in `cuts.json` |
| Speed/audio mismatch | Editor changes speed in `cuts.json`; sound designer adjusted audio for old speed | Video/audio speed values diverge |
| Speed/duration stale | Editor changes speed; parallel branch modifies same clip's duration | Merged record_end_frame doesn't match speed-adjusted source |

## Merge Flow

```
vit merge <branch>            (Vit Studio uses the same steps 1–3)
    │
    ▼
1. Clip-level 3-way merge (merge_utils.perform_merge)
   - base / ours / theirs loaded from git
   - clips matched by id, markers by frame, grades by clip id,
     then merged field by field
    │
    ├─ Same property changed differently on both sides?
    │     → nothing is touched; CLI asks "keep ours / take theirs"
    │       per conflict (Vit Studio shows both values side by side)
    │
    ▼
2. git merge --no-ff --no-commit, write merged JSON, pick binary
   sidecars (.drx/.cube/.comp) from the side the merged JSON kept,
   commit → a real two-parent merge commit
    │
    ▼
3. Post-merge validation (validator.py)
    │
    ├─ Valid? → Done ✓
    │
    ├─ Errors (orphaned refs, overlaps, …) and AI enabled?
    ▼
4. Send to LLM (ai_merge.merge_with_ai)
   - base / merged / theirs domain JSON + detected issues
    │
    ▼
5. Show user what AI changed, ask for confirmation
    │
    ▼
6. Write resolved files, commit as "AI fix-up after merging …"
```

Independent edits — even to the same JSON file — never need the LLM:
line-based git would conflict when two editors add clips to the same track,
but the clip-level merge combines them. The LLM only handles *semantic*
problems that survive a correct merge (e.g. one branch deleted a clip the
other graded).

## LLM Prompt Template

```python
prompt = f"""
You are resolving a merge conflict in a video editing timeline.

The timeline is split into domain files: cuts.json, color.json, audio.json, etc.
Clips are linked across files by their "id" field.

BASE (common ancestor):
{base_json}

OURS (current branch):
{ours_json}

THEIRS (incoming branch):
{theirs_json}

DETECTED ISSUES:
{validation_issues}

Rules:
- If a clip was deleted in one branch, remove its references from ALL domain files
- Audio clip boundaries must match their corresponding video clip boundaries
- No two clips may overlap on the same track at the same timecode
- Preserve as much work from both branches as possible
- When in doubt, prefer the branch that made the more recent commit

Return the resolved JSON for each domain file.
"""
```

## Implementation Notes

- Uses Gemini API via `google-generativeai` Python SDK
- Called only when post-merge validation finds errors (never for plain conflicts)
- For the common case (independent edits), AI is never invoked
- User always sees what the AI changed before commit
- If the AI fix-up is declined, the merge stays committed and the issues are listed for manual fixing
