"""Sourceror — a source-checking helper for Wikipedia editors.

Audits the citations already in an article against Wikipedia's own reliability
consensus, and looks for sources to fill the gaps.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request

from sourceror import analyze as az
from sourceror import apis
from sourceror import config as cfg
from sourceror import llm
from sourceror.reliability import Reliability

DEFAULT_LANG = os.environ.get("SOURCEROR_LANG", "it")
LANGS = ["it", "en", "fr", "de", "es", "pt", "nl", "ca", "la"]

app = Flask(__name__)

rel = Reliability()
_kb_lock = threading.Lock()
_kb_status: dict = {"loaded": False, "loading": False, "error": None}

# Single-user local tool, so an in-memory job table is enough.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_TTL = 3600


def ensure_kb(force: bool = False) -> dict:
    with _kb_lock:
        if _kb_status["loaded"] and not force:
            return _kb_status
        _kb_status.update(loading=True, error=None)
        try:
            _kb_status.update(rel.load(force=force), loaded=True, error=None)
        except Exception as exc:
            _kb_status["error"] = str(exc)
        finally:
            _kb_status["loading"] = False
    return _kb_status


@app.route("/")
def index():
    return render_template("index.html", langs=LANGS, default_lang=DEFAULT_LANG)


@app.route("/api/kb")
def kb():
    return jsonify(ensure_kb())


@app.route("/api/kb/refresh", methods=["POST"])
def kb_refresh():
    return jsonify(ensure_kb(force=True))


@app.route("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    lang = request.args.get("lang", DEFAULT_LANG)
    if len(q) < 2:
        return jsonify([])
    return jsonify(apis.search_titles(q, lang, limit=8))


@app.route("/api/analyze", methods=["POST"])
def start_analysis():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "missing_title"}), 400
    title = _title_from_url(title) or title
    lang = body.get("lang") or DEFAULT_LANG
    if lang not in LANGS:
        lang = DEFAULT_LANG

    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "queued", "title": title, "lang": lang,
           "phase": "starting", "done": 0, "total": 0,
           "started": time.time(), "result": None, "error": None}
    with JOBS_LOCK:
        _reap_jobs()
        JOBS[job_id] = job

    opts = {"check_links": bool(body.get("check_links", True)),
            "discover_sources": bool(body.get("discover_sources", True)),
            "use_llm": bool(body.get("use_llm", True))}
    threading.Thread(target=_run_job, args=(job_id, title, lang, opts),
                     daemon=True).start()
    return jsonify({"job": job_id})


def _run_job(job_id: str, title: str, lang: str, opts: dict) -> None:
    def progress(done, total, phase):
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j:
                j.update(done=done, total=total, phase=phase, state="running")

    try:
        ensure_kb()
        result = az.analyze(title, lang, rel=rel, progress=progress, **opts)
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j:
                j.update(state="done", result=result, phase="complete",
                         finished=time.time())
    except Exception as exc:
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j:
                j.update(state="error", error=f"{type(exc).__name__}: {exc}",
                         finished=time.time())


@app.route("/api/job/<job_id>")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown_job"}), 404
        out = {k: v for k, v in job.items() if k != "result"}
        if job["state"] == "done":
            out["result"] = job["result"]
    return jsonify(out)


@app.route("/api/cite", methods=["POST"])
def make_citation():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    lang = body.get("lang") or DEFAULT_LANG
    if not url:
        return jsonify({"error": "missing_url"}), 400
    return jsonify(az.build_citation(url, lang))


@app.route("/api/discover", methods=["POST"])
def discover_for_claim():
    from sourceror import discover
    body = request.get_json(silent=True) or {}
    claim = (body.get("claim") or "").strip()
    title = (body.get("title") or "").strip()
    lang = body.get("lang") or DEFAULT_LANG
    if not claim:
        return jsonify({"error": "missing_claim"}), 400
    ensure_kb()
    return jsonify(discover.for_claim(claim, title, rel, lang, limit=5))


@app.route("/api/llm", methods=["GET", "POST"])
def llm_config():
    """Read or update the optional LLM settings (OpenRouter by default)."""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        updates = {k: v for k, v in body.items() if k in cfg.DEFAULTS}
        # An empty key field means "leave it alone", not "clear it".
        if not (updates.get("llm_api_key") or "").strip():
            updates.pop("llm_api_key", None)
        cfg.save(updates)
    return jsonify({**cfg.public(), "available": llm.available()})


@app.route("/api/llm/models")
def llm_models():
    return jsonify(llm.list_models())


@app.route("/api/llm/test", methods=["POST"])
def llm_test():
    return jsonify(llm.test())


@app.route("/api/domain")
def check_domain():
    """Ad-hoc lookup: is this site usable as a source?"""
    d = (request.args.get("d") or "").strip().lower()
    if not d:
        return jsonify({"error": "missing_domain"}), 400
    ensure_kb()
    d = apis.domain_of(d) or d
    verdicts = rel.check_domain(d)
    return jsonify({"domain": d,
                    "verdicts": [v.__dict__ for v in verdicts],
                    "verdict": verdicts[0].severity if verdicts else "unlisted"})


def _title_from_url(text: str) -> str | None:
    import re
    from urllib.parse import unquote
    m = re.match(r"https?://([a-z\-]+)\.wikipedia\.org/wiki/([^?#]+)", text, re.I)
    if m:
        return unquote(m.group(2)).replace("_", " ")
    return None


def _reap_jobs() -> None:
    cutoff = time.time() - JOB_TTL
    for k in [k for k, v in JOBS.items() if v.get("started", 0) < cutoff]:
        JOBS.pop(k, None)


if __name__ == "__main__":
    threading.Thread(target=ensure_kb, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1", threaded=True)
