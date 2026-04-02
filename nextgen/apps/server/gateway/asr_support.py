"""ASR 事件解析与热词判断支持代码。"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple


def shorten_text(text: str, limit: int = 200) -> str:
    """缩短调试文本。"""

    if not text:
        return ""
    return text if len(text) <= limit else f"{text[:limit]}…"


def safe_to_dict(value: Any) -> Dict[str, Any]:
    """尽可能将事件对象转为字典。"""

    if isinstance(value, dict):
        return value

    for attr in ("to_dict", "model_dump", "__dict__"):
        try:
            candidate = getattr(value, attr, None)
        except Exception:
            candidate = None

        if callable(candidate):
            try:
                data = candidate()
            except Exception:
                data = None
            if isinstance(data, dict):
                return data
        elif isinstance(candidate, dict):
            return candidate

    try:
        text = str(value)
        if text and text.lstrip().startswith("{") and text.rstrip().endswith("}"):
            return json.loads(text)
    except Exception:
        pass

    return {"_raw": str(value)}


def extract_sentence(event_obj: Any) -> Tuple[Optional[str], Optional[bool]]:
    """从 ASR 事件中提取句子文本与是否句末。"""

    data = safe_to_dict(event_obj)
    candidates: List[Dict[str, Any]] = [data]

    for key in ("output", "data", "result"):
        value = data.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    for item in candidates:
        sentence = item.get("sentence")
        if isinstance(sentence, dict):
            text = sentence.get("text")
            is_end = sentence.get("sentence_end")
            if is_end is not None:
                is_end = bool(is_end)
            return text, is_end

    for item in candidates:
        if "text" in item and isinstance(item.get("text"), str):
            return item.get("text"), None

    return None, None


def normalize_cn_text(text: str) -> str:
    """归一化中文文本，便于做热词匹配。"""

    try:
        import unicodedata

        text = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text)
        return text.strip().lower()
    except Exception:
        return (text or "").strip().lower()


class InterruptKeywordMatcher:
    """热词匹配器。"""

    def __init__(self, keywords: Optional[List[str]] = None) -> None:
        """初始化热词匹配器。"""

        if keywords is None:
            keywords = os.getenv("INTERRUPT_KEYWORDS", "停下,别说了,停止").split(",")
        self.keywords = [normalize_cn_text(item) for item in keywords if normalize_cn_text(item)]

    def has_hotword(self, text: str) -> bool:
        """判断文本中是否命中热词。"""

        normalized = normalize_cn_text(text)
        if not normalized:
            return False
        return any(keyword in normalized for keyword in self.keywords)
