"""
Docling Redactor — web app
===========================
Flask front end for the docling-powered redaction engine. Files are
uploaded, redacted in a background thread (with a polled job log so large
files don't hit Heroku's 30s router timeout), and served back as
downloadable files or a zip.

Run locally:
    python app.py

Serve with gunicorn (used in production / Heroku, see Procfile):
    gunicorn app:app --workers 1 --threads 8 --timeout 300
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, abort
from redis import Redis
from rq import Queue
from werkzeug.utils import secure_filename, safe_join

from patterns import BUILTIN_PATTERNS, BUILTIN_PATTERN_ORDER
from redaction_engine import RedactionEngine, build_rules
from rq.job import Job
from rq.exceptions import NoSuchJobError



MAX_UPLOAD_MB = 60
JOB_TTL_SECONDS = 60 * 60  # clean up job files after an hour

JOBS_ROOT = Path(tempfile.gettempdir()) / "docling_redactor_jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
shutdown_token = secrets.token_urlsafe(32)

redis_connection = None
document_queue = None

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


if os.environ.get("REDIS_URL"):
    redis_connection = Redis.from_url(
        os.environ["REDIS_URL"],
        ssl_cert_reqs=None,
    )
    document_queue = Queue(
        "docling",
        connection=redis_connection,
        default_timeout=1800,
    )

def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)

# ---------------------------------------------------------------------- #
# Job bookkeeping (in-memory — see README for the single-worker constraint)
# ---------------------------------------------------------------------- #
def _new_job(job_id: str, total_files: int) -> None:
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",  # running | done | error
            "log": [],
            "progress": {"current": 0, "total": total_files},
            "results": [],  # [{input, output, ok}]
            "error": None,
            "created": time.time(),
        }


def _log(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["log"].append(message)


def _set_progress(job_id: str, current: int, total: int) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["progress"] = {"current": current, "total": total}


def _add_result(job_id: str, input_name: str, output_filename: str, ok: bool) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["results"].append(
                {"input": input_name, "output": output_filename, "ok": ok}
            )


def _finish_job(job_id: str, status: str, error: str | None = None) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status
            _jobs[job_id]["error"] = error


def _cleanup_old_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items() if j.get("created", 0) < cutoff]
        for jid in stale:
            _jobs.pop(jid, None)
    for job_dir in JOBS_ROOT.iterdir():
        if job_dir.is_dir() and job_dir.stat().st_mtime < cutoff:
            shutil.rmtree(job_dir, ignore_errors=True)


# ---------------------------------------------------------------------- #
# Routes
# ---------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html", patterns=BUILTIN_PATTERN_ORDER)


@app.route("/api/redact", methods=["POST"])
def api_redact():
    _cleanup_old_jobs()

    files = request.files.getlist("files")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"error": "No files uploaded."}), 400

    selected = request.form.getlist("builtin")
    keywords = [k for k in request.form.get("keywords", "").splitlines() if k.strip()]
    regexes = [r for r in request.form.get("regex", "").splitlines() if r.strip()]
    case_sensitive = request.form.get("case_sensitive") == "true"
    whole_word = request.form.get("whole_word") == "true"
    style = request.form.get("style", "box")
    if style not in ("box", "label"):
        style = "box"

    builtin_selected = {
        label: BUILTIN_PATTERNS[label] for label in selected if label in BUILTIN_PATTERNS
    }

    try:
        rules = build_rules(builtin_selected, keywords, regexes, case_sensitive, whole_word)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not rules:
        return jsonify(
            {"error": "Select at least one detector, or add a custom keyword / regex pattern."}
        ), 400

    job_id = uuid.uuid4().hex
    job_dir = JOBS_ROOT / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for f in files:
        filename = secure_filename(f.filename)
        if not filename:
            continue
        dest = input_dir / filename
        f.save(dest)
        saved_paths.append(dest)

    if not saved_paths:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "No valid files uploaded."}), 400

    _new_job(job_id, len(saved_paths))

    thread = threading.Thread(
        target=_run_job, args=(job_id, saved_paths, output_dir, rules, style), daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id})


def _run_job(job_id: str, files: list[Path], output_dir: Path, rules, style: str) -> None:
    engine = RedactionEngine(rules, style=style, log_fn=lambda m: _log(job_id, m))
    try:
        for i, path in enumerate(files, start=1):
            _log(job_id, f"[{i}/{len(files)}] Scanning {path.name} with docling...")
            try:
                matches = engine.scan(path)
                if matches:
                    summary = ", ".join(f"{k}: {v}" for k, v in matches.items())
                    _log(job_id, f"  Detected -> {summary}")
                else:
                    _log(job_id, "  No matches in docling preview (Markdown export still runs).")

                _log(job_id, f"  Redacting {path.name}...")
                out_path = engine.redact(path, output_dir)
                _log(job_id, f"  Saved -> {out_path.name}")
                _add_result(job_id, path.name, out_path.name, True)
            except Exception as exc:  # noqa: BLE001
                _log(job_id, f"  FAILED: {exc}")
                _add_result(job_id, path.name, "", False)
            _set_progress(job_id, i, len(files))
        _finish_job(job_id, "done")
    except Exception as exc:  # noqa: BLE001
        _finish_job(job_id, "error", str(exc))


# @app.route("/api/jobs/<job_id>")
# def api_job_status(job_id: str):
#     with _jobs_lock:
#         job = _jobs.get(job_id)
#         if not job:
#             return jsonify({"error": "Unknown or expired job."}), 404
#         return jsonify(job)


def _job_output_path(job_id: str, filename: str) -> Path | None:
    safe_job_id = secure_filename(job_id)
    if not safe_job_id:
        return None

    output_dir = JOBS_ROOT / safe_job_id / "output"
    candidate = safe_join(str(output_dir), filename)
    if not candidate:
        return None

    path = Path(candidate)
    if not path.is_file():
        return None
    return path


@app.route("/api/jobs/<job_id>/download/<path:filename>")
def api_download(job_id: str, filename: str):
    path = _job_output_path(job_id, filename)
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/api/jobs/<job_id>/download-all")
def api_download_all(job_id: str):
    job_id = secure_filename(job_id)
    output_dir = JOBS_ROOT / job_id / "output"
    if not output_dir.exists():
        abort(404)
    zip_path = JOBS_ROOT / job_id / "redacted_files.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                zf.write(f, arcname=f.name)
    return send_file(zip_path, as_attachment=True, download_name="redacted_files.zip")


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": f"Upload too large. Limit is {MAX_UPLOAD_MB} MB total."}), 413


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.post("/app/shutdown")
def shutdown_application():
    # Only permit requests originating from this computer.
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(403)

    if request.headers.get("X-Shutdown-Token") != shutdown_token:
        abort(403)

    # Delay termination long enough to return the response.
    threading.Timer(0.5, lambda: os._exit(0)).start()

    return jsonify({"message": "Docling is shutting down."})

@app.context_processor
def provide_shutdown_token():
    return {"shutdown_token": shutdown_token}



@app.post("/process")
def process_document():
    # Save/upload the submitted file first.
    input_reference = "..."

    if document_queue is None:
        return jsonify(error="Background processing is unavailable"), 503

    job = document_queue.enqueue(
        "redaction_engine.process_document_job",
        input_reference,
        job_timeout=1800,
        result_ttl=3600,
        failure_ttl=86400,
    )

    return jsonify(
        job_id=job.id,
        status="queued",
    ), 202

@app.get("/jobs/<job_id>")
def job_status(job_id):
    if redis_connection is None:
        return jsonify(error="Background processing is unavailable"), 503

    try:
        job = Job.fetch(job_id, connection=redis_connection)
    except NoSuchJobError:
        return jsonify(
            status="missing",
            error="Job was not found or expired",
        ), 404

    status = job.get_status(refresh=True)

    response = {
        "job_id": job.id,
        "status": status,
    }

    if job.is_finished:
        response["result"] = job.result

    if job.is_failed:
        response["error"] = job.exc_info

    return jsonify(response)
if __name__ == "__main__":
    app.run(debug=True, port=5000)
