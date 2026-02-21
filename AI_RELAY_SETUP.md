# AI 릴레이 서버 셋업 가이드 (서버PC)

## 개요

Railway 웹서버에서 AI가 필요할 때, 서버PC의 Claude Code를 경유하여 응답을 생성합니다.

```
리뷰어 → Railway(웹서버) --HTTP-→ 서버PC(claude -p) → AI 응답 반환
```

---

## 1. 서버PC에 파일 생성

서버PC에 `ai_relay_server.py` 파일을 만들어주세요:

```python
"""
ai_relay_server.py - Claude Code 릴레이 서버

Railway 웹서버에서 AI 요청을 받아 claude -p로 처리 후 응답 반환.
서버PC에서 실행합니다.
"""

import os
import subprocess
import logging
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "")


@app.route("/ai", methods=["POST"])
def ai_relay():
    """AI 프롬프트를 받아 claude -p로 처리"""
    # API 키 인증
    if API_KEY:
        key = request.headers.get("X-API-Key", "")
        if key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "").strip()

    if not prompt:
        return jsonify({"error": "prompt 필요"}), 400

    logger.info(f"AI 요청 수신 (길이: {len(prompt)}자)")

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error(f"claude 실행 에러: {result.stderr}")
            return jsonify({"error": "claude 실행 실패", "detail": result.stderr}), 500

        response_text = result.stdout.strip()
        logger.info(f"AI 응답 완료 (길이: {len(response_text)}자)")

        return jsonify({"response": response_text})

    except subprocess.TimeoutExpired:
        logger.warning("claude 실행 타임아웃 (30초)")
        return jsonify({"error": "타임아웃"}), 504

    except FileNotFoundError:
        logger.error("claude 명령어를 찾을 수 없습니다. Claude Code가 설치되어 있는지 확인하세요.")
        return jsonify({"error": "claude 명령어 없음"}), 500

    except Exception as e:
        logger.error(f"릴레이 에러: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """헬스체크"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("RELAY_PORT", 5002))
    logger.info(f"AI 릴레이 서버 시작 (포트: {port})")
    app.run(host="0.0.0.0", port=port)
```

---

## 2. 서버PC에서 실행

```bash
# Flask 설치 (없으면)
pip install flask

# 릴레이 서버 실행
python ai_relay_server.py
```

기본 포트: **5002** (변경하려면 `RELAY_PORT` 환경변수 설정)

```bash
# 포트 변경 예시
set RELAY_PORT=8080
python ai_relay_server.py
```

---

## 3. Railway 환경변수 설정

Railway 대시보드에서 환경변수 추가:

```
AI_RELAY_URL=http://서버PC의_공인IP:5002
```

서버PC의 API_KEY를 사용 중이라면, Railway의 `API_KEY`와 동일하게 서버PC에도 설정:

```bash
set API_KEY=your-api-key-here
python ai_relay_server.py
```

---

## 4. 방화벽 / 포트포워딩

서버PC가 외부에서 접근 가능해야 합니다:

- **공유기 포트포워딩**: 외부 5002 → 서버PC 내부IP:5002
- **Windows 방화벽**: 5002 포트 인바운드 허용

---

## 5. 테스트

### 서버PC 로컬 테스트
```bash
curl -X POST http://localhost:5002/ai -H "Content-Type: application/json" -d "{\"prompt\": \"안녕하세요\"}"
```

### 외부에서 테스트
```bash
curl -X POST http://서버PC_공인IP:5002/ai -H "Content-Type: application/json" -d "{\"prompt\": \"안녕하세요\"}"
```

### 헬스체크
```bash
curl http://서버PC_공인IP:5002/health
```

정상이면 `{"status": "ok"}` 응답.

---

## 동작 확인

1. 서버PC에서 `python ai_relay_server.py` 실행 중인지 확인
2. 카비서 챗봇에서 "오류가 있어요" 같은 자유 텍스트 입력
3. AI가 적절한 안내 응답을 하면 성공
4. 서버PC 콘솔에 "AI 요청 수신" / "AI 응답 완료" 로그 확인
