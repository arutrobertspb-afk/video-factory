"""TTS module: wraps the generate_voice.py skill for Video Factory use."""
import os
import asyncio
from pathlib import Path

SKILL_SCRIPT = "/Users/robert/Desktop/test/agent-workspace/telegram-agent/skills/tts-voice-generation/scripts/generate_voice.py"
PYTHON = "/opt/homebrew/bin/python3"


async def _run(cmd, timeout=300):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"timeout {timeout}s", 1


async def generate_voice(text: str, voice: str = "af_sarah", out_path: str = None, speed: float = 1.0) -> str:
    """Generate WAV from text. Returns absolute path to WAV."""
    if out_path is None:
        out_path = f"/tmp/vf_tts_{hash(text) % 100000}.wav"

    cmd = [
        PYTHON, SKILL_SCRIPT,
        "--text", text,
        "--voice", voice,
        "--speed", str(speed),
        "--out", out_path,
    ]
    stdout, stderr, code = await _run(cmd, timeout=300)
    if code != 0:
        raise RuntimeError(f"TTS failed: {stderr[:500] or stdout[:500]}")
    if not os.path.exists(out_path):
        raise RuntimeError(f"TTS output missing: {out_path}")
    return out_path


async def add_voiceover_to_video(
    video_path: str,
    text: str,
    out_path: str,
    voice: str = "af_sarah",
    mode: str = "overlay",
    volume: float = 1.0,
    bg_volume: float = 0.3,
) -> str:
    """Generate voiceover and mix into video. Returns path to result mp4."""
    cmd = [
        PYTHON, SKILL_SCRIPT,
        "--text", text,
        "--voice", voice,
        "--mix-into", video_path,
        "--out", out_path,
        "--mode", mode,
        "--volume", str(volume),
        "--bg-volume", str(bg_volume),
    ]
    stdout, stderr, code = await _run(cmd, timeout=600)
    if code != 0:
        raise RuntimeError(f"voiceover mix failed: {stderr[:500] or stdout[:500]}")
    if not os.path.exists(out_path):
        raise RuntimeError(f"voiceover output missing: {out_path}")
    return out_path


# Available voices reference
VOICES = {
    "af_sarah": "American female, warm narrator (default)",
    "af_bella": "American female, energetic",
    "af_nicole": "American female, calm whisper",
    "am_adam": "American male, deep",
    "am_michael": "American male, neutral",
    "bf_emma": "British female, refined",
    "bf_isabella": "British female, soft",
    "bm_george": "British male, storyteller",
    "bm_lewis": "British male, authoritative",
}
