"""SQLite storage: every track ever served, banned artists, settings, and discovery paging state."""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "music.db"))

DEFAULT_SETTINGS = {
    "sources": {"audius": True, "jamendo": True, "youtube": False},
    "max_bpm": 110,             # tracks with a reported BPM above this are skipped
    "min_duration": 90,         # seconds
    "max_duration": 900,        # seconds, single tracks (Audius / Jamendo)
    "youtube_mixes": True,      # allow long YouTube mixes (most AI study music is published this way)
    "youtube_max_duration": 4 * 3600,
    "extra_queries": [],        # your own search phrases, mixed into discovery
    "keywords_off": [],         # built-in search phrases you switched off
    "blocked_words": [],        # extra words that reject a track if found in title/tags
}

_lock = threading.Lock()
_conn = None


def conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS history (
                key TEXT PRIMARY KEY,          -- source:id
                fingerprint TEXT NOT NULL,     -- normalized artist|title, catches re-uploads
                source TEXT, source_id TEXT, title TEXT, artist TEXT,
                url TEXT, artwork TEXT, duration INTEGER,
                served_at REAL, rating INTEGER DEFAULT 0,   -- 1 liked, -1 disliked
                skipped INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_hist_fp ON history(fingerprint);
            CREATE TABLE IF NOT EXISTS banned_artists (artist TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS tempo (key TEXT PRIMARY KEY, bpm REAL);  -- measured BPM cache
            """
        )
    return _conn


def kv_get(k, default=None):
    row = conn().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def kv_set(k, v):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))
        conn().commit()


def tempo_get(key):
    row = conn().execute("SELECT bpm FROM tempo WHERE key=?", (key,)).fetchone()
    return row["bpm"] if row else None


def tempo_set(key, bpm):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO tempo(key,bpm) VALUES(?,?)", (key, bpm))
        conn().commit()


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(kv_get("settings", {}))
    s["sources"] = {**DEFAULT_SETTINGS["sources"], **s.get("sources", {})}
    return s


def save_settings(new):
    s = get_settings()
    s.update({k: v for k, v in new.items() if k in DEFAULT_SETTINGS})
    kv_set("settings", s)
    return s


def is_heard(key, fingerprint):
    return conn().execute(
        "SELECT 1 FROM history WHERE key=? OR fingerprint=? LIMIT 1", (key, fingerprint)
    ).fetchone() is not None


def is_banned(artist):
    return conn().execute(
        "SELECT 1 FROM banned_artists WHERE artist=?", ((artist or "").lower(),)
    ).fetchone() is not None


def mark_served(t):
    with _lock:
        conn().execute(
            """INSERT OR IGNORE INTO history
               (key,fingerprint,source,source_id,title,artist,url,artwork,duration,served_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (t["key"], t["fingerprint"], t["source"], t["source_id"], t["title"], t["artist"],
             t["url"], t["artwork"], t["duration"], time.time()),
        )
        conn().commit()


def feedback(key, rating=None, skipped=None, ban_artist=False):
    with _lock:
        c = conn()
        if rating is not None:
            c.execute("UPDATE history SET rating=? WHERE key=?", (rating, key))
        if skipped is not None:
            c.execute("UPDATE history SET skipped=? WHERE key=?", (1 if skipped else 0, key))
        if ban_artist:
            row = c.execute("SELECT artist FROM history WHERE key=?", (key,)).fetchone()
            if row and row["artist"]:
                c.execute("INSERT OR IGNORE INTO banned_artists VALUES (?)", (row["artist"].lower(),))
        c.commit()


def history(limit=200, liked_only=False):
    q = "SELECT * FROM history"
    if liked_only:
        q += " WHERE rating=1"
    q += " ORDER BY served_at DESC LIMIT ?"
    return [dict(r) for r in conn().execute(q, (limit,)).fetchall()]


def stats():
    c = conn()
    one = lambda q: c.execute(q).fetchone()[0]
    return {
        "heard": one("SELECT COUNT(*) FROM history"),
        "liked": one("SELECT COUNT(*) FROM history WHERE rating=1"),
        "skipped": one("SELECT COUNT(*) FROM history WHERE skipped=1"),
        "banned_artists": one("SELECT COUNT(*) FROM banned_artists"),
        "seconds": one("SELECT COALESCE(SUM(duration),0) FROM history WHERE skipped=0"),
    }


def banned_list():
    return [r[0] for r in conn().execute("SELECT artist FROM banned_artists ORDER BY artist")]


def unban(artist):
    with _lock:
        conn().execute("DELETE FROM banned_artists WHERE artist=?", (artist.lower(),))
        conn().commit()
