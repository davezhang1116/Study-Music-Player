"""Music sources. Each `discover()` returns a list of normalized candidate tracks.

Discovery walks through many search phrases and keeps paging deeper over time
(paging state is stored in the DB), so you keep reaching new music instead of page 1 again.
"""
import os
import random

import httpx

from . import db

APP_NAME = os.environ.get("AUDIUS_APP_NAME", "slow-study-radio")
JAMENDO_CLIENT_ID = os.environ.get("JAMENDO_CLIENT_ID", "").strip()

BASE_QUERIES = [
    "slow guitar", "acoustic guitar instrumental", "lofi guitar", "ambient guitar", "chill guitar",
    "fingerstyle guitar", "mellow guitar", "jazz guitar lofi", "bass guitar chill", "soft guitar",
    "guitar study", "calm guitar", "nylon guitar", "guitar lofi beat", "slow bass", "deep bass chill",
    "guitar ambient", "electric guitar chill", "neo soul guitar instrumental", "guitar sleep",
]
# Shown as tappable chips in Settings, off until you tap them.
KEYWORD_SUGGESTIONS = [
    "ai study music", "bossa nova guitar", "classical guitar", "spanish guitar", "blues guitar slow",
    "jazz guitar", "upright bass jazz", "dub bass", "lofi bass", "post rock ambient", "cinematic guitar",
    "ambient drone", "slide guitar", "steel guitar", "folk guitar", "rainy day guitar",
    "night drive chill", "japanese lofi", "chillhop", "piano and guitar",
]
BLOCKED_SUGGESTIONS = [
    "piano", "rain sounds", "nature sounds", "asmr", "binaural", "christmas", "meditation",
    "sleep music", "8d audio", "remix", "cover", "drums",
]

_http = httpx.AsyncClient(timeout=20, follow_redirects=True,
                          headers={"User-Agent": "slow-study-radio/1.0"})


def _next_page(source, queries, page_size, max_offset):
    """Pick a random query and return (query, offset), advancing that query's offset."""
    state = db.kv_get(f"paging:{source}", {})
    q = random.choice(queries)
    off = state.get(q, 0)
    state[q] = off + page_size if off + page_size < max_offset else 0
    db.kv_set(f"paging:{source}", state)
    return q, off


def active_keywords(settings):
    """Built-in phrases you haven't switched off, plus the ones you added."""
    off = set(settings.get("keywords_off", []))
    qs = [q for q in BASE_QUERIES if q not in off]
    qs += [q for q in settings.get("extra_queries", []) if q.strip() and q not in qs]
    return qs or BASE_QUERIES   # never search with nothing


# ---------------- Audius: free, ad-free, full tracks, no API key ----------------
async def audius(settings):
    q, off = _next_page("audius", active_keywords(settings), 50, 500)
    r = await _http.get("https://api.audius.co/v1/tracks/search",
                        params={"query": q, "limit": 50, "offset": off, "app_name": APP_NAME})
    r.raise_for_status()
    out = []
    for t in r.json().get("data", []):
        if not t.get("is_streamable", True) or t.get("is_stream_gated") or t.get("is_delete"):
            continue
        art = t.get("artwork") or {}
        out.append({
            "source": "audius", "source_id": t["id"], "title": t.get("title", ""),
            "artist": (t.get("user") or {}).get("name", ""), "duration": t.get("duration") or 0,
            "tags": t.get("tags") or "", "genre": t.get("genre") or "", "mood": t.get("mood"),
            "bpm": t.get("bpm"), "artwork": art.get("480x480") or art.get("150x150") or "",
            "url": f"https://audius.co{t.get('permalink', '')}",
        })
    return out


def audius_stream_url(track_id):
    return f"https://api.audius.co/v1/tracks/{track_id}/stream?app_name={APP_NAME}"


# ---------------- Jamendo: free CC music, has instrumental + speed filters ----------------
JAMENDO_TAGS = ["guitar", "acoustic", "lounge", "chillout", "ambient", "jazz", "lofi", "bass", "easylistening"]


async def jamendo(settings):
    if not JAMENDO_CLIENT_ID:
        return []
    tag, off = _next_page("jamendo", JAMENDO_TAGS, 50, 2000)
    params = {
        "client_id": JAMENDO_CLIENT_ID, "format": "json", "limit": 50, "offset": off,
        "fuzzytags": f"{tag}+guitar" if tag != "guitar" else "guitar+chillout",
        "vocalinstrumental": "instrumental", "speed": "verylow+low", "include": "musicinfo",
        "audioformat": "mp32", "order": "popularity_total",
    }
    r = await _http.get("https://api.jamendo.com/v3.0/tracks/", params=params)
    r.raise_for_status()
    out = []
    for t in r.json().get("results", []):
        mi = t.get("musicinfo") or {}
        tags = mi.get("tags") or {}
        if not t.get("audio"):
            continue
        out.append({
            "source": "jamendo", "source_id": str(t["id"]), "title": t.get("name", ""),
            "artist": t.get("artist_name", ""), "duration": t.get("duration") or 0,
            "tags": " ".join(tags.get("genres", []) + tags.get("instruments", []) + tags.get("vartags", [])),
            "genre": "", "mood": None, "bpm": None, "instrumental": True,
            "artwork": t.get("image") or t.get("album_image") or "",
            "url": t.get("shareurl") or "", "stream": t["audio"],
        })
    return out


# ---------------- YouTube via yt-dlp: audio proxied through your server = no ads ----------------
def _yt_opts(extra=None):
    o = {"quiet": True, "no_warnings": True, "skip_download": True}
    rt = os.environ.get("YTDLP_JS_RUNTIME")  # e.g. "deno" (Docker image) or "node"
    if rt:
        o["js_runtimes"] = {rt: {}}
    o.update(extra or {})
    return o


def _youtube_search_sync(query, n):
    import yt_dlp
    with yt_dlp.YoutubeDL(_yt_opts({"extract_flat": True})) as y:
        return y.extract_info(f"ytsearch{n}:{query}", download=False).get("entries") or []


async def youtube(settings):
    from starlette.concurrency import run_in_threadpool
    kw, off = _next_page("youtube", active_keywords(settings), 20, 100)
    q = kw if "instrumental" in kw else f"{kw} instrumental"   # steer YouTube away from vocals
    # ytsearch has no offset, so ask for more results the deeper we go
    entries = await run_in_threadpool(_youtube_search_sync, q, off + 20)
    out = []
    for e in entries[off:]:
        if not e or not e.get("id") or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        dur = e.get("duration") or 0
        thumbs = e.get("thumbnails") or []
        out.append({
            "source": "youtube", "source_id": e["id"], "title": e.get("title", ""),
            "artist": e.get("channel") or e.get("uploader") or "", "duration": int(dur),
            "tags": "", "genre": "", "mood": None, "bpm": None, "is_mix": dur > 15 * 60,
            "artwork": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg" if not thumbs else thumbs[-1]["url"],
            "url": f"https://www.youtube.com/watch?v={e['id']}",
        })
    return out


def youtube_audio_url_sync(video_id):
    import yt_dlp
    with yt_dlp.YoutubeDL(_yt_opts({"format": "bestaudio[ext=m4a]/bestaudio"})) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    return info["url"], info.get("http_headers") or {}, "audio/mp4" if info.get("ext") == "m4a" else "audio/webm"


SOURCES = {"audius": audius, "jamendo": jamendo, "youtube": youtube}
WEIGHTS = {"audius": 5, "jamendo": 3, "youtube": 2}
