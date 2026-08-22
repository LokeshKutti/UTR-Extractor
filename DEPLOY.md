# Deploying this as a public website

This is a separate path from the desktop build (`utrextractor.spec`). The
desktop build hands someone an .exe that runs entirely on their own PC and
never talks to a network. This path runs the same app as a server that
anyone with the URL can reach in their browser -- which means uploaded
documents leave the visitor's device to get processed. The interface tells
visitors that plainly (see `PUBLIC_DEPLOYMENT` below); there's no way to
offer a shared public tool without that trade-off, so it needs to be an
honest one.

What was changed to make this deployable, and why, is in `Dockerfile`,
`.dockerignore`, and the `PUBLIC_DEPLOYMENT` / rate-limiter additions in
`server.py` -- each has a comment at the point of the change.

## What this deployment does and doesn't do

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
  substitute for a real WAF if this ever gets serious traffic.

## 1. Get the code into a git repository

This project had no git history until now. From `E:\utr-extractor`:

```bash
git init
git add .
git commit -m "Initial commit"
```

Then create an empty repository on GitHub (github.com -> New repository --
do **not** initialize it with a README) and push:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

Double check `.env` is not in the commit (`git show --stat HEAD` should not
list it) -- `.gitignore` already excludes it, but it's worth a glance before
a push, especially since this repo may be public.

## 2. Deploy the container (Render, recommended)

Render's free web service tier needs no credit card and builds straight from
a `Dockerfile` in a GitHub repo, which is exactly what's here.

1. Go to render.com and sign in with your GitHub account.
2. **New +** -> **Web Service** -> pick the repository you just pushed.
3. Render detects the `Dockerfile` automatically -- leave build/start
   commands blank, it uses the `CMD` already in the Dockerfile.
4. Instance type: **Free** is enough to try it out. Note its trade-off: the
   service spins down after ~15 minutes idle, so the first request after a
   quiet spell takes 30-60s to wake back up. Upgrade to a paid instance
   later if that cold start becomes annoying.
5. You do not need to set `PUBLIC_DEPLOYMENT` yourself -- it's already
   baked into the image via the Dockerfile's `ENV`. The only environment
   variable worth adding here is `GEMINI_API_KEY`, and only if you've
   decided you're fine with the trade-off described above.
6. Click **Create Web Service**. First build takes a few minutes (RapidOCR,
   onnxruntime and opencv are sizeable downloads) -- once it's done, Render
   gives you a `https://<something>.onrender.com` URL. That's the link you
   share.

**Alternatives**: Railway and Fly.io both deploy the same `Dockerfile` with
an equally short setup; pick whichever you already have an account on. A
plain VPS (DigitalOcean, etc.) works too, at the cost of managing updates
and HTTPS yourself instead of the platform doing it -- `docker run -p
80:8000 <image>` behind a reverse proxy is all that's really needed there.

## 3. Verify it after deploying

Open the live URL and check:
- The lede under the title reads as a server notice, not "everything runs
  locally" (confirms `PUBLIC_DEPLOYMENT` took effect).
- A test upload completes and the download buttons work.
- `curl -I https://<your-url>/` returns `200`.

## Changing the rate limit

`server.py`, near the top: `_RATE_LIMIT_MAX_REQUESTS` (default 20) and
`_RATE_LIMIT_WINDOW_S` (default 60). Raise the limit if legitimate batch
uploads (many images chunked across several requests) are getting throttled;
lower it if the free tier is getting hammered.
