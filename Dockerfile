FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLIPSENSE_CACHE_DIR=/data/cache \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[api,url,sounds]"

RUN useradd --create-home app && mkdir -p /data/cache && chown -R app /data
USER app
VOLUME /data

EXPOSE 8000
CMD ["sh", "-c", "clipsense serve --http --host 0.0.0.0 --port ${PORT}"]
