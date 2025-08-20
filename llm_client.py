# -*- coding: utf-8 -*-
"""
GROQ(API) 또는 OPENAI 호환 엔드포인트와 대화하여 JSON을 반환하는 경량 클라이언트.
환경변수:
- GROQ_API_KEY (필수)
- MODEL (기본: llama-3.1-8b-instant)
- GROQ_BASE_URL (옵션, 기본: https://api.groq.com/openai/v1)
LLM이 없으면 LLMUnavailable 예외로 알리고, 상위에서 백업 규칙 사용.
"""

import os
import json
import time
import requests

class LLMUnavailable(Exception):
    pass

class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.groq.com/openai/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_env(cls) -> "LLMClient":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise LLMUnavailable("GROQ_API_KEY 미설정")
        model = os.getenv("MODEL", "llama-3.1-8b-instant")
        base = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        return cls(api_key, model, base)

    def _chat(self, prompt: str, max_tokens: int = 512) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a precise JSON generator. Never include markdown fences."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def json_chat(self, prompt: str, max_tokens: int = 512) -> dict:
        # 재시도 2회
        last_err = None
        for _ in range(2):
            try:
                txt = self._chat(prompt, max_tokens=max_tokens)
                # JSON만 남도록 앞뒤 잡음 제거
                start = txt.find("{")
                end = txt.rfind("}")
                if start != -1 and end != -1 and end >= start:
                    txt = txt[start:end+1]
                return json.loads(txt)
            except Exception as e:
                last_err = e
                time.sleep(0.8)
        raise last_err
