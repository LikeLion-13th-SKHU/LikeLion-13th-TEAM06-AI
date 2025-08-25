# -*- coding: utf-8 -*-
import os
import re
import json
from collections import Counter
from typing import List, Tuple, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from llm_client import LLMClient, LLMUnavailable

DEBUG = os.getenv("DEBUG", "0").lower() in ("1", "true", "yes")
MIN_TEXT_CHARS = int(os.getenv("MIN_TEXT_CHARS", "50"))
MAX_SUMMARY_LINES = 3

# ======================
#   카테고리 정의(틀 유지)
# ======================
_PRIMARY_CATEGORIES = [
    "정책_정부", "산업_기업", "연구_기술", "규제_제도",
    "수출_글로벌", "투자_금융", "인사_조직", "사회", "기타"
]

# 범주 가이드(LLM용)
_CATEGORY_GUIDE: Dict[str, str] = {
    "정책_정부": "정부 정책/사업 공지, 부처 발표, 공공행사 안내, 정부주도 캠페인",
    "산업_기업": "기업 활동, 산업 동향, 신제품/서비스, 생산/공장/유통, 산업안전",
    "연구_기술": "연구 성과, 신기술/과학, R&D, AI/디지털 전환",
    "규제_제도": "법/시행령/고시 개정, 규제 완화/강화, 제도 개선",
    "수출_글로벌": "수출, 무역, 해외 진출, 국제협력/외교 경제",
    "투자_금융": "금융 정책/지원, 대출/보험/금융사, 투자/펀드/증권",
    "인사_조직": "채용/고용/임금/노사, 조직 개편/인사 이동",
    "사회": "지역 행사/축제/문화/복지/교육, 재난/안전/보건, 시민 대상 서비스",
    "기타": "상기 어디에도 명확히 속하지 않는 경우"
}

# 서브카테고리 힌트(LLM 참고용)
_SUBCATEGORY_HINTS = [
    "정책", "제도개선", "규제완화", "금융지원", "세제", "투자유치",
    "수출", "무역", "글로벌진출", "고용", "채용", "노사",
    "안전관리", "재난대응", "환경", "에너지", "디지털전환",
    "R&D", "AI", "혁신", "지역", "지자체", "행사", "문화",
    "소비자보호", "보험", "대출", "노동법", "근로시간", "복지", "교육"
]

# 지역 감지
_REGION_KWS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산",
    "세종", "경기", "강원", "충북", "충남", "전북", "전남",
    "경북", "경남", "제주",
]
_REGION_ALIASES: Dict[str, str] = {
    "서울시": "서울", "부산시": "부산", "대구시": "대구", "인천시": "인천",
    "광주시": "광주", "대전시": "대전", "울산시": "울산", "세종시": "세종",
    "경기도": "경기", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전라북도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주도": "제주", "수도권": "경기",
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
        s = s.replace("R & D", "R&D").replace("r&d", "R&D")
        if s.lower() in ("ai", "인공지능"):
            s = "AI"
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
    blob = (text or "").strip()
    if not blob:
        return []
    blob = re.sub(r"\s+", " ", blob)
    sents = re.split(r"(?<=[\.!?]|다)\s+", blob)
    sents = [s.strip() for s in sents if s and len(s.strip()) > 4]
    return sents

# 간단 키워드 추출(품사기반 없이 빈도 기반)
_STOPWORDS = {
    "및", "그리고", "등", "으로", "에서", "대한", "관련", "이번", "지난", "통해",
    "대해", "을", "를", "은", "는", "이", "가", "와", "과", "또는", "또", "더", "등의",
    "한다", "했다", "합니다", "이다", "한다는", "있는", "없는", "가장", "최대", "최소",
    "서울시", "부산시", "대구시", "인천시", "광주시", "대전시", "울산시", "세종시"
}
def _auto_keywords(text: str, topk: int = 8) -> List[str]:
    tokens = re.split(r"[^0-9A-Za-z가-힣]+", text or "")
    tokens = [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS]
    cnt = Counter(tokens)
    return [w for w, _ in cnt.most_common(topk)]

# ======================
#   Processor
# ======================
class Processor:
    def __init__(self):
        try:
            self.llm = LLMClient.from_env()
            safe_debug("[processors] LLM 사용 모드")
        except LLMUnavailable:
            self.llm = None
            safe_debug("[processors] 룰베이스 모드")

    # -------- 요약 --------
    def summarize(self, text: str, title: str = None) -> List[str]:
        text = (text or "").strip()
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
        return self._fallback_summary(text, title)

    def _fallback_summary(self, text: str, title: str = None) -> List[str]:
        sents = _split_sentences_ko(text)
        def score(s: str) -> int:
            sc = 0
            if re.search(r"\d{4}년|\d+월|\d+일|\d+%", s): sc += 3
            if re.search(r"[0-9][0-9,\.]{0,6}", s): sc += 2
            if re.search(r"(부|청|처|원|공사|위원회|정부|부처)", s): sc += 2
            if re.search(r"(지원|확대|개선|도입|발표|시행|확정|투자|수출|안전|출시)", s): sc += 2
            if len(s) >= 20: sc += 1
            return sc
        picked = sorted(sents, key=score, reverse=True)[:MAX_SUMMARY_LINES]
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

    # -------- 분류 --------
    def classify(self, text: str, title: str = None) -> Tuple[str, List[str]]:
        blob = f"{title or ''}\n{text or ''}".strip()
        region = self.detect_region(text, title)

        # LLM 우선 + 엄격 프롬프트
        if self.llm and len(blob) >= 1:
            try:
                js = self.llm.json_chat(
                    self._category_prompt(text, title, region),
                    max_tokens=300
                ) or {}
                primary = _normalize_primary(js.get("primary"))
                subs = _normalize_subs(js.get("subcategories") or [])

                # 정책_정부 치우침 방지: 강한 비정책 신호가 있으면 교정
                primary = self._debias_primary(primary, text, title, region)

                # 서브카테고리 4개 채우기(자동 키워드 보강)
                if subs.count("") > 0:
                    auto = self._suggest_subs_from_text(text, title, region, primary)
                    subs = _normalize_subs(subs + auto)

                return primary, subs
            except Exception as e:
                safe_debug(f"[processors] LLM 분류 실패 → 백업 사용: {e}")

        # LLM 불가 시 룰베이스
        return self._fallback_classify(text, title, region)

    def _suggest_subs_from_text(self, text: str, title: str, region: str, primary: str) -> List[str]:
        base = (title or "") + " " + (text or "")
        keys = _auto_keywords(base, topk=12)
        prefer = []

        # 카테고리별 선호 키워드
        pref_map = {
            "사회": ["행사", "축제", "문화", "복지", "교육", "시민", "청년", "노인"],
            "투자_금융": ["금융", "보험", "대출", "보조금", "세제", "지원"],
            "산업_기업": ["기업", "산업", "생산", "공장", "유통", "안전"],
            "연구_기술": ["R&D", "AI", "기술", "혁신", "연구", "개발"],
            "규제_제도": ["규제완화", "제도개선", "개정", "법안", "시행령"],
            "수출_글로벌": ["수출", "무역", "해외", "글로벌", "FTA"],
            "인사_조직": ["채용", "고용", "임금", "노사", "근로시간"],
            "정책_정부": ["정책", "정부", "부처", "위원회"]
        }
        prefer += pref_map.get(primary, [])

        # 지역 감지 시 지역/지자체 보강
        if region and region != "전국":
            prefer += [region, "지자체", "지역"]

        # 후보 결합
        out = []
        for k in prefer + keys + _SUBCATEGORY_HINTS:
            k = k.strip()
            if not k or k in out:
                continue
            out.append(k)
            if len(out) >= 6:
                break
        return out

    def _debias_primary(self, primary: str, text: str, title: str, region: str) -> str:
        t = ((title or "") + " " + (text or "")).lower()

        def has_any(terms: List[str]) -> bool:
            return any(term in t for term in terms)

        # 강한 도메인 신호가 있으면 해당 카테고리로 교정
        if has_any(["보험", "대출", "연체", "금융", "펀드", "증권", "보조금", "세제"]):
            return "투자_금융"
        if has_any(["수출", "무역", "해외", "글로벌", "fta"]):
            return "수출_글로벌"
        if has_any(["r&d", "연구", "기술", "ai", "인공지능", "혁신", "디지털전환"]):
            return "연구_기술"
        if has_any(["채용", "고용", "임금", "노사", "근로시간"]):
            return "인사_조직"
        if has_any(["산업재해", "산재", "중대재해", "산업안전"]):
            return "산업_기업"

        # 지역행사/축제/문화 + 지역명 → 사회로 교정
        if region != "전국" and has_any(["행사", "축제", "기념식", "페스티벌", "공연", "전시"]):
            return "사회"

        # 규제/제도 신호
        if has_any(["규제완화", "규제 완화", "제도개선", "제도 개선", "개정", "시행령", "시행규칙"]):
            return "규제_제도"

        # 특별히 교정할 신호가 없으면 그대로
        return primary

    def _fallback_classify(self, text: str, title: str = None, region: str = "전국") -> Tuple[str, List[str]]:
        t_orig = (title or "") + " " + (text or "")
        t = t_orig.lower()

        subs: List[str] = []
        scores = {k: 0 for k in _PRIMARY_CATEGORIES}
        def bump(cat: str, n: int = 1): scores[cat] = scores.get(cat, 0) + n
        def has_any(terms: List[str]) -> bool:
            return any(term in t for term in terms)

        # 강한 신호
        if has_any(["대출", "은행", "보험", "카드", "신용", "금감원", "채무조정", "연체", "보험료", "금융지원", "보조금", "세제"]):
            bump("투자_금융", 5); subs += ["금융지원", "세제"]
        if has_any(["산업재해", "산재", "중대재해", "노동안전", "안전사고", "산업안전"]):
            bump("산업_기업", 5); subs += ["안전관리"]
        if has_any(["수출", "무역", "해외", "글로벌", "fta"]):
            bump("수출_글로벌", 5); subs += ["수출"]
        if has_any(["r&d", "연구", "기술", "ai", "인공지능", "혁신", "디지털전환"]):
            bump("연구_기술", 5); subs += ["R&D", "AI"]
        if has_any(["채용", "고용", "임금", "노사", "근로시간"]):
            bump("인사_조직", 4); subs += ["채용"]

        # 사회/행사
        if has_any(["행사", "축제", "기념식", "경축식", "초대합니다", "페스티벌", "시민 참여", "광복", "문화", "공연", "전시"]):
            bump("사회", 4); subs += ["행사", "문화"]

        # 지역경제/소비 진작
        if has_any(["농축산물", "전통시장", "온누리상품권", "하나로마트", "할인", "쿠폰", "소비진작", "환급"]):
            bump("사회", 3); subs += ["지역경제"]

        # 규제/제도
        if has_any(["규제 완화", "규제완화", "제도 개선", "제도개선", "개정", "시행령", "시행규칙", "법안", "조례"]):
            bump("규제_제도", 3); subs += ["제도개선", "규제완화"]

        # 정책/정부는 기본 낮게, 강한 신호만 가산
        if has_any(["대통령", "국무회의", "국정", "국정운영", "청와대", "국무위원"]):
            bump("정책_정부", 3); subs += ["정책"]
        if has_any(["정부", "부처", "위원회", "정책", "정부합동", "국정브리핑"]):
            bump("정책_정부", 1); subs += ["정책"]

        # 지역행사 + 지역명 → 사회 가산
        if region != "전국" and has_any(["행사", "축제", "기념식", "페스티벌"]):
            bump("사회", 2)

        # 타이브레이커
        max_score = max(scores.values()) if scores else 0
        if max_score == 0:
            primary = "기타"
        else:
            tied = [cat for cat, sc in scores.items() if sc == max_score]
            if len(tied) == 1:
                primary = tied[0]
            else:
                non_policy = [c for c in tied if c != "정책_정부"]
                primary = non_policy[0] if non_policy else tied[0]

        # 서브카테고리 보강
        subs = _normalize_subs(subs)
        if subs.count("") > 0:
            subs = _normalize_subs(subs + self._suggest_subs_from_text(text, title, region, primary))

        return primary, subs

    def _category_prompt(self, text: str, title: str = None, region: str = "전국") -> str:
        cats = ", ".join(_PRIMARY_CATEGORIES)
        guide = json.dumps(_CATEGORY_GUIDE, ensure_ascii=False, indent=2)
        hints = ", ".join(_SUBCATEGORY_HINTS)
        region_hint = f"(참고 지역: {region})" if region else ""
        return (
            "너는 한국어 뉴스 분류기다. 다음 기사에 대해 **주카테고리 정확히 1개**와 **서브카테고리 정확히 4개**를 JSON으로만 출력하라.\n"
            f"- 주카테고리 후보: [{cats}]\n"
            f"- 카테고리 가이드:\n{guide}\n"
            f"- 서브카테고리 예시(자유 조합): [{hints}]\n"
            "- 규칙:\n"
            "  1) 본문 도메인 신호가 강하면 해당 도메인을 우선(예: 금융/보험/대출 → 투자_금융, 수출/무역 → 수출_글로벌, R&D/AI → 연구_기술, 채용/임금/노사 → 인사_조직, 지역행사/축제 → 사회).\n"
            "  2) 단지 '정부/정책'이라는 단어가 포함되었다고 해서 무조건 정책_정부로 분류하지 말 것.\n"
            "  3) 서브카테고리는 중복/유사어 금지, 실제 기사 핵심 키워드 4개로만 구성.\n"
            "  4) 출력은 JSON 하나만. 추가 설명/주석 금지.\n"
            f"{region_hint}\n"
            '출력 예시: {"primary":"사회","subcategories":["행사","문화","지역경제","지자체"]}\n'
            f"제목: {title or ''}\n본문:\n{text[:7000]}"
        )

    # -------- 지역 검출 --------
    def detect_region(self, text: str, title: str = None) -> str:
        blob = f"{title or ''}\n{text or ''}"
        for alias, base in _REGION_ALIASES.items():
            if alias in blob:
                return base
        for kw in _REGION_KWS:
            if kw in blob:
                return kw
        return "전국"

def build_processor() -> "Processor":
    return Processor()
