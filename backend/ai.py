"""AI layer: uses Claude CLI subprocess to answer questions about videos."""
import os
import asyncio
import json

CLAUDE_CLI = "/opt/homebrew/bin/claude"


def _make_env():
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    return env


async def _call_claude(prompt: str, timeout: int = 180) -> str:
    """Low-level: call claude CLI with a prompt, return output."""
    cmd = [
        CLAUDE_CLI,
        "-p", prompt,
        "--model", "claude-sonnet-4-6",
        "--output-format", "text",
        "--dangerously-skip-permissions",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_make_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return f"❌ Claude CLI error: {stderr.decode('utf-8', errors='replace')[:500]}"
        return stdout.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        return f"⏱ Timeout after {timeout}s"
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"


async def describe_frames_batch(frame_paths: list, batch_size: int = 12) -> list:
    """Describe a list of frames. Returns list of descriptions matching input order."""
    if not frame_paths:
        return []

    frames_list = "\n".join(f"{i+1}. {p}" for i, p in enumerate(frame_paths))

    prompt = f"""Опиши КАЖДЫЙ из следующих кадров видео кратко (1 короткое предложение на кадр). Прочитай каждый кадр через Read и опиши что на нём. Отвечай строго JSON массивом строк в том же порядке, без markdown:

Кадры:
{frames_list}

Формат ответа (строго JSON массив, ничего кроме него):
["описание 1", "описание 2", ...]
"""

    result = await _call_claude(prompt, timeout=600)
    # Try to parse JSON
    try:
        # Find JSON array in response
        import re
        match = re.search(r'\[[\s\S]*\]', result)
        if match:
            descs = json.loads(match.group())
            if isinstance(descs, list):
                return descs
    except Exception as e:
        print(f"[describe_frames] parse failed: {e}, raw: {result[:300]}")
    # Fallback: split by newlines
    return [result[:100]] * len(frame_paths)


async def autonomous_pipeline(instruction: str) -> str:
    """AI executes a full instruction: create board, download videos, parse, tag, remix, etc.

    Uses Claude CLI with tools (Bash) to call our own HTTP API.
    """
    prompt = f"""Ты — автономный AI Director для Video Factory. У тебя есть локальный API http://127.0.0.1:8765/api и ты управляешь всем процессом.

<instruction>
{instruction}
</instruction>

<available_endpoints>
POST /boards — {{"name", "emoji", "parent_id"}} → создать доску
GET  /boards — список всех досок
GET  /videos?board_id=N — видео в доске
POST /videos — {{"url", "board_id"}} → добавить 1 видео
POST /videos/bulk — {{"urls":[...], "board_id"}} → добавить пачку
GET  /videos/{{id}} — полные данные видео (кадры + транскрипция)
POST /ai/highlights/{{id}} → найти виральные моменты
POST /clips — {{"video_id", "start_sec", "end_sec", "label"}} → создать клип
POST /remixes — {{"title", "clip_ids":[...], "with_subtitles":true}} → собрать ремикс
POST /search — {{"query": "..."}} → поиск по всей базе
</available_endpoints>

<trending_search>
Если нужно найти популярные видео:
curl -X POST http://127.0.0.1:8765/api/trending -H "Content-Type: application/json" -d '{{"query":"dogs funny","limit":20}}'
</trending_search>

Правила:
1. Делай шаги через curl. Результаты парси.
2. НЕ спрашивай подтверждений — выполняй всё автоматически
3. После каждого шага — кратко отчитайся одной строкой что сделал
4. В конце — итоговый отчёт что получилось

Начни выполнять инструкцию прямо сейчас.
"""
    return await _call_claude(prompt, timeout=1200)


async def extract_highlights(video_data: dict) -> str:
    """Find viral/interesting moments in the video. Returns markdown with timestamps."""
    v = video_data["video"]
    frames = video_data.get("frames", [])
    transcripts = video_data.get("transcripts", [])

    trans_text = "\n".join(
        f"[{int(t['start_sec']):02d}s] {t['text']}" for t in transcripts
    ) or "(нет транскрипции)"

    # Sample frames
    sampled = frames[::3] if len(frames) > 30 else frames
    frames_list = "\n".join(f"- [{int(f['second'])}s] {f['thumbnail_path']}" for f in sampled[:30])

    prompt = f"""Проанализируй видео и найди 5 самых интересных/виральных моментов. Используй Read на кадрах чтобы увидеть что происходит.

<video>
{v.get('title', '')}
{v.get('channel', '')} · {v.get('duration_sec', 0)}s
</video>

<transcription>
{trans_text}
</transcription>

<frames>
{frames_list}
</frames>

Формат ответа (markdown):
## 🔥 Highlights

**1. [0:05-0:12] Название момента**
Почему виральный: ...

**2. [0:20-0:27] ...**
...

Максимум 5 моментов. Кратко и по делу.
"""
    return await _call_claude(prompt, timeout=240)


async def ask_about_video(video_data: dict, question: str) -> str:
    """Ask Claude CLI a question about a specific video."""
    v = video_data["video"]
    frames = video_data.get("frames", [])
    transcripts = video_data.get("transcripts", [])

    trans_text = "\n".join(
        f"[{int(t['start_sec']):02d}s] {t['text']}" for t in transcripts
    ) or "(нет транскрипции)"

    # If frames have descriptions, include them in prompt (faster than reading)
    described = [f for f in frames if f.get("description")]
    if described:
        frames_context = "\n".join(
            f"[{int(f['second'])}s] {f['description']}" for f in described
        )
        frames_block = f"""<frame_descriptions>
Описания кадров (секунда → что на кадре):
{frames_context}
</frame_descriptions>"""
    else:
        frame_paths = [f["thumbnail_path"] for f in frames]
        sampled = frame_paths[::5] if len(frame_paths) > 20 else frame_paths
        frames_list = "\n".join(f"- {p}" for p in sampled[:20])
        frames_block = f"""<frames_available>
Пути к кадрам. Используй Read чтобы посмотреть нужные:
{frames_list}
</frames_available>"""

    prompt = f"""Ты анализируешь видео в Video Factory. Отвечай на русском, кратко.

<video>
{v.get('title', 'unknown')}
{v.get('channel', 'unknown')} · {v.get('duration_sec', 0)}s · {len(frames)} кадров
</video>

<transcription>
{trans_text}
</transcription>

{frames_block}

<question>
{question}
</question>

Правила:
- Кратко, по делу, максимум 400 слов
- Если кадры описаны — используй описания
- Если описаний нет — можешь прочитать нужные через Read
"""
    return await _call_claude(prompt, timeout=180)
