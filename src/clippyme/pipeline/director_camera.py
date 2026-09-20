"""Offline director-camera planning for interview/podcast reframing.

This module ports the strongest ideas from the Clipper Pro reframe pipeline into
ClippyMe without replacing the existing AUTO camera:

* word-level diarization says who talks and when;
* camera cues move 200 ms early (offline look-ahead, not prediction);
* MediaPipe mouth-motion variance maps each diarized speaker to an on-screen
  face position;
* mapping is rebuilt per source scene, so 3-5+ speakers and edited multi-camera
  shows do not assume one global left/right position for the whole clip.

The pure timeline functions are dependency-free and host-testable. Heavy cv2 /
MediaPipe imports stay call-scoped inside locate_speakers_by_scene.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_GAP_TOLERANCE = 0.8
DEFAULT_CAMERA_LEAD = 0.2
DEFAULT_MIN_HOLD = 1.0
DEFAULT_SAMPLES_PER_TURN = 10
_MIN_TURN_SECONDS = 0.45
_MIN_MAR_SAMPLES = 3


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def extract_clip_diarized_words(
    transcript: dict | None,
    clip_start: float,
    clip_end: float,
) -> list[dict]:
    """Return clip-relative diarized words overlapping the requested clip.

    Speaker labels may live on each word (Deepgram/ElevenLabs) or only on the
    containing segment. When no labels exist the returned list is empty, which
    deliberately selects Director's visual fallback.
    """
    if not transcript:
        return []
    try:
        clip_start = float(clip_start)
        clip_end = float(clip_end)
    except (TypeError, ValueError):
        return []
    if clip_end <= clip_start:
        return []

    out: list[dict] = []
    for segment in transcript.get("segments", []) or []:
        seg_speaker = segment.get("speaker")
        for word in segment.get("words", []) or []:
            try:
                start = float(word.get("start"))
                end = float(word.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= clip_start or start >= clip_end or end < start:
                continue
            speaker = word.get("speaker", seg_speaker)
            try:
                speaker = int(speaker)
            except (TypeError, ValueError):
                continue
            out.append({
                "start": max(0.0, start - clip_start),
                "end": max(0.0, min(end, clip_end) - clip_start),
                "speaker": speaker,
                "word": word.get("word", ""),
            })
    out.sort(key=lambda item: item["start"])
    return out


def build_speaker_turns(
    words: list[dict],
    *,
    gap_tolerance: float = DEFAULT_GAP_TOLERANCE,
) -> list[SpeakerTurn]:
    """Collapse diarized words into stable turns for an arbitrary speaker count."""
    if gap_tolerance < 0:
        raise ValueError("gap_tolerance must be >= 0")
    clean: list[tuple[float, float, int]] = []
    for word in words or []:
        try:
            start = float(word["start"])
            end = float(word["end"])
            speaker = int(word["speaker"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        clean.append((start, end, speaker))
    clean.sort(key=lambda item: item[0])
    if not clean:
        return []

    turns: list[SpeakerTurn] = []
    cur_start, cur_end, cur_speaker = clean[0]
    for start, end, speaker in clean[1:]:
        if speaker == cur_speaker and start - cur_end <= gap_tolerance:
            cur_end = max(cur_end, end)
            continue
        turns.append(SpeakerTurn(cur_start, max(cur_end, cur_start + 0.04), cur_speaker))
        cur_start, cur_end, cur_speaker = start, end, speaker
    turns.append(SpeakerTurn(cur_start, max(cur_end, cur_start + 0.04), cur_speaker))
    return turns


def apply_camera_lead(
    turns: list[SpeakerTurn],
    *,
    lead: float = DEFAULT_CAMERA_LEAD,
    min_hold: float = DEFAULT_MIN_HOLD,
) -> list[SpeakerTurn]:
    """Shift speaker changes earlier while guaranteeing a minimum shot hold."""
    if lead < 0 or min_hold < 0:
        raise ValueError("lead and min_hold must be >= 0")
    if not turns:
        return []

    starts = [turns[0].start]
    for index in range(1, len(turns)):
        wanted = turns[index].start - lead
        floor = starts[index - 1] + min_hold
        starts.append(max(wanted, floor, starts[index - 1]))

    shifted: list[SpeakerTurn] = []
    for index, turn in enumerate(turns):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else turn.end
        if end > start:
            shifted.append(SpeakerTurn(start, end, turn.speaker))
    return shifted


def speaker_at(turns: list[SpeakerTurn], timestamp: float) -> int | None:
    """Return the speaker whose planned shot covers timestamp."""
    for turn in turns:
        if turn.start <= timestamp < turn.end:
            return turn.speaker
    if turns and timestamp >= turns[-1].end:
        return turns[-1].speaker
    return None


def _face_box(face: object) -> tuple[float, float, float, float] | None:
    raw = face.get("box") if isinstance(face, dict) else face
    try:
        x, y, w, h = (float(value) for value in tuple(raw)[:4])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _intersect_turn(turn: SpeakerTurn, start: float, end: float) -> SpeakerTurn | None:
    left = max(turn.start, start)
    right = min(turn.end, end)
    if right <= left:
        return None
    return SpeakerTurn(left, right, turn.speaker)


def locate_speakers_by_scene(
    video_path: str,
    turns: list[SpeakerTurn],
    scene_ranges: list[tuple[float, float]],
    *,
    samples_per_turn: int = DEFAULT_SAMPLES_PER_TURN,
) -> list[dict[int, float]]:
    """Resolve speaker_id -> centre_x separately for every source scene.

    Per-scene mapping matters for edited shows: after a source-camera cut the
    same person may move from the left side to the right. Unresolved speakers are
    omitted so rendering can use the visual/object fallback instead of inventing
    a position.
    """
    if not turns or not scene_ranges:
        return [{} for _ in scene_ranges]

    import cv2

    from clippyme.pipeline.reframe_detect import (
        compute_mouth_aspect_ratio,
        detect_face_candidates,
    )

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return [{} for _ in scene_ranges]
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            return [{} for _ in scene_ranges]

        result: list[dict[int, float]] = []
        for scene_start, scene_end in scene_ranges:
            evidence: dict[int, list[tuple[float, float]]] = {}
            for turn in turns:
                part = _intersect_turn(turn, scene_start, scene_end)
                if part is None or part.duration < _MIN_TURN_SECONDS:
                    continue
                best = _best_face_for_turn(
                    capture,
                    cv2,
                    compute_mouth_aspect_ratio,
                    detect_face_candidates,
                    part,
                    fps,
                    samples_per_turn,
                )
                if best is not None:
                    evidence.setdefault(part.speaker, []).append(best)

            mapping: dict[int, float] = {}
            for speaker, samples in evidence.items():
                total = sum(weight for weight, _ in samples)
                if total <= 0:
                    continue
                mapping[speaker] = sum(weight * x for weight, x in samples) / total
            result.append(mapping)
        return result
    finally:
        capture.release()


def _best_face_for_turn(
    capture,
    cv2,
    mar_fn,
    detect_fn,
    turn: SpeakerTurn,
    fps: float,
    samples: int,
) -> tuple[float, float] | None:
    """Return (MAR variance, centre_x) for the most speech-like face."""
    count = max(3, int(samples))
    step = turn.duration / count
    tracks: dict[tuple[int, int], tuple[list[float], list[float]]] = {}

    for index in range(count):
        timestamp = turn.start + (index + 0.5) * step
        capture.set(cv2.CAP_PROP_POS_FRAMES, round(timestamp * fps))
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        try:
            faces = detect_fn(frame) or []
        except Exception:
            continue
        for face in faces:
            box = _face_box(face)
            if box is None:
                continue
            x, y, w, h = box
            try:
                mar = mar_fn(frame, (x, y, w, h))
            except Exception:
                mar = None
            if mar is None:
                continue
            key = (int(x + w / 2) // 64, int(y + h / 2) // 64)
            mars, centers = tracks.setdefault(key, ([], []))
            mars.append(float(mar))
            centers.append(float(x + w / 2))

    best: tuple[float, float] | None = None
    for mars, centers in tracks.values():
        if len(mars) < _MIN_MAR_SAMPLES:
            continue
        mean = sum(mars) / len(mars)
        variance = sum((value - mean) ** 2 for value in mars) / len(mars)
        center = sum(centers) / len(centers)
        if best is None or variance > best[0]:
            best = (variance, center)
    return best
