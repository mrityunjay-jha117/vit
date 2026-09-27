from vit.merge_utils import (
    merge_timeline_domains_for_overlays,
    referenced_sidecars,
    three_way_merge_domains,
)


def test_title_collision_becomes_v2_overlay():
    ours = {
        "cuts": {
            "video_tracks": [
                {
                    "index": 1,
                    "items": [
                        {
                            "id": "item_001_000",
                            "name": "0117.mov",
                            "media_ref": "sha256:clip",
                            "record_start_frame": 0,
                            "record_end_frame": 120,
                            "source_start_frame": 0,
                            "source_end_frame": 120,
                            "track_index": 1,
                            "transform": {},
                        }
                    ],
                }
            ]
        },
        "color": {
            "grades": {
                "item_001_000": {"drx_file": "item_001_000_1.1.1.drx"}
            }
        },
        "audio": {"audio_tracks": []},
        "effects": {},
        "markers": {"markers": []},
        "metadata": {"track_count": {"video": 1, "audio": 1}},
        "manifest": {"assets": {"sha256:clip": {"path": "/clip.mov"}}},
    }
    theirs = {
        "cuts": {
            "video_tracks": [
                {
                    "index": 1,
                    "items": [
                        {
                            "id": "item_001_000",
                            "name": "Text+",
                            "media_ref": "generator:item_001_000",
                            "record_start_frame": 0,
                            "record_end_frame": 120,
                            "source_start_frame": 0,
                            "source_end_frame": 120,
                            "track_index": 1,
                            "item_type": "title",
                            "generator_name": "Text+",
                            "fusion_comp_file": "item_001_000.comp",
                            "text_properties": {"styled_text": "lucas"},
                            "transform": {},
                        }
                    ],
                }
            ]
        },
        "color": {
            "grades": {
                "item_001_000": {"drx_file": "item_001_000_1.1.1.drx"}
            }
        },
        "audio": {"audio_tracks": []},
        "effects": {},
        "markers": {"markers": []},
        "metadata": {"track_count": {"video": 1, "audio": 0}},
        "manifest": {"assets": {}},
    }

    merged, plan = merge_timeline_domains_for_overlays(theirs, ours, theirs)

    v1 = merged["cuts"]["video_tracks"][0]["items"][0]
    v2_track = merged["cuts"]["video_tracks"][1]
    overlay = v2_track["items"][0]

    assert v1["name"] == "0117.mov"
    assert overlay["name"] == "Text+"
    assert overlay["track_index"] == 2
    assert overlay["id"] == "item_001_000_overlay"
    assert overlay["media_ref"] == "generator:item_001_000_overlay"
    assert overlay["fusion_comp_file"] == "item_001_000_overlay.comp"
    assert merged["metadata"]["track_count"]["video"] == 2
    assert merged["color"]["grades"]["item_001_000"]["drx_file"] == "item_001_000_1.1.1.drx"
    assert (
        merged["color"]["grades"]["item_001_000_overlay"]["drx_file"]
        == "item_001_000_overlay_1.1.1.drx"
    )
    assert plan.generator_renames == {
        "item_001_000.comp": "item_001_000_overlay.comp"
    }
    assert plan.grade_renames == {
        "item_001_000_1.1.1.drx": "item_001_000_overlay_1.1.1.drx"
    }
    assert plan.grade_restore_ours == {"item_001_000_1.1.1.drx"}


def test_overlay_normalization_preserves_existing_non_overlay_merge_edits():
    ours = {
        "cuts": {
            "video_tracks": [
                {
                    "index": 1,
                    "items": [
                        {
                            "id": "item_001_000",
                            "name": "clip",
                            "media_ref": "sha256:clip",
                            "record_start_frame": 0,
                            "record_end_frame": 100,
                            "source_start_frame": 0,
                            "source_end_frame": 100,
                            "track_index": 1,
                            "transform": {},
                        }
                    ],
                }
            ]
        },
        "color": {"grades": {}},
        "audio": {"audio_tracks": []},
        "effects": {},
        "markers": {"markers": []},
        "metadata": {"track_count": {"video": 1, "audio": 0}},
        "manifest": {"assets": {}},
    }
    theirs = {
        "cuts": {
            "video_tracks": [
                {
                    "index": 1,
                    "items": [
                        {
                            "id": "item_001_000",
                            "name": "Text+",
                            "media_ref": "generator:item_001_000",
                            "record_start_frame": 0,
                            "record_end_frame": 120,
                            "source_start_frame": 0,
                            "source_end_frame": 120,
                            "track_index": 1,
                            "item_type": "title",
                            "generator_name": "Text+",
                            "fusion_comp_file": "item_001_000.comp",
                            "text_properties": {"styled_text": "lucas"},
                            "transform": {},
                        }
                    ],
                }
            ]
        },
        "color": {"grades": {}},
        "audio": {"audio_tracks": []},
        "effects": {},
        "markers": {"markers": []},
        "metadata": {"track_count": {"video": 1, "audio": 0}},
        "manifest": {"assets": {}},
    }
    current_merged = {
        "cuts": {
            "video_tracks": [
                {
                    "index": 1,
                    "items": [
                        {
                            "id": "item_001_000",
                            "name": "Text+",
                            "media_ref": "generator:item_001_000",
                            "record_start_frame": 0,
                            "record_end_frame": 120,
                            "source_start_frame": 0,
                            "source_end_frame": 120,
                            "track_index": 1,
                            "item_type": "title",
                            "generator_name": "Text+",
                            "fusion_comp_file": "item_001_000.comp",
                            "text_properties": {"styled_text": "lucas"},
                            "transform": {},
                        },
                        {
                            "id": "item_002_000",
                            "name": "broll",
                            "media_ref": "sha256:broll",
                            "record_start_frame": 130,
                            "record_end_frame": 200,
                            "source_start_frame": 0,
                            "source_end_frame": 70,
                            "track_index": 1,
                            "transform": {},
                        },
                    ],
                }
            ]
        },
        "color": {"grades": {}},
        "audio": {"audio_tracks": []},
        "effects": {},
        "markers": {"markers": []},
        "metadata": {"track_count": {"video": 1, "audio": 0}},
        "manifest": {"assets": {}},
    }

    merged, _ = merge_timeline_domains_for_overlays(current_merged, ours, theirs)

    assert merged["cuts"]["video_tracks"][0]["items"][0]["name"] == "clip"
    assert merged["cuts"]["video_tracks"][0]["items"][1]["name"] == "broll"
    assert merged["cuts"]["video_tracks"][1]["items"][0]["name"] == "Text+"


def test_referenced_sidecars_tracks_renamed_overlay_assets():
    merged = {
        "cuts": {
            "video_tracks": [
                {
                    "index": 2,
                    "items": [
                        {
                            "id": "item_001_000_overlay",
                            "fusion_comp_file": "item_001_000_overlay.comp",
                        }
                    ],
                }
            ]
        },
        "color": {
            "grades": {
                "item_001_000_overlay": {
                    "drx_file": "item_001_000_overlay_1.1.1.drx"
                }
            }
        },
    }

    generators, grades = referenced_sidecars(merged)

    assert generators == {"timeline/generators/item_001_000_overlay.comp"}
    assert grades == {"timeline/grades/item_001_000_overlay_1.1.1.drx"}


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
