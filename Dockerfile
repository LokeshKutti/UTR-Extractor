# Public-deployment image. The desktop build (utrextractor.spec) is a
# separate, unrelated path for handing someone an .exe that runs entirely on
# their own PC -- this image is for running the same app as a server that a
# browser somewhere else talks to over the network.
FROM python:3.13-slim

WORKDIR /app

# requirements.txt asks for opencv-python-headless, but rapidocr-onnxruntime
# itself declares a hard dependency on plain opencv-python (confirmed via
# `pip show rapidocr-onnxruntime`) -- pip installs both, and the GUI-linked
# one is what actually resolves at import time. Rather than fight pip's
# resolver over which cv2 wins, this just satisfies the shared libraries the
# full build needs, none of which require an actual display.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libsm6 libxext6 libxrender1 libxcb1 \
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
