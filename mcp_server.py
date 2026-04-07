#!/usr/bin/env python3
"""
Video Factory MCP server.

Exposes Video Factory HTTP API as MCP tools so any MCP-compatible agent
(Claude Code, Telegram bot, etc.) can manage videos, boards, clips, and remixes.

Run via stdio transport (Claude CLI / agent picks this up automatically when
configured in mcp-config.json).
"""
import asyncio
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

API_BASE = os.environ.get("VIDEO_FACTORY_URL", "http://127.0.0.1:8765/api")
TIMEOUT = 1800  # seconds — long enough for AI Director / parsing

server = Server("video-factory")


def _http_call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> str:
    """Synchronous HTTP call to Video Factory API. Returns response text."""
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return json.dumps({"error": f"HTTP {e.code}: {body_text}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


async def _async_call(method: str, path: str, body=None, params=None) -> str:
    """Run blocking HTTP call in a thread so MCP event loop stays responsive."""
    return await asyncio.to_thread(_http_call, method, path, body, params)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="vf_list_boards",
            description="List all boards (folders for videos) in Video Factory. Returns tree of boards with parent_id, names, emojis.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="vf_create_board",
            description="Create a new board (folder). Boards can be nested via parent_id. Use this to organize videos by topic.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Board name (required)"},
                    "emoji": {"type": "string", "description": "Emoji icon, e.g. 🐶 🎬 🍳", "default": "🎬"},
                    "parent_id": {"type": ["integer", "null"], "description": "Parent board id for nesting. Null for root."},
                    "description": {"type": "string", "default": ""},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="vf_delete_board",
            description="Delete a board by id. Videos inside become unassigned.",
            inputSchema={"type": "object", "properties": {"board_id": {"type": "integer"}}, "required": ["board_id"]},
        ),
        Tool(
            name="vf_list_videos",
            description="List videos. Optionally filter by board_id. Returns title, channel, duration, status, view_count for each.",
            inputSchema={
                "type": "object",
                "properties": {"board_id": {"type": ["integer", "null"], "description": "Filter by board, null for all"}},
            },
        ),
        Tool(
            name="vf_get_video",
            description="Get full video details: metadata + all extracted frames (with AI descriptions) + transcription. Use this to inspect what's in a specific video.",
            inputSchema={
                "type": "object",
                "properties": {"video_id": {"type": "integer"}},
                "required": ["video_id"],
            },
        ),
        Tool(
            name="vf_add_video",
            description="Add a single YouTube URL to a board. Triggers full pipeline: download → extract frames → transcribe → AI vision tag. Returns video id immediately, processing happens in background.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "YouTube URL"},
                    "board_id": {"type": ["integer", "null"], "description": "Target board, null for Inbox"},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="vf_bulk_import",
            description="Add many YouTube URLs at once to a board. They will be processed in parallel (3 concurrent). Use this when you have a list of videos to dump in.",
            inputSchema={
                "type": "object",
                "properties": {
                    "urls": {"type": "array", "items": {"type": "string"}, "description": "List of YouTube URLs"},
                    "board_id": {"type": ["integer", "null"]},
                },
                "required": ["urls"],
            },
        ),
        Tool(
            name="vf_move_video",
            description="Move a video to a different board.",
            inputSchema={
                "type": "object",
                "properties": {
                    "video_id": {"type": "integer"},
                    "board_id": {"type": "integer"},
                },
                "required": ["video_id", "board_id"],
            },
        ),
        Tool(
            name="vf_search",
            description="Search across all videos by frame descriptions and transcription text. Returns matching frames + transcript segments with timestamps. Use this to find specific moments across the entire library.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (matches frame descriptions and transcripts)"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="vf_trending",
            description="Search YouTube for trending videos by keyword. Returns top videos sorted by view count. Use this to find viral content to import.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "YouTube search query"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="vf_ask_video",
            description="Ask Claude a question about a specific video. Claude has access to the video's transcription and AI-generated frame descriptions. Good for: 'describe this video', 'find moments where X happens', 'suggest titles', 'what is the hook?'",
            inputSchema={
                "type": "object",
                "properties": {
                    "video_id": {"type": "integer"},
                    "question": {"type": "string"},
                },
                "required": ["video_id", "question"],
            },
        ),
        Tool(
            name="vf_extract_highlights",
            description="Use AI to find 5 most viral/interesting moments in a video with timestamps. Returns markdown with timestamped highlights.",
            inputSchema={
                "type": "object",
                "properties": {"video_id": {"type": "integer"}},
                "required": ["video_id"],
            },
        ),
        Tool(
            name="vf_describe_frames",
            description="Run Claude Vision on every frame of a video to generate descriptions. Auto-runs during initial parsing, but you can re-run it.",
            inputSchema={
                "type": "object",
                "properties": {"video_id": {"type": "integer"}},
                "required": ["video_id"],
            },
        ),
        Tool(
            name="vf_create_clip",
            description="Mark a sub-range in a video as a reusable clip. Used as building block for remixes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "video_id": {"type": "integer"},
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "label": {"type": "string", "default": ""},
                    "tags": {"type": "string", "default": ""},
                    "notes": {"type": "string", "default": ""},
                    "board_id": {"type": ["integer", "null"]},
                },
                "required": ["video_id", "start_sec", "end_sec"],
            },
        ),
        Tool(
            name="vf_list_clips",
            description="List clips, optionally filtered by video_id or board_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "video_id": {"type": ["integer", "null"]},
                    "board_id": {"type": ["integer", "null"]},
                },
            },
        ),
        Tool(
            name="vf_create_remix",
            description="Build a remix by concatenating clips into one mp4. Optionally burns subtitles from transcription. Returns remix id; processing happens in background.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "clip_ids": {"type": "array", "items": {"type": "integer"}, "description": "List of existing clip ids"},
                    "manual_clips": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "video_id": {"type": "integer"},
                                "start_sec": {"type": "number"},
                                "end_sec": {"type": "number"},
                            },
                        },
                        "description": "Or pass ad-hoc clips without saving them first",
                    },
                    "with_subtitles": {"type": "boolean", "default": False},
                    "board_id": {"type": ["integer", "null"]},
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="vf_list_remixes",
            description="List built remixes with their status and output paths.",
            inputSchema={
                "type": "object",
                "properties": {"board_id": {"type": ["integer", "null"]}},
            },
        ),
        Tool(
            name="vf_director",
            description="HIGH-LEVEL: Hand off a complete instruction to the Video Factory AI Director, which will autonomously execute multi-step workflows (create boards, find trending, download, parse, extract highlights, build remixes). Use when you want one-shot pipelines like 'create Dogs board, find 20 trending shorts, build a compilation'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "Plain English description of the full task"},
                },
                "required": ["instruction"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch tool calls to the HTTP API."""
    try:
        if name == "vf_list_boards":
            result = await _async_call("GET", "/boards")

        elif name == "vf_create_board":
            result = await _async_call("POST", "/boards", body={
                "name": arguments["name"],
                "emoji": arguments.get("emoji", "🎬"),
                "parent_id": arguments.get("parent_id"),
                "description": arguments.get("description", ""),
            })

        elif name == "vf_delete_board":
            result = await _async_call("DELETE", f"/boards/{arguments['board_id']}")

        elif name == "vf_list_videos":
            result = await _async_call("GET", "/videos", params={"board_id": arguments.get("board_id")})

        elif name == "vf_get_video":
            result = await _async_call("GET", f"/videos/{arguments['video_id']}")

        elif name == "vf_add_video":
            result = await _async_call("POST", "/videos", body={
                "url": arguments["url"],
                "board_id": arguments.get("board_id"),
            })

        elif name == "vf_bulk_import":
            result = await _async_call("POST", "/videos/bulk", body={
                "urls": arguments["urls"],
                "board_id": arguments.get("board_id"),
            })

        elif name == "vf_move_video":
            result = await _async_call("PATCH", f"/videos/{arguments['video_id']}/move", body={
                "board_id": arguments["board_id"],
            })

        elif name == "vf_search":
            result = await _async_call("POST", "/search", body={
                "query": arguments["query"],
                "limit": arguments.get("limit", 50),
            })

        elif name == "vf_trending":
            result = await _async_call("POST", "/trending", body={
                "query": arguments["query"],
                "limit": arguments.get("limit", 20),
            })

        elif name == "vf_ask_video":
            result = await _async_call("POST", "/ai/ask", body={
                "video_id": arguments["video_id"],
                "question": arguments["question"],
            })

        elif name == "vf_extract_highlights":
            result = await _async_call("POST", f"/ai/highlights/{arguments['video_id']}")

        elif name == "vf_describe_frames":
            result = await _async_call("POST", f"/ai/describe_frames/{arguments['video_id']}")

        elif name == "vf_create_clip":
            result = await _async_call("POST", "/clips", body={
                "video_id": arguments["video_id"],
                "start_sec": arguments["start_sec"],
                "end_sec": arguments["end_sec"],
                "label": arguments.get("label", ""),
                "tags": arguments.get("tags", ""),
                "notes": arguments.get("notes", ""),
                "board_id": arguments.get("board_id"),
            })

        elif name == "vf_list_clips":
            result = await _async_call("GET", "/clips", params={
                "video_id": arguments.get("video_id"),
                "board_id": arguments.get("board_id"),
            })

        elif name == "vf_create_remix":
            result = await _async_call("POST", "/remixes", body={
                "title": arguments["title"],
                "clip_ids": arguments.get("clip_ids", []),
                "manual_clips": arguments.get("manual_clips", []),
                "with_subtitles": arguments.get("with_subtitles", False),
                "board_id": arguments.get("board_id"),
            })

        elif name == "vf_list_remixes":
            result = await _async_call("GET", "/remixes", params={"board_id": arguments.get("board_id")})

        elif name == "vf_director":
            result = await _async_call("POST", "/ai/director", body={
                "instruction": arguments["instruction"],
            })

        else:
            result = json.dumps({"error": f"Unknown tool: {name}"})

        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": f"{type(e).__name__}: {e}"}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
