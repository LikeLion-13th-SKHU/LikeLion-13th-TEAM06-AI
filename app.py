# app.py
import os
import json
import tempfile
import subprocess
import shutil
from typing import Optional, List, Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Body, Form
from pydantic import BaseModel

# .env 로드 (실패해도 무시)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

API_KEY = os.getenv("AI_API_KEY")                          # 없으면 인증 생략
PY_CMD = os.getenv("PY_CMD", "python")                     # 윈도우/리눅스 호환
RUN_SCRIPT = os.getenv("RUN_SCRIPT", "run_all.py")         # 파이프라인 엔트리
DEBUG = os.getenv("DEBUG", "0").lower() in ("1", "true", "yes")

app = FastAPI(title="Hackathon AI Pipeline API", version="1.0.0")  # ← 반드시 'app'

# -----------------------------
# Pydantic 스키마 (수신 전용)
# -----------------------------
class Item(BaseModel):
    newsIdentifyId: Optional[str] = None
    title: Optional[str] = None
    contents: Optional[str] = None  # 백엔드가 주는 키 (정규화 단계에서 text로 변환)

class RunRequest(BaseModel):
    items: List[Item]

# -----------------------------
# 공통 함수
# -----------------------------
def _check_auth(authorization: Optional[str]):
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="unauthorized")

def _coerce_item_keys(d: dict) -> dict:
    """
    백엔드가 주는 다양한 키 이름을 파이프라인 표준키로 통일.
    - 본문계 키: contents/content/body/desc/description → text
    - 제목 보정: newsTitle/name/headline → title
    - 아이디 보정: newsIdentifyId → id
    """
    if not isinstance(d, dict):
        return d

    out = dict(d)  # shallow copy

    # 본문(text) 통일
    if "text" not in out:
        for k in ("contents", "content", "body", "desc", "description"):
            v = out.get(k)
            if isinstance(v, str) and v.strip():
                out["text"] = v
                break

    # 제목(title) 보정
    if "title" not in out:
        for k in ("newsTitle", "name", "headline"):
            v = out.get(k)
            if isinstance(v, str) and v.strip():
                out["title"] = v
                break

    # 아이디(id) 보정 (선택)
    if "id" not in out and "newsIdentifyId" in out:
        out["id"] = out["newsIdentifyId"]

    return out

def _normalize_payload_for_pipeline(input_payload: Any) -> Any:
    """
    파이프라인이 리스트([]) 또는 {"items":[...]} 둘 다 받을 수 있게 유연화.
    runjson/data/payload 필드에 JSON 문자열이 들어오는 경우도 처리.
    그리고 각 아이템의 키를 파이프라인 표준으로 교정(contents→text 등).
    """
    # 1) 문자열이면 JSON 파싱
    if isinstance(input_payload, str):
        try:
            input_payload = json.loads(input_payload)
        except Exception:
            raise HTTPException(status_code=400, detail="Body is a string but not valid JSON")

    # 2) {"runjson": "..."} / {"data": "..."} / {"payload": "..."} 형태 처리
    if isinstance(input_payload, dict):
        for key in ("runjson", "data", "payload"):
            if key in input_payload and isinstance(input_payload[key], str):
                try:
                    input_payload = json.loads(input_payload[key])
                except Exception:
                    raise HTTPException(status_code=400, detail=f"'{key}' is not valid JSON string")

    # 3) items 래핑 해제
    if isinstance(input_payload, dict) and "items" in input_payload and isinstance(input_payload["items"], list):
        input_payload = input_payload["items"]

    # 4) 각 아이템 키 보정
    if isinstance(input_payload, list):
        fixed = []
        for it in input_payload:
            fixed.append(_coerce_item_keys(it) if isinstance(it, dict) else it)
        # 디버그: 첫 아이템 상태 확인
        if DEBUG and fixed:
            try:
                print("[DEBUG] first_item_keys=", list(fixed[0].keys()))
                print("[DEBUG] title_len=", len((fixed[0].get("title") or "")))
                print("[DEBUG] text_len=", len((fixed[0].get("text") or "")))
            except Exception:
                pass
        return fixed

    # dict 단일 아이템이면 리스트로 감싸기
    if isinstance(input_payload, dict):
        one = _coerce_item_keys(input_payload)
        if DEBUG:
            try:
                print("[DEBUG] single_item_keys=", list(one.keys()))
                print("[DEBUG] title_len=", len((one.get("title") or "")))
                print("[DEBUG] text_len=", len((one.get("text") or "")))
            except Exception:
                pass
        return [one]

    # 그 외는 그대로 (파이프라인이 처리)
    return input_payload

def _run_pipeline(input_payload: Any) -> Any:
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "input.json")
        out_path = os.path.join(td, "output.json")

        payload = _normalize_payload_for_pipeline(input_payload)

        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        p = subprocess.run(
            [PY_CMD, RUN_SCRIPT, in_path, out_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        if p.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"pipeline failed (code {p.returncode}). log:\n{p.stdout[-4000:]}"
            )

        if not os.path.exists(out_path):
            raise HTTPException(status_code=500, detail="output.json not found")

        with open(out_path, "r", encoding="utf-8") as f:
            out = json.load(f)

        if DEBUG:
            # 파이프라인 로그 꼬리와 함께 반환 (원인 추적 용이)
            return {"output": out, "log_tail": p.stdout[-4000:], "script": RUN_SCRIPT}
        return out

# -----------------------------
# 헬스/레디
# -----------------------------
@app.get("/healthz")
def healthz():
    return "ok"

@app.get("/readyz")
def readyz():
    return {
        "ready": True,
        "script": RUN_SCRIPT,
        "GROQ_API_KEY_set": bool(os.getenv("GROQ_API_KEY")),
        "GROQ_MODEL": os.getenv("GROQ_MODEL"),
    }

# -----------------------------
# 실행 엔드포인트들
# -----------------------------
@app.post("/run/json")
async def run_json(
    payload: Any = Body(..., media_type="application/json"),
    authorization: Optional[str] = Header(None),
):
    """
    - Content-Type: application/json
    - 허용 입력: {"items":[...]} / [...] / {"runjson":"..."} / {"data":"..."} / {"payload":"..."} / "JSON문자열"
    """
    _check_auth(authorization)
    return _run_pipeline(payload)

@app.post("/run/raw")
async def run_raw(
    runjson: str = Form(..., description="JSON string"),
    authorization: Optional[str] = Header(None),
):
    """
    - Content-Type: application/x-www-form-urlencoded 또는 multipart/form-data
    - 필드명 runjson 에 JSON 문자열을 담아 전송
    """
    _check_auth(authorization)
    try:
        data = json.loads(runjson)
    except Exception:
        raise HTTPException(status_code=400, detail="runjson is not valid JSON")
    return _run_pipeline(data)

@app.post("/run/file")
def run_file(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, file.filename or "input.json")
        out_path = os.path.join(td, "output.json")
        with open(in_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        p = subprocess.run(
            [PY_CMD, RUN_SCRIPT, in_path, out_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        if p.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"pipeline failed (code {p.returncode}). log:\n{p.stdout[-4000:]}"
            )

        if not os.path.exists(out_path):
            raise HTTPException(status_code=500, detail="output.json not found")

        with open(out_path, "r", encoding="utf-8") as f:
            out = json.load(f)

        if DEBUG:
            return {"output": out, "log_tail": p.stdout[-4000:], "script": RUN_SCRIPT}
        return out
