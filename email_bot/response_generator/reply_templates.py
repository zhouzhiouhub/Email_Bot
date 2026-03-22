"""
Localized writing guidance for customer replies (Phase 2).

Injected into the draft system prompt so the model follows locale-appropriate tone
without hardcoding strings into draft_builder.
"""
from __future__ import annotations

# Short hints appended to SYSTEM_PROMPT; same languages as VIDEO_SUGGESTION_TEXT where possible.
LOCALIZED_WRITING_GUIDANCE: dict[str, str] = {
    "en": (
        "Use natural informal English (contractions OK). Avoid corporate clichés "
        '("Dear valued customer", "We apologize for the inconvenience", '
        '"Please do not hesitate"). Prefer "I" over "we" where it fits.'
    ),
    "zh-CN": (
        "使用自然、口语化的中文，少用公文套话；避免「尊敬的客户」「感谢您的来信」等刻板开头。"
        "可用「我」作主语；疑问句直达重点，少用冗长敬语。"
    ),
    "zh-TW": (
        "使用自然、口語化的繁體中文，少用制式公文用語；可用「我」作主語，語氣簡潔親切。"
    ),
    "ja": (
        "です・ます調で統一。過度に堅いビジネス決まり文句は避け、簡潔に。"
        "必要なら軽い共感を入れるが、長いお詫びの定型文は使わない。"
    ),
    "ru": (
        "Живой разговорный русский, без канцелярита «Уважаемый клиент» и длинных извинений. "
        "Обращение на «вы» естественно, можно сокращать формальность если уместно."
    ),
    "de": (
        "Du/Sie: lieber Sie-Form in Support-Mails, aber freundlich und knapp — "
        "keine Floskeln wie „Sehr geehrte Dam und Herren“. Direkt aufs Problem eingehen."
    ),
    "tr": (
        "Samimi ama profesyonel bir üslup; gereksiz kurumsal kalıplardan kaçın. "
        "Kısa cümleler, net sorular."
    ),
    "ko": (
        "자연스러운 존댓말(습니다/해요체)로 통일. 과한 격식어나 긴 사과 상투구는 피하고 "
        "핵심만 전달."
    ),
    "fr": (
        "Français naturel et direct; éviter les formules admin lourdes. Tutoiement ou vouvoiement "
        "selon ce qui sonne le plus naturel pour un support logiciel (souvent vouvoiement)."
    ),
    "es": (
        "Español cercano y claro; evita muletillas corporativas. Frases cortas; tono de persona "
        "que realmente leyó el mensaje."
    ),
    "it": (
        "Italiano naturale e diretto; evita formule burocratiche. Frasi brevi; tono da supporto umano "
        "che ha letto il messaggio."
    ),
    "pt": (
        "Português claro e próximo; evite clichês corporativos. Frases curtas; soe como quem leu o e-mail com atenção."
    ),
    "pl": (
        "Żywy polski bez urzędniczego tonu; unikaj fraz typu „Szanowny Kliencie”. "
        "Krótkie zdania, konkretne pytania."
    ),
    "vi": (
        "Tiếng Việt tự nhiên, thân thiện; tránh văn mẫu hành chính. Câu ngắn, đi thẳng vào vấn đề."
    ),
    "nl": (
        "Heldere informele Nederlandse toon; geen zakelijke holle frasen. Korte zinnen; alsof je het mailtje echt gelezen hebt."
    ),
    "id": (
        "Bahasa Indonesia ramah dan lugas; hindari frasa birokratis panjang. Kalimat pendek, fokus ke solusi."
    ),
}


def get_localized_writing_guidance(language: str) -> str:
    """Return guidance text for BCP-47-ish language tags (e.g. zh-CN, en)."""
    if language in LOCALIZED_WRITING_GUIDANCE:
        return LOCALIZED_WRITING_GUIDANCE[language]
    base = language.split("-", 1)[0].lower()
    for key, text in LOCALIZED_WRITING_GUIDANCE.items():
        if key.split("-", 1)[0].lower() == base:
            return text
    return LOCALIZED_WRITING_GUIDANCE["en"]
