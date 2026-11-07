"""Decides whether a candidate track fits: slow, instrumental, guitar/bass leaning."""
import re
import unicodedata

# Any of these in title/tags means "probably has singing or is the wrong vibe".
VOCAL_WORDS = [
    "vocal", "vocals", "lyric", "lyrics", "feat", "ft", "featuring", "singing", "sing along",
    "rap", "rapper", "acapella", "a cappella", "karaoke", "choir", "r&b", "rnb", "song lyrics",
    "with lyrics", "music video", "official video", "nightcore", "sped up", "phonk", "drill",
    "trap", "edm", "dubstep", "hardstyle", "techno", "workout", "gym", "hype", "upbeat",
    "dance", "party", "metal", "rock anthem", "live stream", "24/7", "radio",
]
CALM_MOODS = {"peaceful", "melancholy", "tender", "sentimental", "romantic", "easygoing",
              "sophisticated", "cool", "brooding", "yearning", "gritty", "sensual"}
LOUD_MOODS = {"energizing", "aggressive", "fiery", "excited", "rowdy", "upbeat", "defiant",
              "stirring", "empowering"}
GOOD_WORDS = ["guitar", "bass", "acoustic", "fingerstyle", "instrumental", "slow", "chill",
              "lofi", "lo-fi", "ambient", "mellow", "calm", "study", "jazz", "nylon", "soft"]


def normalize(text):
    """Fold fancy unicode (𝙻𝚘𝚏𝚒 -> lofi), lowercase, keep letters/digits/spaces."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w&/ ]+", " ", text)).strip()


def fingerprint(artist, title):
    """artist|title with bracketed noise removed, so re-uploads of the same song match."""
    t = unicodedata.normalize("NFKC", title or "").lower()
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)          # (Official), [free download], ...
    t = re.sub(r"\b(free download|prod\.?.*|remaster(ed)?|lofi|lo-fi|instrumental|mix)\b", " ", t)
    t = re.sub(r"[^\w]+", "", t)
    a = re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", artist or "").lower())
    return f"{a}|{t}"


def _has_word(text, word):
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None


def check(track, settings):
    """Return (ok, score). Higher score = better fit."""
    text = normalize(" ".join([track.get("title", ""), track.get("tags", ""), track.get("genre", "")]))
    for w in VOCAL_WORDS + [b.lower() for b in settings.get("blocked_words", [])]:
        if w and _has_word(text, w):
            return False, 0

    dur = track.get("duration") or 0
    if track.get("is_mix"):
        if not settings.get("youtube_mixes") or not (20 * 60 <= dur <= settings["youtube_max_duration"]):
            return False, 0
    elif not (settings["min_duration"] <= dur <= settings["max_duration"]):
        return False, 0

    mood = (track.get("mood") or "").lower()
    if mood in LOUD_MOODS:
        return False, 0

    score = sum(1 for w in GOOD_WORDS if w in text)
    if "guitar" in text or "bass" in text:
        score += 3
    if "instrumental" in text or track.get("instrumental"):
        score += 2
    if mood in CALM_MOODS:
        score += 2

    bpm = track.get("bpm")
    if bpm:
        if bpm > settings["max_bpm"]:
            return False, 0
        if bpm <= 95:
            score += 2
    return score >= 2, score
