# -*- coding: utf-8 -*-
import os
import re
from typing import List, Tuple, Dict, Optional

# ---- .env 로드 (LLM 키 누락 시 조용히 실패하는 것 방지) ----
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from llm_client import LLMClient, LLMUnavailable

# ===== 설정값(환경변수로 조정 가능) =====
DEBUG = os.getenv("DEBUG", "0").lower() in ("1", "true", "yes")
MIN_TEXT_CHARS = int(os.getenv("MIN_TEXT_CHARS", "50"))      # 이 길이보다 짧아도 요약은 생성(더미 포함)
MAX_SUMMARY_LINES = 3

_PRIMARY_CATEGORIES = [
    "정책_정부", "산업_기업", "연구_기술", "규제_제도",
    "수출_글로벌", "투자_금융", "인사_조직", "사회", "기타"
]

_SUBCATEGORY_HINTS = [
    "정책", "제도개선", "규제완화", "금융지원", "세제", "투자유치",
    "수출", "무역", "글로벌진출", "고용", "채용", "노사",
    "안전관리", "재난대응", "환경", "에너지", "디지털전환",
    "R&D", "AI", "혁신", "지역", "지자체"
]

# 지역 키워드(기본형)
_REGION_KWS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산",
    "세종", "경기", "강원", "충북", "충남", "전북", "전남",
    "경북", "경남", "제주",
]

# 흔한 표기 → 기본형 매핑
_REGION_ALIASES: Dict[str, str] = {
    "서울시": "서울",
    "부산시": "부산",
    "대구시": "대구",
    "인천시": "인천",
    "광주시": "광주",
    "대전시": "대전",
    "울산시": "울산",
    "세종시": "세종",
    "경기도": "경기",
    "강원도": "강원",
    "충청북도": "충북",
    "충청남도": "충남",
    "전라북도": "전북",
    "전라남도": "전남",
    "경상북도": "경북",
    "경상남도": "경남",
    "제주도": "제주",
    # 축약/다른 표현
    "수도권": "경기",   # 필요시 조정
}

def safe_debug(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def _pad4(xs: List[str]) -> List[str]:
    xs = [x.strip() for x in (xs or []) if isinstance(x, str) and x.strip()]
    xs = list(dict.fromkeys(xs))[:4]
    while len(xs) < 4:
        xs.append("")
    return xs

def _normalize_primary(cat: Optional[str]) -> str:
    cat = (cat or "").strip()
    if cat in _PRIMARY_CATEGORIES:
        return cat
    # 느슨한 정규화(영문/변형 대비)
    mapping = {
        "정책/정부": "정책_정부",
        "산업/기업": "산업_기업",
        "연구/기술": "연구_기술",
        "규제/제도": "규제_제도",
        "수출/글로벌": "수출_글로벌",
        "투자/금융": "투자_금융",
        "인사/조직": "인사_조직",
    }
    return mapping.get(cat, "기타")

def _normalize_subs(subs: List[str]) -> List[str]:
    cleaned = []
    for s in subs or []:
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        # 자잘한 동어정리
        s = s.replace("R & D", "R&D").replace("r&d", "R&D")
        if s.lower() in ("ai", "인공지능"): s = "AI"
        cleaned.append(s)
    return _pad4(cleaned)

def _close_sentence(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    if re.search(r"[.!?]$", s) or re.search(r"(다|요|합니다|이다)$", s):
        return s
    return s.rstrip("…,:;") + "다"

def _split_sentences_ko(text: str) -> List[str]:
    # 한국어/영문 혼용 문장 분할 (너무 세밀하게 안 가고 안정적인 룰)
    blob = (text or "").strip()
    if not blob:
        return []
    blob = re.sub(r"\s+", " ", blob)
    # '다.' / '.', '!' '?' 뒤 공백 기준
    sents = re.split(r"(?<=[\.!?]|다)\s+", blob)
    sents = [s.strip() for s in sents if s and len(s.strip()) > 4]
    return sents

class Processor:
    def __init__(self):
        try:
            self.llm = LLMClient.from_env()
            safe_debug("[processors] LLM 사용 모드")
        except LLMUnavailable:
            self.llm = None
            safe_debug("[processors] 룰베이스 모드")

    # ===== 요약 =====
    def summarize(self, text: str, title: str = None) -> List[str]:
        text = (text or "").strip()
        # 짧아도 3줄을 보장(LLM 실패/미사용 대비)
        if self.llm and len(text) >= 1:
            try:
                js = self.llm.json_chat(
                    self._summary_prompt(text, title),
                    max_tokens=420
                ) or {}
                lines = js.get("summary_lines") or []
                lines = [ln for ln in lines if isinstance(ln, str)]
                lines = [_close_sentence(ln) for ln in lines if ln.strip()]
                if len(lines) >= 1:
                    return (lines + ["", "", ""])[:MAX_SUMMARY_LINES]
            except Exception as e:
                safe_debug(f"[processors] LLM 요약 실패 → 백업 사용: {e}")

        # Fallback
        return self._fallback_summary(text, title)

    def _fallback_summary(self, text: str, title: str = None) -> List[str]:
        sents = _split_sentences_ko(text)
        # 간단한 점수 베이스로 상위문장 선택
        def score(s: str) -> int:
            sc = 0
            if re.search(r"\d{4}년|\d+월|\d+일|\d+%", s): sc += 3
            if re.search(r"[0-9][0-9,\.]{0,6}", s): sc += 2
            if re.search(r"(부|청|처|원|공사|위원회|정부|부처)", s): sc += 2
            if re.search(r"(지원|확대|개선|도입|발표|시행|확정|투자|수출|안전|출시)", s): sc += 2
            if len(s) >= 20: sc += 1
            return sc

        picked = sorted(sents, key=score, reverse=True)[:MAX_SUMMARY_LINES]

        # 제목을 1문장으로 보강 (없거나 비슷하면 생략)
        if title and title.strip():
            if not picked or title.strip() not in picked[0]:
                picked.insert(0, title.strip())

        picked = [_close_sentence(p) for p in picked[:MAX_SUMMARY_LINES]]
        while len(picked) < MAX_SUMMARY_LINES:
            picked.append("")
        return picked[:MAX_SUMMARY_LINES]

    def _summary_prompt(self, text: str, title: str = None) -> str:
        return (
            "너는 한국어 뉴스 요약기다. 아래 기사를 완결된 문장 3줄로 요약하라.\n"
            "- 정확히 3줄, 각 줄은 독립적인 핵심 문장으로 끝맺음(다/이다/합니다 등).\n"
            "- 숫자(날짜·비율·횟수), 기관·정책명, 조치/영향을 우선 포함.\n"
            "- 불필요한 인용부호·이모지·머리표·중복 금지. 문장 중간 생략 금지.\n"
            'JSON만 출력: {"summary_lines": ["...", "...", "..."]}\n'
            f"제목: {title or ''}\n본문:\n{text[:7000]}"
        )

    # ===== 분류 =====
    def classify(self, text: str, title: str = None) -> Tuple[str, List[str]]:
        blob = f"{title or ''}\n{text or ''}".strip()
        if self.llm and len(blob) >= 1:
            try:
                js = self.llm.json_chat(
                    self._category_prompt(text, title),
                    max_tokens=260
                ) or {}
                primary = _normalize_primary(js.get("primary"))
                subs = _normalize_subs(js.get("subcategories") or [])
                return primary, subs
            except Exception as e:
                safe_debug(f"[processors] LLM 분류 실패 → 백업 사용: {e}")
        return self._fallback_classify(text, title)

    def _fallback_classify(self, text: str, title: str = None) -> Tuple[str, List[str]]:
        t = ((title or "") + " " + (text or "")).lower()
        subs = []
        scores = {k: 0 for k in _PRIMARY_CATEGORIES}

        def bump(cat, n=1): scores[cat] = scores.get(cat, 0) + n

        if any(k in t for k in ["정책", "법안", "국회", "정부", "부처", "위원회", "조례"]):
            bump("정책_정부", 2); subs.append("정책")
        if any(k in t for k in ["제도 개선", "제도개선", "규제 완화", "규제완화", "특례"]):
            bump("규제_제도", 2); subs.append("제도개선")
        if any(k in t for k in ["금융", "세제", "대출", "펀드", "투자", "보조금"]):
            bump("투자_금융", 2); subs.append("금융지원")
        if any(k in t for k in ["기업", "산업", "중소기업", "대기업", "스타트업", "공기업"]):
            bump("산업_기업", 2)
        if any(k in t for k in ["r&d", "연구", "기술", "ai", "인공지능", "혁신", "제품화"]):
            bump("연구_기술", 2); subs.append("R&D")
        if any(k in t for k in ["수출", "무역", "해외", "글로벌", "무역수지"]):
            bump("수출_글로벌", 2); subs.append("수출")
        if any(k in t for k in ["노사", "임금", "채용", "고용", "노동", "복지"]):
            bump("인사_조직", 1); subs.append("채용")
        if any(k in t for k in ["재난", "안전", "사고", "산업재해", "치안", "범죄", "보건"]):
            bump("사회", 1); subs.append("안전관리")

        primary = max(scores.items(), key=lambda x: x[1])[0]
        if scores[primary] == 0:
            primary = "기타"
        subs = _normalize_subs(subs)
        return primary, subs

    def _category_prompt(self, text: str, title: str = None) -> str:
        cats = ", ".join(_PRIMARY_CATEGORIES)
        hints = ", ".join(_SUBCATEGORY_HINTS)
        return (
            "너는 한국어 뉴스 분류기다. 다음 기사에 대해 주카테고리 1개와 서브카테고리 최대 4개를 JSON으로 출력하라.\n"
            f"- 주카테고리 후보: [{cats}]\n"
            f"- 서브카테고리 예시(참고, 자유 조합): [{hints}]\n"
            "- 서브카테고리는 기사 핵심 주제를 다양하게 포괄하되 중복/동어반복 금지.\n"
            'JSON만 출력: {"primary":"정책_정부","subcategories":["정책","금융지원","R&D","지역"]}\n'
            f"제목: {title or ''}\n본문:\n{text[:7000]}"
        )

    # ===== 지역 판별 =====
    def detect_region(self, text: str, title: str = None) -> str:
        blob = f"{title or ''}\n{text or ''}"
        # 1) alias 우선 매핑
        for alias, base in _REGION_ALIASES.items():
            if alias in blob:
                return base
        # 2) 기본 키워드
        for kw in _REGION_KWS:
            if kw in blob:
                return kw
        return "전국"

def build_processor() -> "Processor":
    return Processor()
