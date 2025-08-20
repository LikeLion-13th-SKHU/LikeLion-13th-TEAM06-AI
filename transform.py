# -*- coding: utf-8 -*-
import re
from html import unescape

_TAG_RX = re.compile(r"<[a-zA-Z][^>]*>")

def strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<(script|style).*?>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<.*?>", " ", text, flags=re.DOTALL)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()

def has_html(contents: str) -> bool:
    if not contents:
        return False
    return bool(_TAG_RX.search(contents))

def normalize_items(items):
    norm = []
    for it in items:
        contents = it.get("contents") or ""
        plain = strip_html(contents)
        norm.append({
            "NewsItemId": it.get("NewsItemId"),
            "title": it.get("title"),
            "contents": contents,
            "plain_text": plain,
            "has_html": has_html(contents),
        })
    return norm
