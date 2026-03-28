"""
game-balance-analyzer — Flask 웹 서버
Phase 3에서 구현 예정

현재: 기본 라우트 스켈레톤
"""

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

@app.route("/")
def index():
    return "game-balance-analyzer — Phase 3 구현 예정"

@app.route("/analyze", methods=["POST"])
def analyze():
    # Phase 3에서 구현
    # 1. CSV 파일 수신
    # 2. Claude Code 호출 (로컬 C# 스크립트 분석)
    # 3. Claude API 호출 (밸런스 분석)
    # 4. 결과 반환
    return jsonify({"status": "Phase 3 구현 예정"})

if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_ENV") == "development"
    app.run(port=port, debug=debug)
