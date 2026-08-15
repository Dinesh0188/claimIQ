# ClaimIQ backend image.
#
# Builds the FastAPI engine (claimiq/) for deployment to Render, Fly.io, Railway or
# any other container host. This image does NOT include the React frontend --
# that is deployed separately to Vercel and talks to this container over HTTPS.
#
# The bundled static UI under web/ is still served by this same app at "/", so the
# image is also a complete standalone demo if you only deploy this one container.

FROM python:3.12-slim AS base

# System libraries needed by the document pipeline:
#   libgomp1        onnxruntime (fastembed, rapidocr) OpenMP runtime
#   libglib2.0-0,
#   libsm6, libxext6, libxrender1   rapidocr's OpenCV dependency chain
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first so this layer caches across code-only changes.
# requirements-api.txt is requirements.txt minus streamlit/plotly, which belong to
# the legacy ui/ Streamlit app and are never imported by claimiq.api -- dropping them
# noticeably cuts image size and build time on a free-tier container host.
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Application code and the corpus/static assets, which never change at runtime.
COPY claimiq/ ./claimiq/
COPY corpus/ ./corpus/
COPY web/ ./web/
# Only the embeddings cache -- .cache/llm is a local dev cache of past LLM responses
# keyed by content hash, not something to bake into a shipped image.
COPY .cache/embeddings/ ./.cache/embeddings/

# The seeded database, sample claims and trace history go to data-seed rather than
# data/ directly. data/ is where a mounted volume (Render disk, Fly volume, etc.)
# attaches -- and a fresh volume is empty on first mount, so anything COPY'd straight
# to data/ would be invisible the moment persistence is turned on. docker-entrypoint.sh
# copies this seed into data/ exactly once, on the first boot against an empty volume.
COPY data/ ./data-seed/
RUN mkdir -p ./data
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Render, Fly and Railway all inject $PORT; default to 8000 for `docker run` locally.
ENV PORT=8000
EXPOSE 8000

# docker-entrypoint.sh binds uvicorn to $CLAIMIQ_BIND, which defaults to 127.0.0.1
# (the bare-metal `start.ps1` posture). Container platforms set CLAIMIQ_BIND=0.0.0.0
# so their edge proxy can reach the app -- a container's loopback is not reachable
# from the host or the platform's edge proxy.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
