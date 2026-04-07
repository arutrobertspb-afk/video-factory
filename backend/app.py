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


async def _process_in_background(video_id, url):
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
