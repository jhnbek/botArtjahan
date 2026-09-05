"""Full lecture ingest: transcript (faster-whisper) + scene-aware frames + OCR.

Per-lecture artifacts (kept compatible with build_knowledge_base.py):
    transcript.json   — faster-whisper segments with word-level timestamps and
                        per-segment confidence stats.
    transcript.txt    — joined plain text.
    transcript.srt    — subtitle file from segments.
    frames/           — JPEG frames (one per detected scene midpoint + max-gap
                        fallbacks).
    visual_index.json — scenes[], frames[{time, path, trigger, scene_index,
                        phash, duplicate_of, linked_segment_index, ocr*}].
    visual_ocr.json   — full OCR per kept frame (lines, bboxes, confidences).

Root-level manifest.json aggregates per-lecture metadata.

Heavy deps loaded lazily so --dry-run works without them.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------- constants ----------

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi"}
FRAME_STRATEGY = "scenedetect_content_with_max_gap_v3"
INGEST_VERSION = "3.0.0"


# ---------- small helpers ----------

def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def safe_dir_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip().strip(".")
    return cleaned or "lecture"


def ensure_unique_dir(root: Path, desired_name: str) -> Path:
    candidate = root / desired_name
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        alt = root / f"{desired_name} ({index})"
        if not alt.exists():
            return alt
        index += 1


def find_videos(source_dir: Path, recursive: bool) -> list[Path]:
    iterator = source_dir.rglob("*") if recursive else source_dir.iterdir()
    videos = [p for p in iterator if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
    return sorted(videos, key=lambda p: str(p).lower())


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe_duration_seconds(video_path: Path) -> float | None:
    if not ffprobe_available():
        return None
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except (TypeError, ValueError):
        return None


def format_srt_time(value: float) -> str:
    total_ms = max(0, int(round(value * 1000)))
    h = total_ms // 3_600_000
    m = (total_ms % 3_600_000) // 60_000
    s = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        lines.extend([
            str(idx),
            f"{format_srt_time(float(seg['start']))} --> {format_srt_time(float(seg['end']))}",
            str(seg["text"]),
            "",
        ])
    return "\n".join(lines).strip() + "\n"


# ---------- data ----------

@dataclass
class TranscriptResult:
    language: str | None
    text: str
    segments: list[dict[str, Any]]
    model_id: str = ""
    vad_filter: bool = True
    beam_size: int = 5


@dataclass
class FrameRecord:
    time: float
    path: str
    trigger: str  # scene | start | max_gap
    scene_index: int | None = None
    phash: str | None = None
    duplicate_of: str | None = None
    linked_segment_index: int | None = None
    ocr_text: str = ""
    ocr_line_count: int = 0


class WhisperUnavailable(RuntimeError):
    pass


class FrameExtractionError(RuntimeError):
    pass


# ---------- transcription (faster-whisper) ----------

def load_faster_whisper(model_name: str, compute_type: str, device: str, batch_size: int = 1):
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise WhisperUnavailable(
            "faster-whisper is not installed. Install: pip install faster-whisper"
        ) from exc
    print(f"loading faster-whisper model={model_name} compute={compute_type} device={device} batch_size={batch_size}", flush=True)
    base = WhisperModel(model_name, device=device, compute_type=compute_type)
    if batch_size and batch_size > 1:
        try:
            from faster_whisper import BatchedInferencePipeline  # type: ignore
        except ImportError:
            print("  BatchedInferencePipeline unavailable; falling back to sequential transcribe", flush=True)
            return base
        return BatchedInferencePipeline(model=base)
    return base


def transcribe_with_faster_whisper(
    model: Any,
    video_path: Path,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
    model_id: str,
    batch_size: int = 1,
) -> TranscriptResult:
    print(f"transcribe: {video_path.name} language={language} vad={vad_filter} beam={beam_size} batch={batch_size}", flush=True)
    kwargs: dict[str, Any] = dict(
        language=language,
        task="transcribe",
        beam_size=beam_size,
        vad_filter=vad_filter,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    is_batched = type(model).__name__ == "BatchedInferencePipeline"
    if is_batched and batch_size and batch_size > 1:
        kwargs["batch_size"] = batch_size
    else:
        kwargs["chunk_length"] = 30
    seg_iter, info = model.transcribe(str(video_path), **kwargs)
    segments: list[dict[str, Any]] = []
    texts: list[str] = []
    for idx, seg in enumerate(seg_iter):
        text = (seg.text or "").strip()
        if not text:
            continue
        words = []
        if seg.words:
            for w in seg.words:
                if w.word is None:
                    continue
                words.append({
                    "start": round(float(w.start or seg.start), 3),
                    "end": round(float(w.end or seg.end), 3),
                    "word": w.word,
                    "prob": round(float(w.probability or 0.0), 3),
                })
        segments.append({
            "id": idx,
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "text": text,
            "words": words,
            "avg_logprob": round(float(seg.avg_logprob or 0.0), 4),
            "no_speech_prob": round(float(seg.no_speech_prob or 0.0), 4),
            "compression_ratio": round(float(seg.compression_ratio or 0.0), 3),
        })
        texts.append(text)
        if idx % 50 == 0 and idx > 0:
            print(f"  ... {idx} segments, last end={seg.end:.1f}s", flush=True)
    return TranscriptResult(
        language=info.language,
        text="\n".join(texts),
        segments=segments,
        model_id=model_id,
        vad_filter=vad_filter,
        beam_size=beam_size,
    )


def transcript_payload(video_path: Path, transcript: TranscriptResult) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "knowledge_bot/ingest_raw_lectures.py",
        "ingest_version": INGEST_VERSION,
        "source_video": str(video_path),
        "language": transcript.language,
        "model": transcript.model_id,
        "vad_filter": transcript.vad_filter,
        "beam_size": transcript.beam_size,
        "text": transcript.text,
        "segments": transcript.segments,
    }


def load_existing_transcript(path: Path) -> TranscriptResult:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    raw = parsed.get("segments", [])
    segments: list[dict[str, Any]] = []
    texts: list[str] = []
    if isinstance(raw, list):
        for idx, seg in enumerate(raw):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            try:
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", start))
            except (TypeError, ValueError):
                continue
            if end < start:
                end = start
            segments.append({
                "id": int(seg.get("id", idx)) if not isinstance(seg.get("id"), bool) else idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "words": seg.get("words", []) or [],
                "avg_logprob": seg.get("avg_logprob"),
                "no_speech_prob": seg.get("no_speech_prob"),
                "compression_ratio": seg.get("compression_ratio"),
            })
            texts.append(text)
    return TranscriptResult(
        language=(str(parsed.get("language")) if parsed.get("language") else None),
        text=str(parsed.get("text", "")) or "\n".join(texts),
        segments=segments,
        model_id=str(parsed.get("model") or ""),
        vad_filter=bool(parsed.get("vad_filter", True)),
        beam_size=int(parsed.get("beam_size", 5) or 5),
    )


# ---------- scene detection (PySceneDetect) ----------

@dataclass
class Scene:
    index: int
    start: float
    end: float

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2.0


def detect_scenes_pyscenedetect(
    video_path: Path,
    content_threshold: float,
    min_scene_len_seconds: float,
) -> list[Scene]:
    try:
        from scenedetect import open_video, SceneManager  # type: ignore
        from scenedetect.detectors import ContentDetector  # type: ignore
    except ImportError as exc:
        raise FrameExtractionError(
            "PySceneDetect is not installed. Install: pip install 'scenedetect[opencv]'"
        ) from exc

    video = open_video(str(video_path))
    fps = float(video.frame_rate) or 25.0
    min_len_frames = max(1, int(round(min_scene_len_seconds * fps)))
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=content_threshold, min_scene_len=min_len_frames))
    sm.detect_scenes(video, show_progress=False)
    scene_list = sm.get_scene_list()

    if not scene_list:
        # Whole video is one "scene"
        duration = probe_duration_seconds(video_path) or 0.0
        return [Scene(index=0, start=0.0, end=max(duration, 0.0))]

    scenes: list[Scene] = []
    for i, (start_tc, end_tc) in enumerate(scene_list):
        scenes.append(Scene(index=i, start=float(start_tc.get_seconds()),
                            end=float(end_tc.get_seconds())))
    return scenes


def build_frame_schedule(
    scenes: list[Scene],
    duration: float | None,
    max_gap_seconds: float,
) -> list[tuple[float, str, int | None]]:
    """Return list of (time, trigger, scene_index)."""
    events: list[tuple[float, str, int | None]] = []
    seen_times: list[float] = []

    def add(t: float, trig: str, scene_idx: int | None) -> None:
        t = max(0.0, t)
        if duration is not None and duration > 0:
            t = min(t, max(0.0, duration - 0.05))
        t = round(t, 3)
        for existing in seen_times:
            if abs(existing - t) < 0.5:
                return
        seen_times.append(t)
        events.append((t, trig, scene_idx))

    # Always have a start frame
    add(0.0, "start", scenes[0].index if scenes else None)
    # One frame per scene at midpoint
    for scene in scenes:
        add(scene.midpoint, "scene", scene.index)
    # Fill any gap larger than max_gap_seconds with fallback frames
    if max_gap_seconds > 0 and duration and duration > 0:
        events.sort(key=lambda e: e[0])
        filled = list(events)
        i = 0
        while i < len(filled) - 1:
            t0, _, _ = filled[i]
            t1, _, _ = filled[i + 1]
            if t1 - t0 > max_gap_seconds * 1.5:
                new_t = t0 + max_gap_seconds
                filled.insert(i + 1, (round(new_t, 3), "max_gap", None))
            i += 1
        # Also pad to end-of-video
        last_t = filled[-1][0]
        while duration - last_t > max_gap_seconds * 1.5:
            last_t += max_gap_seconds
            filled.append((round(last_t, 3), "max_gap", None))
        events = filled
    events.sort(key=lambda e: e[0])
    return events


def extract_frame_at_time(
    video_path: Path,
    output_path: Path,
    timestamp_seconds: float,
    frame_width: int,
    jpeg_quality: int,
) -> None:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{timestamp_seconds:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"scale='min({frame_width},iw)':-2",
        "-q:v", str(jpeg_quality),
        str(output_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise FrameExtractionError(
            completed.stderr.strip() or f"ffmpeg failed for {video_path.name} at {timestamp_seconds:.3f}s"
        )


# ---------- perceptual hash dedup ----------

def compute_phash(image_path: Path) -> str | None:
    try:
        import imagehash  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        with Image.open(image_path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def dedup_frames_by_phash(frames: list[FrameRecord], threshold: int) -> int:
    """Mark near-duplicate frames as duplicate_of and return number of dedups.

    Preserves scene/start frames (only flags duplicates for max_gap or later scenes).
    """
    try:
        import imagehash  # type: ignore
    except ImportError:
        return 0
    last_kept: FrameRecord | None = None
    dedup_count = 0
    for f in frames:
        if f.phash is None:
            last_kept = f
            continue
        if last_kept is None or last_kept.phash is None:
            last_kept = f
            continue
        h1 = imagehash.hex_to_hash(f.phash)
        h0 = imagehash.hex_to_hash(last_kept.phash)
        try:
            distance = h1 - h0
        except Exception:
            last_kept = f
            continue
        if distance <= threshold:
            f.duplicate_of = last_kept.path
            dedup_count += 1
            # Do not update last_kept — keep comparing to the kept anchor
            continue
        last_kept = f
    return dedup_count


# ---------- OCR (EasyOCR) ----------

_OCR_READER: Any = None


def get_ocr_reader(languages: list[str], use_gpu: bool):
    global _OCR_READER
    if _OCR_READER is not None:
        return _OCR_READER
    try:
        import easyocr  # type: ignore
    except ImportError as exc:
        raise FrameExtractionError(
            "EasyOCR is not installed. Install: pip install easyocr"
        ) from exc
    print(f"loading EasyOCR languages={languages} gpu={use_gpu}", flush=True)
    _OCR_READER = easyocr.Reader(languages, gpu=use_gpu, verbose=False)
    return _OCR_READER


def release_ocr_reader() -> None:
    """Free EasyOCR model from RAM (and GPU) between lectures to avoid OOM
    during the next Whisper feature extraction (STFT keeps a 1-2 GB array)."""
    global _OCR_READER
    if _OCR_READER is None:
        return
    try:
        import torch  # type: ignore
        del _OCR_READER
        _OCR_READER = None
        import gc
        gc.collect()
        if hasattr(torch, "cuda") and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        _OCR_READER = None


def ocr_frame(reader: Any, frame_path: Path, min_conf: float, max_dimension: int | None = None) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        # OpenCV's imread is not reliable with non-ASCII Windows paths. EasyOCR
        # calls imread internally when given a path, so decode bytes ourselves.
        raw = np.fromfile(str(frame_path), dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if image is None:
            return {"text": "", "lines": [], "error": f"could not read image: {frame_path}"}
        bbox_scale = 1.0
        if max_dimension:
            height, width = image.shape[:2]
            source_max_dimension = max(height, width)
            if source_max_dimension > max_dimension:
                scale = max_dimension / source_max_dimension
                resized_width = max(1, int(width * scale))
                resized_height = max(1, int(height * scale))
                image = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
                bbox_scale = 1.0 / scale
        readtext_kwargs = {"detail": 1, "paragraph": False}
        if max_dimension:
            readtext_kwargs["canvas_size"] = max_dimension
        result = reader.readtext(image, **readtext_kwargs)
    except Exception as exc:
        return {"text": "", "lines": [], "error": str(exc)}
    lines: list[dict[str, Any]] = []
    for entry in result:
        try:
            points, text, conf = entry
        except (TypeError, ValueError):
            continue
        text = str(text).strip()
        if not text:
            continue
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            conf_f = 0.0
        if conf_f < min_conf:
            continue
        try:
            bbox = [[float(p[0]) * bbox_scale, float(p[1]) * bbox_scale] for p in points]
        except (TypeError, ValueError, IndexError):
            bbox = []
        lines.append({"text": text, "conf": round(conf_f, 3), "bbox": bbox})
    full_text = "\n".join(line["text"] for line in lines)
    return {"text": full_text, "lines": lines}


# ---------- frame ↔ segment linking ----------

def link_time_to_segment(time_value: float, segments: list[dict[str, Any]]) -> int | None:
    if not segments:
        return None
    for seg in segments:
        try:
            start = float(seg["start"]); end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= time_value <= end:
            try:
                return int(seg["id"])
            except (KeyError, TypeError, ValueError):
                continue
    # Nearest by midpoint
    best_idx = 0
    best_dist = float("inf")
    for i, seg in enumerate(segments):
        try:
            mid = (float(seg["start"]) + float(seg["end"])) / 2.0
        except (KeyError, TypeError, ValueError):
            continue
        d = abs(mid - time_value)
        if d < best_dist:
            best_dist = d
            best_idx = i
    try:
        return int(segments[best_idx]["id"])
    except (KeyError, TypeError, ValueError):
        return None


# ---------- main visual pipeline ----------

@dataclass
class VisualConfig:
    max_gap_seconds: float
    content_threshold: float
    min_scene_len_seconds: float
    frame_width: int
    jpeg_quality: int
    phash_threshold: int
    ocr_enabled: bool
    ocr_languages: list[str]
    ocr_min_conf: float
    ocr_gpu: bool


def build_visual_artifacts(
    video_path: Path,
    lecture_dir: Path,
    transcript: TranscriptResult,
    cfg: VisualConfig,
    overwrite: bool,
) -> tuple[list[FrameRecord], list[Scene], dict[str, Any]]:
    frames_dir = lecture_dir / "frames"
    if overwrite and frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration_seconds(video_path)
    print(f"scene detection: {video_path.name} duration={duration}", flush=True)
    scenes = detect_scenes_pyscenedetect(
        video_path, cfg.content_threshold, cfg.min_scene_len_seconds
    )
    print(f"  scenes={len(scenes)}", flush=True)

    schedule = build_frame_schedule(scenes, duration, cfg.max_gap_seconds)
    print(f"  scheduled frames={len(schedule)}", flush=True)

    frame_records: list[FrameRecord] = []
    for i, (t, trig, scene_idx) in enumerate(schedule, start=1):
        out_path = frames_dir / f"frame_{i:05d}.jpg"
        extract_frame_at_time(video_path, out_path, t, cfg.frame_width, cfg.jpeg_quality)
        rel = str(out_path.relative_to(lecture_dir)).replace("\\", "/")
        rec = FrameRecord(time=t, path=rel, trigger=trig, scene_index=scene_idx)
        rec.phash = compute_phash(out_path)
        rec.linked_segment_index = link_time_to_segment(t, transcript.segments)
        frame_records.append(rec)

    dedup_count = dedup_frames_by_phash(frame_records, cfg.phash_threshold)
    print(f"  dedup: {dedup_count} duplicates flagged", flush=True)

    ocr_payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "easyocr" if cfg.ocr_enabled else "none",
        "languages": cfg.ocr_languages,
        "min_conf": cfg.ocr_min_conf,
        "frames": [],
    }
    if cfg.ocr_enabled:
        try:
            reader = get_ocr_reader(cfg.ocr_languages, cfg.ocr_gpu)
        except FrameExtractionError as exc:
            print(f"warning: {exc}; skipping OCR", flush=True)
            cfg.ocr_enabled = False
            ocr_payload["engine"] = "none"
        if cfg.ocr_enabled:
            ocr_targets = [f for f in frame_records if f.duplicate_of is None]
            print(f"  ocr: {len(ocr_targets)} unique frames", flush=True)
            for idx, rec in enumerate(ocr_targets, start=1):
                full_path = lecture_dir / rec.path
                ocr = ocr_frame(reader, full_path, cfg.ocr_min_conf)
                rec.ocr_text = ocr.get("text", "")
                rec.ocr_line_count = len(ocr.get("lines", []))
                ocr_payload["frames"].append({
                    "path": rec.path,
                    "time": rec.time,
                    "text": rec.ocr_text,
                    "lines": ocr.get("lines", []),
                })
                if idx % 20 == 0:
                    print(f"    ocr progress: {idx}/{len(ocr_targets)}", flush=True)

    visual_index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "knowledge_bot/ingest_raw_lectures.py",
        "ingest_version": INGEST_VERSION,
        "source_video": str(video_path),
        "duration_seconds": duration,
        "frame_strategy": FRAME_STRATEGY,
        "max_gap_seconds": cfg.max_gap_seconds,
        "frame_interval_seconds": cfg.max_gap_seconds,  # backward-compat alias
        "content_threshold": cfg.content_threshold,
        "min_scene_len_seconds": cfg.min_scene_len_seconds,
        "frame_width": cfg.frame_width,
        "jpeg_quality": cfg.jpeg_quality,
        "phash_threshold": cfg.phash_threshold,
        "scene_count": len(scenes),
        "frame_count": len(frame_records),
        "unique_frame_count": sum(1 for f in frame_records if f.duplicate_of is None),
        "duplicate_count": sum(1 for f in frame_records if f.duplicate_of is not None),
        "ocr_engine": "easyocr" if cfg.ocr_enabled else "none",
        "ocr_languages": cfg.ocr_languages if cfg.ocr_enabled else [],
        "scenes": [{"index": s.index, "start": round(s.start, 3), "end": round(s.end, 3)} for s in scenes],
        "frames": [
            {
                "time": f.time,
                "path": f.path,
                "trigger": f.trigger,
                "scene_index": f.scene_index,
                "phash": f.phash,
                "duplicate_of": f.duplicate_of,
                "linked_segment_index": f.linked_segment_index,
                "ocr_text": f.ocr_text,
                "ocr_line_count": f.ocr_line_count,
            }
            for f in frame_records
        ],
    }
    (lecture_dir / "visual_index.json").write_text(
        json.dumps(visual_index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (lecture_dir / "visual_ocr.json").write_text(
        json.dumps(ocr_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return frame_records, scenes, visual_index


def visual_index_needs_refresh(path: Path, cfg: VisualConfig) -> bool:
    if not path.exists():
        return True
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    if parsed.get("frame_strategy") != FRAME_STRATEGY:
        return True
    try:
        return not (
            abs(float(parsed.get("max_gap_seconds", -1)) - cfg.max_gap_seconds) < 0.001
            and abs(float(parsed.get("content_threshold", -1)) - cfg.content_threshold) < 0.001
            and abs(float(parsed.get("min_scene_len_seconds", -1)) - cfg.min_scene_len_seconds) < 0.001
            and int(parsed.get("frame_width", -1)) == cfg.frame_width
            and int(parsed.get("jpeg_quality", -1)) == cfg.jpeg_quality
            and int(parsed.get("phash_threshold", -1)) == cfg.phash_threshold
            and bool(parsed.get("ocr_engine", "none") != "none") == cfg.ocr_enabled
        )
    except (TypeError, ValueError):
        return True


# ---------- manifest ----------

def manifest_record(
    source_path: Path,
    lecture_dir: Path,
    transcript: TranscriptResult,
    visual_index: dict[str, Any] | None,
    cfg: VisualConfig,
) -> dict[str, Any]:
    duration = max((float(s["end"]) for s in transcript.segments), default=0.0)
    if visual_index and visual_index.get("duration_seconds"):
        try:
            duration = max(duration, float(visual_index["duration_seconds"]))
        except (TypeError, ValueError):
            pass
    return {
        "source": str(source_path),
        "output_dir": str(lecture_dir),
        "transcript_json": str(lecture_dir / "transcript.json"),
        "transcript_txt": str(lecture_dir / "transcript.txt"),
        "transcript_srt": str(lecture_dir / "transcript.srt"),
        "visual_index_json": str(lecture_dir / "visual_index.json"),
        "visual_ocr_json": str(lecture_dir / "visual_ocr.json"),
        "duration_seconds": round(duration, 3),
        "segment_count": len(transcript.segments),
        "frame_count": (visual_index or {}).get("frame_count", 0),
        "scene_count": (visual_index or {}).get("scene_count", 0),
        "unique_frame_count": (visual_index or {}).get("unique_frame_count", 0),
        "duplicate_count": (visual_index or {}).get("duplicate_count", 0),
        "max_gap_seconds": cfg.max_gap_seconds,
        "frame_interval_seconds": cfg.max_gap_seconds,  # backward compat
        "language": transcript.language,
        "model": transcript.model_id,
        "ocr_engine": (visual_index or {}).get("ocr_engine", "none"),
    }


def load_existing_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    results = parsed.get("results", [])
    if not isinstance(results, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for record in results:
        if isinstance(record, dict):
            src = record.get("source")
            if isinstance(src, str):
                out[src] = record
    return out


def write_manifest(path: Path, source_dir: Path, records_by_source: dict[str, dict[str, Any]]) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "knowledge_bot/ingest_raw_lectures.py",
        "ingest_version": INGEST_VERSION,
        "source_root": str(source_dir),
        "results": [records_by_source[k] for k in sorted(records_by_source)],
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_visual_index(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------- CLI ----------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Full lecture ingest (faster-whisper + PySceneDetect + EasyOCR)."
    )
    p.add_argument("--source-dir", required=True, type=Path)
    p.add_argument("--output-root", type=Path, default=default_root())
    p.add_argument("--language", default="ru", help="Whisper language hint, or 'auto'.")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--overwrite", action="store_true",
                   help="Re-transcribe and re-extract frames even if outputs exist.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-frames", action="store_true",
                   help="Do not extract frames or run OCR.")
    p.add_argument("--skip-transcription", action="store_true",
                   help="Reuse existing transcript.json; only refresh visual artifacts.")

    # Transcription
    p.add_argument("--model", default="large-v3-turbo",
                   help="faster-whisper model name: tiny | base | small | medium | large-v3 | large-v3-turbo | distil-large-v3.")
    p.add_argument("--compute-type", default="int8",
                   help="ctranslate2 compute type: int8 | int8_float16 | float16 | float32.")
    p.add_argument("--device", default="auto", help="cpu | cuda | auto")
    p.add_argument("--beam-size", type=int, default=5)
    p.add_argument("--batch-size", type=int, default=4,
                   help="BatchedInferencePipeline batch size (>=2 enables batched). 1 = sequential. "
                        "Default 4 = safe ceiling for GTX 1050 Ti 4GB on large-v3-turbo int8_float32.")
    p.add_argument("--no-vad", action="store_true", help="Disable VAD filter.")
    p.add_argument("--delete-source", action="store_true",
                   help="Delete the source video file after the lecture is fully processed and recorded in manifest.")

    # Frames
    p.add_argument("--max-gap-seconds", type=float, default=60.0,
                   help="Maximum gap between visual frames; scenes fill in between.")
    p.add_argument("--frame-interval-seconds", type=float, default=None,
                   help="Backward-compat alias for --max-gap-seconds.")
    p.add_argument("--scene-threshold", type=float, default=27.0,
                   help="PySceneDetect ContentDetector threshold (HSV mean diff). 27 default.")
    p.add_argument("--scene-min-len-seconds", type=float, default=2.0)
    p.add_argument("--scene-min-gap-seconds", type=float, default=None,
                   help="Backward-compat alias for --scene-min-len-seconds.")
    p.add_argument("--frame-width", type=int, default=1280)
    p.add_argument("--frame-jpeg-quality", type=int, default=4,
                   help="2-31, lower is better quality.")

    # Dedup
    p.add_argument("--phash-threshold", type=int, default=5)

    # OCR
    p.add_argument("--no-ocr", action="store_true", help="Disable OCR pass.")
    p.add_argument("--ocr-languages", default="ru,en")
    p.add_argument("--ocr-min-conf", type=float, default=0.30)
    p.add_argument("--ocr-gpu", action="store_true")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_dir = args.source_dir.resolve()
    output_root = args.output_root.resolve()
    language = None if str(args.language).lower() == "auto" else args.language

    # Resolve backward-compat aliases
    max_gap = args.max_gap_seconds
    if args.frame_interval_seconds is not None:
        max_gap = float(args.frame_interval_seconds)
    min_scene_len = args.scene_min_len_seconds
    if args.scene_min_gap_seconds is not None:
        min_scene_len = float(args.scene_min_gap_seconds)

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"error: source directory not found: {source_dir}", file=sys.stderr)
        return 2
    if not ffmpeg_available():
        print("error: ffmpeg is not available in PATH", file=sys.stderr)
        return 2
    if args.frame_width <= 0:
        print("error: --frame-width must be positive", file=sys.stderr); return 2
    if not 2 <= args.frame_jpeg_quality <= 31:
        print("error: --frame-jpeg-quality must be 2..31", file=sys.stderr); return 2
    if max_gap < 0:
        print("error: --max-gap-seconds must be >= 0", file=sys.stderr); return 2
    if args.scene_threshold <= 0:
        print("error: --scene-threshold must be > 0", file=sys.stderr); return 2

    videos = find_videos(source_dir, args.recursive)
    if args.limit is not None:
        videos = videos[: max(0, args.limit)]
    if not videos:
        print(f"error: no supported videos found in {source_dir}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"Found {len(videos)} videos in {source_dir}")
        for v in videos:
            print(f"- {v.name} -> {safe_dir_name(v.stem)}")
        return 0

    cfg = VisualConfig(
        max_gap_seconds=max_gap,
        content_threshold=args.scene_threshold,
        min_scene_len_seconds=min_scene_len,
        frame_width=args.frame_width,
        jpeg_quality=args.frame_jpeg_quality,
        phash_threshold=args.phash_threshold,
        ocr_enabled=not args.no_ocr,
        ocr_languages=[s.strip() for s in args.ocr_languages.split(",") if s.strip()],
        ocr_min_conf=args.ocr_min_conf,
        ocr_gpu=args.ocr_gpu,
    )

    model = None
    model_id = f"faster-whisper:{args.model}:{args.compute_type}"
    if not args.skip_transcription:
        try:
            model = load_faster_whisper(args.model, args.compute_type, args.device, batch_size=args.batch_size)
        except WhisperUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.json"
    records_by_source = load_existing_manifest(manifest_path)
    processed = 0
    refreshed_visual = 0
    skipped = 0

    for video in videos:
        desired = safe_dir_name(video.stem)
        lecture_dir = output_root / desired
        transcript_path = lecture_dir / "transcript.json"
        visual_path = lecture_dir / "visual_index.json"
        lecture_dir.mkdir(parents=True, exist_ok=True)

        # --- transcript ---
        transcript: TranscriptResult | None = None
        if transcript_path.exists() and not args.overwrite:
            transcript = load_existing_transcript(transcript_path)
            print(f"reuse transcript: {video.name} segments={len(transcript.segments)}")
        else:
            if args.skip_transcription:
                if not transcript_path.exists():
                    print(f"skip {video.name}: --skip-transcription but no transcript.json yet")
                    skipped += 1
                    continue
                transcript = load_existing_transcript(transcript_path)
            else:
                assert model is not None
                transcript = transcribe_with_faster_whisper(
                    model, video, language,
                    beam_size=args.beam_size,
                    vad_filter=not args.no_vad,
                    model_id=model_id,
                    batch_size=args.batch_size,
                )
                transcript_path.write_text(
                    json.dumps(transcript_payload(video, transcript), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (lecture_dir / "transcript.txt").write_text(
                    (transcript.text or "") + ("\n" if transcript.text else ""), encoding="utf-8"
                )
                (lecture_dir / "transcript.srt").write_text(
                    render_srt(transcript.segments), encoding="utf-8"
                )
                processed += 1

        # --- visual ---
        if args.skip_frames:
            visual_index = load_existing_visual_index(visual_path)
        else:
            need_refresh = args.overwrite or visual_index_needs_refresh(visual_path, cfg)
            if need_refresh:
                _, _, visual_index = build_visual_artifacts(
                    video, lecture_dir, transcript, cfg, overwrite=True
                )
                refreshed_visual += 1
            else:
                visual_index = load_existing_visual_index(visual_path)
                print(f"reuse visual: {video.name} frames={(visual_index or {}).get('frame_count', 0)}")

        records_by_source[str(video)] = manifest_record(
            video, lecture_dir, transcript, visual_index, cfg
        )
        write_manifest(manifest_path, source_dir, records_by_source)

        # Free EasyOCR (~1 GB RAM) before next Whisper STFT — avoids OOM on
        # long (2h+) lectures where the FFT alone needs ~1.8 GB contiguous.
        release_ocr_reader()

        # Optionally delete the source video to free disk space after success.
        if args.delete_source:
            try:
                video.unlink()
                print(f"deleted source: {video.name}")
            except OSError as exc:
                print(f"warn: could not delete {video.name}: {exc}", file=sys.stderr)

    write_manifest(manifest_path, source_dir, records_by_source)

    print(f"Processed transcripts: {processed}")
    print(f"Refreshed visual: {refreshed_visual}")
    print(f"Skipped: {skipped}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
