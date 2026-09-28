"""Host tests for the experimental Director camera timeline."""
import pytest

from clippyme.pipeline.director_camera import (
    SpeakerTurn,
    apply_camera_lead,
    build_speaker_turns,
    extract_clip_diarized_words,
    speaker_at,
)


def test_extract_clip_words_preserves_five_speakers_and_rebases_time():
    transcript = {
        "segments": [
            {
                "speaker": 0,
                "words": [
                    {"word": "a", "start": 10.0, "end": 10.3, "speaker": 0},
                    {"word": "b", "start": 11.0, "end": 11.3, "speaker": 1},
                    {"word": "c", "start": 12.0, "end": 12.3, "speaker": 2},
                    {"word": "d", "start": 13.0, "end": 13.3, "speaker": 3},
                    {"word": "e", "start": 14.0, "end": 14.3, "speaker": 4},
                ],
            }
        ]
    }
    words = extract_clip_diarized_words(transcript, 10.0, 15.0)
    assert {word["speaker"] for word in words} == {0, 1, 2, 3, 4}
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[-1]["start"] == pytest.approx(4.0)


def test_extract_uses_segment_speaker_when_word_has_no_label():
    transcript = {
        "segments": [
            {
                "speaker": 3,
                "words": [{"word": "hello", "start": 20.0, "end": 20.5}],
            }
        ]
    }
    assert extract_clip_diarized_words(transcript, 20.0, 21.0)[0]["speaker"] == 3


def test_extract_uses_segment_speaker_when_word_label_is_null():
    """Explicit JSON null is equivalent to an omitted word label."""
    transcript = {
        "segments": [
            {
                "speaker": 3,
                "words": [{"word": "hello", "start": 20.0, "end": 20.5, "speaker": None}],
            }
        ]
    }
    assert extract_clip_diarized_words(transcript, 20.0, 21.0)[0]["speaker"] == 3


def test_extract_without_any_diarization_returns_empty():
    transcript = {
        "segments": [
            {"words": [{"word": "hello", "start": 0.0, "end": 0.5}]}
        ]
    }
    assert extract_clip_diarized_words(transcript, 0.0, 1.0) == []


def test_build_turns_supports_many_speakers_and_merges_same_turn():
    words = [
        {"start": 0.0, "end": 0.2, "speaker": 0},
        {"start": 0.3, "end": 0.5, "speaker": 0},
        {"start": 1.0, "end": 1.2, "speaker": 1},
        {"start": 2.0, "end": 2.2, "speaker": 2},
        {"start": 3.0, "end": 3.2, "speaker": 3},
        {"start": 4.0, "end": 4.2, "speaker": 4},
    ]
    turns = build_speaker_turns(words)
    assert [turn.speaker for turn in turns] == [0, 1, 2, 3, 4]
    assert turns[0].end == pytest.approx(0.5)


def test_camera_lead_moves_switch_early_but_respects_minimum_hold():
    turns = [
        SpeakerTurn(0.0, 5.0, 0),
        SpeakerTurn(5.0, 7.0, 1),
        SpeakerTurn(7.0, 9.0, 2),
    ]
    shifted = apply_camera_lead(turns, lead=0.2, min_hold=1.0)
    assert shifted[1].start == pytest.approx(4.8)
    assert shifted[0].end == shifted[1].start
    assert shifted[1].end == shifted[2].start


def test_camera_min_hold_prevents_ping_pong():
    turns = [
        SpeakerTurn(0.0, 0.3, 0),
        SpeakerTurn(0.35, 0.7, 1),
        SpeakerTurn(0.75, 2.0, 2),
    ]
    shifted = apply_camera_lead(turns, lead=0.2, min_hold=1.0)
    starts = [turn.start for turn in shifted]
    assert starts == sorted(starts)
    assert all(b - a >= 1.0 for a, b in zip(starts, starts[1:]))


def test_min_hold_drops_short_interjection_without_losing_later_speaker():
    turns = [
        SpeakerTurn(0.0, 0.3, 0),
        SpeakerTurn(0.35, 0.7, 1),
        SpeakerTurn(0.75, 2.0, 2),
    ]
    shifted = apply_camera_lead(turns, lead=0.2, min_hold=1.0)
    assert [turn.speaker for turn in shifted] == [0, 2]
    assert shifted[0].start == pytest.approx(0.0)
    assert shifted[0].end == pytest.approx(1.0)
    assert shifted[1].start == pytest.approx(1.0)
    assert shifted[1].end == pytest.approx(2.0)


def test_speaker_at_uses_hard_turn_boundaries():
    turns = [SpeakerTurn(0.0, 2.0, 0), SpeakerTurn(2.0, 4.0, 4)]
    assert speaker_at(turns, 1.99) == 0
    assert speaker_at(turns, 2.0) == 4
    assert speaker_at(turns, 99.0) == 4
