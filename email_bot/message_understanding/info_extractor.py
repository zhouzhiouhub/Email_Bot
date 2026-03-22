"""
Information extraction: OS, device model, software version, error text, use case.
Also performs missing-field detection and intent classification.

Uses rule-based patterns for speed; falls back to LLM extraction for complex cases.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import structlog
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

from config import settings
from models.schemas import ExtractedInfo

log = structlog.get_logger(__name__)

# ── Rule-based patterns ────────────────────────────────────────────────────────

_OS_PATTERNS = [
    (re.compile(r"\bWindows\s*(10|11|7|8\.1|8)\b", re.I), lambda m: f"Windows {m.group(1)}"),
    (re.compile(r"\bmacOS\b|\bMac OS X\b", re.I), lambda _: "macOS"),
    (re.compile(r"\bLinux\b", re.I), lambda _: "Linux"),
]

_VERSION_PATTERNS = [
    re.compile(r"[Vv]ersion\s*[:\-]?\s*([\d]+\.[\d.]+)"),
    re.compile(r"\bv([\d]+\.[\d.]+)\b"),
    re.compile(r"\b([\d]+\.[\d]+\.[\d]+)\b"),
]

_DEVICE_KEYWORDS = [
    "keyboard", "mouse", "headset", "headphone", "mousepad", "monitor",
    "hub", "controller", "fan", "cooler", "led strip",
    # add product model patterns here
]
_DEVICE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _DEVICE_KEYWORDS) + r")\b", re.I
)

_ERROR_PATTERN = re.compile(
    r"(error\s*[\d\w]+|exception|0x[0-9a-fA-F]+|\w+\.dll|\w+Error)", re.I
)

_INTENT_KEYWORDS = {
    "refund": "complaint",
    "warranty": "complaint",
    "return": "complaint",
    "complaint": "complaint",
    "not working": "bug_report",
    "crash": "bug_report",
    "bug": "bug_report",
    "how to": "feature_inquiry",
    "can i": "feature_inquiry",
    "support": "feature_inquiry",
    "compatible": "feature_inquiry",
}

# Fields whose absence triggers NEED_MORE_INFO (require ≥2 missing to trigger)
_REQUIRED_FIELDS = ["os", "device_model", "software_version"]

# Sensitive topics → force human review
_SENSITIVE_PATTERNS = re.compile(
    r"\b(refund|warranty|DRM|bypass|crack|pirat|法律|诉讼|投诉升级)\b", re.I
)

# Video-needed keywords
_VIDEO_NEEDED_PATTERNS = re.compile(
    r"\b(flicker|闪烁|不亮|not bright|not sync|不同步|识别不了|not detected)\b", re.I
)

# Pre-install: user cannot obtain the software (download / installer). KB is mostly post-install.
_ACQUISITION_ISSUE_PATTERNS = (
    re.compile(r"\b(can'?t|cannot|unable to)\s+download\b", re.I),
    re.compile(r"\bfailed to download\b", re.I),
    re.compile(r"\b(download|downloading)\s+(this|the|it|software|program)\b", re.I),
    re.compile(
        r"\b(from|on)\s+(your|the|yr)\s+site\b.{0,120}\b(download|version|installer|file|link)\b",
        re.I,
    ),
    re.compile(
        r"\b(download|link).{0,80}\b(from|on)\s+(your|the|yr)\s+site\b",
        re.I,
    ),
    re.compile(r"\bsend\s+me\s+.+\s+(via|by|through)\s+e?-?mail\b", re.I),
    re.compile(r"\b(mirror|alternative)\s+(link|download|host)\b", re.I),
    re.compile(r"\blink\s+(is\s+)?(broken|dead)\b", re.I),
    re.compile(r"无法下载|下载不了|下载失败|下不了|安装包.{0,8}(没有|无法|不能|找不到)"),
    re.compile(r"官网.{0,8}(下载|安装包)|从.{0,6}站.{0,6}下载"),
)


def _rule_extract(text: str) -> ExtractedInfo:
    os_val: Optional[str] = None
    for pattern, formatter in _OS_PATTERNS:
        m = pattern.search(text)
        if m:
            os_val = formatter(m)
            break

    version_val: Optional[str] = None
    for vp in _VERSION_PATTERNS:
        m = vp.search(text)
        if m:
            version_val = m.group(1)
            break

    device_val: Optional[str] = None
    m = _DEVICE_PATTERN.search(text)
    if m:
        device_val = m.group(1)

    error_val: Optional[str] = None
    m = _ERROR_PATTERN.search(text)
    if m:
        error_val = m.group(1)

    intent = "general"
    lower = text.lower()
    for kw, cat in _INTENT_KEYWORDS.items():
        if kw in lower:
            intent = cat
            break

    missing = [f for f in _REQUIRED_FIELDS if locals().get(f"{f}_val") is None]
    # Rename: os_val→os, etc.
    missing_clean = [
        f.replace("_val", "") if f.endswith("_val") else f for f in missing
    ]
    # Correct mapping
    missing_fields: list[str] = []
    if os_val is None:
        missing_fields.append("os")
    if device_val is None:
        missing_fields.append("device_model")
    if version_val is None:
        missing_fields.append("software_version")

    return ExtractedInfo(
        os=os_val,
        device_model=device_val,
        software_version=version_val,
        error_text=error_val,
        missing_fields=missing_fields,
        intent=intent,
    )


_EXTRACTION_SYSTEM_PROMPT = """\
You are an information extraction assistant.
Given a customer support email body, extract the following fields as JSON:
{
  "os": "<Windows 10 | Windows 11 | macOS | Linux | null>",
  "device_model": "<device name/model or null>",
  "software_version": "<version string or null>",
  "error_text": "<exact error message or null>",
  "use_case": "<brief description of what the user was doing or null>",
  "intent": "<bug_report | feature_inquiry | complaint | general>"
}
Return ONLY valid JSON. No explanation.
"""


def _llm_extract(text: str) -> Optional[ExtractedInfo]:
    try:
        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0,
        )
        response = llm.invoke([
            SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=text[:3000]),
        ])
        data = json.loads(response.content)
        info = ExtractedInfo(
            os=data.get("os"),
            device_model=data.get("device_model"),
            software_version=data.get("software_version"),
            error_text=data.get("error_text"),
            use_case=data.get("use_case"),
            intent=data.get("intent", "general"),
        )
        # Compute missing fields
        for field in _REQUIRED_FIELDS:
            attr = field  # os, device_model, software_version
            if getattr(info, attr) is None:
                info.missing_fields.append(attr)
        return info
    except Exception:
        log.exception("llm_extraction_failed")
        return None


def extract_info(text: str, use_llm_fallback: bool = True) -> ExtractedInfo:
    """
    Extract structured information from email body text.
    Uses rule-based extraction first, then LLM fallback if >2 fields are missing.
    """
    info = _rule_extract(text)

    # Use LLM if too many missing fields and LLM fallback is enabled
    if len(info.missing_fields) >= 3 and use_llm_fallback:
        llm_info = _llm_extract(text)
        if llm_info:
            # Merge: LLM fills gaps
            if not info.os and llm_info.os:
                info.os = llm_info.os
            if not info.device_model and llm_info.device_model:
                info.device_model = llm_info.device_model
            if not info.software_version and llm_info.software_version:
                info.software_version = llm_info.software_version
            if not info.error_text and llm_info.error_text:
                info.error_text = llm_info.error_text
            if not info.use_case and llm_info.use_case:
                info.use_case = llm_info.use_case
            info.intent = llm_info.intent

            # Recompute missing
            info.missing_fields = [
                f for f in _REQUIRED_FIELDS if getattr(info, f) is None
            ]

    log.debug("info_extracted", missing=info.missing_fields, intent=info.intent)
    return info


def is_sensitive(text: str) -> bool:
    """Check for refund/warranty/DRM-related content that requires human review."""
    return bool(_SENSITIVE_PATTERNS.search(text))


def needs_video_evidence(text: str) -> bool:
    """Check if the issue is better diagnosed with a video (flickering, sync issues, etc.)."""
    return bool(_VIDEO_NEEDED_PATTERNS.search(text))


def is_software_acquisition_issue(text: str) -> bool:
    """
    True if the user is still trying to get the installer / download (pre-install).
    FAQs are dominated by post-install issues; do not route to MORE_INFO for missing OS/device alone.
    """
    if not text or len(text.strip()) < 8:
        return False
    return any(p.search(text) for p in _ACQUISITION_ISSUE_PATTERNS)


def should_request_more_info(text: str, info: ExtractedInfo) -> bool:
    """
    True if we need to ask the user for more details before proceeding.
    Conditions (any one triggers):
      - Body text < 20 chars
      - All three required fields missing
      - ≥ 2 missing fields
    Skipped when user still cannot obtain the software (acquisition issue → human instead).
    """
    if is_software_acquisition_issue(text):
        return False
    if len(text.strip()) < 20:
        return True
    if len(info.missing_fields) >= 2:
        return True
    return False
