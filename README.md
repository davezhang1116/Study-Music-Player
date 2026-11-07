# Slow Study Radio

An endless web radio of slow, instrumental, guitar- and bass-leaning study music that **never plays the same track twice** and has **no ads**.

## How it works

- **Discovery** keeps searching many phrases ("slow guitar", "ambient guitar", "deep bass chill", …) and pages deeper over time, so it keeps reaching new music instead of the same top results.
- **Filtering** drops anything that looks like it has vocals (vocal, lyrics, feat., rap, R&B…), energetic moods, or a tempo above your limit (110 BPM by default). Guitar, bass, instrumental and calm moods are ranked first.
- **Tempo for every source**: Audius lists a BPM for most tracks. For YouTube, Jamendo, and Audius tracks without one, the server reads a short excerpt of the audio and measures the tempo (long mixes are checked at 3 points and the fastest one counts). Results are cached. Tracks whose tempo can't be measured are skipped. It's an estimate: very slow or beat-less music is sometimes read at half or double speed.
- **Never repeat**: every track that gets played is saved in a small SQLite database (`data/music.db`). A track is skipped forever if its ID *or* its normalized "artist + title" was played before, which also catches most re-uploads.
- **No ads**: none of the sources play through an ad-supported player:
  | Source | What it is | Key needed |
  |---|---|---|
  | **Audius** | Free music platform, lots of lo-fi / ambient / AI music, full tracks | No |
  | **Jamendo** | Free Creative Commons catalog with built-in "instrumental" and "slow" filters | Free key |
  | **YouTube** (off by default) | Audio only, fetched by *your server* with yt-dlp and passed through, so YouTube's player and its ads never load | No |

  Deezer and Spotify aren't included: without a paid account and their official player they only give 30-second previews.

## Controls

- **Skip**: next track (the skipped one still never comes back).
- **Dislike**: skip, and optionally block the artist entirely.
- **Like**: saves it to your liked list (History → Liked only) with a link back to the source, so you can find favorites even though autoplay won't repeat them.
- **Settings**: turn sources on/off, change max tempo and track length, tap keyword chips to choose what it searches for (turn built-in ones off, add suggestions or your own), tap to block words, unblock artists. Keywords drive Audius and YouTube searches; Jamendo uses its own genre tags.
- Space = play/pause, Shift+→ = skip. Works with media keys / lock screen. Sleep timer included.

## Run on your server (Docker)

```bash
cp .env.example .env      # add JAMENDO_CLIENT_ID if you have one
docker compose up -d --build
```

Open `http://your-server:8000`. There's no password by default, which is fine on a home network or behind a VPN. If you ever expose it to the internet, set `APP_PASSWORD` in `.env` so nobody else can use your server as a YouTube proxy.

### Jamendo key (optional, free, 2 minutes)

Sign up at <https://devportal.jamendo.com>, create an app, and put its **Client ID** in `.env` as `JAMENDO_CLIENT_ID`. Restart with `docker compose up -d`.

### YouTube source (optional)

Turn it on in Settings. Notes:
- Most AI study music on YouTube is uploaded as 1–4 hour mixes, so with "Allow long YouTube mixes" on you get whole mixes (each one still never repeats). Turn it off to only get single tracks.
- YouTube sometimes blocks data-center IPs. If YouTube tracks stop loading, the radio simply keeps going with the other sources; the error shows at the bottom of Settings. A home server usually works fine.
- yt-dlp is updated each time the container starts, since YouTube changes often.

## Run without Docker

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Environment variables: `APP_USER`, `APP_PASSWORD`, `JAMENDO_CLIENT_ID`, `DB_PATH` (default `data/music.db`), `YTDLP_JS_RUNTIME` (`deno` or `node`, helps yt-dlp with YouTube).

## Limits

- "Never the same song" is matched by ID and artist + title. If someone re-uploads the same audio under a completely different name, it can slip through.
- Vocal detection is based on titles, tags and moods, not on listening to the audio, so an occasional track with humming or a vocal sample can get through. Dislike it and, if needed, block the artist or add a word to "Blocked words".
