"""Slow Study Radio: endless, never-repeating, ad-free study music."""
import asyncio
import base64
import logging
import os
import random
import secrets
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import db, filters, sources, tempo

log = logging.getLogger("radio")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

APP_USER = os.environ.get("APP_USER", "me")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Slow Study Radio")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Optional password so strangers can't use your server as a YouTube proxy."""
    if APP_PASSWORD:
        expected = "Basic " + base64.b64encode(f"{APP_USER}:{APP_PASSWORD}".encode()).decode()
        if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="radio"'})
    return await call_next(request)


# ---------------- candidate pool ----------------
pool: list[dict] = []          # ready-to-play tracks, best first
pool_keys: set[str] = set()
refill_lock = asyncio.Lock()
source_errors: dict[str, str] = {}


def _prepare(t):
    t["key"] = f"{t['source']}:{t['source_id']}"
    t["fingerprint"] = filters.fingerprint(t["artist"], t["title"])
    return t


MEASURE_PER_ROUND = 8          # tracks without a BPM get measured, best candidates first
measure_sem = asyncio.Semaphore(3)


async def _measure(t):
    """Tempo for tracks whose source doesn't give one (YouTube, Jamendo, some Audius)."""
    cached = db.tempo_get(t["key"])
    if cached:
        return cached
    async with measure_sem:
        try:
            if t["source"] == "youtube":
                _, url, headers, _ = await _yt_resolve(t["source_id"])
            elif t["source"] == "jamendo":
                url, headers = t["stream"], None
            else:
                url, headers = sources.audius_stream_url(t["source_id"]), None
            bpm = await run_in_threadpool(tempo.measure, url, headers, t["duration"], t.get("is_mix"))
        except Exception as e:
            log.warning("tempo check failed for %s: %s", t["key"], e)
            source_errors["tempo"] = f"{t['source']}: {str(e)[:150]}"
            return None
    source_errors.pop("tempo", None)
    if bpm:
        db.tempo_set(t["key"], bpm)
    return bpm


async def refill(target=8, max_rounds=6):
    async with refill_lock:
        settings = db.get_settings()
        enabled = [s for s, on in settings["sources"].items() if on and s in sources.SOURCES]
        if not enabled:
            return
        rounds = 0
        while len(pool) < target and rounds < max_rounds:
            rounds += 1
            name = random.choices(enabled, weights=[sources.WEIGHTS[s] for s in enabled])[0]
            try:
                found = await sources.SOURCES[name](settings)
                source_errors.pop(name, None)
            except Exception as e:  # one broken source shouldn't stop the music
                log.warning("source %s failed: %s", name, e)
                source_errors[name] = str(e)[:200]
                continue
            fps = {p["fingerprint"] for p in pool}
            fits = []
            for t in map(_prepare, found):
                if t["key"] in pool_keys or t["fingerprint"] in fps:
                    continue
                if db.is_heard(t["key"], t["fingerprint"]) or db.is_banned(t["artist"]):
                    continue
                if not t.get("bpm"):
                    t["bpm"] = db.tempo_get(t["key"])
                    t["bpm_measured"] = bool(t["bpm"])
                ok, score = filters.check(t, settings)   # tempo is checked here when already known
                if not ok:
                    continue
                t["score"] = score + random.random() * 2   # a little shuffle
                fits.append(t)
                fps.add(t["fingerprint"])

            # no BPM from the source: measure it from the audio, then apply the tempo limit
            unknown = sorted((t for t in fits if not t.get("bpm")), key=lambda t: -t["score"])
            unknown = unknown[:MEASURE_PER_ROUND]
            bpms = await asyncio.gather(*(_measure(t) for t in unknown))
            for t, bpm in zip(unknown, bpms):
                t["bpm"], t["bpm_measured"] = bpm, True
            accepted = [t for t in fits if t.get("bpm") and filters.check(t, settings)[0]]

            for t in accepted:
                pool.append(t)
                pool_keys.add(t["key"])
            log.info("%s: %d found, %d fit, %d measured, %d added (pool=%d)",
                     name, len(found), len(fits), len(unknown), len(accepted), len(pool))
            # keep the best at the front
            pool.sort(key=lambda t: -t["score"])


def _take():
    if not pool:
        return None
    last = db.kv_get("last_artist", "")
    idx = next((i for i, t in enumerate(pool) if t["artist"] != last), 0)
    t = pool.pop(idx)
    pool_keys.discard(t["key"])
    db.kv_set("last_artist", t["artist"])
    return t


def _public(t):
    return {k: t.get(k) for k in ("key", "source", "title", "artist", "duration", "artwork", "url",
                                  "mood", "bpm", "bpm_measured", "genre")} | {
        "stream": f"/api/stream/{t['source']}/{t['source_id']}"}


@app.get("/api/next")
async def next_track():
    if not pool:
        await refill(target=1)
    t = _take()
    if not t:
        raise HTTPException(503, detail={"message": "No new tracks found right now", "errors": source_errors})
    db.mark_served(t)
    if t["source"] == "jamendo":
        jamendo_streams[t["source_id"]] = t["stream"]
    if len(pool) < 6:
        asyncio.create_task(refill())
    return _public(t)


@app.post("/api/feedback")
async def post_feedback(body: dict):
    rating = body.get("rating")
    db.feedback(body["key"], rating=rating, skipped=body.get("skipped"), ban_artist=bool(body.get("ban_artist")))
    if body.get("ban_artist"):
        banned = {a for a in db.banned_list()}
        pool[:] = [t for t in pool if (t["artist"] or "").lower() not in banned]
    return {"ok": True}


# ---------------- streaming ----------------
jamendo_streams: dict[str, str] = {}
yt_cache: dict[str, tuple[float, str, dict, str]] = {}
_yt_http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), follow_redirects=True)


async def _yt_resolve(sid):
    """Direct audio URL for a YouTube video, cached (shared by tempo check and playback)."""
    cached = yt_cache.get(sid)
    if not cached or cached[0] < time.time():
        url, headers, ctype = await run_in_threadpool(sources.youtube_audio_url_sync, sid)
        cached = (time.time() + 3 * 3600, url, headers, ctype)
        yt_cache[sid] = cached
    return cached


@app.get("/api/stream/{source}/{sid}")
async def stream(source: str, sid: str, request: Request):
    if source == "audius":
        return RedirectResponse(sources.audius_stream_url(sid))
    if source == "jamendo":
        url = jamendo_streams.get(sid) or (
            f"https://prod-1.storage.jamendo.com/?trackid={sid}&format=mp32")
        return RedirectResponse(url)
    if source != "youtube":
        raise HTTPException(404)

    # YouTube: we fetch the raw audio and pass it through, so the browser never loads YouTube's player (no ads).
    try:
        _, url, headers, ctype = await _yt_resolve(sid)
    except Exception as e:
        raise HTTPException(502, f"YouTube extraction failed: {e}")
    fwd = dict(headers)
    if "range" in request.headers:
        fwd["Range"] = request.headers["range"]
    req = _yt_http.build_request("GET", url, headers=fwd)
    upstream = await _yt_http.send(req, stream=True)
    if upstream.status_code >= 400:
        await upstream.aclose()
        yt_cache.pop(sid, None)
        raise HTTPException(502, f"YouTube returned {upstream.status_code}")
    out_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for h in ("content-length", "content-range"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers, media_type=ctype)


# ---------------- history / settings ----------------
@app.get("/api/history")
def get_history(liked: bool = False, limit: int = 200):
    return db.history(limit=limit, liked_only=liked)


@app.get("/api/stats")
def get_stats():
    return db.stats() | {"pool": len(pool), "source_errors": source_errors,
                         "jamendo_key": bool(sources.JAMENDO_CLIENT_ID)}


def _settings_payload(s):
    return s | {"banned_artists": db.banned_list(),
                "keyword_builtin": sources.BASE_QUERIES,
                "keyword_suggestions": sources.KEYWORD_SUGGESTIONS,
                "blocked_suggestions": sources.BLOCKED_SUGGESTIONS}


@app.get("/api/settings")
def get_settings():
    return _settings_payload(db.get_settings())


@app.put("/api/settings")
async def put_settings(body: dict):
    for a in body.pop("unban", []):
        db.unban(a)
    s = db.save_settings(body)
    pool.clear()          # rebuild the queue with the new rules
    pool_keys.clear()
    asyncio.create_task(refill())
    return _settings_payload(s)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({"name": "Slow Study Radio", "short_name": "Study Radio", "start_url": "/",
                         "display": "standalone", "background_color": "#14110f", "theme_color": "#14110f",
                         "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}]})
