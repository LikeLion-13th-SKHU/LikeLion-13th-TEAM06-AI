#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import sys
import shutil
from pathlib import Path
from typing import Any, Dict, List

from processors import build_processor, safe_debug

# ---- .env 로드 ----
try:
    from dotenv import load_dotenv
    load_dotenv()
    safe_debug("[pipeline] .env loaded")
except Exception as e:
    safe_debug(f"[pipeline] .env load skipped ({e})")

# __pycache__ 정리
for cache_dir in Path(".").rglob("__pycache__"):
    try:
        shutil.rmtree(cache_dir)
        print(f"[CLEAN] __pycache__ 삭제: {cache_dir}")
    except Exception as e:
        print(f"[WARN] __pycache__ 삭제 실패: {cache_dir} ({e})")

# ---------------- 공통 유틸 ----------------

def _read_text_any_encoding(path: Path) -> str:
    """utf-8 → utf-8-sig → cp949 순으로 시도해 읽는다."""
    exc = None
    for enc in ("utf-8", "utf-8-sig", "cp949"):
        try:
            s = path.read_text(encoding=enc)
            safe_debug(f"[pipeline] read '{path}' with {enc}")
            return s
        except Exception as e:
            exc = e
    raise exc

# 후보 키들
_CAND_LIST_KEYS = ["items", "data", "list", "rows", "results", "records", "news", "articles"]
_TEXT_KEYS = ["contents", "content", "text", "body", "description", "desc", "article", "contentBody", "content_html", "html"]
_TITLE_KEYS = ["title", "headline", "subject", "name"]

def _is_item_dict(d: Dict[str, Any]) -> bool:
    if not isinstance(d, dict):
        return False
    return any((k in d) and isinstance(d[k], (str, bytes)) for k in _TEXT_KEYS)

def _extract_candidate_items_anywhere(node: Any, found: List[Dict[str, Any]]):
    """중첩 구조 어디서든 텍스트가 있는 dict 리스트를 찾아준다."""
    if isinstance(node, list):
        ok = [x for x in node if isinstance(x, dict)]
        if ok and any(_is_item_dict(x) for x in ok):
            found.extend(ok)
            return
        for x in node:
            _extract_candidate_items_anywhere(x, found)
    elif isinstance(node, dict):
        # 흔한 리스트 키 먼저 확인
        for key in _CAND_LIST_KEYS:
            v = node.get(key)
            if isinstance(v, list):
                ok = [x for x in v if isinstance(x, dict)]
                if ok and any(_is_item_dict(x) for x in ok):
                    found.extend(ok)
                else:
                    _extract_candidate_items_anywhere(v, found)
        # 나머지 값도 재귀
        for v in node.values():
            _extract_candidate_items_anywhere(v, found)

# ---------------- HTML 처리 ----------------

def _local_strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<(script|style).*?>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<.*?>", " ", text, flags=re.DOTALL)
    from html import unescape as _unescape
    text = _unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()

try:
    import transform as _t
    if hasattr(_t, "strip_html"):
        strip_html = _t.strip_html
        safe_debug("[pipeline] transform.strip_html 사용")
    else:
        strip_html = _local_strip_html
        safe_debug("[pipeline] transform 모듈에 strip_html 없음 → 로컬 함수 사용")
except Exception as e:
    strip_html = _local_strip_html
    safe_debug(f"[pipeline] transform 임포트 실패({e}) → 로컬 함수 사용")

_TAG_RX = re.compile(r"<[a-zA-Z][^>]*>")
def _has_html(s: str) -> bool:
    return bool(s and _TAG_RX.search(s))

def _coerce_id(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None

def _get_news_id(item: dict):
    exact_keys = [
        "NewsItemId", "news_item_id",
        "newsIdentifyId", "newsIdentifyID", "newsidentifyid",
        "newsId", "newsID",
        "id",
    ]
    for k in exact_keys:
        if k in item:
            v = _coerce_id(item.get(k))
            if v:
                return v
    # 느슨한 탐지
    for k, v in item.items():
        key_norm = re.sub(r"[^a-z0-9]", "", k.lower())
        if ("news" in key_norm and "id" in key_norm) or ("identify" in key_norm and "id" in key_norm):
            vv = _coerce_id(v)
            if vv:
                return vv
    return None

# ---------------- 핵심: 입력 로더(다중 루트/배열 연속/JSONL 보정) ----------------

def _parse_multiple_json_values(text: str) -> List[Any]:
    """
    파일에 JSON 값이 여러 개 연달아 붙은 경우 전부 파싱.
    예) [..][..]   {...}{...}   [..]{..} 등
    """
    dec = json.JSONDecoder()
    i = 0
    n = len(text)
    values = []
    while i < n:
        # 공백 스킵
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        try:
            val, j = dec.raw_decode(text, i)
            values.append(val)
            i = j
        except json.JSONDecodeError:
            # 흔한 오염: 인접 배열 `][` 또는 `] [`
            if i + 1 < n and ((text[i] == ']' and text[i+1] == '[') or (text[i:i+2] == '] ' and i + 2 < n and text[i+2] == '[')):
                safe_debug("[pipeline] detected adjacent arrays while decoding → injecting ','")
                text = text[:i] + ',' + text[i+1:]
                n = len(text)
                continue
            # 더 진행 불가
            raise
    return values

def _merge_values_into_items(values: List[Any]) -> List[Dict[str, Any]]:
    """파싱된 값들을 뉴스 아이템 리스트로 합친다."""
    items: List[Dict[str, Any]] = []
    for val in values:
        if isinstance(val, list):
            for x in val:
                if isinstance(x, dict):
                    items.append(x)
        elif isinstance(val, dict):
            # 흔한 리스트 키 우선 사용
            hit = False
            for key in _CAND_LIST_KEYS:
                v = val.get(key)
                if isinstance(v, list):
                    for x in v:
                        if isinstance(x, dict):
                            items.append(x)
                    hit = True
                    break
            if not hit:
                items.append(val)
        # 문자열/숫자 등은 무시
    return items

def _load_input(path: Path) -> List[Dict[str, Any]]:
    """
    지원:
    - JSON Lines(.jsonl)
    - 단일 JSON (list/dict)
    - 다중 루트 JSON이 연달아 있는 형태 (ex: ][)
    - {items|data|list|rows|results|records|news|articles: [...]}
    - 깊은 중첩에서도 본문 키가 보이는 dict 리스트를 재귀 탐색
    - JSON 파싱 실패 시: 전체를 단일 본문으로 간주
    """
    text = _read_text_any_encoding(path)
    raw = text.strip()
    if not raw:
        return []

    # JSON Lines 추정 (줄마다 완전한 객체)
    if raw.lstrip().startswith("{") and "\n" in raw:
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        try:
            parsed = [json.loads(ln) for ln in lines]
            if parsed and all(isinstance(x, dict) for x in parsed):
                safe_debug("[pipeline] JSONL 모드로 파싱")
                return parsed
        except Exception:
            pass

    values: List[Any] = []
    # 1차: 한 방에 파싱
    try:
        data = json.loads(raw)
        values = [data]
    except Exception:
        # 실패 → 다중 값 파서 (][, }{ 등)
        try:
            values = _parse_multiple_json_values(raw)
            safe_debug(f"[pipeline] multi-root JSON 파싱: {len(values)}개 값")
        except Exception:
            # JSON 아님 → 통째로 본문 1건
            safe_debug("[pipeline] JSON 파싱 실패 → plain text 단일 아이템으로 처리")
            return [{"title": "", "text": raw}]

    # 1차 머지
    items = _merge_values_into_items(values)

    # ★ 추가: 최상위가 '빈 리스트'로만 읽힌 경우 재시도
    if not items and values and isinstance(values[0], list) and len(values[0]) == 0:
        safe_debug("[pipeline] top-level empty list → 다중 값 파서로 재시도")
        try:
            values = _parse_multiple_json_values(raw)
            items = _merge_values_into_items(values)
        except Exception:
            pass

    # 그래도 없으면 래핑/딥서치
    if not items and values and isinstance(values[0], dict):
        found: List[Dict[str, Any]] = []
        _extract_candidate_items_anywhere(values[0], found)
        if found:
            safe_debug(f"[pipeline] 딥서치로 {len(found)}개 추출")
            items = found

    if not items and values and isinstance(values[0], list):
        items = [x for x in values[0] if isinstance(x, dict)]

    # ★ 최후수단: 여전히 0개면 raw 전체를 단일 본문으로 변환
    if not items:
        safe_debug("[pipeline] items=0 → raw를 단일 본문으로 강제 변환")
        return [{"title": "", "text": raw}]

    safe_debug(f"[pipeline] raw_items={len(items)} (샘플 keys: {list(items[0].keys()) if items else 'N/A'})")
    return items

# ---------------- 정규화 ----------------

def _normalize_items(items: List[Dict[str, Any]]):
    norm = []
    for it in items:
        # title 후보
        title = ""
        for k in _TITLE_KEYS:
            if k in it and isinstance(it[k], str):
                title = it[k]
                break

        # 본문 후보
        contents = ""
        for k in _TEXT_KEYS:
            if k in it and isinstance(it[k], str):
                contents = it[k]
                break

        plain = strip_html(contents)
        norm.append({
            "NewsItemId": _get_news_id(it),
            "title": title,
            "contents": contents,
            "plain_text": plain,
            "has_html": _has_html(contents),
        })
    return norm

def _pad4(xs: List[str]) -> List[str]:
    xs = [x for x in (xs or []) if isinstance(x, str) and x.strip()]
    xs = list(dict.fromkeys(xs))[:4]
    while len(xs) < 4:
        xs.append("")
    return xs

# ---------------- 메인 ----------------

def main():
    if len(sys.argv) != 3:
        print("사용법: python pipeline.py <input.json|.jsonl> <output.json>")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    if not in_path.exists():
        print(f"[ERROR] 입력 파일을 찾을 수 없습니다: {in_path}")
        sys.exit(2)

    raw_items = _load_input(in_path)
    norm_items = _normalize_items(raw_items)
    safe_debug(f"[pipeline] norm_items={len(norm_items)} (첫 item 본문 길이: {len(norm_items[0].get('plain_text','')) if norm_items else 0})")

    proc = build_processor()
    results = []

    total = len(norm_items)
    for i, item in enumerate(norm_items, start=1):
        safe_debug(f"[{i}/{total}] 처리 중: {item.get('title','(제목없음)')}")
        text = item.get("plain_text", "")
        title = item.get("title")

        summary_lines = proc.summarize(text, title=title) or []
        primary_cat, subcats = proc.classify(text, title=title)
        region = proc.detect_region(text, title=title) or "전국"

        results.append({
            "NewsItemId": item.get("NewsItemId"),
            "title": item.get("title"),
            "summary": "\n".join(summary_lines[:3]),
            "summary_lines": summary_lines[:3],
            "category": primary_cat,
            "subcategories": _pad4(subcats),
            "region": region,
            "source_meta": {
                "has_html": item.get("has_html", False),
                "length_chars": len(text),
            },
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 결과 저장: {out_path} (items={len(results)})")

if __name__ == "__main__":
    main()
