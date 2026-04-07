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

for d in (VIDEOS_DIR, FRAMES_DIR, AUDIO_DIR):
    d.mkdir(parents=True, exist_ok=True)

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


async def process_video(video_id, url, progress_cb=None):
    """Full pipeline: metadata → download → frames → audio → transcribe."""
    import db

    async def _progress(status):
        if progress_cb:
            try:
                await progress_cb(status)
            except Exception:
                pass

    # Metadata
    await _progress("fetching_metadata")
    meta = await fetch_metadata(url)
    db.update_video(
        video_id,
        title=meta["title"],
        channel=meta["channel"],
        duration_sec=meta["duration"],
        view_count=meta["view_count"],
        status="downloading",
    )

    # Download
    await _progress("downloading")
    video_path = await download_video(video_id, url)
    db.update_video(video_id, local_path=video_path, status="extracting_frames")

    # Frames
    await _progress("extracting_frames")
    frames = await extract_frames(video_id, video_path, fps=1)
    for idx, f_path in enumerate(frames):
        db.add_frame(video_id, second=idx, thumbnail_path=f_path)
    db.update_video(video_id, status="extracting_audio")

    # Audio
    await _progress("extracting_audio")
    audio_path = await extract_audio(video_id, video_path)
    db.update_video(video_id, status="transcribing")

    # Transcribe
    await _progress("transcribing")
    try:
        segments = await transcribe(video_id, audio_path)
        for seg in segments:
            db.add_transcript(video_id, seg["start"], seg["end"], seg["text"])
    except Exception as e:
        print(f"[transcribe] failed for {video_id}: {e}")

    db.update_video(video_id, status="ready")
    await _progress("ready")
    return {"frames": len(frames), "meta": meta}
