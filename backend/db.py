"""SQLite schema and helpers."""
import sqlite3
import os
import json
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "factory.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER REFERENCES boards(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            emoji TEXT DEFAULT '🎬',
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER REFERENCES boards(id) ON DELETE SET NULL,
            youtube_url TEXT,
            title TEXT,
            channel TEXT,
            duration_sec REAL,
            local_path TEXT,
            thumbnail_path TEXT,
            status TEXT DEFAULT 'pending',
            view_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            second REAL NOT NULL,
            thumbnail_path TEXT,
            description TEXT,
            tags TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            text TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            board_id INTEGER REFERENCES boards(id) ON DELETE SET NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            label TEXT,
            tags TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS remixes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER REFERENCES boards(id) ON DELETE SET NULL,
            title TEXT,
            clips_json TEXT,
            output_path TEXT,
            with_subtitles INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_frames_video ON frames(video_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_transcripts_video ON transcripts(video_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_videos_board ON videos(board_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_clips_board ON clips(board_id)")

    # Seed default boards if empty
    row = c.execute("SELECT COUNT(*) AS n FROM boards").fetchone()
    if row["n"] == 0:
        c.execute("INSERT INTO boards (name, emoji) VALUES (?, ?)", ("Inbox", "📥"))

    conn.commit()
    conn.close()


def list_boards():
    conn = get_db()
    rows = conn.execute("SELECT * FROM boards ORDER BY parent_id NULLS FIRST, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_board(name, emoji="🎬", parent_id=None, description=""):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO boards (name, emoji, parent_id, description) VALUES (?, ?, ?, ?)",
        (name, emoji, parent_id, description)
    )
    board_id = c.lastrowid
    conn.commit()
    conn.close()
    return board_id


def list_videos(board_id=None):
    conn = get_db()
    if board_id is not None:
        rows = conn.execute(
            "SELECT * FROM videos WHERE board_id = ? ORDER BY created_at DESC",
            (board_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM videos ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_video(video_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    frames = conn.execute(
        "SELECT * FROM frames WHERE video_id = ? ORDER BY second",
        (video_id,)
    ).fetchall()
    transcripts = conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? ORDER BY start_sec",
        (video_id,)
    ).fetchall()
    conn.close()
    if not row:
        return None
    return {
        "video": dict(row),
        "frames": [dict(f) for f in frames],
        "transcripts": [dict(t) for t in transcripts],
    }


def create_video(board_id, youtube_url, title="", status="pending"):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO videos (board_id, youtube_url, title, status) VALUES (?, ?, ?, ?)",
        (board_id, youtube_url, title, status)
    )
    vid = c.lastrowid
    conn.commit()
    conn.close()
    return vid


def update_video(video_id, **fields):
    if not fields:
        return
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [video_id]
    conn.execute(f"UPDATE videos SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def move_video(video_id, new_board_id):
    conn = get_db()
    conn.execute("UPDATE videos SET board_id = ? WHERE id = ?", (new_board_id, video_id))
    conn.commit()
    conn.close()


def delete_board(board_id):
    conn = get_db()
    conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
    conn.commit()
    conn.close()


def add_frame(video_id, second, thumbnail_path, description="", tags=""):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO frames (video_id, second, thumbnail_path, description, tags) VALUES (?, ?, ?, ?, ?)",
        (video_id, second, thumbnail_path, description, tags)
    )
    conn.commit()
    conn.close()


def update_frame_description(frame_id, description, tags=""):
    conn = get_db()
    conn.execute(
        "UPDATE frames SET description = ?, tags = ? WHERE id = ?",
        (description, tags, frame_id)
    )
    conn.commit()
    conn.close()


def list_frames(video_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM frames WHERE video_id = ? ORDER BY second",
        (video_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLIPS ──
def create_clip(video_id, start_sec, end_sec, label="", tags="", notes="", board_id=None):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO clips (video_id, board_id, start_sec, end_sec, label, tags, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (video_id, board_id, start_sec, end_sec, label, tags, notes)
    )
    cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid


def list_clips(video_id=None, board_id=None):
    conn = get_db()
    if video_id is not None:
        rows = conn.execute("SELECT * FROM clips WHERE video_id = ? ORDER BY start_sec", (video_id,)).fetchall()
    elif board_id is not None:
        rows = conn.execute("SELECT * FROM clips WHERE board_id = ? ORDER BY created_at DESC", (board_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM clips ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_clip(clip_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_clip(clip_id):
    conn = get_db()
    conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    conn.commit()
    conn.close()


# ── REMIXES ──
def create_remix(board_id, title, clips_json, with_subtitles=False):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO remixes (board_id, title, clips_json, with_subtitles, status) VALUES (?, ?, ?, ?, 'pending')",
        (board_id, title, clips_json, int(with_subtitles))
    )
    rid = c.lastrowid
    conn.commit()
    conn.close()
    return rid


def update_remix(remix_id, **fields):
    if not fields:
        return
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [remix_id]
    conn.execute(f"UPDATE remixes SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def list_remixes(board_id=None):
    conn = get_db()
    if board_id is not None:
        rows = conn.execute("SELECT * FROM remixes WHERE board_id = ? ORDER BY created_at DESC", (board_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM remixes ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_remix(remix_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM remixes WHERE id = ?", (remix_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── CROSS-BOARD SEARCH ──
def search_frames(query, limit=50):
    """Full-text search in frame descriptions."""
    conn = get_db()
    q = f"%{query}%"
    rows = conn.execute(
        "SELECT f.*, v.title as video_title, v.board_id FROM frames f JOIN videos v ON v.id = f.video_id WHERE f.description LIKE ? LIMIT ?",
        (q, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_transcripts(query, limit=50):
    conn = get_db()
    q = f"%{query}%"
    rows = conn.execute(
        "SELECT t.*, v.title as video_title, v.board_id FROM transcripts t JOIN videos v ON v.id = t.video_id WHERE t.text LIKE ? LIMIT ?",
        (q, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_transcript(video_id, start_sec, end_sec, text):
    conn = get_db()
    conn.execute(
        "INSERT INTO transcripts (video_id, start_sec, end_sec, text) VALUES (?, ?, ?, ?)",
        (video_id, start_sec, end_sec, text)
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
