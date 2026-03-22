"""
Language detection using langdetect.

- Type A: detect on the full cleaned body.
- Type B: detect only on the extracted feedback content (not template labels).

Returns an ISO 639-1 code (e.g. "en", "ru", "zh-CN").
"""
from __future__ import annotations

import structlog
from langdetect import detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

log = structlog.get_logger(__name__)

# Fix random seed for reproducible results
DetectorFactory.seed = 0

# Normalise some codes to BCP 47 style
_NORMALISE_MAP = {
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
    "zh": "zh-CN",
}

_MIN_TEXT_LENGTH = 10
_MIN_CJK_LENGTH = 4

_CJK_RANGES = (
    ("\u4e00", "\u9fff"),   # CJK Unified Ideographs (shared by zh/ja/ko)
    ("\u3040", "\u309f"),   # Hiragana (Japanese only)
    ("\u30a0", "\u30ff"),   # Katakana (Japanese only)
    ("\uac00", "\ud7af"),   # Hangul Syllables (Korean only)
)

_HANGUL_RANGE = ("\uac00", "\ud7af")
_HIRAGANA_RANGE = ("\u3040", "\u309f")
_KATAKANA_RANGE = ("\u30a0", "\u30ff")


def _has_cjk(text: str) -> bool:
    """Return True if text contains any CJK/Japanese/Korean characters."""
    for ch in text:
        for lo, hi in _CJK_RANGES:
            if lo <= ch <= hi:
                return True
    return False


def _has_chars_in_range(text: str, *ranges: tuple[str, str]) -> bool:
    for ch in text:
        for lo, hi in ranges:
            if lo <= ch <= hi:
                return True
    return False


def _fix_cjk_misdetection(code: str, text: str) -> str:
    """Correct ko/ja misdetection when text contains only CJK ideographs.

    langdetect often confuses short Chinese text with Korean or Japanese
    because CJK Unified Ideographs are shared across all three languages.
    If the detected language is ko but no Hangul syllables are present,
    or ja but no Hiragana/Katakana are present, override to zh-CN.
    """
    if code == "ko" and not _has_chars_in_range(text, _HANGUL_RANGE):
        log.debug("cjk_fix_ko_to_zh", original=code)
        return "zh-CN"
    if code == "ja" and not _has_chars_in_range(text, _HIRAGANA_RANGE, _KATAKANA_RANGE):
        log.debug("cjk_fix_ja_to_zh", original=code)
        return "zh-CN"
    return code


def detect_language(text: str, fallback: str = "en") -> str:
    """
    Detect the language of *text*.

    Returns a BCP 47-ish language code.  Falls back to `fallback` if detection
    fails or the text is too short.

    CJK text uses a lower minimum length threshold since each character
    carries significantly more meaning than a Latin letter.
    """
    stripped = text.strip() if text else ""
    min_len = _MIN_CJK_LENGTH if _has_cjk(stripped) else _MIN_TEXT_LENGTH
    if not stripped or len(stripped) < min_len:
        log.warning("language_detect_text_too_short", length=len(stripped))
        return fallback

    try:
        code = detect(text)
        code = _fix_cjk_misdetection(code, stripped)
        normalised = _NORMALISE_MAP.get(code, code)
        log.debug("language_detected", code=normalised)
        return normalised
    except LangDetectException:
        log.warning("language_detect_failed", text_snippet=text[:80])
        return fallback
