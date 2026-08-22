# Deploying this as a public website

This is a separate path from the desktop build (`utrextractor.spec`). The
desktop build hands someone an .exe that runs entirely on their own PC and
never talks to a network. This path runs the same app as a server that
anyone with the URL can reach in their browser -- which means uploaded
documents leave the visitor's device to get processed. The interface tells
visitors that plainly (see `PUBLIC_DEPLOYMENT` below); there's no way to
offer a shared public tool without that trade-off, so it needs to be an
honest one.

Two ways to actually host it:

- **Option A -- Render (backend) + Vercel (frontend), no Docker.** The
  recommended path. Two small services instead of one container; each
  platform does the part it's built for.
- **Option B -- a single Docker image.** Simpler in that it's one deploy
  instead of two, at the cost of needing a container platform.

## What this deployment does and doesn't do (both options)

- Uploaded files are read into memory, processed, and the result is sent
  back. Nothing is written to disk and nothing is stored -- confirmed by
  reading through every file-write in the codebase before writing this.
- There is no login. Anyone with the link can use it.
- The optional Gemini AI fallback is **off** unless you explicitly add
  `GEMINI_API_KEY` as an environment variable on the host. Left off by
  default here on purpose: turning it on for a public, login-free site means
  any visitor's usage draws on *your* API key and your billing, with no way
  to tell who used how much.
- A basic per-IP rate limit (20 requests/minute to the OCR endpoints) is
  built in so the site can't be trivially hammered into unusability, or --
  if you're on a paid tier -- into an unexpected bill. It's intentionally
  simple (in-memory, per-process) -- enough to stop casual abuse, not a
  substitute for a real WAF if this ever gets serious traffic. It reads the
  real visitor IP from `X-Forwarded-For` rather than the connection's own
  peer address, since both Render and Vercel sit in front of the app as a
  reverse proxy -- without that, every visitor would share one bucket.

## 1. Get the code into a git repository

This project had no git history until this session. From `E:\utr-extractor`:

```bash
git init
git add .
git commit -m "Initial commit"
```

(Already done in this session, in fact -- skip straight to creating the
GitHub repo below if `git log` already shows commits.)

Create an empty repository on GitHub (github.com -> New repository -- do
**not** initialize it with a README) and push:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

Double check `.env` is not in the commit (`git show --stat HEAD` should not
list it) -- `.gitignore` already excludes it, but it's worth a glance before
a push, especially since this repo may be public.

---

## Option A: Render (backend) + Vercel (frontend), no Docker

### A1. Deploy the backend on Render

`render.yaml` at the repo root already describes the whole service, so
Render's Blueprint feature does the setup in one pass:

1. Go to render.com, sign in with GitHub, **New +** -> **Blueprint**, pick
   this repository. Render reads `render.yaml` and shows you the one service
   it defines (`utr-extractor-api`) before creating anything.
2. It asks you to fill in `ALLOWED_ORIGIN` -- leave it blank for now, you'll
   set it in step A3 once the frontend has a URL. Everything else
   (`PUBLIC_DEPLOYMENT`, the Python version, the build/start commands) is
   already in the file.
3. Click through to create it. First build takes a few minutes -- RapidOCR,
   onnxruntime and opencv are sizeable downloads.
4. Free tier note: the service spins down after ~15 minutes idle, so the
   first request after a quiet spell takes 30-60s to wake back up.

**Why the build command is two `pip install` calls**: `rapidocr-onnxruntime`
declares a hard dependency on plain `opencv-python`, which needs GUI-linked
system libraries (`libGL`, `libxcb`, ...) that a minimal server doesn't have
and that a Docker-free Render service has no way to install (no apt-get
access here, unlike the Dockerfile in Option B). Installing it with
`--no-deps` first, then everything else including
`opencv-python-headless` from `requirements.txt`, gets the same `cv2` import
satisfied by the headless build instead -- which needs none of those
libraries. Verified directly: importing both `rapidocr_onnxruntime` and
`cv2` this way succeeds in a bare `python:3.13-slim` container with *zero*
extra system packages installed.

Your backend's URL will be `https://utr-extractor-api.onrender.com` (the
name is set in `render.yaml`) unless that name is already taken by someone
else's Render project, in which case Render will ask you to pick a different
one -- use whatever it gives you in step A2 below instead.

### A2. Point the frontend at that backend

`web/index.html` talks to whatever server is hosting the page by default,
which only works when both are the same origin. Since Vercel and Render are
different domains, open `web/index.html` and change one line near the top
of the `<script>` block:

```js
const API_BASE = "";
```

to your actual Render URL, no trailing slash:

```js
const API_BASE = "https://utr-extractor-api.onrender.com";
```

Commit and push that change.

### A3. Deploy the frontend on Vercel

1. Go to vercel.com, sign in with GitHub, **Add New** -> **Project**, pick
   this repository.
2. Framework preset: **Other**. `vercel.json` already sets the output
   directory to `web/`, so there's nothing else to configure -- leave the
   build command empty, there isn't one (no Node, no npm, matching the rest
   of this project).
3. Deploy. You get a URL like `https://<project>.vercel.app`.

### A4. Close the loop: tell the backend to trust the frontend's origin

Back on Render: open the `utr-extractor-api` service -> **Environment** ->
set `ALLOWED_ORIGIN` to the Vercel URL from step A3 (e.g.
`https://my-project.vercel.app`) -> save. Render redeploys automatically
when an environment variable changes. Vercel's per-push preview URLs
(`my-project-git-branch-you.vercel.app`) are covered automatically by a
pattern match in `server.py`, so only the main production URL needs to be
listed here.

### A5. Verify

Open the Vercel URL and check:
- The lede under the title reads as a server notice, not "everything runs
  locally" (confirms `PUBLIC_DEPLOYMENT` reached the backend).
- A test upload completes with no CORS error in the browser console (F12 ->
  Console). A CORS error here almost always means step A4 wasn't done yet,
  or the URL there doesn't exactly match what's in the address bar.
- The download buttons work.

---

## Option B: a single Docker image

One deploy instead of two, on any platform that runs a container -- Render,
Railway, Fly.io, or a plain VPS. `Dockerfile` and `.dockerignore` at the repo
root cover this; `PUBLIC_DEPLOYMENT` is already baked in via the
Dockerfile's `ENV`, so nothing extra to configure for that part.

On Render specifically: **New +** -> **Web Service** -> pick the repo ->
Render detects the `Dockerfile` automatically and uses its `CMD` as the
start command. Since frontend and backend are the same service here, there's
no `ALLOWED_ORIGIN`/CORS step needed -- everything is same-origin.

This path was built and verified first (image builds, container starts, a
real image posted to `/api/extract` inside it reads back the correct UTR,
amount and payee) before Option A existed -- see the git history for that
verification if you want the details.

**Alternatives to Render for this option**: Railway and Fly.io both deploy
the same `Dockerfile` with an equally short setup. A plain VPS works too, at
the cost of managing updates and HTTPS yourself -- `docker run -p 80:8000
<image>` behind a reverse proxy is all that's really needed there.

---

## Changing the rate limit

`server.py`, near the top: `_RATE_LIMIT_MAX_REQUESTS` (default 20) and
`_RATE_LIMIT_WINDOW_S` (default 60). Raise the limit if legitimate batch
uploads (many images chunked across several requests) are getting throttled;
lower it if the free tier is getting hammered.
