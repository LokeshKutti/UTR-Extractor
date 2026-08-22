# Public-deployment image. The desktop build (utrextractor.spec) is a
# separate, unrelated path for handing someone an .exe that runs entirely on
# their own PC -- this image is for running the same app as a server that a
# browser somewhere else talks to over the network.
FROM python:3.13-slim

WORKDIR /app

# opencv-python-headless still resolves a couple of shared libraries through
# glib at import time even with no GUI code in play; without this the first
# `import cv2` inside core.py fails with a missing .so rather than anything
# that points at the real cause.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core.py medical.py server.py ai_assist.py ./
COPY web ./web

# Cloud hosts (Render, Railway, Fly, ...) inject the real port via $PORT at
# start-up; server.py's --port already falls back to it, so it doesn't need
# to be repeated here. --host must be 0.0.0.0 -- the 127.0.0.1 default is
# correct for the desktop build, where the only visitor is the same machine,
# but it would make a container unreachable from outside itself.
# Tells the interface to show visitors the "this leaves your device" notice
# instead of the desktop build's "everything stays local" claim, which would
# be false here. See server.py's PUBLIC_DEPLOYMENT.
ENV PUBLIC_DEPLOYMENT=1

EXPOSE 8000
CMD ["python", "server.py", "--host", "0.0.0.0"]
