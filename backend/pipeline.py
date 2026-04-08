"""Video pipeline: download → frames → transcribe."""
import os
import subprocess
import json
import asyncio
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
VIDEOS_DIR = DATA / "videos"
FRAMES_DIR = DATA / "frames"
AUDIO_DIR = DATA / "audio"
CLIPS_DIR = DATA / "clips"
REMIXES_DIR = DATA / "remixes"

for d in (VIDEOS_DIR, FRAMES_DIR, AUDIO_DIR, CLIPS_DIR, REMIXES_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# PIPELINE CONFIG — per-video tuneable limits
# ═══════════════════════════════════════════
CONFIG_PATH = BASE / "pipeline_config.json"
DEFAULT_CONFIG = {
    # Hard limits to prevent runaway costs
    "max_video_duration_sec": 900,        # 15 min max, None = unlimited
    "skip_if_duration_over": 900,         # reject at metadata stage
    # Frame extraction
    "frame_fps_default": 1,               # 1 frame/sec for short videos
    "frame_fps_long_video": 0.2,          # 1 frame / 5 sec for videos > 5 min
    "long_video_threshold_sec": 300,      # videos > 5 min use frame_fps_long_video
    "max_frames": 300,                    # NEVER extract more than this
    # Auto vision tagging
    "auto_tag_frames": False,             # DEFAULT OFF — opt-in per request
    "auto_tag_max_frames": 30,            # if on, tag only first N frames
    "auto_tag_model": "claude-haiku-4-5", # cheap model, not Sonnet
    # Transcription
    "whisper_model": "tiny",              # tiny is fast + cheap (local)
    "skip_transcription": False,
    # Cost logging
    "log_costs": True,
}


def load_pipeline_config() -> dict:
    """Load config from JSON file, fall back to defaults."""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            import json
            with open(CONFIG_PATH) as f:
                user_cfg = json.load(f)
            cfg.update(user_cfg)
        except Exception as e:
            print(f"[pipeline_config] load failed: {e}, using defaults")
    return cfg


def save_pipeline_config(cfg: dict):
    """Persist config to disk."""
    import json
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# Homebrew binary paths (not always on $PATH for GUI apps)
YT_DLP = "/opt/homebrew/bin/yt-dlp"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
WHISPER = "/Users/robert/Library/Python/3.9/bin/whisper"


async def _run(cmd, cwd=None, timeout=900):
    """Run subprocess async, return (stdout, stderr, code)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"TIMEOUT after {timeout}s", 1


async def fetch_metadata(url):
    """Get title, duration, channel, views via yt-dlp --dump-json."""
    stdout, stderr, code = await _run([YT_DLP, "--dump-json", "--no-warnings", url], timeout=60)
    if code != 0 or not stdout.strip():
        raise RuntimeError(f"yt-dlp metadata failed: {stderr[:300]}")
    info = json.loads(stdout.strip().splitlines()[0])
    return {
        "title": info.get("title", ""),
        "duration": info.get("duration", 0),
        "channel": info.get("uploader") or info.get("channel", ""),
        "view_count": info.get("view_count", 0),
        "thumbnail": info.get("thumbnail", ""),
    }


async def download_video(video_id, url):
    """Download video as mp4 (<=720p) into data/videos/{video_id}.mp4."""
    out_path = VIDEOS_DIR / f"{video_id}.mp4"
    cmd = [
        YT_DLP,
        "-f", "best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(out_path),
        "--no-warnings",
        url,
    ]
    stdout, stderr, code = await _run(cmd, timeout=900)
    if code != 0 or not out_path.exists():
        raise RuntimeError(f"yt-dlp download failed: {stderr[:500]}")
    return str(out_path)


async def extract_frames(video_id, video_path, fps=1):
    """Extract 1 frame per second into data/frames/{video_id}/frame_%04d.jpg."""
    out_dir = FRAMES_DIR / str(video_id)
    out_dir.mkdir(exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-i", video_path,
        "-vf", f"fps={fps},scale=320:-1",
        "-q:v", "4",
        str(out_dir / "frame_%04d.jpg"),
    ]
    _, stderr, code = await _run(cmd, timeout=600)
    if code != 0:
        raise RuntimeError(f"ffmpeg frames failed: {stderr[:500]}")
    frames = sorted(out_dir.glob("frame_*.jpg"))
    return [str(f) for f in frames]


async def extract_audio(video_id, video_path):
    """Extract audio as wav (16kHz mono for whisper)."""
    out_path = AUDIO_DIR / f"{video_id}.wav"
    cmd = [
        FFMPEG, "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        str(out_path),
    ]
    _, stderr, code = await _run(cmd, timeout=300)
    if code != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg audio failed: {stderr[:500]}")
    return str(out_path)


async def transcribe(video_id, audio_path, model="tiny"):
    """Run whisper on audio, return list of {start, end, text}."""
    out_dir = AUDIO_DIR / f"{video_id}_whisper"
    out_dir.mkdir(exist_ok=True)
    cmd = [
        WHISPER, audio_path,
        "--model", model,
        "--output_format", "json",
        "--output_dir", str(out_dir),
        "--verbose", "False",
        "--fp16", "False",
    ]
    _, stderr, code = await _run(cmd, timeout=1800)
    if code != 0:
        raise RuntimeError(f"whisper failed: {stderr[:500]}")
    json_file = out_dir / (Path(audio_path).stem + ".json")
    if not json_file.exists():
        raise RuntimeError(f"whisper json not found: {json_file}")
    data = json.loads(json_file.read_text())
    segments = data.get("segments", [])
    return [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in segments
    ]


async def process_video(video_id, url, progress_cb=None, overrides: dict = None):
    """Full pipeline: metadata → download → frames → audio → transcribe.

    overrides: dict to override specific config keys for THIS video.
        Example: {"auto_tag_frames": True, "max_frames": 60}
    """
    import db

    cfg = load_pipeline_config()
    if overrides:
        cfg.update(overrides)

    async def _progress(status):
        if progress_cb:
            try:
                await progress_cb(status)
            except Exception:
                pass

    # Metadata
    await _progress("fetching_metadata")
    meta = await fetch_metadata(url)

    # GUARD 1: reject if video too long
    duration = meta.get("duration", 0) or 0
    max_dur = cfg.get("skip_if_duration_over")
    if max_dur and duration > max_dur:
        db.update_video(
            video_id,
            title=meta["title"],
            channel=meta["channel"],
            duration_sec=duration,
            view_count=meta["view_count"],
            status=f"skipped: too long ({int(duration)}s > {max_dur}s limit)",
        )
        await _progress("skipped_too_long")
        return {"skipped": True, "reason": "too_long", "duration": duration}

    db.update_video(
        video_id,
        title=meta["title"],
        channel=meta["channel"],
        duration_sec=duration,
        view_count=meta["view_count"],
        status="downloading",
    )

    # Download
    await _progress("downloading")
    video_path = await download_video(video_id, url)
    db.update_video(video_id, local_path=video_path, status="extracting_frames")

    # Frames — use fps based on video length
    await _progress("extracting_frames")
    long_thresh = cfg.get("long_video_threshold_sec", 300)
    if duration > long_thresh:
        fps = cfg.get("frame_fps_long_video", 0.2)
    else:
        fps = cfg.get("frame_fps_default", 1)

    frames = await extract_frames(video_id, video_path, fps=fps)

    # GUARD 2: cap total frames
    max_frames = cfg.get("max_frames", 300)
    if max_frames and len(frames) > max_frames:
        frames_trimmed = frames[:max_frames]
        for extra in frames[max_frames:]:
            try:
                os.remove(extra)
            except Exception:
                pass
        frames = frames_trimmed

    for idx, f_path in enumerate(frames):
        second = idx / fps if fps > 0 else idx
        db.add_frame(video_id, second=second, thumbnail_path=f_path)
    db.update_video(video_id, status="extracting_audio")

    # Audio
    await _progress("extracting_audio")
    audio_path = await extract_audio(video_id, video_path)
    db.update_video(video_id, status="transcribing")

    # Transcribe
    if not cfg.get("skip_transcription", False):
        await _progress("transcribing")
        try:
            whisper_model = cfg.get("whisper_model", "tiny")
            segments = await transcribe(video_id, audio_path, model=whisper_model)
            for seg in segments:
                db.add_transcript(video_id, seg["start"], seg["end"], seg["text"])
        except Exception as e:
            print(f"[transcribe] failed for {video_id}: {e}")

    # Auto vision tagging — OPT-IN, capped, cheaper model
    if cfg.get("auto_tag_frames", False):
        db.update_video(video_id, status="tagging_frames")
        await _progress("tagging_frames")
        try:
            import ai
            frame_rows = db.list_frames(video_id)
            tag_limit = cfg.get("auto_tag_max_frames", 30)
            frame_rows = frame_rows[:tag_limit]
            batch_size = 10
            for i in range(0, len(frame_rows), batch_size):
                batch = frame_rows[i:i+batch_size]
                paths = [f["thumbnail_path"] for f in batch]
                descriptions = await ai.describe_frames_batch(paths)
                for frame, desc in zip(batch, descriptions):
                    db.update_frame_description(frame["id"], desc)
        except Exception as e:
            print(f"[auto-tag] failed for {video_id}: {e}")

    db.update_video(video_id, status="ready")
    await _progress("ready")
    return {"frames": len(frames), "meta": meta, "cfg_used": cfg}


async def cut_clip(video_path, start_sec, end_sec, out_path):
    """Extract a sub-clip from video using ffmpeg, re-encode for seekability."""
    duration = end_sec - start_sec
    cmd = [
        FFMPEG, "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    _, stderr, code = await _run(cmd, timeout=300)
    if code != 0:
        raise RuntimeError(f"ffmpeg cut failed: {stderr[:300]}")
    return str(out_path)


async def concat_clips(clip_paths, out_path):
    """Concatenate multiple mp4 clips into one using ffmpeg concat demuxer."""
    if not clip_paths:
        raise RuntimeError("no clips to concat")
    # Write concat list
    list_file = out_path.parent / f"{out_path.stem}_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in clip_paths))

    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    _, stderr, code = await _run(cmd, timeout=600)
    list_file.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg concat failed: {stderr[:300]}")
    return str(out_path)


async def burn_subtitles(video_path, srt_path, out_path):
    """Burn SRT subtitles into video."""
    # Escape path for ffmpeg subtitles filter
    srt_escaped = str(srt_path).replace(":", r"\:").replace(",", r"\,")
    cmd = [
        FFMPEG, "-y",
        "-i", video_path,
        "-vf", f"subtitles={srt_escaped}:force_style='FontName=Arial,FontSize=24,PrimaryColour=&Hffffff,OutlineColour=&H000000,BorderStyle=3,Outline=2,Alignment=2,MarginV=60'",
        "-c:a", "copy",
        str(out_path),
    ]
    _, stderr, code = await _run(cmd, timeout=600)
    if code != 0:
        raise RuntimeError(f"ffmpeg subtitles failed: {stderr[:300]}")
    return str(out_path)


def _sec_to_srt_ts(sec):
    """Convert seconds to SRT timestamp HH:MM:SS,mmm."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcripts_to_srt(transcripts, srt_path, time_offset=0):
    """Convert transcript list to SRT file. Applies time_offset to all timestamps (for clips)."""
    lines = []
    for i, t in enumerate(transcripts, 1):
        start = max(0, t["start_sec"] - time_offset)
        end = max(0, t["end_sec"] - time_offset)
        lines.append(f"{i}")
        lines.append(f"{_sec_to_srt_ts(start)} --> {_sec_to_srt_ts(end)}")
        lines.append(t["text"])
        lines.append("")
    Path(srt_path).write_text("\n".join(lines))


async def build_remix(remix_id, clips, with_subtitles=False, progress_cb=None):
    """Build a full remix: cut clips, concat, optionally burn subtitles.
    clips: list of {video_id, start_sec, end_sec}
    """
    import db as _db

    async def _p(msg):
        if progress_cb:
            try:
                await progress_cb(msg)
            except Exception:
                pass

    # Cut each clip
    clip_files = []
    await _p("cutting")
    for i, c in enumerate(clips):
        v = _db.get_video(c["video_id"])
        if not v:
            continue
        video_path = v["video"]["local_path"]
        if not video_path or not os.path.exists(video_path):
            continue
        clip_out = CLIPS_DIR / f"remix{remix_id}_clip{i}.mp4"
        await cut_clip(video_path, c["start_sec"], c["end_sec"], clip_out)
        clip_files.append(str(clip_out))

    if not clip_files:
        raise RuntimeError("no clips could be cut")

    # Concat
    await _p("concatenating")
    raw_out = REMIXES_DIR / f"remix{remix_id}_raw.mp4"
    await concat_clips(clip_files, raw_out)

    final_out = REMIXES_DIR / f"remix{remix_id}.mp4"

    # Subtitles
    if with_subtitles:
        await _p("generating subtitles")
        # Collect transcripts across all source clips with offset
        all_subs = []
        time_cursor = 0
        for c in clips:
            v_data = _db.get_video(c["video_id"])
            if not v_data:
                continue
            for t in v_data["transcripts"]:
                # Only include transcripts within clip range
                if t["start_sec"] >= c["start_sec"] and t["end_sec"] <= c["end_sec"]:
                    all_subs.append({
                        "start_sec": time_cursor + (t["start_sec"] - c["start_sec"]),
                        "end_sec": time_cursor + (t["end_sec"] - c["start_sec"]),
                        "text": t["text"],
                    })
            time_cursor += (c["end_sec"] - c["start_sec"])

        if all_subs:
            srt_path = REMIXES_DIR / f"remix{remix_id}.srt"
            transcripts_to_srt(all_subs, srt_path)
            await _p("burning subtitles")
            await burn_subtitles(str(raw_out), str(srt_path), str(final_out))
            raw_out.unlink(missing_ok=True)
        else:
            raw_out.rename(final_out)
    else:
        raw_out.rename(final_out)

    # Cleanup temporary clip files
    for cf in clip_files:
        try:
            os.remove(cf)
        except Exception:
            pass

    await _p("ready")
    return str(final_out)
