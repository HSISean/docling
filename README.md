# 🔒 Docling Redactor — Web

A browser-based version of the redaction tool, built to deploy on Heroku.
**docling** parses supported source files and exports their content as
Markdown. The selected detectors redact sensitive content, including EINs,
and every successful result is downloaded as a `.md` file.

## What's in here

```
app.py                 Flask backend (upload, background job, download)
redaction_engine.py     Redaction logic (shared with the desktop app)
patterns.py              Built-in detector regexes
templates/index.html    UI
static/css/style.css    Styling
static/js/app.js        Upload, polling, results UI
requirements.txt
Procfile                 Heroku process definition
Dockerfile / heroku.yml  Container-based deploy (recommended, see below)
runtime.txt               Python version pin (buildpack deploys)
```

## ⚠️ Before you deploy: pick buildpack vs. container

`docling` pulls in `torch`, `transformers`, and a few other sizeable
libraries for its layout/document models. This affects which Heroku deploy
method to use:

- **Container deploy (recommended).** Heroku's container/OCI images can be
  up to **5 GB**, well clear of docling's footprint. This repo includes a
  `Dockerfile` and `heroku.yml` for this path.
- **Buildpack (git push) deploy.** Heroku's default slug size limit is now
  **1000 MB** (raised from the old 500 MB limit), which may be enough — but
  it's close, and it depends on exact dependency versions at deploy time.
  If you hit a slug-too-large error, switch to the container path below.

Either way, use at least a **Standard-1X/1GB dyno**. Docling's models need
real memory to load; an Eco/Basic (512 MB) dyno will likely OOM on the
first document.

### Option A — Container deploy (recommended)

```bash
heroku create your-app-name
heroku stack:set container -a your-app-name
git push heroku main
```

Heroku will build the `Dockerfile` and use `heroku.yml` automatically.

### Option B — Buildpack deploy

```bash
heroku create your-app-name
heroku buildpacks:set heroku/python -a your-app-name
git push heroku main
```

If the build fails with a slug size error, switch to Option A.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. The first PDF you process will trigger docling
to download its layout model, so local dev needs internet on first run too.

## How it works

1. You drop in files and pick redaction rules in the browser.
2. The browser uploads everything to `POST /api/redact`, which saves the
   files to a temp job folder and starts a **background thread** — this
   keeps the HTTP request itself fast, well under Heroku's 30-second router
   timeout, even for slow documents.
3. Docling converts each supported source to Markdown and the selected
   redaction rules are applied to the exported content.
4. The page polls `GET /api/jobs/<id>` once a second for log lines,
   progress, and final results.
5. Redacted `.md` files are served from `GET /api/jobs/<id>/download/<file>`, or
   all together as a zip from `GET /api/jobs/<id>/download-all`.
6. Job files are cleaned up automatically after an hour.

## Important constraint: single web dyno

Job state lives in an in-memory dictionary inside the running process. The
`Procfile` runs a single gunicorn worker (`--workers 1`) so all requests for
a job land on the same process. **Don't scale this app past one web dyno**
(`heroku ps:scale web=1`) without first moving job state to something
shared like Redis — otherwise a status poll can hit a different dyno than
the one running the job and come back 404.

## Limits & tuning

- `MAX_UPLOAD_MB` in `app.py` caps total upload size (default 60 MB) —
  raise it if you need to handle larger files, keeping dyno memory in mind.
- Gunicorn's timeout is set to 300s in the `Procfile`/`Dockerfile` to give
  slow, multi-file docling jobs room to finish inside a single worker
  thread without the process itself being killed (this is separate from
  Heroku's router timeout, which the background-job design already avoids).
- For PDF-heavy workloads, a Performance dyno will noticeably speed up
  docling's model inference.

## Limitations (same as the desktop engine)

- Built-in patterns (credit cards, passports, etc.) are heuristic regexes
  and can have false positives/negatives.
- Output is a Markdown representation of the source, not an edited copy in
  the original file format.
