"""FastAPI server for Video Factory."""
import os
import asyncio
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import pipeline

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"
DATA = BASE / "data"

app = FastAPI(title="Video Factory")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DB on startup
db.init_db()


# ── Models ──
class BoardIn(BaseModel):
    name: str
    emoji: str = "🎬"
    parent_id: Optional[int] = None
    description: str = ""


class VideoIn(BaseModel):
    url: str
    board_id: Optional[int] = None


class AIAskIn(BaseModel):
    video_id: int
    question: str


class ClipIn(BaseModel):
    video_id: int
    start_sec: float
    end_sec: float
    label: str = ""
    tags: str = ""
    notes: str = ""
    board_id: Optional[int] = None


class RemixIn(BaseModel):
    board_id: Optional[int] = None
    title: str = "Untitled Remix"
    clip_ids: list = []
    manual_clips: list = []  # [{video_id, start_sec, end_sec}, ...]
    with_subtitles: bool = False


class BulkImportIn(BaseModel):
    urls: list
    board_id: Optional[int] = None


class SearchIn(BaseModel):
    query: str
    limit: int = 50


# ── Static files: frontend + data/frames + data/videos ──
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
app.mount("/data", StaticFiles(directory=str(DATA)), name="data")


# ── Routes ──
@app.get("/")
def root():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/boards")
def get_boards():
    return db.list_boards()


@app.post("/api/boards")
def post_board(b: BoardIn):
    board_id = db.create_board(b.name, b.emoji, b.parent_id, b.description)
    return {"id": board_id}


@app.get("/api/videos")
def get_videos(board_id: Optional[int] = None):
    return db.list_videos(board_id)


@app.patch("/api/videos/{video_id}/move")
def move_video(video_id: int, body: dict):
    new_board_id = body.get("board_id")
    db.move_video(video_id, new_board_id)
    return {"status": "ok"}


@app.delete("/api/boards/{board_id}")
def del_board(board_id: int):
    db.delete_board(board_id)
    return {"status": "deleted"}


@app.get("/api/videos/{video_id}")
def get_video(video_id: int):
    data = db.get_video(video_id)
    if not data:
        raise HTTPException(404, "video not found")
    return data


PARALLEL_LIMIT = 3  # max concurrent videos in pipeline
_pipeline_sem = asyncio.Semaphore(PARALLEL_LIMIT)


async def _process_in_background(video_id, url):
    async with _pipeline_sem:
        try:
            await pipeline.process_video(video_id, url)
        except Exception as e:
            print(f"[pipeline] video {video_id} failed: {e}")
            db.update_video(video_id, status=f"error: {str(e)[:200]}")


@app.post("/api/videos")
async def post_video(v: VideoIn, background: BackgroundTasks):
    # Default to Inbox board if none specified
    board_id = v.board_id
    if board_id is None:
        boards = db.list_boards()
        inbox = next((b for b in boards if b["name"] == "Inbox"), None)
        board_id = inbox["id"] if inbox else 1

    video_id = db.create_video(board_id, v.url, title="(loading...)", status="queued")
    # Run pipeline in background
    asyncio.create_task(_process_in_background(video_id, v.url))
    return {"id": video_id, "status": "queued"}


@app.post("/api/videos/{video_id}/reparse")
async def reparse_video(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(404)
    url = video["video"]["youtube_url"]
    asyncio.create_task(_process_in_background(video_id, url))
    return {"status": "reparsing"}


@app.post("/api/ai/ask")
async def ai_ask(body: AIAskIn):
    """Ask Claude about a specific video."""
    import ai
    data = db.get_video(body.video_id)
    if not data:
        raise HTTPException(404, "video not found")
    try:
        answer = await ai.ask_about_video(data, body.question)
        return {"answer": answer}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/describe_frames/{video_id}")
async def ai_describe_frames(video_id: int):
    """Describe every frame of the video using Claude vision. Saves descriptions to DB."""
    import ai
    frames = db.list_frames(video_id)
    if not frames:
        raise HTTPException(404, "no frames")

    # Process in batches of 10 to avoid too-long prompts
    batch_size = 10
    total_described = 0
    for i in range(0, len(frames), batch_size):
        batch = frames[i:i+batch_size]
        paths = [f["thumbnail_path"] for f in batch]
        descriptions = await ai.describe_frames_batch(paths)
        for frame, desc in zip(batch, descriptions):
            db.update_frame_description(frame["id"], desc)
            total_described += 1

    return {"described": total_described, "total_frames": len(frames)}


@app.post("/api/ai/highlights/{video_id}")
async def ai_highlights(video_id: int):
    """Extract viral highlights from a video."""
    import ai
    data = db.get_video(video_id)
    if not data:
        raise HTTPException(404)
    try:
        result = await ai.extract_highlights(data)
        return {"highlights": result}
    except Exception as e:
        return {"error": str(e)}


# ── CLIPS ──
@app.get("/api/clips")
def get_clips(video_id: Optional[int] = None, board_id: Optional[int] = None):
    return db.list_clips(video_id=video_id, board_id=board_id)


@app.post("/api/clips")
def post_clip(body: ClipIn):
    clip_id = db.create_clip(
        body.video_id, body.start_sec, body.end_sec,
        body.label, body.tags, body.notes, body.board_id
    )
    return {"id": clip_id}


@app.delete("/api/clips/{clip_id}")
def del_clip(clip_id: int):
    db.delete_clip(clip_id)
    return {"status": "deleted"}


# ── REMIXES ──
@app.get("/api/remixes")
def get_remixes(board_id: Optional[int] = None):
    return db.list_remixes(board_id)


@app.get("/api/remixes/{remix_id}")
def get_remix(remix_id: int):
    r = db.get_remix(remix_id)
    if not r:
        raise HTTPException(404)
    return r


async def _build_remix_bg(remix_id: int, manual_clips: list, with_subtitles: bool):
    try:
        db.update_remix(remix_id, status="building")
        out_path = await pipeline.build_remix(remix_id, manual_clips, with_subtitles)
        db.update_remix(remix_id, status="ready", output_path=out_path)
    except Exception as e:
        db.update_remix(remix_id, status=f"error: {str(e)[:200]}")


@app.post("/api/remixes")
async def post_remix(body: RemixIn):
    import json as _json
    manual_clips = list(body.manual_clips)
    # Expand clip_ids into manual_clips
    for cid in body.clip_ids:
        c = db.get_clip(cid)
        if c:
            manual_clips.append({
                "video_id": c["video_id"],
                "start_sec": c["start_sec"],
                "end_sec": c["end_sec"],
            })
    if not manual_clips:
        raise HTTPException(400, "no clips provided")

    remix_id = db.create_remix(
        body.board_id, body.title,
        _json.dumps(manual_clips),
        body.with_subtitles
    )
    asyncio.create_task(_build_remix_bg(remix_id, manual_clips, body.with_subtitles))
    return {"id": remix_id, "status": "queued"}


# ── BULK IMPORT ──
@app.post("/api/videos/bulk")
async def bulk_import(body: BulkImportIn):
    board_id = body.board_id
    if board_id is None:
        boards = db.list_boards()
        inbox = next((b for b in boards if b["name"] == "Inbox"), None)
        board_id = inbox["id"] if inbox else 1

    created = []
    for url in body.urls:
        url = url.strip()
        if not url:
            continue
        vid = db.create_video(board_id, url, title="(queued)", status="queued")
        asyncio.create_task(_process_in_background(vid, url))
        created.append(vid)
    return {"queued": len(created), "ids": created}


# ── SEARCH ──
@app.post("/api/search")
def search(body: SearchIn):
    frames = db.search_frames(body.query, body.limit)
    transcripts = db.search_transcripts(body.query, body.limit)
    return {"frames": frames, "transcripts": transcripts}


# ── TRENDING ──
class TrendingIn(BaseModel):
    query: str
    limit: int = 20


@app.post("/api/trending")
async def trending(body: TrendingIn):
    import trending as tr
    try:
        videos = await tr.search_youtube(body.query, body.limit)
        return {"videos": videos}
    except Exception as e:
        return {"error": str(e)}


# ── AUTONOMOUS AI BOT ──
class DirectorIn(BaseModel):
    instruction: str


@app.post("/api/ai/director")
async def ai_director(body: DirectorIn):
    """Full autonomous pipeline: AI does everything by calling our own API."""
    import ai
    try:
        result = await ai.autonomous_pipeline(body.instruction)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
