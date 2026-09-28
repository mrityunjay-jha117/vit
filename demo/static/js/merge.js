"use strict";

// ── Merge ──────────────────────────────────────────────────────────────────

function conflictLabel(path) {
  const parts = path.split("/");
  const name = (id) => { const c = findClip(id, S.files); return c ? `"${c.name}" (${id})` : id; };
  const rest = (from) => (parts.length > from ? " › " + parts.slice(from).join(" › ") : "");
  if (parts[0] === "cuts" && parts[2]) return `Clip ${name(parts[2])}${rest(3)}`;
  if (parts[0] === "color" && parts[2]) return `Color grade of ${name(parts[2])}${rest(3)}`;
  if (parts[0] === "effects" && parts[2]) return `Effects on ${name(parts[2])}${rest(3)}`;
  if (parts[0] === "audio" && parts[2]) return `Audio of ${name(videoIdFor(parts[2]))}${rest(3)}`;
  if (parts[0] === "markers" && parts[2]) return `Marker at ${tc(Number(parts[2]))}${rest(3)}`;
  return parts.join(" › ");
}

const showValue = (v) => (v === null ? "(deleted on this branch)" : JSON.stringify(v, null, 2));

function openMerge() {
  const others = S.branches.filter((b) => b !== S.branch);
  if (S.detached) return toast("Switch to a branch before merging.", true);
  if (!others.length) return toast("Create another branch first, make some edits there, then merge it back.");
  const select = h("select", {}, others.map((b) => h("option", { value: b }, b)));
  const result = h("div", { style: "display:flex;flex-direction:column;gap:14px" });
  select.addEventListener("change", () => compare(select.value, result));
  openModal(`Merge into ${S.branch}`,
    h("div", { class: "branch-row" }, h("label", { class: "muted", style: "align-self:center" }, "Bring in changes from"), select),
    result);
  compare(select.value, result);
}

async function compare(branch, target) {
  target.replaceChildren(h("div", { class: "muted" }, "Comparing…"));
  try {
    await flushSync();
    const r = await api(`/api/compare?branch=${encodeURIComponent(branch)}`);
    if (r.up_to_date) {
      target.replaceChildren(h("div", { class: "callout good" }, h("b", {}, "Nothing to merge"), `${S.branch} already contains everything from ${branch}.`));
      return;
    }
    target.replaceChildren(
      h("div", { class: "cols2" },
        h("div", {}, h("h3", {}, `Changed on ${S.branch} since the split`), diffBox(r.ours_diff)),
        h("div", {}, h("h3", {}, `Changed on ${branch} since the split`), diffBox(r.theirs_diff))),
      r.git_text_conflicts.length
        ? h("div", { class: "callout warn" }, h("b", {}, "Plain git (line-by-line) would stop here"),
          `It would report conflicts in ${r.git_text_conflicts.join(", ")} — both branches edited nearby lines of the same JSON file.`)
        : h("div", { class: "callout good" }, h("b", {}, "Plain git would merge these files cleanly too"), "The branches touched different parts of the JSON."),
      r.conflicts.length
        ? h("div", { class: "callout bad" }, h("b", {}, `Vit clip-level merge: ${r.conflicts.length} decision${r.conflicts.length === 1 ? "" : "s"} needed`),
          "Both branches changed the same property of the same clip. You'll pick which one wins.")
        : h("div", { class: "callout good" }, h("b", {}, "Vit clip-level merge: no conflicts"),
          "Clips are matched by id, so every clip, grade, effect and marker from both branches is kept."),
      r.dirty ? h("div", { class: "muted" }, "Your unsaved changes will be saved as a version first.") : null,
      h("div", { class: "modal-actions" },
        h("button", { class: "ghost", onclick: closeModal }, "Cancel"),
        h("button", { class: "accent", onclick: () => doMerge(branch, target, {}) }, `Merge ${branch} → ${S.branch}`)),
    );
  } catch (e) {
    target.replaceChildren(h("div", { class: "callout bad" }, e.message));
  }
}

async function doMerge(branch, target, resolutions) {
  try {
    await flushSync();
    const r = await api("/api/merge", { branch, author: author(), resolutions });
    if (r.status === "up_to_date") { toast("Already up to date."); closeModal(); return; }
    if (r.status === "conflicts") return showConflicts(branch, r, target);
    await loadAll();
    target.replaceChildren(
      h("div", { class: "callout good" }, h("b", {}, `Merged ${branch} into ${S.branch}  ·  ${r.hash}`),
        "git recorded a merge commit with two parents; vit supplied the merged timeline."),
      r.git_text_conflicts.length
        ? h("div", { class: "callout warn" }, h("b", {}, "This is the part plain git couldn't do"),
          `Line-based merging conflicted in ${r.git_text_conflicts.join(", ")}. Vit merged them clip by clip instead.`)
        : "",
      h("h3", {}, `What came in from ${branch}`), diffBox(r.diff),
      r.issues.length
        ? h("div", { class: "callout warn" }, h("b", {}, "Post-merge validation"), r.issues.map((i) => h("div", {}, `• ${i.message}`)),
          h("div", { class: "muted", style: "margin-top:6px" }, "These are also listed under Unsaved changes, with quick fixes."))
        : h("div", { class: "callout good" }, h("b", {}, "Post-merge validation passed"), "No orphaned grades, overlaps or sync problems."),
      h("div", { class: "modal-actions" }, h("button", { class: "accent", onclick: closeModal }, "Done")),
    );
  } catch (e) {
    toast(e.message, true);
  }
}

function showConflicts(branch, r, target) {
  const choices = {};
  const rows = r.conflicts.map((c) => {
    const nameAttr = "c_" + c.path.replace(/[^a-z0-9]/gi, "_");
    choices[c.path] = "ours";
    const opt = (side, label, value) => h("label", { class: "choice" },
      h("span", {}, h("input", { type: "radio", name: nameAttr, checked: side === "ours", onchange: () => { choices[c.path] = side; } }), label),
      h("pre", {}, showValue(value)));
    return h("div", { class: "conflict" },
      h("div", { class: "path" }, conflictLabel(c.path)),
      h("div", { class: "cols2" }, opt("ours", `Keep ${r.ours}`, c.ours), opt("theirs", `Take ${r.theirs}`, c.theirs)));
  });
  target.replaceChildren(
    h("div", { class: "callout bad" }, h("b", {}, "Same property, two different decisions"),
      "Everything else merged automatically. Pick a winner for each item below."),
    ...rows,
    h("div", { class: "modal-actions" },
      h("button", { class: "ghost", onclick: closeModal }, "Cancel merge"),
      h("button", { class: "accent", onclick: () => doMerge(branch, target, choices) }, "Complete merge")),
  );
}
