"""Tests for the reel pipeline's decision logic (pipeline/).

Everything here is pure logic — encode profiles, grade decisions, caption
chunking, silence remapping, QC scoring — so no media files and no ffmpeg are
required. The one thing these tests deliberately lock down is the set of rules it
would be expensive to get wrong silently: a draft overwriting a shipping file, an
unbounded colour correction, a caption timeline that drifts after a trim, and a
mid-file `afade` (which mutes a reel rather than smoothing it).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import audio_master  # noqa: E402
import backdrop  # noqa: E402
import encode  # noqa: E402
import grade  # noqa: E402
import overlays  # noqa: E402
import screenshare_vertical as ssv  # noqa: E402
import silence  # noqa: E402
import transcribe  # noqa: E402
import verify_reel  # noqa: E402


# --- caption placement (platform safe zone) -----------------------------------
#
# These two constants are coupled: raising the caption to clear the app UI can
# push it onto the screen-share camera well. Both bounds are asserted so neither
# can be nudged in isolation.

def _caption_bar(lines, center=None):
    """(top, bottom) of the rendered caption bar, mirroring make_caption's maths."""
    center = overlays.CAPTION_CENTER_Y if center is None else center
    line_h, pad_y = 84, 34
    block_h = line_h * lines
    top = center - block_h // 2
    return top - pad_y, top + block_h + pad_y - 10


@pytest.mark.parametrize("lines", [1, 2])
def test_captions_clear_the_platform_ui_band(lines):
    """Captions must not intrude on the bottom band the apps write over."""
    _, bottom = _caption_bar(lines)
    clear_px = overlays.H - bottom
    assert clear_px >= overlays.PLATFORM_UI_RESERVED_PX, (
        "a %d-line caption ends %dpx from the bottom; apps reserve %dpx"
        % (lines, clear_px, overlays.PLATFORM_UI_RESERVED_PX))


def test_captions_do_not_cover_the_screen_share_camera_well():
    """The tallest caption must start below the stacked layout's camera well."""
    top, _ = _caption_bar(2)
    well_bottom = ssv.FACE_Y + ssv.FACE_H
    assert top > well_bottom, (
        "caption starts at %d but the camera well ends at %d" % (top, well_bottom))


def test_screen_share_wells_clear_the_name_tag():
    """The shared-screen well must not collide with the name tag above it."""
    name_tag_bottom = 248            # _brand_bg draws it at y=110, height 138
    assert ssv.SCREEN_Y > name_tag_bottom
    assert ssv.FACE_Y > ssv.SCREEN_Y + ssv.SCREEN_H


def test_legacy_caption_position_would_fail_the_safe_zone():
    """Guards the fix: the old value is retained only as documentation."""
    _, bottom = _caption_bar(2, center=overlays.CAPTION_CENTER_Y_LEGACY)
    assert (overlays.H - bottom) < overlays.PLATFORM_UI_RESERVED_PX


# --- backdrop styles ----------------------------------------------------------

def test_every_style_is_either_blur_or_a_real_plate():
    """A style name with no backdrop recipe would fail only at render time."""
    assert ssv.STYLES[0] == "blur"
    assert set(ssv.STYLES[1:]) == set(backdrop.PLATE_STYLES)


def test_plate_is_rendered_at_the_delivery_canvas(tmp_path):
    for style in backdrop.PLATE_STYLES:
        out = backdrop.plate(style, str(tmp_path / ("%s.png" % style)))
        from PIL import Image
        with Image.open(out) as im:
            assert im.size == (backdrop.W, backdrop.H)


def test_plate_is_cached_not_rebuilt(tmp_path):
    """Plates are static; rebuilding one per question would be pure waste."""
    path = str(tmp_path / "p.png")
    backdrop.plate("studio_bands", path)
    stamp = os.path.getmtime(path)
    backdrop.plate("studio_bands", path)
    assert os.path.getmtime(path) == stamp


def test_photo_backdrop_covers_the_canvas_from_any_aspect(tmp_path):
    """A landscape photo must cover-crop to 9:16 — never letterbox or squash."""
    from PIL import Image
    src = str(tmp_path / "room.jpg")
    Image.new("RGB", (2400, 1600), (120, 110, 98)).save(src)
    out = backdrop.plate("studio_real", str(tmp_path / "p.png"), photo=src)
    with Image.open(out) as im:
        assert im.size == (backdrop.W, backdrop.H)


def test_photo_backdrop_is_darker_than_its_source(tmp_path):
    """The plate sits behind the speaker, so it must be pulled down in exposure —
    a backdrop brighter than the face pulls the eye off him."""
    import numpy as np
    from PIL import Image
    src = str(tmp_path / "bright.jpg")
    Image.new("RGB", (1200, 2000), (180, 175, 170)).save(src)
    out = backdrop.plate("studio_real", str(tmp_path / "p.png"), photo=src)
    with Image.open(out) as im:
        assert np.asarray(im).mean() < 180 * 0.7


def test_unknown_backdrop_style_fails_loudly():
    with pytest.raises(KeyError):
        backdrop.plate("neon_cyberpunk", "/tmp/never_written.png")


def test_studio_inset_window_clears_the_caption_bar():
    """The studio_set window must not run under the captions."""
    top, _ = _caption_bar(2)
    assert ssv.SET_WIN_Y + ssv.SET_WIN_H < top


def test_studio_inset_dimensions_are_even():
    """Odd dimensions break yuv420p and desync the alphamerge mask."""
    assert ssv.SET_WIN_W % 2 == 0
    assert ssv.SET_WIN_H % 2 == 0


def test_grain_is_not_a_temporal_ffmpeg_filter():
    """Temporal `noise` destroyed inter-frame compression (18.7 Mbps). Baked instead."""
    assert "noise" not in ssv.STUDIO_VIGNETTE
    assert not hasattr(ssv, "STUDIO_GRAIN")


# --- encode profiles ----------------------------------------------------------

def test_every_rung_shares_one_canvas_and_fps():
    """Parts can only be concat-copied if the rungs agree on canvas and fps."""
    profiles = [encode.profile(q) for q in encode.LADDER]
    assert len({(p["w"], p["h"], p["fps"]) for p in profiles}) == 1


def test_final_keeps_the_expensive_filters():
    """`final` must never use the cheap blur stand-in — that is the shipped look."""
    assert encode.profile("final")["cheap_filters"] is False
    assert encode.profile("draft")["cheap_filters"] is True
    assert encode.profile("preview")["cheap_filters"] is True


def test_unknown_quality_or_orientation_fails_loudly():
    with pytest.raises(KeyError):
        encode.profile("hi-res")
    with pytest.raises(KeyError):
        encode.profile("final", orientation="portrait")


def test_orientations_are_even_dimensioned():
    """x264 with yuv420p requires even width and height."""
    for w, h in encode.ORIENTATIONS.values():
        assert w % 2 == 0 and h % 2 == 0


def test_video_args_pin_a_closed_gop():
    """Deterministic GOP across parts is what makes the copy-join reliable."""
    args = encode.video_args(encode.profile("final"))
    assert "-sc_threshold" in args and args[args.index("-sc_threshold") + 1] == "0"
    assert args[args.index("-g") + 1] == args[args.index("-keyint_min") + 1]


# --- colour grade -------------------------------------------------------------

def test_grade_leaves_correctly_exposed_footage_alone():
    """Measured 17 Jul values: no gamma change should be emitted."""
    vf = grade.filter_from_stats(
        {"luma": 0.658, "range": 0.830, "saturation": 0.0279})
    assert "gamma" not in vf


@pytest.mark.parametrize("stats", [
    {"luma": 0.10, "range": 0.20, "saturation": 0.001},   # pathologically dark
    {"luma": 0.95, "range": 0.99, "saturation": 0.500},   # blown out
    {"luma": 0.50, "range": 0.50, "saturation": 0.030},   # flat
])
def test_grade_adjustments_are_always_clamped(stats):
    """No input, however extreme, may push an adjustment past its clamp."""
    vf = grade.filter_from_stats(stats)
    if not vf:
        return
    for term in vf.replace("eq=", "").split(":"):
        name, value = term.split("=")
        low, high = grade.CLAMP[name]
        assert low <= float(value) <= high


def test_grade_never_shifts_hue():
    """Corrective only: no colorbalance/curves/hue, per the brand rule."""
    for stats in ({"luma": 0.2, "range": 0.3, "saturation": 0.01},
                  {"luma": 0.9, "range": 0.9, "saturation": 0.4}):
        vf = grade.filter_from_stats(stats)
        assert "colorbalance" not in vf and "curves" not in vf and "hue" not in vf


def test_grade_falls_back_when_analysis_fails():
    assert grade.filter_from_stats(None) == grade.FALLBACK


# --- audio mastering ----------------------------------------------------------

def test_master_chain_has_no_midfile_fade_in():
    """A mid-file `afade=t=in` mutes everything before it — must never appear."""
    chain = audio_master._chain("loudnorm=I=-14", False, 90.0)
    fade_ins = [f for f in chain.split(",") if f.startswith("afade=t=in")]
    assert len(fade_ins) == 1
    assert fade_ins[0].startswith("afade=t=in:st=0:")


def test_master_targets_the_social_loudness_standard():
    assert audio_master.TARGET_I == -14.0
    assert audio_master.TARGET_TP == -1.0


def test_denoise_is_opt_in():
    assert audio_master.DENOISE_FILTER not in audio_master._chain("loudnorm", False, 10.0)
    assert audio_master.DENOISE_FILTER in audio_master._chain("loudnorm", True, 10.0)


def test_very_short_clip_gets_no_overlapping_fades():
    chain = audio_master._chain("loudnorm", False, 0.02)
    assert "afade=t=out" not in chain


# --- caption drafting ---------------------------------------------------------

def _words(pairs):
    return [{"start": s, "end": e, "word": w} for s, e, w in pairs]


def test_draft_captions_breaks_on_a_pause():
    words = _words([(0.0, 0.4, "pehla"), (0.5, 0.9, "jumla"),
                    (3.0, 3.4, "doosra"), (3.5, 3.9, "jumla")])
    blocks = transcribe.draft_captions(words)
    assert len(blocks) == 2
    assert blocks[0][2] == "pehla jumla"


def test_draft_captions_respects_the_word_cap():
    words = _words([(i * 0.3, i * 0.3 + 0.25, "w%d" % i) for i in range(24)])
    blocks = transcribe.draft_captions(words)
    assert blocks
    assert all(len(b[2].split()) <= transcribe.CAP_MAX_WORDS for b in blocks)


def test_draft_captions_merges_unreadably_short_blocks():
    words = _words([(0.0, 0.2, "hmm."), (0.3, 2.4, "phir"), (2.5, 4.0, "batao")])
    for block in transcribe.draft_captions(words):
        assert (block[1] - block[0]) >= transcribe.CAP_MIN_SECONDS


def test_draft_captions_handles_no_speech():
    assert transcribe.draft_captions([]) == []


# --- silence / tightening -----------------------------------------------------

def test_find_gaps_includes_lead_in_and_tail():
    words = _words([(2.0, 2.5, "start"), (3.0, 3.5, "end")])
    gaps = silence.find_gaps(words, min_gap=0.7, clip_dur=6.0)
    assert (0.0, 2.0) in gaps
    assert (3.5, 6.0) in gaps


def test_keep_ranges_never_exceed_the_clip():
    words = _words([(1.0, 1.5, "a"), (5.0, 5.5, "b")])
    for start, end in silence.keep_ranges(words, clip_dur=7.0):
        assert 0.0 <= start < end <= 7.0


def test_remap_time_is_monotonic_and_snaps_removed_moments():
    keeps = [[0.0, 10.0], [15.0, 25.0]]
    assert silence.remap_time(5.0, keeps) == 5.0
    assert silence.remap_time(20.0, keeps) == 15.0       # 5s of gap removed
    assert silence.remap_time(12.5, keeps) == 10.0       # inside the removed gap
    times = [silence.remap_time(t, keeps) for t in range(0, 26)]
    assert times == sorted(times)


def test_remap_captions_drops_blocks_whose_audio_was_cut():
    keeps = [[0.0, 10.0], [15.0, 25.0]]
    caps = [[2, 4, "kept"], [11, 13, "removed"], [16, 18, "shifted"]]
    out = silence.remap_captions(caps, keeps)
    assert [c[2] for c in out] == ["kept", "shifted"]
    assert out[-1][:2] == [11.0, 13.0]


def test_remap_preserves_highlight_words():
    out = silence.remap_captions([[1, 3, "Docker rocks", ["docker"]]], [[0.0, 10.0]])
    assert out[0][3] == ["docker"]


# --- QC -----------------------------------------------------------------------

def test_caption_coverage_is_a_fraction_of_the_body():
    caps = [[0.0, 5.0, "a"], [5.0, 10.0, "b"]]
    assert verify_reel.caption_coverage(caps, 20.0) == pytest.approx(0.5)
    assert verify_reel.caption_coverage([], 20.0) == 0.0


def test_caption_coverage_ignores_overhang_past_the_body():
    caps = [[0.0, 40.0, "runs long"]]
    assert verify_reel.caption_coverage(caps, 20.0) == pytest.approx(1.0)


def test_report_fails_only_on_failures():
    rep = verify_reel.Report("x.mp4")
    rep.ok("a")
    rep.warn("b")
    assert not rep.failed
    rep.fail("c")
    assert rep.failed
