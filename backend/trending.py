"""Trending YouTube scraper via yt-dlp search."""
import asyncio
import json
from pipeline import _run, YT_DLP


async def search_youtube(query: str, limit: int = 20) -> list:
    """Search YouTube for videos matching query. Returns list of {url, title, views, duration}."""
    cmd = [
        YT_DLP,
        f"ytsearch{limit}:{query}",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
    ]
    stdout, stderr, code = await _run(cmd, timeout=60)
    if code != 0:
        raise RuntimeError(f"yt-dlp search failed: {stderr[:300]}")

    results = []
    for line in stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            info = json.loads(line)
            results.append({
                "url": info.get("url") or f"https://youtube.com/watch?v={info.get('id')}",
                "title": info.get("title", ""),
                "channel": info.get("uploader") or info.get("channel", ""),
                "duration": info.get("duration", 0),
                "view_count": info.get("view_count", 0),
            })
        except Exception:
            continue
    # Sort by views descending
    results.sort(key=lambda x: x.get("view_count") or 0, reverse=True)
    return results


async def fetch_trending_shorts(query: str, limit: int = 20) -> list:
    """Search for YouTube Shorts on a topic (short videos preferred)."""
    return await search_youtube(f"{query} #shorts", limit)
