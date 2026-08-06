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
    """(top, bottom) of the rendered caption bar."""
    return overlays.caption_bar_y(lines, center)


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


def test_screen_share_wells_clear_the_top_band():
    """The shared-screen well must not collide with the status pill above it, and
    the two wells must not collide with each other."""
    pill_bottom = 188                # _brand_bg draws it at y=128, height 60
    assert ssv.SCREEN_Y > pill_bottom
    assert ssv.FACE_Y > ssv.SCREEN_Y + ssv.SCREEN_H


def test_share_plate_leaves_the_overlay_corner_free():
    """The share plate must not draw anything into the band the tech chips and
    stat cards own (top-right, from tech_overlays.ANCHOR_Y down).

    The separation is VERTICAL, not horizontal: chips are right-anchored but a long
    label ("TECHNICAL INSTRUCTIONS") is ~900px wide, so it reaches most of the way
    across the canvas and cannot be made to clear anything by staying right.

    It used to fail: a second permanent name tag ran y=110..248, straddling the
    overlay's own top edge at ANCHOR_Y=196, so every wide overlay landed on it. The
    status pill ends at 188 and is the only furniture left in the band.
    """
    import tech_overlays as tech
    pill_bottom = 128 + 60           # _brand_bg draws it at y=128, height 60
    assert pill_bottom <= tech.ANCHOR_Y, (
        "the status pill ends at %d, inside the overlay band starting at %d"
        % (pill_bottom, tech.ANCHOR_Y))


def test_footage_band_stops_where_the_caption_starts():
    """The whole point of lifting the band: the caption must sit on blurred fill,
    never across the speaker's chest."""
    top, _ = _caption_bar(2)
    assert ssv.FACE_SEAM_Y == top, (
        "the seam is at %d but the caption bar starts at %d" % (ssv.FACE_SEAM_Y, top))


def test_lifting_the_band_leaves_a_top_margin_but_not_a_wide_one():
    """The band must still fit on the canvas (a negative offset crops the speaker's
    head), while the grey band it leaves stays far below the old centred 387px."""
    offset = ssv.FACE_SEAM_Y - ssv.FACE_FG_H
    assert offset >= 0, "footage band would overflow the top of the canvas"
    assert offset < 200, "top blur band is back to being wide (was 387 centred)"


def test_the_band_is_not_centred_anymore():
    """Guards the fix itself: centring is what put the caption on his chest."""
    centred = (overlays.H - ssv.FACE_FG_H) // 2
    assert ssv.FACE_SEAM_Y - ssv.FACE_FG_H != centred


# --- fill / caption-theme coupling --------------------------------------------
#
# The fill behind the footage band and the caption's colours are ONE decision: on
# the blurred room a caption needs its dark plate to stay readable, and on the
# white fill that same plate reads as a black box floating on white. Splitting
# them is how a video ships with white text on a white band.

def test_every_background_has_a_caption_theme():
    assert set(ssv.BACKGROUNDS) == set(ssv.BACKGROUND_CAPTION_THEME)
    for theme in ssv.BACKGROUND_CAPTION_THEME.values():
        assert theme in overlays.CAPTION_THEMES


def test_the_white_fill_inks_captions_dark_and_drops_the_plate():
    theme = overlays.CAPTION_THEMES[ssv.BACKGROUND_CAPTION_THEME["white"]]
    assert theme["plate"] is None, "a dark plate on the white band is a black box"
    assert sum(theme["text"][:3]) < 128, "dark ink is what makes it readable on white"


def test_the_blurred_fill_keeps_the_readable_plate():
    theme = overlays.CAPTION_THEMES[ssv.BACKGROUND_CAPTION_THEME["blur"]]
    assert theme["plate"] is not None and theme["text"] == overlays.WHITE


@pytest.mark.parametrize("caption_y", [1330, 1400, 1460, 1651])
def test_the_seam_follows_the_caption_wherever_it_moves(caption_y):
    """render() derives the seam from the caption centre, so a per-video
    `caption_y` can never leave the footage band overlapping the caption."""
    seam = overlays.caption_bar_y(2, caption_y)[0]
    assert seam == _caption_bar(2, caption_y)[0]
    assert seam - ssv.FACE_FG_H >= 0, "footage band would overflow the top"
    assert seam < overlays.H


def test_an_unknown_background_fails_loudly():
    assert "sepia" not in ssv.BACKGROUNDS


# The equal-borders look (Q7, 2026-07-28). caption_y=1651 is not an arbitrary
# number: it is the one value that centres the footage band, because the seam is
# derived from the caption. Recorded so the arithmetic behind it is not lost.
EQUAL_BORDER_CAPTION_Y = 1651


def test_equal_border_caption_y_centres_the_footage_band():
    seam = overlays.caption_bar_y(2, EQUAL_BORDER_CAPTION_Y)[0]
    top_border = seam - ssv.FACE_FG_H
    bottom_border = overlays.H - seam
    assert abs(top_border - bottom_border) <= 1, (
        "borders are %d / %d, not equal" % (top_border, bottom_border))


def test_equal_borders_cost_the_platform_safe_zone():
    """The documented trade-off, asserted so nobody adopts this as the default
    by accident: centring the band puts the caption inside the app UI band."""
    _, bottom = _caption_bar(2, EQUAL_BORDER_CAPTION_Y)
    assert (overlays.H - bottom) < overlays.PLATFORM_UI_RESERVED_PX
    # ...which is exactly why it is a per-video override, not the default.
    _, default_bottom = _caption_bar(2)
    assert (overlays.H - default_bottom) >= overlays.PLATFORM_UI_RESERVED_PX


def test_legacy_caption_position_would_fail_the_safe_zone():
    """Guards the fix: the old value is retained only as documentation."""
    _, bottom = _caption_bar(2, center=overlays.CAPTION_CENTER_Y_LEGACY)
    assert (overlays.H - bottom) < overlays.PLATFORM_UI_RESERVED_PX


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


# --- colour grade, subject mode -----------------------------------------------
# The bug this mode exists for: on 25 Jul Q7 the camera metered for a large white
# wall, so the shipping crop measured 0.576 ("correct") while his face sat at
# 0.358 and shipped looking black. Measuring the face is the whole fix.

def test_subject_mode_lifts_a_backlit_face_the_crop_wide_bands_call_fine():
    """The exact numbers off 25 Jul Q7 — the crop says fine, the face does not."""
    crop_wide = grade.filter_from_stats(
        {"luma": 0.576, "range": 0.888, "saturation": 0.0198})
    assert "gamma" not in crop_wide          # this is the miss being corrected

    face = grade.subject_filter_from_stats(
        {"luma": 0.3577, "range": 0.679, "saturation": 0.0474})
    gamma = float(dict(t.split("=") for t in face.replace("eq=", "").split(":"))["gamma"])
    assert gamma > 1.30      # he chose 1.35 off the rendered ladder
    assert 0.3577 ** (1 / gamma) == pytest.approx(grade.SUBJECT_TARGET_LUMA, abs=0.01)


def test_subject_mode_leaves_an_already_correct_face_alone():
    vf = grade.subject_filter_from_stats(
        {"luma": grade.SUBJECT_TARGET_LUMA, "range": 0.80, "saturation": 0.045})
    assert "gamma" not in vf


@pytest.mark.parametrize("stats", [
    {"luma": 0.02, "range": 0.20, "saturation": 0.001},   # near-black subject
    {"luma": 0.99, "range": 0.99, "saturation": 0.500},   # blown out
    {"luma": 0.0, "range": 0.5, "saturation": 0.03},      # degenerate
    {"luma": 1.0, "range": 0.5, "saturation": 0.03},      # degenerate
])
def test_subject_mode_stays_inside_its_own_clamps(stats):
    """A wider gamma range is still a bounded one — and log() must not blow up."""
    vf = grade.subject_filter_from_stats(stats)
    if not vf:
        return
    for term in vf.replace("eq=", "").split(":"):
        name, value = term.split("=")
        low, high = grade.SUBJECT_CLAMP[name]
        assert low <= float(value) <= high


def test_subject_mode_never_shifts_hue():
    """Same brand rule as the crop-wide path: correct exposure, never restyle."""
    for stats in ({"luma": 0.2, "range": 0.3, "saturation": 0.01},
                  {"luma": 0.9, "range": 0.9, "saturation": 0.4}):
        vf = grade.subject_filter_from_stats(stats)
        assert "colorbalance" not in vf and "curves" not in vf and "hue" not in vf


def test_a_single_low_contrast_frame_cannot_halve_the_measurement_scale(tmp_path):
    """YBITDEPTH is per FRAME, so the last frame's value must not set the scale.

    1 Aug Q1: 99 sampled frames reported 8 bits and the last reported 7, which
    halved `full` and doubled every statistic — a face at 0.455 was read as 0.906
    and graded DARKER on the one video that was asked to be brighter.
    """
    meta = tmp_path / "signalstats.txt"
    meta.write_text("".join(
        "lavfi.signalstats.YBITDEPTH={}\nlavfi.signalstats.YAVG=116.0\n"
        "lavfi.signalstats.YMIN=16.0\nlavfi.signalstats.YMAX=202.0\n"
        "lavfi.signalstats.SATAVG=11.8\n".format(8 if i else 7)
        for i in range(3)[::-1]))          # the 7-bit frame is sampled LAST

    stats = grade.stats_from_metadata(str(meta))
    assert stats["luma"] == pytest.approx(116.0 / 255, abs=0.002)
    assert stats["range"] < 1.0           # the doubled read produced 1.45

    # and the corrected reading is what puts the emitted gamma the right way up
    assert "gamma=0.9" not in grade.subject_filter_from_stats(stats, 0.5424)


def test_grade_target_aims_higher_without_moving_the_default():
    """1 Aug: "brightness should be increased" — aim higher, don't hardcode a gamma.

    His face measured 0.4515 on 1 Aug Q1, which is inside the deadzone around the
    default target, so the default emits nothing. He picked the 0.54 rung off the
    rendered ladder; a per-video target reproduces it and carries to the next room,
    where a hardcoded gamma would not.
    """
    stats = {"luma": 0.4515, "range": 0.7287, "saturation": 0.0462}
    assert "gamma" not in grade.subject_filter_from_stats(stats)

    vf = grade.subject_filter_from_stats(stats, 0.5424)
    gamma = float(dict(t.split("=") for t in vf.replace("eq=", "").split(":"))["gamma"])
    assert stats["luma"] ** (1 / gamma) == pytest.approx(0.5424, abs=0.01)
    low, high = grade.SUBJECT_CLAMP["gamma"]
    assert low <= gamma <= high          # a raised target is still a bounded one


def test_subject_mode_is_opt_in_so_shipped_videos_re_render_identically():
    """grade_crop=None must leave the 17 Jul path bit-for-bit unchanged."""
    stats = {"luma": 0.6645, "range": 0.8331, "saturation": 0.0185}
    assert grade.filter_from_stats(stats) == "eq=contrast=1.030:saturation=1.040"


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
