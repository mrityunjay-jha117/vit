"""Vit standalone demo — a browser timeline editor backed by real vit-core + git.

No NLE needed. The browser plays the role of the NLE: it edits the timeline
and posts it here; this server is the "serializer" that writes domain-split
JSON with vit's own models/json_writer, and every version-control action runs
through vit.core (the system git binary).

Run:  python demo/server.py        then open http://127.0.0.1:8765
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(DEMO_DIR))

from vit.core import (  # noqa: E402
    GitError,
    git_add,
    git_branch,
    git_checkout,
    git_commit,
    git_config_set,
    git_current_branch,
    git_diff,
    git_init,
    git_is_clean,
    git_list_branches,
    git_log_graph,
    git_merge_base,
    git_merge_tree_conflicts,
    git_rev_parse,
    git_show_file,
)
from vit.differ import diff_from_project, format_diff  # noqa: E402
from vit.json_writer import read_all_domain_files, write_timeline  # noqa: E402
from vit.merge_utils import domain_file_map, perform_merge, three_way_merge_domains  # noqa: E402
from vit.models import (  # noqa: E402
    Asset,
    AudioTrack,
    ColorGrade,
    Marker,
    Timeline,
    TimelineMetadata,
    VideoTrack,
)
from vit.validator import validate_project  # noqa: E402

STATIC_DIR = os.path.join(DEMO_DIR, "static")
WORKSPACE = os.environ.get("VIT_DEMO_WORKSPACE", os.path.join(DEMO_DIR, "workspace"))
PROJECT_DIR = os.path.join(WORKSPACE, "project")
MEDIA_DIR = os.path.join(WORKSPACE, "media")
LIBRARY_FILE = os.path.join(MEDIA_DIR, "library.json")

FPS = 24.0
LOCK = threading.Lock()

# Built-in footage. Frames are drawn procedurally in the browser from
# (kind, source_frame), so the demo needs no media files at all — exactly like
# vit, which only ever stores *references* to frames.
SYNTHETIC_SOURCES = [
    {"ref": "demo:interview", "name": "Interview_A.mov", "kind": "interview", "duration_frames": 1440, "color": "#c9a227"},
    {"ref": "demo:sunset", "name": "Sunset_Beach.mov", "kind": "sunset", "duration_frames": 720, "color": "#e8804a"},
    {"ref": "demo:city", "name": "City_Night.mov", "kind": "city", "duration_frames": 960, "color": "#6b7bff"},
    {"ref": "demo:forest", "name": "Forest_Drone.mov", "kind": "forest", "duration_frames": 720, "color": "#3fa36b"},
    {"ref": "demo:ocean", "name": "Ocean_Waves.mov", "kind": "ocean", "duration_frames": 600, "color": "#2aa4c8"},
    {"ref": "demo:bars", "name": "Color_Bars.mov", "kind": "bars", "duration_frames": 480, "color": "#9a9a9a"},
]

# Integer-valued fields in the domain JSON. Every other number is written as a
# float, so a value that round-trips through the browser (where 1.0 becomes 1)
# produces byte-identical JSON and git doesn't see a phantom change.
INT_KEYS = {
    "record_start_frame", "record_end_frame", "source_start_frame", "source_end_frame",
    "start_frame", "end_frame", "track_index", "index", "num_nodes", "frame", "duration",
    "duration_frames", "width", "height", "video", "audio", "composite_mode",
    "dynamic_zoom_ease", "retime_process", "motion_estimation", "fade_in", "fade_out",
}


# ── Media library (lives on disk, outside git — like real footage) ──────────

def load_library():
    library = [dict(s, available=True) for s in SYNTHETIC_SOURCES]
    if os.path.exists(LIBRARY_FILE):
        with open(LIBRARY_FILE) as f:
            for entry in json.load(f):
                entry = dict(entry, kind="video")
                entry["available"] = os.path.exists(os.path.join(MEDIA_DIR, entry["file"]))
                library.append(entry)
    return library


def save_imported(entry):
    entries = []
    if os.path.exists(LIBRARY_FILE):
        with open(LIBRARY_FILE) as f:
            entries = json.load(f)
    entries = [e for e in entries if e["ref"] != entry["ref"]] + [entry]
    with open(LIBRARY_FILE, "w") as f:
        json.dump(entries, f, indent=2, sort_keys=True)


def build_assets(files):
    """Manifest = every media ref the timeline uses (paths + checksums, never bytes)."""
    library = {s["ref"]: s for s in load_library()}
    previous = (files.get("manifest") or {}).get("assets", {})
    used = set()
    for track in files.get("cuts", {}).get("video_tracks", []):
        used.update(i["media_ref"] for i in track.get("items", []))
    for track in files.get("audio", {}).get("audio_tracks", []):
        used.update(i["media_ref"] for i in track.get("items", []))

    assets = {}
    for ref in sorted(used):
        src = library.get(ref)
        if src is None and ref in previous:
            assets[ref] = Asset.from_dict(previous[ref])
        elif src is not None:
            procedural = src["kind"] != "video"
            assets[ref] = Asset(
                filename=src["name"],
                original_path="(procedural demo source)" if procedural
                else os.path.join(MEDIA_DIR, src["file"]).replace("\\", "/"),
                duration_frames=int(src["duration_frames"]),
                codec="procedural" if procedural else "browser-decoded",
                resolution=f"{src.get('width', 1920)}x{src.get('height', 1080)}",
            )
    return assets


# ── "Serializer": browser timeline → domain-split JSON ──────────────────────

def _normalize_numbers(value, key=None):
    if isinstance(value, dict):
        return {k: _normalize_numbers(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(v, key) for v in value]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return int(round(value)) if key in INT_KEYS else float(value)


def write_working(files):
    """Write the timeline through vit's models + json_writer (round-trip validated)."""
    files = _normalize_numbers(files)
    timeline = Timeline(
        metadata=TimelineMetadata.from_dict(files.get("metadata", {})),
        video_tracks=[VideoTrack.from_dict(t) for t in files.get("cuts", {}).get("video_tracks", [])],
        audio_tracks=[AudioTrack.from_dict(t) for t in files.get("audio", {}).get("audio_tracks", [])],
        color_grades={k: ColorGrade.from_dict(v) for k, v in files.get("color", {}).get("grades", {}).items()},
        effects={"clip_effects": files.get("effects", {}).get("clip_effects", {})},
        markers=[Marker.from_dict(m) for m in files.get("markers", {}).get("markers", [])],
        assets=build_assets(files),
    )
    write_timeline(PROJECT_DIR, timeline)


def files_at_ref(ref):
    files = {}
    for domain, path in domain_file_map().items():
        content = git_show_file(PROJECT_DIR, ref, path) if ref else None
        files[domain] = json.loads(content) if content else {}
    return files


def issues_json():
    return [
        {"severity": i.severity, "category": i.category, "message": i.message, "details": i.details}
        for i in validate_project(PROJECT_DIR)
    ]


def stage_all():
    git_add(PROJECT_DIR, ["timeline/", "assets/", ".vit/", ".gitignore"])


def set_author(author):
    name = (author or "").strip() or "Vit Demo"
    slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".") or "user"
    git_config_set(PROJECT_DIR, "user.name", name)
    git_config_set(PROJECT_DIR, "user.email", f"{slug}@vit.demo")


def commit_if_changed(message, author=None):
    """Stage + commit. Returns short hash, or None when the timeline is unchanged."""
    set_author(author)
    stage_all()
    try:
        return git_commit(PROJECT_DIR, message)
    except GitError as e:
        if "nothing to commit" in str(e) or "nothing added to commit" in str(e):
            return None
        raise


def autosave_if_dirty(reason, author):
    if not git_is_clean(PROJECT_DIR):
        return commit_if_changed(f"vit: auto-save before {reason}", author)
    return None


# ── Project bootstrap ───────────────────────────────────────────────────────

def _video(clip_id, src, rec_start, rec_end, source_start, track=1):
    return {
        "id": clip_id, "name": src["name"], "media_ref": src["ref"],
        "record_start_frame": rec_start, "record_end_frame": rec_end,
        "source_start_frame": source_start, "source_end_frame": source_start + (rec_end - rec_start),
        "track_index": track,
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0, "Opacity": 100.0},
    }


def _audio(video):
    return {
        "id": "a" + video["id"][1:], "media_ref": video["media_ref"],
        "start_frame": video["record_start_frame"], "end_frame": video["record_end_frame"],
        "volume": 0.0, "pan": 0.0,
    }


def starter_files():
    sources = {s["kind"]: s for s in SYNTHETIC_SOURCES}
    v1 = _video("v_intro1", sources["interview"], 0, 192, 240)
    v2 = _video("v_beach1", sources["sunset"], 192, 384, 96)
    return {
        "metadata": {
            "project_name": "Vit Demo", "timeline_name": "Main Edit", "frame_rate": FPS,
            "resolution": {"width": 1920, "height": 1080}, "start_timecode": "01:00:00:00",
            "track_count": {"video": 2, "audio": 2},
        },
        "cuts": {"video_tracks": [{"index": 1, "items": [v1, v2]}, {"index": 2, "items": []}]},
        "audio": {"audio_tracks": [{"index": 1, "items": [_audio(v1), _audio(v2)]}, {"index": 2, "items": []}]},
        "color": {"grades": {}},
        "effects": {"clip_effects": {}},
        "markers": {"markers": []},
    }


def _force_remove(func, path, _exc):
    # git marks object files read-only; Windows refuses to delete those.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def reset_project():
    workspace = os.path.abspath(WORKSPACE)
    target = os.path.abspath(PROJECT_DIR)
    if os.path.commonpath([workspace, target]) != workspace:
        raise RuntimeError(f"Refusing to delete {target}: outside demo workspace")
    if os.path.exists(target):
        if sys.version_info >= (3, 12):
            shutil.rmtree(target, onexc=_force_remove)
        else:
            shutil.rmtree(target, onerror=_force_remove)
    git_init(PROJECT_DIR)
    git_branch(PROJECT_DIR, "main")  # name the unborn branch 'main' regardless of git defaults
    write_working(starter_files())
    commit_if_changed("vit: initial snapshot", "Vit Demo")


def ensure_project():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(PROJECT_DIR, ".git")):
        reset_project()


# ── API ─────────────────────────────────────────────────────────────────────

def api_state(_q, _body):
    branch = git_current_branch(PROJECT_DIR)
    return {
        "branch": branch,
        "detached": branch == "HEAD",
        "branches": git_list_branches(PROJECT_DIR),
        "head": (git_rev_parse(PROJECT_DIR, "HEAD") or "")[:7],
        "dirty": not git_is_clean(PROJECT_DIR),
        "files": read_all_domain_files(PROJECT_DIR),
        "library": load_library(),
        "diff": diff_from_project(PROJECT_DIR, "HEAD"),
        "issues": issues_json(),
        "project_dir": PROJECT_DIR,
    }


def api_working(_q, body):
    write_working(body["files"])
    return {
        "dirty": not git_is_clean(PROJECT_DIR),
        "diff": diff_from_project(PROJECT_DIR, "HEAD"),
        "issues": issues_json(),
    }


def api_commit(_q, body):
    message = (body.get("message") or "").strip() or "save version"
    if not message.startswith("vit:"):
        message = f"vit: {message}"
    commit_hash = commit_if_changed(message, body.get("author"))
    return {"hash": commit_hash, "message": message}


def api_branch(_q, body):
    name = (body.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name):
        raise ValueError("Branch names may use letters, numbers, '.', '_', '-' and '/'.")
    git_branch(PROJECT_DIR, name)
    return {"branch": name}


def api_checkout(_q, body):
    ref = body["ref"]
    saved = autosave_if_dirty(f"switching to '{ref}'", body.get("author"))
    git_checkout(PROJECT_DIR, ref)
    return {"branch": git_current_branch(PROJECT_DIR), "autosaved": saved}


def api_log(_q, _body):
    return {"commits": git_log_graph(PROJECT_DIR), "branch": git_current_branch(PROJECT_DIR)}


def api_files(q, _body):
    return {"files": files_at_ref(q["ref"])}


def api_commit_detail(q, _body):
    ref = q["ref"]
    parent = git_rev_parse(PROJECT_DIR, f"{ref}^")
    return {"diff": format_diff(files_at_ref(parent), files_at_ref(ref))}


def api_raw(q, _body):
    path = q["path"]
    if path not in domain_file_map().values():
        raise ValueError(f"Unknown file {path}")
    full = os.path.join(PROJECT_DIR, path)
    content = open(full).read() if os.path.exists(full) else ""
    return {"content": content, "git_diff": git_diff(PROJECT_DIR, "HEAD", [path])}


def _merge_inputs(branch):
    base = git_merge_base(PROJECT_DIR, "HEAD", branch)
    return base, files_at_ref(base), files_at_ref("HEAD"), files_at_ref(branch)


def api_compare(q, _body):
    branch = q["branch"]
    if git_rev_parse(PROJECT_DIR, branch) is None:
        raise ValueError(f"Unknown branch '{branch}'")
    base, base_f, ours_f, theirs_f = _merge_inputs(branch)
    _, conflicts = three_way_merge_domains(base_f, ours_f, theirs_f)
    return {
        "ours_diff": format_diff(base_f, ours_f),
        "theirs_diff": format_diff(base_f, theirs_f),
        "conflicts": conflicts,
        "git_text_conflicts": git_merge_tree_conflicts(PROJECT_DIR, "HEAD", branch) or [],
        "up_to_date": base == git_rev_parse(PROJECT_DIR, branch),
        "dirty": not git_is_clean(PROJECT_DIR),
    }


def api_merge(_q, body):
    branch = body["branch"]
    author = body.get("author")
    current = git_current_branch(PROJECT_DIR)
    if branch == current:
        raise ValueError("Can't merge a branch into itself.")
    autosave_if_dirty(f"merging '{branch}'", author)
    set_author(author)

    # Same clip-level merge `vit merge` uses: git records the
    # two-parent merge commit, vit supplies the merged timeline.
    outcome = perform_merge(PROJECT_DIR, branch, body.get("resolutions"))
    if outcome.status == "up_to_date":
        return {"status": "up_to_date"}
    if outcome.status == "conflicts":
        return {"status": "conflicts", "conflicts": outcome.conflicts, "ours": current, "theirs": branch}
    return {
        "status": "merged",
        "hash": outcome.commit,
        "git_text_conflicts": outcome.git_text_conflicts,
        "diff": format_diff(outcome.ours_files, files_at_ref("HEAD")),
        "issues": issues_json(),
    }


def api_restore(_q, body):
    ref = body["ref"]
    write_working(files_at_ref(ref))
    commit_hash = commit_if_changed(f"vit: restore version {ref}", body.get("author"))
    return {"hash": commit_hash}


def api_reset(_q, _body):
    reset_project()
    return {"ok": True}


GET_ROUTES = {
    "/api/state": api_state,
    "/api/log": api_log,
    "/api/files": api_files,
    "/api/commit": api_commit_detail,
    "/api/raw": api_raw,
    "/api/compare": api_compare,
}
POST_ROUTES = {
    "/api/working": api_working,
    "/api/commit": api_commit,
    "/api/branch": api_branch,
    "/api/checkout": api_checkout,
    "/api/merge": api_merge,
    "/api/restore": api_restore,
    "/api/reset": api_reset,
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".m4v": "video/mp4", ".ogv": "video/ogg",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if "/api/" in self.path and not self.path.startswith("/api/working"):
            sys.stderr.write(f"  {self.command} {self.path.split('?')[0]}\n")

    def _json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, routes, body):
        url = urlparse(self.path)
        handler = routes.get(url.path)
        if handler is None:
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            with LOCK:
                result = handler(query, body)
            self._json(result)
        except (GitError, ValueError, KeyError, RuntimeError) as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

    def _trusted(self, write):
        """Block other websites from driving this local server.

        The Host check stops DNS-rebinding pages from reading the API; the
        custom header on writes can't be sent cross-origin without a CORS
        preflight (which this server never approves), so a malicious page
        can't POST /api/reset or commit on the user's behalf.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            self._json({"error": "Forbidden host"}, HTTPStatus.FORBIDDEN)
            return False
        if write and self.headers.get("X-Vit-Demo") != "1":
            self._json({"error": "Missing X-Vit-Demo header"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self):
        if not self._trusted(write=False):
            return
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._dispatch(GET_ROUTES, None)
        if path.startswith("/media/"):
            return self._serve_file(MEDIA_DIR, unquote(path[len("/media/"):]))
        return self._serve_file(STATIC_DIR, "index.html" if path == "/" else path.lstrip("/"))

    def do_POST(self):
        if not self._trusted(write=True):
            return
        path = urlparse(self.path).path
        if path == "/api/media":
            return self._upload()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            return self._json({"error": f"Invalid JSON body: {e}"}, HTTPStatus.BAD_REQUEST)
        self._dispatch(POST_ROUTES, body)

    def _upload(self):
        """Raw-body upload of a video file into the media folder (not into git)."""
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        name = os.path.basename(q.get("name", "clip.mp4"))
        ext = os.path.splitext(name)[1].lower() or ".mp4"
        remaining = int(self.headers.get("Content-Length") or 0)
        digest = hashlib.sha256()
        os.makedirs(MEDIA_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=MEDIA_DIR, suffix=".part")
        with os.fdopen(fd, "wb") as out:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                remaining -= len(chunk)
        sha = digest.hexdigest()
        filename = f"{sha[:16]}{ext}"
        os.replace(tmp_path, os.path.join(MEDIA_DIR, filename))
        hue = int(sha[:2], 16) * 360 // 256
        entry = {
            "ref": f"sha256:{sha}", "name": name, "file": filename,
            "duration_frames": max(1, int(float(q.get("duration_frames", 240)))),
            "width": int(float(q.get("width", 1920))), "height": int(float(q.get("height", 1080))),
            "color": f"hsl({hue}, 55%, 55%)",
        }
        with LOCK:
            save_imported(entry)
        self._json(dict(entry, kind="video", available=True))

    def _serve_file(self, root, rel):
        root = os.path.abspath(root)
        full = os.path.abspath(os.path.join(root, rel))
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        size = os.path.getsize(full)
        ctype = CONTENT_TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and size:
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
            end = min(end, size - 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(full, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(remaining, 1 << 16))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass  # browser cancelled a range request while seeking — normal for <video>


def main():
    parser = argparse.ArgumentParser(description="Vit standalone demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reset", action="store_true", help="start from a fresh demo project")
    args = parser.parse_args()

    if args.reset and os.path.exists(PROJECT_DIR):
        reset_project()
    ensure_project()

    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"\n  Vit demo running at {url}")
    print(f"  Project repo (plain git + JSON): {PROJECT_DIR}")
    print("  Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
