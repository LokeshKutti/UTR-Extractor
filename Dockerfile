# Public-deployment image. The desktop build (utrextractor.spec) is a
# separate, unrelated path for handing someone an .exe that runs entirely on
# their own PC -- this image is for running the same app as a server that a
# browser somewhere else talks to over the network.
FROM python:3.13-slim

WORKDIR /app

# Belt-and-suspenders alongside the --no-deps install below: rapidocr's own
# opencv-python dependency needs these even though it's no longer installed
# on purpose, kept here in case a future change to requirements.txt or a
# rapidocr version bump reintroduces it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# rapidocr-onnxruntime declares a hard dependency on plain opencv-python
# (confirmed via `pip show rapidocr-onnxruntime`), which needs the libraries
# above -- but requirements.txt deliberately asks for opencv-python-headless
# instead, which doesn't. --no-deps stops pip from pulling in the GUI-linked
# build at all, so the headless one (installed normally, right after) is the
# only cv2 that ends up on disk. See the comment at the top of
# requirements.txt for the full explanation; render.yaml's buildCommand does
# the same two steps for the Docker-free deployment path.
RUN pip install --no-cache-dir --no-deps rapidocr-onnxruntime==1.2.3 \
    && pip install --no-cache-dir -r requirements.txt

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
# Most container hosts cap a free/starter instance well under 1GB. The real
# mechanism (see core.py's DET_LIMIT_SIDE_LEN comment): RapidOCR's detection
# network scales every image up to 736px minimum by default, and running that
# through the network is what spikes memory close to a 512MB ceiling -- not
# model loading, variant count, or thread pools, all ruled out first.
# DET_LIMIT_SIDE_LEN=480 cuts that peak by roughly 40% with no measured
# accuracy loss. MAX_ACCURACY_TIER stays at "fast" as a second, already-
# verified-safe layer. See render.yaml's copy of both for the Docker-free path.
ENV MAX_ACCURACY_TIER=fast
ENV DET_LIMIT_SIDE_LEN=480

EXPOSE 8000
CMD ["python", "server.py", "--host", "0.0.0.0"]
