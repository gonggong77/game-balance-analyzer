"""
app.py — Flask 웹앱 (Phase 2: Claude API + 스트리밍)
"""

import os
import json
import tempfile
import shutil
import threading
import time
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from dotenv import load_dotenv

load_dotenv()

from balance_checker import run_analysis, report_to_dict
from claude_analyzer import analyze_with_claude_sync

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB

# 대화 세션 (메모리 기반, 실서비스엔 Redis 권장)
SESSIONS: dict[str, list] = {}

# 비동기 분석 작업 저장소
JOBS: dict[str, dict] = {}


def _cleanup_jobs():
    """1시간 이상 된 완료 작업 정리"""
    cutoff = time.time() - 3600
    stale = [jid for jid, job in list(JOBS.items()) if job.get("created_at", 0) < cutoff]
    for jid in stale:
        JOBS.pop(jid, None)


# ──────────────────────────────────────────────
# 정적 파일
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ──────────────────────────────────────────────
# 1. CSV 업로드 → 비동기 분석 시작
# ──────────────────────────────────────────────

@app.route("/api/analyze/stream", methods=["POST"])
def analyze_stream():
    """
    multipart/form-data:
      - files[]: CSV 파일들
      - schema: schema.json (optional)
      - context: 사용자 추가 설명 (optional)
      - session_id: 대화 세션 ID
    반환: {"job_id": "...", "session_id": "..."}
    """
    session_id = request.form.get("session_id", "default")
    user_context = request.form.get("context", "")

    tmp_dir = tempfile.mkdtemp(prefix="balance_")
    data_dir = os.path.join(tmp_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    uploaded_files = request.files.getlist("files[]")
    schema_file = request.files.get("schema")

    for f in uploaded_files:
        if f.filename.lower().endswith(".csv"):
            safe_name = Path(f.filename).name
            f.save(os.path.join(data_dir, safe_name))

    if schema_file:
        schema_file.save(os.path.join(tmp_dir, "schema.json"))
    else:
        default_schema = Path(__file__).parent / "schema.json"
        if default_schema.exists():
            shutil.copy(default_schema, os.path.join(tmp_dir, "schema.json"))

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "running", "result": "", "error": "", "created_at": time.time()}

    def run():
        try:
            result = analyze_with_claude_sync(base_dir=tmp_dir, user_context=user_context)
            SESSIONS[session_id] = result["conversation"]
            JOBS[job_id]["result"] = result["ai_response"]
            JOBS[job_id]["status"] = "done"
        except Exception as e:
            JOBS[job_id]["error"] = str(e)
            JOBS[job_id]["status"] = "error"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _cleanup_jobs()

    threading.Thread(target=run, daemon=True).start()

    return jsonify({"job_id": job_id, "session_id": session_id})


@app.route("/api/status/<job_id>")
def job_status(job_id):
    """분석 작업 상태 폴링"""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
    return jsonify({
        "status": job["status"],   # running | done | error
        "result": job.get("result", ""),
        "error": job.get("error", ""),
    })


# ──────────────────────────────────────────────
# 2. 기본 디렉토리 분석 (로컬 data/ 폴더 사용)
# ──────────────────────────────────────────────

@app.route("/api/analyze/local", methods=["POST"])
def analyze_local():
    """로컬 data/ 폴더의 CSV를 직접 분석 (업로드 없이)."""
    body = request.get_json(force=True, silent=True) or {}
    user_context = body.get("context", "")
    session_id = body.get("session_id", "default")

    base_dir = str(Path(__file__).parent)

    def generate():
        for chunk in analyze_with_claude_stream(
            base_dir=base_dir,
            user_context=user_context,
        ):
            yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ──────────────────────────────────────────────
# 3. 대화형 Q&A (후속 질문)
# ──────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    """
    JSON body:
      - session_id: 세션 ID (이전 /api/analyze 응답에서 받은 것)
      - message: 사용자 후속 질문
    """
    body = request.get_json(force=True, silent=True) or {}
    session_id = body.get("session_id", "default")
    message = body.get("message", "").strip()

    if not message:
        return jsonify({"error": "message 필드가 필요합니다."}), 400

    history = SESSIONS.get(session_id, [])

    if not history:
        # 세션이 없으면 로컬 데이터로 새 분석 시작
        base_dir = str(Path(__file__).parent)
        result = analyze_with_claude_sync(
            base_dir=base_dir,
            user_context=message,
        )
    else:
        base_dir = str(Path(__file__).parent)
        result = analyze_with_claude_sync(
            base_dir=base_dir,
            user_context=message,
            conversation_history=history,
        )

    SESSIONS[session_id] = result["conversation"]

    return jsonify({
        "session_id": session_id,
        "ai_response": result["ai_response"],
        "usage": result["usage"],
        "turn": len(result["conversation"]) // 2,
    })


# ──────────────────────────────────────────────
# 4. 빠른 정량 분석만 (AI 없이)
# ──────────────────────────────────────────────

@app.route("/api/quick-check", methods=["GET", "POST"])
def quick_check():
    """
    AI 없이 정량 분석만 JSON으로 반환.
    GET  ?warn=10&crit=20          — 쿼리스트링으로 임계값 override
    POST { warn: 10, crit: 20 }   — JSON body로 임계값 override
    """
    base_dir = str(Path(__file__).parent)

    # 임계값 파싱 (GET 쿼리 or POST body 둘 다 지원)
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        warn_thr = body.get("warn")
        crit_thr = body.get("crit")
    else:
        warn_thr = request.args.get("warn")
        crit_thr = request.args.get("crit")

    try:
        schema_path = str(Path(base_dir) / "schema.json")
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        # 임계값 override (숫자 검증 포함)
        applied = {}
        if warn_thr is not None:
            w = float(warn_thr)
            schema["validation_rules"]["dominance_threshold"] = w
            applied["warn"] = w
        if crit_thr is not None:
            c = float(crit_thr)
            schema["validation_rules"]["critical_threshold"] = c
            applied["crit"] = c

        # override 값이 논리적으로 맞는지 확인
        vr = schema["validation_rules"]
        if vr["dominance_threshold"] >= vr["critical_threshold"]:
            return jsonify({"error": "warn 임계값은 crit 임계값보다 작아야 합니다."}), 400

        report = run_analysis(base_dir=base_dir, schema_override=schema if applied else None, schema_path=schema_path)
        result = report_to_dict(report)
        # 응답에 실제 적용된 임계값 포함
        result["applied_thresholds"] = {
            "warn": vr["dominance_threshold"],
            "crit": vr["critical_threshold"],
            "overridden": bool(applied),
        }
        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": f"임계값은 숫자여야 합니다: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/thresholds", methods=["GET"])
def get_thresholds():
    """현재 schema.json의 임계값 반환."""
    base_dir = str(Path(__file__).parent)
    try:
        schema_path = str(Path(base_dir) / "schema.json")
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
        vr = schema["validation_rules"]
        return jsonify({
            "warn": vr["dominance_threshold"],
            "crit": vr["critical_threshold"],
            "outlier_delta": vr.get("outlier_delta_threshold", 50.0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/thresholds", methods=["POST"])
def save_thresholds():
    """
    schema.json의 임계값을 영구 저장.
    POST { warn: 10, crit: 20 }
    """
    body = request.get_json(force=True, silent=True) or {}
    base_dir = str(Path(__file__).parent)
    try:
        schema_path = str(Path(base_dir) / "schema.json")
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        w = float(body.get("warn", schema["validation_rules"]["dominance_threshold"]))
        c = float(body.get("crit", schema["validation_rules"]["critical_threshold"]))

        if w <= 0 or c <= 0:
            return jsonify({"error": "임계값은 0보다 커야 합니다."}), 400
        if w >= c:
            return jsonify({"error": "warn 임계값은 crit 임계값보다 작아야 합니다."}), 400
        if c > 100:
            return jsonify({"error": "crit 임계값은 100 이하여야 합니다."}), 400

        schema["validation_rules"]["dominance_threshold"] = w
        schema["validation_rules"]["critical_threshold"] = c

        with open(schema_path, "w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)

        return jsonify({
            "status": "saved",
            "warn": w,
            "crit": c,
            "message": f"임계값 저장 완료 — WARNING: {w}% / CRITICAL: {c}%"
        })
    except ValueError as e:
        return jsonify({"error": f"숫자 형식 오류: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────

@app.route("/health")
def health():
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return jsonify({
        "status": "ok",
        "api_key_configured": api_key_set,
        "version": "2.0.0-phase2",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
