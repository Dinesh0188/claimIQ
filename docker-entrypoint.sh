#!/bin/sh
# Seeds a fresh, empty persistent volume from the image's baked-in snapshot.
#
# Why this exists: the image bakes data/ in at build time (COPY data/ ./data/ in the
# Dockerfile) so a plain `docker run` with no volume works standalone. But a Render
# disk (or any bind-mounted volume) is EMPTY on its first attach and mounts *over*
# /app/data, hiding everything the image put there -- the seeded claimiq.db, the
# sample claims, the trace history. Without this script, the very first boot against
# a fresh disk would look like data loss.
#
# So the baked-in snapshot ships at /app/data-seed (see Dockerfile) instead of
# /app/data directly, and this script copies it into the mounted volume exactly once,
# the first time the volume is empty. Every boot after that leaves the volume alone --
# a real ledger and real audit history must never be silently overwritten by a stale
# baked-in copy on redeploy.

set -e

SEED_DIR="/app/data-seed"
DATA_DIR="/app/data"

if [ -d "$SEED_DIR" ] && [ ! -f "$DATA_DIR/.seeded" ]; then
  echo "First boot against this volume -- seeding $DATA_DIR from the image snapshot..."
  cp -rn "$SEED_DIR"/. "$DATA_DIR"/ 2>/dev/null || true
  touch "$DATA_DIR/.seeded"
  echo "Seed complete."
fi

exec uvicorn claimiq.api:app --host 0.0.0.0 --port "${PORT:-8000}"
