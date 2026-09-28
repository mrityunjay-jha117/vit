"""Clip-level three-way merge of domain files (pure, no git)."""

from vit.merge import three_way_merge_domains


def _clip(clip_id, start, end, track=1, **extra):
    item = {
        "id": clip_id,
        "name": f"{clip_id}.mov",
        "media_ref": f"sha256:{clip_id}",
        "record_start_frame": start,
        "record_end_frame": end,
        "source_start_frame": 0,
        "source_end_frame": end - start,
        "track_index": track,
        "transform": {"Opacity": 100.0, "ZoomX": 1.0},
    }
    item.update(extra)
    return item


def _cuts(*items, tracks=1):
    video_tracks = [{"index": i, "items": []} for i in range(1, tracks + 1)]
    for item in items:
        video_tracks[item["track_index"] - 1]["items"].append(item)
    return {"video_tracks": video_tracks}


def test_three_way_merge_combines_clips_added_on_both_branches():
    base = {"cuts": _cuts(_clip("a", 0, 100))}
    ours = {"cuts": _cuts(_clip("a", 0, 100), _clip("b", 100, 200))}
    theirs = {"cuts": _cuts(_clip("a", 0, 100), _clip("c", 200, 300))}

    merged, conflicts = three_way_merge_domains(base, ours, theirs)

    assert conflicts == []
    ids = [i["id"] for i in merged["cuts"]["video_tracks"][0]["items"]]
    assert ids == ["a", "b", "c"]


def test_three_way_merge_combines_different_fields_of_same_clip():
    base = {"cuts": _cuts(_clip("a", 0, 100))}
    ours = {"cuts": _cuts(_clip("a", 0, 80))}
    theirs_clip = _clip("a", 0, 100)
    theirs_clip["transform"]["ZoomX"] = 1.5
    theirs = {"cuts": _cuts(theirs_clip)}

    merged, conflicts = three_way_merge_domains(base, ours, theirs)

    assert conflicts == []
    item = merged["cuts"]["video_tracks"][0]["items"][0]
    assert item["record_end_frame"] == 80
    assert item["transform"]["ZoomX"] == 1.5


def test_three_way_merge_reports_and_resolves_same_field_conflict():
    base = {"color": {"grades": {"a": {"nodes": [{"saturation": 1.0}]}}}}
    ours = {"color": {"grades": {"a": {"nodes": [{"saturation": 1.4}]}}}}
    theirs = {"color": {"grades": {"a": {"nodes": [{"saturation": 0.2}]}}}}

    _, conflicts = three_way_merge_domains(base, ours, theirs)
    assert len(conflicts) == 1
    assert conflicts[0]["path"] == "color/grades/a/nodes"
    assert conflicts[0]["ours"] == [{"saturation": 1.4}]
    assert conflicts[0]["theirs"] == [{"saturation": 0.2}]

    merged, conflicts = three_way_merge_domains(
        base, ours, theirs, resolutions={"color/grades/a/nodes": "theirs"}
    )
    assert conflicts == []
    assert merged["color"]["grades"]["a"]["nodes"] == [{"saturation": 0.2}]


def test_three_way_merge_delete_vs_modify_is_conflict():
    base = {"cuts": _cuts(_clip("a", 0, 100))}
    ours = {"cuts": _cuts()}
    theirs = {"cuts": _cuts(_clip("a", 0, 50))}

    _, conflicts = three_way_merge_domains(base, ours, theirs)

    assert [c["path"] for c in conflicts] == ["cuts/video_tracks/a"]
    assert conflicts[0]["ours"] is None


def test_three_way_merge_keeps_audio_tracks_and_markers_shape():
    base = {
        "audio": {"audio_tracks": [{"index": 1, "items": []}, {"index": 2, "items": []}]},
        "markers": {"markers": [{"frame": 10, "name": "x"}]},
    }
    ours = {
        "audio": {"audio_tracks": [
            {"index": 1, "items": [{"id": "a1", "media_ref": "m", "start_frame": 0,
                                    "end_frame": 10, "volume": 0.0, "pan": 0.0}]},
            {"index": 2, "items": []},
        ]},
        "markers": {"markers": [{"frame": 10, "name": "x"}, {"frame": 5, "name": "y"}]},
    }
    theirs = base

    merged, conflicts = three_way_merge_domains(base, ours, theirs)

    assert conflicts == []
    assert merged["audio"] == ours["audio"]
    assert [m["frame"] for m in merged["markers"]["markers"]] == [5, 10]
