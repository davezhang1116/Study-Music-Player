FROM python:3.12-slim

# deno lets yt-dlp solve YouTube's JavaScript challenges (only needed for the YouTube source)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV DB_PATH=/data/music.db YTDLP_JS_RUNTIME=deno PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

# YouTube changes often; refresh yt-dlp on every start so the YouTube source keeps working
CMD sh -c 'pip install -q -U "yt-dlp[default]" || true; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers'
