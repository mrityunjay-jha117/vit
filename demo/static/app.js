"use strict";

// Vit Studio — a tiny NLE whose whole timeline is the domain-split JSON that
// vit versions. Edits mutate that JSON in memory; the server writes it to
// disk with vit's own models, and git does everything else.

const FPS = 24;
const VW = 960, VH = 540;
const VIDEO_KINDS = new Set(["video"]);
const MARKER_COLORS = { Blue: "#5b8cff", Red: "#ff5b5b", Green: "#4ade80", Yellow: "#facc15", Purple: "#b57bff", Cyan: "#22d3ee" };
const GRADE_DEFAULTS = { saturation: 1, contrast: 1, gain_m: 1, hue: 0, temperature: 0 };
const LANE_COLORS = ["#f5a524", "#6ea8fe", "#4ade80", "#f472b6", "#a78bfa", "#22d3ee", "#fb923c"];

const S = {
  files: null, library: [], lib: {},
  branch: "main", branches: [], detached: false, dirty: false, diff: "", issues: [], head: "",
  commits: [],
  selected: null,           // {type: "clip"|"marker", id}
  playhead: 0, playing: false, zoom: 2,
  preview: null,            // {ref, files, label} — read-only view of an older version
  view: "viewer", jsonFile: "timeline/cuts.json",
  drag: null,
};

const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const clone = (x) => JSON.parse(JSON.stringify(x));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const round3 = (v) => Math.round(v * 1000) / 1000;

function tc(frames) {
  const f = Math.max(0, Math.floor(frames));
  const ff = f % FPS, s = Math.floor(f / FPS);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}:${p(ff)}`;
}

function toast(msg, isError = false) {
  const t = h("div", { class: "toast" + (isError ? " error" : "") }, msg);
  $("toasts").append(t);
  setTimeout(() => t.remove(), isError ? 6000 : 3500);
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Vit-Demo": "1" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function author() {
  return $("authorInput").value.trim() || "Vit Demo";
}

// ── Timeline model helpers ─────────────────────────────────────────────────

const view = () => (S.preview ? S.preview.files : S.files);
const readonly = () => Boolean(S.preview);
const speedOf = (item) => ((item.speed && item.speed.speed_percent) || 100) / 100;
const audioIdFor = (vid) => "a" + vid.slice(1);
const videoIdFor = (aid) => "v" + aid.slice(1);

function ensureShape(files) {
  files.cuts = files.cuts || {};
  files.audio = files.audio || {};
  files.cuts.video_tracks = files.cuts.video_tracks || [];
  files.audio.audio_tracks = files.audio.audio_tracks || [];
  for (const [list, n] of [[files.cuts.video_tracks, 2], [files.audio.audio_tracks, 2]]) {
    for (let i = 1; i <= n; i++) if (!list.find((t) => t.index === i)) list.push({ index: i, items: [] });
    list.sort((a, b) => a.index - b.index);
  }
  files.color = files.color || {};
  files.color.grades = files.color.grades || {};
  files.effects = files.effects || {};
  files.effects.clip_effects = files.effects.clip_effects || {};
  files.markers = files.markers || {};
  files.markers.markers = files.markers.markers || [];
  files.metadata = files.metadata || {};
  const tcount = files.metadata.track_count || {};
  files.metadata.track_count = {
    video: Math.max(tcount.video || 0, files.cuts.video_tracks.length),
    audio: Math.max(tcount.audio || 0, files.audio.audio_tracks.length),
  };
  return files;
}

function trackOf(tracks, index) {
  let t = tracks.find((x) => x.index === index);
  if (!t) { t = { index, items: [] }; tracks.push(t); tracks.sort((a, b) => a.index - b.index); }
  return t;
}

function allClips(files = view()) {
  return files.cuts.video_tracks.flatMap((t) => t.items);
}

function findClip(id, files = view()) {
  return allClips(files).find((c) => c.id === id) || null;
}

function findAudio(aid, files = view()) {
  for (const t of files.audio.audio_tracks) {
    const a = t.items.find((x) => x.id === aid);
    if (a) return a;
  }
  return null;
}

function removeFrom(tracks, id) {
  for (const t of tracks) {
    const i = t.items.findIndex((x) => x.id === id);
    if (i >= 0) return t.items.splice(i, 1)[0];
  }
  return null;
}

function sortAll(files = S.files) {
  const by = (k) => (a, b) => (a[k] - b[k]) || String(a.id).localeCompare(String(b.id));
  files.cuts.video_tracks.forEach((t) => t.items.sort(by("record_start_frame")));
  files.audio.audio_tracks.forEach((t) => t.items.sort(by("start_frame")));
  files.markers.markers.sort((a, b) => a.frame - b.frame);
}

// Every video clip has a linked audio clip (same media, same frames), the way
// an NLE links picture and sound. Sound designers edit its volume/pan.
function syncAudio(v) {
  const aid = audioIdFor(v.id);
  const a = removeFrom(S.files.audio.audio_tracks, aid) || { id: aid, volume: 0.0, pan: 0.0 };
  a.media_ref = v.media_ref;
  a.start_frame = v.record_start_frame;
  a.end_frame = v.record_end_frame;
  if (v.speed) a.speed = { ...v.speed }; else delete a.speed;
  trackOf(S.files.audio.audio_tracks, v.track_index).items.push(a);
}

function overlapsOn(track, start, end, ignoreId, files = S.files) {
  const t = files.cuts.video_tracks.find((x) => x.index === track);
  return (t ? t.items : []).find((c) => c.id !== ignoreId && start < c.record_end_frame && c.record_start_frame < end) || null;
}

function firstFreeStart(track, start, len, ignoreId) {
  let s = start, hit;
  while ((hit = overlapsOn(track, s, s + len, ignoreId))) s = hit.record_end_frame;
  return s;
}

function contentEnd(files = view()) {
  return allClips(files).reduce((m, c) => Math.max(m, c.record_end_frame), 0);
}

function timelineLength() {
  return Math.max(contentEnd() + 10 * FPS, 40 * FPS);
}

function newId(prefix, name) {
  const slug = name.toLowerCase().replace(/\.[^.]+$/, "").replace(/[^a-z0-9]+/g, "").slice(0, 10);
  return `${prefix}_${slug}${Math.random().toString(16).slice(2, 6)}`;
}

function edit(fn) {
  if (readonly()) { toast("You're previewing an old version — go back to the working copy to edit."); return false; }
  fn();
  sortAll();
  render();
  scheduleSync();
  return true;
}

// ── Sync working copy → server (writes JSON to disk) ────────────────────────

let syncTimer = null, pendingSync = false, syncChain = Promise.resolve();

function scheduleSync() {
  pendingSync = true;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(flushSync, 250);
}

function flushSync() {
  clearTimeout(syncTimer);
  if (!pendingSync) return syncChain;
  pendingSync = false;
  const payload = clone(S.files);
  syncChain = syncChain
    .then(() => api("/api/working", { files: payload }))
    .then((r) => {
      S.dirty = r.dirty; S.diff = r.diff; S.issues = r.issues;
      renderVC(); renderTimeline();
      if (S.view === "json") loadJson();
    })
    .catch((e) => toast(e.message, true));
  return syncChain;
}

// ── Clip operations ────────────────────────────────────────────────────────

function addClip(ref, track, frame) {
  const src = S.lib[ref];
  if (!src) return;
  const len = Math.min(5 * FPS, src.duration_frames);
  const srcStart = Math.max(0, Math.min(Math.floor(src.duration_frames * 0.2), src.duration_frames - len));
  const v = {
    id: newId("v", src.name), name: src.name, media_ref: ref,
    record_start_frame: 0, record_end_frame: len,
    source_start_frame: srcStart, source_end_frame: srcStart + len,
    track_index: track,
    transform: { Pan: 0, Tilt: 0, ZoomX: 1, ZoomY: 1, Opacity: 100 },
  };
  const ok = edit(() => {
    v.record_start_frame = firstFreeStart(track, Math.max(0, Math.round(frame)), len, null);
    v.record_end_frame = v.record_start_frame + len;
    trackOf(S.files.cuts.video_tracks, track).items.push(v);
    syncAudio(v);
    S.selected = { type: "clip", id: v.id };
  });
  if (ok) renderInspector();
}

function deleteClip(id) {
  edit(() => {
    removeFrom(S.files.cuts.video_tracks, id);
    removeFrom(S.files.audio.audio_tracks, audioIdFor(id));
    delete S.files.color.grades[id];
    delete S.files.effects.clip_effects[id];
    S.selected = null;
  });
  renderInspector();
}

function splitClip(id, frame) {
  const v = findClip(id, S.files);
  frame = Math.floor(frame);
  if (!v || frame <= v.record_start_frame || frame >= v.record_end_frame) {
    toast("Put the playhead inside the selected clip to split it.");
    return;
  }
  edit(() => {
    const sp = speedOf(v);
    const right = clone(v);
    right.id = newId("v", v.name);
    right.record_start_frame = frame;
    right.source_start_frame = v.source_start_frame + Math.round((frame - v.record_start_frame) * sp);
    v.record_end_frame = frame;
    v.source_end_frame = right.source_start_frame;
    trackOf(S.files.cuts.video_tracks, v.track_index).items.push(right);
    const leftAudio = findAudio(audioIdFor(v.id), S.files);
    syncAudio(v);
    syncAudio(right);
    const rightAudio = findAudio(audioIdFor(right.id), S.files);
    if (leftAudio && rightAudio) { rightAudio.volume = leftAudio.volume; rightAudio.pan = leftAudio.pan; }
    if (S.files.color.grades[v.id]) S.files.color.grades[right.id] = clone(S.files.color.grades[v.id]);
    if (S.files.effects.clip_effects[v.id]) S.files.effects.clip_effects[right.id] = clone(S.files.effects.clip_effects[v.id]);
    S.selected = { type: "clip", id: right.id };
  });
  renderInspector();
}

function setSpeed(v, pct) {
  const src = S.lib[v.media_ref];
  const sp = pct / 100;
  const srcLen = v.source_end_frame - v.source_start_frame;
  let len = Math.max(1, Math.round(srcLen / sp));
  const next = allClips(S.files)
    .filter((c) => c.track_index === v.track_index && c.id !== v.id && c.record_start_frame >= v.record_start_frame)
    .reduce((m, c) => Math.min(m, c.record_start_frame), Infinity);
  len = Math.min(len, next - v.record_start_frame);
  v.record_end_frame = v.record_start_frame + len;
  v.source_end_frame = Math.min(src ? src.duration_frames : Infinity, v.source_start_frame + Math.round(len * sp));
  if (pct === 100) delete v.speed; else v.speed = { speed_percent: pct };
  syncAudio(v);
}

// Quick fixes for clips that ended up in the same slot after a merge.
function moveToFreeTrack(id) {
  const c = findClip(id, S.files);
  if (!c) return;
  const free = S.files.cuts.video_tracks.map((t) => t.index).sort((a, b) => a - b)
    .find((idx) => idx > c.track_index && !overlapsOn(idx, c.record_start_frame, c.record_end_frame, c.id));
  if (!free) { toast("No free track above — try “Place after”."); return; }
  edit(() => { c.track_index = free; syncAudio(c); S.selected = { type: "clip", id }; });
}

function placeAfter(id, anchorId) {
  const c = findClip(id, S.files), anchor = findClip(anchorId, S.files);
  if (!c || !anchor) return;
  const len = c.record_end_frame - c.record_start_frame;
  edit(() => {
    c.record_start_frame = firstFreeStart(c.track_index, anchor.record_end_frame, len, c.id);
    c.record_end_frame = c.record_start_frame + len;
    syncAudio(c);
    S.selected = { type: "clip", id };
  });
}

function addMarker() {
  const frame = Math.floor(S.playhead);
  const existing = view().markers.markers.find((m) => m.frame === frame);
  if (existing) { S.selected = { type: "marker", id: frame }; render(); return; }
  const n = view().markers.markers.length + 1;
  const ok = edit(() => {
    S.files.markers.markers.push({ frame, color: "Blue", name: `Note ${n}`, note: "", duration: 1 });
    S.selected = { type: "marker", id: frame };
  });
  if (ok) renderInspector();
}

// ── Viewer ─────────────────────────────────────────────────────────────────

const canvas = $("viewerCanvas");
const ctx = canvas.getContext("2d");
const srcCanvas = Object.assign(document.createElement("canvas"), { width: VW, height: VH });
const srcCtx = srcCanvas.getContext("2d");
const gradeCanvas = Object.assign(document.createElement("canvas"), { width: VW, height: VH });
const gradeCtx = gradeCanvas.getContext("2d");
const videos = {};

function videoFor(src) {
  if (!videos[src.ref]) {
    const v = document.createElement("video");
    v.src = "/media/" + encodeURIComponent(src.file);
    v.preload = "auto"; v.muted = true; v.playsInline = true;
    v.addEventListener("seeked", requestRender);
    v.addEventListener("loadeddata", requestRender);
    videos[src.ref] = v;
  }
  return videos[src.ref];
}

function drawCover(c, media, mw, mh) {
  const scale = Math.max(VW / mw, VH / mh);
  const w = mw * scale, hh = mh * scale;
  c.drawImage(media, (VW - w) / 2, (VH - hh) / 2, w, hh);
}

function drawSource(src, frame, burnIn) {
  srcCtx.save();
  srcCtx.fillStyle = "#000"; srcCtx.fillRect(0, 0, VW, VH);
  if (!src || !src.available) {
    SOURCES.drawOffline(srcCtx, VW, VH, src ? src.name : "missing media");
  } else if (VIDEO_KINDS.has(src.kind)) {
    const v = videoFor(src);
    if (v.readyState >= 2) drawCover(srcCtx, v, v.videoWidth, v.videoHeight);
    else { srcCtx.fillStyle = "#888"; srcCtx.font = "20px system-ui"; srcCtx.fillText("Loading media…", 30, 50); }
  } else {
    SOURCES.draw(srcCtx, VW, VH, src.kind, frame);
  }
  if (burnIn && src) {
    const label = `${src.name}  ·  SRC ${frame}`;
    srcCtx.font = "600 17px ui-monospace, Consolas, monospace";
    const w = srcCtx.measureText(label).width + 20;
    srcCtx.fillStyle = "rgba(0,0,0,0.65)"; srcCtx.fillRect(14, VH - 44, w, 30);
    srcCtx.fillStyle = "#fff"; srcCtx.fillText(label, 24, VH - 23);
  }
  srcCtx.restore();
}

function coveringClip(track, frame) {
  return track.items.find((c) => c.clip_enabled !== false && c.record_start_frame <= frame && frame < c.record_end_frame);
}

function sourceFrameAt(item, frame) {
  return item.source_start_frame + Math.floor((frame - item.record_start_frame) * speedOf(item));
}

function renderViewer() {
  const files = view();
  const frame = Math.floor(S.playhead);
  const burnIn = $("burnIn").checked;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, VW, VH);
  const lines = [];

  for (const track of [...files.cuts.video_tracks].sort((a, b) => a.index - b.index)) {
    const item = coveringClip(track, frame);
    if (!item) { lines.unshift(h("div", {}, `V${track.index}  ·  —`)); continue; }
    const src = S.lib[item.media_ref];
    const sf = sourceFrameAt(item, frame);
    drawSource(src, sf, burnIn);

    const node = ((files.color.grades[item.id] || {}).nodes || [])[0] || {};
    const g = { ...GRADE_DEFAULTS, ...node };
    const fx = files.effects.clip_effects[item.id] || {};
    gradeCtx.save();
    gradeCtx.clearRect(0, 0, VW, VH);
    gradeCtx.filter = `brightness(${g.gain_m}) contrast(${g.contrast}) saturate(${g.saturation}) hue-rotate(${g.hue}deg)` +
      (fx.blur ? ` blur(${fx.blur}px)` : "");
    gradeCtx.drawImage(srcCanvas, 0, 0);
    gradeCtx.filter = "none";
    if (g.temperature) {
      gradeCtx.globalCompositeOperation = "soft-light";
      gradeCtx.fillStyle = g.temperature > 0 ? "#ff8a1f" : "#1f7bff";
      gradeCtx.globalAlpha = Math.min(1, Math.abs(g.temperature) / 2000) * 0.8;
      gradeCtx.fillRect(0, 0, VW, VH);
      gradeCtx.globalAlpha = 1;
      gradeCtx.globalCompositeOperation = "source-over";
    }
    if (fx.vignette) {
      const vg = gradeCtx.createRadialGradient(VW / 2, VH / 2, VH * 0.3, VW / 2, VH / 2, VW * 0.62);
      vg.addColorStop(0, "rgba(0,0,0,0)");
      vg.addColorStop(1, `rgba(0,0,0,${fx.vignette})`);
      gradeCtx.fillStyle = vg; gradeCtx.fillRect(0, 0, VW, VH);
    }
    gradeCtx.restore();

    const t = item.transform || {};
    let alpha = (t.Opacity ?? 100) / 100;
    const into = frame - item.record_start_frame, left = item.record_end_frame - 1 - frame;
    if (fx.fade_in && into < fx.fade_in) alpha *= into / fx.fade_in;
    if (fx.fade_out && left < fx.fade_out) alpha *= left / fx.fade_out;
    ctx.save();
    ctx.globalAlpha = clamp(alpha, 0, 1);
    ctx.translate(VW / 2 + (t.Pan || 0) / 2, VH / 2 - (t.Tilt || 0) / 2);
    ctx.rotate(((t.RotationAngle || 0) * Math.PI) / 180);
    ctx.scale((t.ZoomX ?? 1) * (t.FlipX ? -1 : 1), (t.ZoomY ?? 1) * (t.FlipY ? -1 : 1));
    ctx.drawImage(gradeCanvas, -VW / 2, -VH / 2);
    ctx.restore();

    lines.unshift(h("div", {},
      `V${track.index}  ·  timeline frame ${frame} → `, h("b", {}, src ? src.name : item.media_ref),
      ` source frame `, h("b", {}, sf),
      speedOf(item) !== 1 ? `  (${Math.round(speedOf(item) * 100)}% speed)` : "",
      files.color.grades[item.id] ? "  ·  graded" : ""));
  }
  $("frameMap").replaceChildren(...lines);
  $("timecode").textContent = tc(frame);
  $("frameNo").textContent = `frame ${frame}`;
  syncVideos(frame);
}

let renderQueued = false;
function requestRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; renderViewer(); });
}

function syncVideos(frame) {
  const files = view();
  const active = {};
  for (const track of files.cuts.video_tracks) {
    const item = coveringClip(track, frame);
    const src = item && S.lib[item.media_ref];
    if (src && src.available && VIDEO_KINDS.has(src.kind)) active[src.ref] = item;
  }
  for (const [ref, v] of Object.entries(videos)) {
    const item = active[ref];
    if (!item) { if (!v.paused) v.pause(); v.muted = true; continue; }
    const t = sourceFrameAt(item, frame) / FPS;
    if (S.playing) {
      const a = findAudio(audioIdFor(item.id), files);
      v.playbackRate = clamp(speedOf(item), 0.25, 4);
      v.volume = clamp(Math.pow(10, ((a && a.volume) || 0) / 20), 0, 1);
      v.muted = false;
      if (v.paused) { v.currentTime = t; v.play().catch(() => {}); }
      else if (Math.abs(v.currentTime - t) > 0.3) v.currentTime = t;
    } else {
      if (!v.paused) v.pause();
      if (Math.abs(v.currentTime - t) > 0.5 / FPS) v.currentTime = t;
    }
  }
}

let lastTs = null;
function togglePlay() {
  S.playing = !S.playing;
  if (S.playing) {
    if (S.playhead >= contentEnd() - 1) S.playhead = 0;
    lastTs = null;
    requestAnimationFrame(tick);
  } else {
    renderViewer();
  }
  $("playBtn").textContent = S.playing ? "❚❚" : "▶";
}

function tick(ts) {
  if (!S.playing) return;
  if (lastTs !== null) S.playhead += ((ts - lastTs) / 1000) * FPS;
  lastTs = ts;
  const end = contentEnd();
  if (S.playhead >= end) { S.playhead = Math.max(0, end - 1); S.playing = false; $("playBtn").textContent = "▶"; }
  renderViewer();
  placePlayhead();
  if (S.playing) requestAnimationFrame(tick);
}

function setPlayhead(frame) {
  S.playhead = clamp(Math.round(frame), 0, timelineLength());
  renderViewer();
  placePlayhead();
}

// ── Timeline ───────────────────────────────────────────────────────────────

const tlContent = $("tlContent");

function issueClipIds() {
  if (readonly()) return new Set();
  const ids = new Set();
  for (const i of S.issues) {
    const d = i.details || {};
    for (const k of ["clip_a", "clip_b", "video_item", "item_id"]) if (d[k]) ids.add(d[k]);
  }
  return ids;
}

function renderTimeline() {
  const files = view();
  const z = S.zoom;
  const len = timelineLength();
  const vTracks = [...files.cuts.video_tracks].sort((a, b) => b.index - a.index);
  const aTracks = [...files.audio.audio_tracks].sort((a, b) => a.index - b.index);
  const selId = S.selected && S.selected.type === "clip" ? S.selected.id : null;
  const flagged = issueClipIds();

  $("tlLabels").replaceChildren(
    h("div", { class: "m" }, "M"),
    ...vTracks.map((t) => h("div", {}, `V${t.index}`)),
    ...aTracks.map((t) => h("div", {}, `A${t.index}`)),
  );

  const pxPerSec = z * FPS;
  const step = [1, 2, 5, 10, 15, 30, 60].find((s) => s * pxPerSec >= 70) || 60;
  const ruler = h("div", { class: "ruler", dataset: { role: "ruler" } });
  for (let s = 0; s * FPS < len; s += step) {
    ruler.append(h("div", { class: "tick", style: `left:${s * pxPerSec}px` }, tc(s * FPS).slice(3, 8)));
  }

  const markerLane = h("div", { class: "lane m", dataset: { role: "lane" } },
    files.markers.markers.map((m) => h("div", {
      class: "marker" + (S.selected && S.selected.type === "marker" && S.selected.id === m.frame ? " selected" : ""),
      style: `left:${m.frame * z}px;background:${MARKER_COLORS[m.color] || MARKER_COLORS.Blue}`,
      title: `${tc(m.frame)} — ${m.name}${m.note ? ": " + m.note : ""}`,
      dataset: { marker: m.frame },
    })));

  const videoLanes = vTracks.map((t) => h("div", { class: "lane", dataset: { role: "lane", vtrack: t.index } },
    t.items.map((c) => {
      const src = S.lib[c.media_ref];
      const badges = [];
      if (files.color.grades[c.id]) badges.push(h("span", {}, "CC"));
      if (files.effects.clip_effects[c.id]) badges.push(h("span", {}, "FX"));
      if (c.speed) badges.push(h("span", {}, `${c.speed.speed_percent}%`));
      return h("div", {
        class: "clip video" + (c.id === selId ? " selected" : "") + (flagged.has(c.id) ? " issue" : "") +
          (c.clip_enabled === false ? " disabled" : "") + (readonly() ? " readonly" : ""),
        style: `left:${c.record_start_frame * z}px;width:${Math.max(4, (c.record_end_frame - c.record_start_frame) * z)}px;background:${src ? src.color : "#777"}`,
        title: `${c.name}\nid ${c.id}\ntimeline ${c.record_start_frame}–${c.record_end_frame}  ·  source ${c.source_start_frame}–${c.source_end_frame}`,
        dataset: { clip: c.id },
      },
      h("div", { class: "name" }, c.name),
      h("div", { class: "badges" }, badges),
      readonly() ? null : h("div", { class: "h l", dataset: { handle: "l" } }),
      readonly() ? null : h("div", { class: "h r", dataset: { handle: "r" } }));
    })));

  const audioLanes = aTracks.map((t) => h("div", { class: "lane", dataset: { role: "lane", atrack: t.index } },
    t.items.map((a) => {
      const vid = videoIdFor(a.id);
      const linked = findClip(vid, files);
      return h("div", {
        class: "clip audio" + (vid === selId ? " selected" : "") + (flagged.has(a.id) || flagged.has(vid) ? " issue" : ""),
        style: `left:${a.start_frame * z}px;width:${Math.max(4, (a.end_frame - a.start_frame) * z)}px`,
        title: `Audio ${a.id}\nvolume ${a.volume} dB  ·  pan ${a.pan}`,
        dataset: { audio: a.id },
      },
      h("div", { class: "wave" }),
      h("div", { class: "name" }, `${linked ? linked.name : a.media_ref}  ${a.volume ? (a.volume > 0 ? "+" : "") + a.volume + " dB" : ""}`));
    })));

  tlContent.style.width = `${len * z}px`;
  tlContent.replaceChildren(ruler, markerLane, ...videoLanes, ...audioLanes, h("div", { class: "playhead", id: "playhead" }));
  placePlayhead();
}

function placePlayhead() {
  const ph = $("playhead");
  if (ph) ph.style.left = `${Math.floor(S.playhead) * S.zoom}px`;
  $("timecode").textContent = tc(S.playhead);
}

function frameAtClientX(x) {
  return (x - tlContent.getBoundingClientRect().left) / S.zoom;
}

function snapFrames(frame, ignoreId) {
  const tol = 8 / S.zoom;
  let best = null;
  const edges = [0, Math.floor(S.playhead)];
  for (const c of allClips(S.files)) if (c.id !== ignoreId) edges.push(c.record_start_frame, c.record_end_frame);
  for (const e of edges) if (Math.abs(frame - e) <= tol && (best === null || Math.abs(frame - e) < Math.abs(frame - best))) best = e;
  return best;
}

tlContent.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  const clipEl = ev.target.closest(".clip.video");
  const audioEl = ev.target.closest(".clip.audio");
  const markerEl = ev.target.closest(".marker");
  const rulerEl = ev.target.closest(".ruler");

  if (markerEl) {
    S.selected = { type: "marker", id: Number(markerEl.dataset.marker) };
    setPlayhead(S.selected.id);
    render();
    return;
  }
  if (audioEl) {
    S.selected = { type: "clip", id: videoIdFor(audioEl.dataset.audio) };
    render();
    return;
  }
  if (clipEl) {
    const id = clipEl.dataset.clip;
    S.selected = { type: "clip", id };
    renderInspector();
    renderTimeline();
    if (readonly()) return;
    const c = findClip(id, S.files);
    const mode = ev.target.dataset.handle === "l" ? "trimL" : ev.target.dataset.handle === "r" ? "trimR" : "move";
    S.drag = {
      id, mode, x0: ev.clientX, moved: false,
      orig: { rs: c.record_start_frame, re: c.record_end_frame, ss: c.source_start_frame, se: c.source_end_frame, track: c.track_index },
    };
    ev.preventDefault();
    return;
  }
  // ruler or empty lane: scrub
  if (rulerEl || ev.target.closest(".lane")) {
    if (!rulerEl) { S.selected = null; renderInspector(); renderTimeline(); }
    S.drag = { mode: "scrub" };
    setPlayhead(frameAtClientX(ev.clientX));
    ev.preventDefault();
  }
});

window.addEventListener("pointermove", (ev) => {
  const d = S.drag;
  if (!d) return;
  if (d.mode === "scrub") { setPlayhead(frameAtClientX(ev.clientX)); return; }
  const c = findClip(d.id, S.files);
  if (!c) return;
  const dx = Math.round((ev.clientX - d.x0) / S.zoom);
  if (dx !== 0) d.moved = true;
  const o = d.orig, sp = speedOf(c);
  const trackItems = allClips(S.files).filter((x) => x.track_index === o.track && x.id !== c.id);

  if (d.mode === "move") {
    const len = o.re - o.rs;
    let s = Math.max(0, o.rs + dx);
    const snapS = snapFrames(s, c.id), snapE = snapFrames(s + len, c.id);
    if (snapS !== null) s = snapS; else if (snapE !== null) s = snapE - len;
    c.record_start_frame = Math.max(0, s);
    c.record_end_frame = c.record_start_frame + len;
    const lane = document.elementsFromPoint(ev.clientX, ev.clientY).find((e) => e.dataset && e.dataset.vtrack);
    if (lane) { c.track_index = Number(lane.dataset.vtrack); d.moved = true; }
  } else if (d.mode === "trimL") {
    const src = S.lib[c.media_ref];
    const prevEnd = trackItems.filter((x) => x.record_end_frame <= o.rs).reduce((m, x) => Math.max(m, x.record_end_frame), 0);
    const minRs = Math.max(prevEnd, o.rs - Math.floor(o.ss / sp));
    let rs = clamp(o.rs + dx, minRs, o.re - 1);
    const snap = snapFrames(rs, c.id);
    if (snap !== null && snap >= minRs && snap < o.re) rs = snap;
    c.record_start_frame = rs;
    c.source_start_frame = clamp(o.ss + Math.round((rs - o.rs) * sp), 0, src ? src.duration_frames - 1 : Infinity);
  } else {
    const src = S.lib[c.media_ref];
    const nextStart = trackItems.filter((x) => x.record_start_frame >= o.re).reduce((m, x) => Math.min(m, x.record_start_frame), Infinity);
    const maxRe = Math.min(nextStart, o.re + Math.floor(((src ? src.duration_frames : o.se) - o.se) / sp));
    let re = clamp(o.re + dx, o.rs + 1, maxRe);
    const snap = snapFrames(re, c.id);
    if (snap !== null && snap > o.rs && snap <= maxRe) re = snap;
    c.record_end_frame = re;
    c.source_end_frame = o.se + Math.round((re - o.re) * sp);
  }
  renderTimeline();
  renderViewer();
});

window.addEventListener("pointerup", () => {
  const d = S.drag;
  S.drag = null;
  if (!d || d.mode === "scrub" || !d.moved) return;
  const c = findClip(d.id, S.files);
  if (!c) return;
  const o = d.orig;
  const unchanged = c.record_start_frame === o.rs && c.record_end_frame === o.re && c.track_index === o.track;
  if (unchanged) return;
  if (overlapsOn(c.track_index, c.record_start_frame, c.record_end_frame, c.id)) {
    Object.assign(c, { record_start_frame: o.rs, record_end_frame: o.re, source_start_frame: o.ss, source_end_frame: o.se, track_index: o.track });
    toast("Clips can't overlap on the same track — try another spot or V2.");
    render();
    return;
  }
  edit(() => syncAudio(c));
  renderInspector();
});

// Drag from the media bin
tlContent.addEventListener("dragover", (ev) => {
  const lane = ev.target.closest(".lane");
  if (!lane || readonly() || !(lane.dataset.vtrack || lane.dataset.atrack)) return;
  ev.preventDefault();
  document.querySelectorAll(".lane.drop").forEach((l) => l.classList.remove("drop"));
  lane.classList.add("drop");
});
tlContent.addEventListener("dragleave", (ev) => {
  const lane = ev.target.closest(".lane");
  if (lane) lane.classList.remove("drop");
});
tlContent.addEventListener("drop", (ev) => {
  const lane = ev.target.closest(".lane");
  document.querySelectorAll(".lane.drop").forEach((l) => l.classList.remove("drop"));
  const ref = ev.dataTransfer.getData("text/vit-ref");
  if (!lane || !ref) return;
  ev.preventDefault();
  addClip(ref, Number(lane.dataset.vtrack || lane.dataset.atrack), frameAtClientX(ev.clientX));
});

// ── Media bin ──────────────────────────────────────────────────────────────

function renderBin() {
  const items = S.library.map((src) => {
    const thumb = h("canvas", { width: 192, height: 108 });
    const tctx = thumb.getContext("2d");
    const paint = (media) => {
      const tmp = Object.assign(document.createElement("canvas"), { width: VW, height: VH });
      const c = tmp.getContext("2d");
      if (!src.available) SOURCES.drawOffline(c, VW, VH, src.name);
      else if (media) drawCoverOn(c, media);
      else SOURCES.draw(c, VW, VH, src.kind, Math.floor(src.duration_frames / 2));
      tctx.drawImage(tmp, 0, 0, 192, 108);
    };
    if (VIDEO_KINDS.has(src.kind) && src.available) {
      const v = h("video", { src: "/media/" + encodeURIComponent(src.file), preload: "auto", muted: true });
      v.addEventListener("loadeddata", () => { v.currentTime = Math.min(1, v.duration / 2); });
      v.addEventListener("seeked", () => paint(v), { once: true });
    } else {
      paint(null);
    }
    return h("div", {
      class: "bin-item", draggable: "true", title: `${src.name}\n${src.ref}\nDouble-click to add at playhead`,
      ondragstart: (ev) => { ev.dataTransfer.setData("text/vit-ref", src.ref); ev.dataTransfer.effectAllowed = "copy"; },
      ondblclick: () => addClip(src.ref, 1, S.playhead),
    }, thumb, h("div", { class: "meta" },
      h("b", {}, src.name),
      h("span", {}, src.available ? `${(src.duration_frames / FPS).toFixed(1)}s · ${src.duration_frames}f` : "offline")));
  });
  $("binList").replaceChildren(...items);
}

function drawCoverOn(c, media) {
  const scale = Math.max(VW / media.videoWidth, VH / media.videoHeight);
  const w = media.videoWidth * scale, hh = media.videoHeight * scale;
  c.drawImage(media, (VW - w) / 2, (VH - hh) / 2, w, hh);
}

$("importInput").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const url = URL.createObjectURL(file);
  const probe = h("video", { preload: "metadata", src: url });
  try {
    await new Promise((res, rej) => { probe.onloadedmetadata = res; probe.onerror = () => rej(new Error("This browser can't play that file. Try an .mp4 (H.264) or .webm.")); });
    toast(`Importing ${file.name}…`);
    const q = new URLSearchParams({
      name: file.name, duration_frames: Math.round(probe.duration * FPS),
      width: probe.videoWidth, height: probe.videoHeight,
    });
    const res = await fetch(`/api/media?${q}`, { method: "POST", body: file, headers: { "X-Vit-Demo": "1" } });
    const entry = await res.json();
    if (!res.ok) throw new Error(entry.error || "Upload failed");
    S.library = S.library.filter((s) => s.ref !== entry.ref).concat(entry);
    S.lib[entry.ref] = entry;
    renderBin();
    toast(`${file.name} is in the media bin. Only its checksum will go into git.`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    URL.revokeObjectURL(url);
  }
});

// ── Inspector ──────────────────────────────────────────────────────────────

function slider(label, value, min, max, step, fmt, onInput) {
  const out = h("output", {}, fmt(value));
  const input = h("input", {
    type: "range", min, max, step, value,
    oninput: (ev) => { const v = Number(ev.target.value); out.textContent = fmt(v); onInput(v); },
  });
  return h("div", { class: "field" }, h("label", {}, label), input, out);
}

// Slider edits mutate in place and re-render the viewer + timeline without
// rebuilding the inspector (which would steal focus from the slider).
function liveEdit(fn) {
  if (readonly()) return;
  fn();
  sortAll();
  renderViewer();
  renderTimeline();
  scheduleSync();
}

function gradeNode(id, create) {
  const grades = S.files.color.grades;
  if (!grades[id] && create) {
    grades[id] = { num_nodes: 1, nodes: [{ index: 1, label: "Primary", lut: "" }], version_name: "Version 1", drx_file: null, lut_file: null };
  }
  return grades[id] ? grades[id].nodes[0] : null;
}

function setGrade(id, key, value) {
  const isDefault = Math.abs(value - GRADE_DEFAULTS[key]) < 1e-9;
  const node = gradeNode(id, !isDefault);
  if (!node) return;
  if (isDefault) delete node[key]; else node[key] = round3(value);
  if (!Object.keys(GRADE_DEFAULTS).some((k) => k in node)) delete S.files.color.grades[id];
}

function setEffect(id, key, value, isDefault) {
  const all = S.files.effects.clip_effects;
  const fx = all[id] || {};
  if (isDefault) delete fx[key]; else fx[key] = value;
  if (Object.keys(fx).length) all[id] = fx; else delete all[id];
}

const GRADE_PRESETS = {
  "Warm film": { saturation: 0.9, contrast: 1.15, temperature: 900, gain_m: 1.05, hue: 0 },
  "Cool night": { saturation: 0.85, contrast: 1.2, temperature: -1200, gain_m: 0.9, hue: 0 },
  "Punchy": { saturation: 1.45, contrast: 1.25, temperature: 0, gain_m: 1, hue: 0 },
  "B&W": { saturation: 0, contrast: 1.3, temperature: 0, gain_m: 1, hue: 0 },
  "Reset": { ...GRADE_DEFAULTS },
};

function renderInspector() {
  const box = $("inspector");
  const sel = S.selected;
  const files = view();
  if (!sel) {
    box.replaceChildren(h("p", { class: "empty" }, "Select a clip or marker on the timeline. Every control here edits one of the JSON files vit tracks — the small label on each group shows which."));
    return;
  }
  if (sel.type === "marker") {
    const m = files.markers.markers.find((x) => x.frame === sel.id);
    if (!m) { S.selected = null; return renderInspector(); }
    const ro = readonly();
    box.replaceChildren(
      h("div", { class: "insp-title" }, `Marker at ${tc(m.frame)}`),
      h("div", { class: "insp-sub" }, "markers.json"),
      h("div", { class: "field text" }, h("label", {}, "Name"),
        h("input", { value: m.name, disabled: ro, oninput: (ev) => liveEdit(() => { m.name = ev.target.value; }) })),
      h("div", { class: "field text" }, h("label", {}, "Note"),
        h("input", { value: m.note, disabled: ro, oninput: (ev) => liveEdit(() => { m.note = ev.target.value; }) })),
      h("div", { class: "field text" }, h("label", {}, "Color"),
        h("select", { disabled: ro, onchange: (ev) => liveEdit(() => { m.color = ev.target.value; }) },
          Object.keys(MARKER_COLORS).map((c) => h("option", { value: c, selected: c === m.color }, c)))),
      ro ? null : h("div", { class: "insp-actions" }, h("button", {
        class: "small danger",
        onclick: () => { edit(() => { S.files.markers.markers = S.files.markers.markers.filter((x) => x !== m); S.selected = null; }); renderInspector(); },
      }, "Delete marker")),
    );
    return;
  }

  const c = findClip(sel.id, files);
  if (!c) { S.selected = null; return renderInspector(); }
  if (readonly()) {
    box.replaceChildren(
      h("div", { class: "insp-title" }, c.name),
      h("div", { class: "insp-sub" }, c.id),
      h("p", { class: "empty" }, "Read-only preview of an older version."),
      h("pre", { class: "diff" }, JSON.stringify(c, null, 2)));
    return;
  }
  const t = c.transform || (c.transform = { Pan: 0, Tilt: 0, ZoomX: 1, ZoomY: 1, Opacity: 100 });
  const node = gradeNode(c.id, false) || {};
  const g = { ...GRADE_DEFAULTS, ...node };
  const fx = S.files.effects.clip_effects[c.id] || {};
  const a = findAudio(audioIdFor(c.id), S.files);
  const src = S.lib[c.media_ref];

  const gradeSliders = h("div", {},
    slider("Exposure", g.gain_m, 0.3, 2, 0.01, (v) => v.toFixed(2), (v) => liveEdit(() => setGrade(c.id, "gain_m", v))),
    slider("Contrast", g.contrast, 0.3, 2, 0.01, (v) => v.toFixed(2), (v) => liveEdit(() => setGrade(c.id, "contrast", v))),
    slider("Saturation", g.saturation, 0, 2, 0.01, (v) => v.toFixed(2), (v) => liveEdit(() => setGrade(c.id, "saturation", v))),
    slider("Temp", g.temperature, -2000, 2000, 50, (v) => `${v > 0 ? "+" : ""}${v}`, (v) => liveEdit(() => setGrade(c.id, "temperature", v))),
    slider("Hue", g.hue, -180, 180, 1, (v) => `${v}°`, (v) => liveEdit(() => setGrade(c.id, "hue", v))),
  );

  box.replaceChildren(
    h("div", { class: "insp-title" }, c.name),
    h("div", { class: "insp-sub" }, `${c.id}  ·  source ${c.source_start_frame}–${c.source_end_frame} of ${src ? src.duration_frames : "?"}`),

    h("div", { class: "insp-group" },
      h("h3", {}, "Edit", h("span", { class: "domain-tag" }, "cuts.json")),
      slider("Zoom", t.ZoomX ?? 1, 0.5, 3, 0.01, (v) => v.toFixed(2), (v) => liveEdit(() => { t.ZoomX = round3(v); t.ZoomY = round3(v); })),
      slider("Pan", t.Pan || 0, -960, 960, 1, String, (v) => liveEdit(() => { t.Pan = v; })),
      slider("Tilt", t.Tilt || 0, -540, 540, 1, String, (v) => liveEdit(() => { t.Tilt = v; })),
      slider("Rotate", t.RotationAngle || 0, -180, 180, 1, (v) => `${v}°`, (v) => liveEdit(() => { if (v) t.RotationAngle = v; else delete t.RotationAngle; })),
      slider("Opacity", t.Opacity ?? 100, 0, 100, 1, (v) => `${v}%`, (v) => liveEdit(() => { t.Opacity = v; })),
      h("div", { class: "field text" }, h("label", {}, "Speed"),
        h("select", { onchange: (ev) => edit(() => setSpeed(c, Number(ev.target.value))) },
          [25, 50, 75, 100, 150, 200, 400].map((p) => h("option", { value: p, selected: p === Math.round(speedOf(c) * 100) }, `${p}%`)))),
      h("div", { class: "field text" }, h("label", {}, "Enabled"),
        h("input", { type: "checkbox", checked: c.clip_enabled !== false, style: "justify-self:start",
          onchange: (ev) => edit(() => { if (ev.target.checked) delete c.clip_enabled; else c.clip_enabled = false; }) })),
    ),

    h("div", { class: "insp-group" },
      h("h3", {}, "Color", h("span", { class: "domain-tag" }, "color.json")),
      h("div", { class: "presets" }, Object.entries(GRADE_PRESETS).map(([name, vals]) => h("button", {
        class: "small ghost",
        onclick: () => { edit(() => Object.entries(vals).forEach(([k, v]) => setGrade(c.id, k, v))); renderInspector(); },
      }, name))),
      gradeSliders,
    ),

    h("div", { class: "insp-group" },
      h("h3", {}, "Effects", h("span", { class: "domain-tag" }, "effects.json")),
      slider("Blur", fx.blur || 0, 0, 20, 0.5, (v) => `${v}px`, (v) => liveEdit(() => setEffect(c.id, "blur", v, v === 0))),
      slider("Vignette", fx.vignette || 0, 0, 1, 0.05, (v) => v.toFixed(2), (v) => liveEdit(() => setEffect(c.id, "vignette", round3(v), v === 0))),
      slider("Fade in", fx.fade_in || 0, 0, 48, 1, (v) => `${v}f`, (v) => liveEdit(() => setEffect(c.id, "fade_in", v, v === 0))),
      slider("Fade out", fx.fade_out || 0, 0, 48, 1, (v) => `${v}f`, (v) => liveEdit(() => setEffect(c.id, "fade_out", v, v === 0))),
    ),

    a ? h("div", { class: "insp-group" },
      h("h3", {}, "Audio", h("span", { class: "domain-tag" }, "audio.json")),
      slider("Volume", a.volume, -40, 12, 0.5, (v) => `${v > 0 ? "+" : ""}${v} dB`, (v) => liveEdit(() => { a.volume = v; })),
      slider("Pan", a.pan, -100, 100, 1, String, (v) => liveEdit(() => { a.pan = v; })),
    ) : null,

    h("div", { class: "insp-actions" },
      h("button", { class: "small", onclick: () => splitClip(c.id, S.playhead) }, "Split at playhead"),
      h("button", { class: "small danger", onclick: () => deleteClip(c.id) }, "Delete clip")),
  );
}

// ── Version control panel ──────────────────────────────────────────────────

function diffLines(text, target) {
  const rows = [];
  for (const raw of (text || "").split("\n")) {
    if (!raw.trim() || raw.trim().startsWith("Timeline:") || raw.trim().startsWith("Branch:")) continue;
    const line = raw.replace(/^ {2}/, "");
    let cls = "meta";
    if (/^[A-Z]+:$/.test(line)) cls = "sec";
    else if (line.startsWith("+ ")) cls = "add";
    else if (line.startsWith("- ")) cls = "del";
    else if (line.startsWith("~ ")) cls = "mod";
    else if (line === "No changes.") cls = "none";
    rows.push(h("div", { class: cls }, line));
  }
  if (!rows.length) rows.push(h("div", { class: "none" }, "No changes."));
  target.replaceChildren(...rows);
  return rows.filter((r) => ["add", "del", "mod"].includes(r.className)).length;
}

function renderVC() {
  $("branchPill").textContent = S.detached ? "detached HEAD" : S.branch;
  $("dirtyPill").hidden = !S.dirty;
  $("branchSelect").replaceChildren(...S.branches.map((b) => h("option", { value: b, selected: b === S.branch }, b)));

  const n = diffLines(S.dirty ? S.diff : "", $("diffView"));
  $("changesCount").textContent = S.dirty ? `${n} change${n === 1 ? "" : "s"} since last version` : "working copy matches last version";
  $("commitBtn").disabled = !S.dirty || readonly();

  $("issuesView").replaceChildren(...S.issues.map((i) => {
    const d = i.details || {};
    let fix = null;
    if (i.category === "orphaned_ref") {
      fix = h("button", { class: "small", onclick: () => edit(() => {
        if (d.domain === "color") delete S.files.color.grades[d.item_id];
        else delete S.files.effects.clip_effects[d.item_id];
      }) }, "Drop it");
    } else if (i.category === "sync" || i.category === "speed_sync") {
      fix = h("button", { class: "small", onclick: () => edit(() => { const v = findClip(d.video_item, S.files); if (v) syncAudio(v); }) }, "Re-link audio");
    } else if (i.category === "overlap") {
      fix = [
        h("button", { class: "small", onclick: () => moveToFreeTrack(d.clip_b) }, "Move up a track"),
        h("button", { class: "small", onclick: () => placeAfter(d.clip_b, d.clip_a) }, "Place after"),
      ];
    }
    return h("div", { class: "issue-row " + i.severity }, h("span", {}, i.message), fix);
  }));
}

function laneLayout(commits) {
  const lanes = [];
  const pos = {};
  commits.forEach((c, row) => {
    let col = lanes.indexOf(c.hash);
    if (col === -1) { col = lanes.indexOf(null); if (col === -1) { col = lanes.length; lanes.push(null); } }
    for (let j = 0; j < lanes.length; j++) if (j !== col && lanes[j] === c.hash) lanes[j] = null;
    pos[c.hash] = { row, col };
    lanes[col] = c.parents[0] || null;
    for (const p of c.parents.slice(1)) {
      if (lanes.includes(p)) continue;
      const free = lanes.indexOf(null);
      if (free === -1) lanes.push(p); else lanes[free] = p;
    }
  });
  return pos;
}

function renderGraph() {
  const commits = S.commits;
  const pos = laneLayout(commits);
  const rowH = 46, laneW = 16, pad = 12;
  const maxCol = Math.max(0, ...Object.values(pos).map((p) => p.col));
  const width = pad * 2 + maxCol * laneW;
  const height = commits.length * rowH;
  const X = (col) => pad + col * laneW, Y = (row) => row * rowH + rowH / 2;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("width", width); svg.setAttribute("height", height);

  commits.forEach((c) => {
    const a = pos[c.hash];
    c.parents.forEach((p, i) => {
      const b = pos[p];
      if (!b) return;
      const path = document.createElementNS(NS, "path");
      let d;
      if (a.col === b.col) d = `M${X(a.col)},${Y(a.row)} L${X(b.col)},${Y(b.row)}`;
      else if (i === 0) d = `M${X(a.col)},${Y(a.row)} L${X(a.col)},${Y(b.row) - rowH} C${X(a.col)},${Y(b.row) - rowH / 3} ${X(b.col)},${Y(b.row) - rowH / 2} ${X(b.col)},${Y(b.row)}`;
      else d = `M${X(a.col)},${Y(a.row)} C${X(a.col)},${Y(a.row) + rowH / 2} ${X(b.col)},${Y(a.row) + rowH / 3} ${X(b.col)},${Y(a.row) + rowH} L${X(b.col)},${Y(b.row)}`;
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", LANE_COLORS[(i === 0 ? a.col : b.col) % LANE_COLORS.length]);
      path.setAttribute("stroke-width", "2");
      svg.append(path);
    });
  });
  commits.forEach((c) => {
    const a = pos[c.hash];
    const dot = document.createElementNS(NS, "circle");
    dot.setAttribute("cx", X(a.col)); dot.setAttribute("cy", Y(a.row));
    dot.setAttribute("r", c.is_head ? 6 : 4.5);
    dot.setAttribute("fill", c.is_head ? "#fff" : LANE_COLORS[a.col % LANE_COLORS.length]);
    dot.setAttribute("stroke", LANE_COLORS[a.col % LANE_COLORS.length]);
    dot.setAttribute("stroke-width", c.is_head ? 3 : 0);
    svg.append(dot);
  });

  const rows = commits.map((c) => h("div", {
    class: "commit-row" + (c.is_head ? " head" : ""), style: `padding-left:${width + 4}px`,
    onclick: () => openCommit(c),
  },
  h("div", { class: "msg" }, c.message.replace(/^vit:\s*/, "")),
  h("div", { class: "sub" },
    c.refs.map((r) => h("span", { class: "ref" + (r === S.branch ? " current" : "") }, r)),
    `${c.author} · ${c.date} · ${c.hash}`)));
  $("graph").replaceChildren(svg, ...rows);
}

// ── Modal helpers ──────────────────────────────────────────────────────────

function openModal(title, ...body) {
  $("modalTitle").textContent = title;
  $("modalBody").replaceChildren(...body);
  $("modal").hidden = false;
}
function closeModal() { $("modal").hidden = true; }
$("modalClose").addEventListener("click", closeModal);
$("modal").addEventListener("click", (ev) => { if (ev.target === $("modal")) closeModal(); });

function diffBox(text) {
  const box = h("div", { class: "diff" });
  diffLines(text, box);
  return box;
}

async function openCommit(c) {
  try {
    const { diff } = await api(`/api/commit?ref=${c.hash}`);
    openModal(`${c.message.replace(/^vit:\s*/, "")}  ·  ${c.hash}`,
      h("div", { class: "muted" }, `${c.author} · ${c.date}${c.parents.length > 1 ? " · merge commit (2 parents)" : ""}`),
      h("h3", {}, c.parents.length ? "What this version changed" : "Initial snapshot"),
      diffBox(diff),
      h("div", { class: "modal-actions" },
        h("button", { onclick: () => startPreview(c) }, "Preview in viewer"),
        h("button", { class: "accent", onclick: () => restoreVersion(c.hash) }, "Restore this version")));
  } catch (e) { toast(e.message, true); }
}

async function startPreview(c) {
  try {
    const { files } = await api(`/api/files?ref=${c.hash}`);
    S.preview = { ref: c.hash, files: ensureShape(files), label: c.message.replace(/^vit:\s*/, "") };
    S.selected = null;
    closeModal();
    render();
  } catch (e) { toast(e.message, true); }
}

async function restoreVersion(ref) {
  try {
    await flushSync();
    const r = await api("/api/restore", { ref, author: author() });
    S.preview = null;
    closeModal();
    await loadAll();
    toast(r.hash ? `Restored ${ref} as a new version (${r.hash}). Nothing in history was lost.` : "Working copy already matches that version.");
  } catch (e) { toast(e.message, true); }
}

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
        : null,
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

// ── JSON-on-disk view ──────────────────────────────────────────────────────

const JSON_FILES = ["timeline/cuts.json", "timeline/color.json", "timeline/audio.json", "timeline/effects.json",
  "timeline/markers.json", "timeline/metadata.json", "assets/manifest.json"];

async function loadJson() {
  $("jsonFiles").replaceChildren(...JSON_FILES.map((f) => h("button", {
    class: "small" + (f === S.jsonFile ? " active" : ""),
    onclick: () => { S.jsonFile = f; loadJson(); },
  }, f)));
  try {
    const r = await api(`/api/raw?path=${encodeURIComponent(S.jsonFile)}`);
    $("jsonContent").textContent = r.content;
    const lines = r.git_diff.split("\n").map((l) => h("div", {
      class: l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : "",
    }, l || " "));
    $("jsonDiff").replaceChildren(...(r.git_diff.trim() ? lines : [h("div", { class: "muted" }, "No unsaved changes in this file.")]));
  } catch (e) { toast(e.message, true); }
}

// ── Loading + top-level render ─────────────────────────────────────────────

async function loadAll() {
  const st = await api("/api/state");
  Object.assign(S, {
    files: ensureShape(st.files), library: st.library, branch: st.branch, branches: st.branches,
    detached: st.detached, dirty: st.dirty, diff: st.diff, issues: st.issues, head: st.head,
  });
  S.lib = Object.fromEntries(S.library.map((s) => [s.ref, s]));
  if (S.selected && S.selected.type === "clip" && !findClip(S.selected.id)) S.selected = null;
  renderBin();
  render();
  await loadLog();
  if (S.view === "json") loadJson();
}

async function loadLog() {
  const r = await api("/api/log");
  S.commits = r.commits;
  renderGraph();
}

function render() {
  const pv = S.preview;
  $("previewBanner").hidden = !pv;
  if (pv) $("previewLabel").textContent = `Previewing ${pv.ref} — “${pv.label}” (read-only)`;
  renderTimeline();
  renderViewer();
  renderInspector();
  renderVC();
}

// ── Wiring ─────────────────────────────────────────────────────────────────

$("playBtn").addEventListener("click", togglePlay);
$("splitBtn").addEventListener("click", () => {
  if (S.selected && S.selected.type === "clip") splitClip(S.selected.id, S.playhead);
  else toast("Select a clip first.");
});
$("markerBtn").addEventListener("click", addMarker);
$("burnIn").addEventListener("change", renderViewer);
$("zoomRange").addEventListener("input", (ev) => { S.zoom = Number(ev.target.value); renderTimeline(); });

document.querySelectorAll(".center-tabs .tab").forEach((tab) => tab.addEventListener("click", () => {
  S.view = tab.dataset.view;
  document.querySelectorAll(".center-tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
  $("viewerWrap").hidden = S.view !== "viewer";
  $("jsonWrap").hidden = S.view !== "json";
  if (S.view === "json") flushSync().then(loadJson);
}));

$("previewExit").addEventListener("click", () => { S.preview = null; S.selected = null; render(); });
$("previewRestore").addEventListener("click", () => restoreVersion(S.preview.ref));

$("commitForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await flushSync();
    const msg = $("commitMsg").value.trim();
    if (!msg) { $("commitMsg").focus(); toast("Give this version a short description."); return; }
    const r = await api("/api/commit", { message: msg, author: author() });
    $("commitMsg").value = "";
    toast(r.hash ? `Saved version ${r.hash} on ${S.branch}` : "Nothing changed since the last version.");
    await loadAll();
  } catch (e) { toast(e.message, true); }
});

$("branchSelect").addEventListener("change", async (ev) => {
  const ref = ev.target.value;
  try {
    await flushSync();
    const r = await api("/api/checkout", { ref, author: author() });
    S.preview = null;
    await loadAll();
    toast(r.autosaved ? `Auto-saved your changes (${r.autosaved}), then switched to ${ref}.` : `Switched to ${ref}. The timeline was rebuilt from its JSON.`);
  } catch (e) { toast(e.message, true); renderVC(); }
});

$("newBranchBtn").addEventListener("click", () => { $("newBranchForm").hidden = false; $("newBranchName").focus(); });
$("newBranchCancel").addEventListener("click", () => { $("newBranchForm").hidden = true; });
$("newBranchForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = $("newBranchName").value.trim();
  if (!name) return;
  try {
    await flushSync();
    const from = S.branch;
    await api("/api/branch", { name });
    $("newBranchName").value = "";
    $("newBranchForm").hidden = true;
    await loadAll();
    toast(`Created ${name} from ${from} — edits here won't touch ${from}.`);
  } catch (e) { toast(e.message, true); }
});

$("mergeBtn").addEventListener("click", async () => { await flushSync(); openMerge(); });

$("resetBtn").addEventListener("click", () => {
  openModal("Reset the demo?",
    h("p", {}, "This deletes the demo git repository (all branches and versions) and starts again from the starter timeline. Imported media stays in the media bin."),
    h("div", { class: "modal-actions" },
      h("button", { class: "ghost", onclick: closeModal }, "Cancel"),
      h("button", { class: "accent", onclick: async () => {
        try {
          clearTimeout(syncTimer); pendingSync = false;
          await api("/api/reset", {});
          S.preview = null; S.selected = null; S.playhead = 0;
          closeModal();
          await loadAll();
          toast("Fresh project on main.");
        } catch (e) { toast(e.message, true); }
      } }, "Reset")));
});

try { $("authorInput").value = localStorage.getItem("vit-demo-author") || ""; } catch (_) { /* storage unavailable */ }
$("authorInput").addEventListener("input", (ev) => {
  try { localStorage.setItem("vit-demo-author", ev.target.value); } catch (_) { /* ignore */ }
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { if (!$("modal").hidden) closeModal(); return; }
  if (ev.target.closest("input, textarea, select") || !$("modal").hidden) return;
  const sel = S.selected;
  if (ev.key === " ") { ev.preventDefault(); togglePlay(); }
  else if (ev.key === "ArrowLeft") { ev.preventDefault(); setPlayhead(S.playhead - (ev.shiftKey ? FPS : 1)); }
  else if (ev.key === "ArrowRight") { ev.preventDefault(); setPlayhead(S.playhead + (ev.shiftKey ? FPS : 1)); }
  else if ((ev.key === "Delete" || ev.key === "Backspace") && sel) {
    ev.preventDefault();
    if (sel.type === "clip") deleteClip(sel.id);
    else { edit(() => { S.files.markers.markers = S.files.markers.markers.filter((m) => m.frame !== sel.id); S.selected = null; }); renderInspector(); }
  }
  else if (ev.key === "s" && sel && sel.type === "clip") splitClip(sel.id, S.playhead);
  else if (ev.key === "m") addMarker();
});

loadAll().catch((e) => toast(`Could not reach the vit demo server: ${e.message}`, true));
