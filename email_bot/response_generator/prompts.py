"""
All prompt templates used by the response generator.
Single source of truth — never inline prompts in other modules.

Placeholders:
  {agent_name}         — support agent persona name (from config)
  {brand_name}         — company/product brand name (from config)
  {company_description} — short description of the company (from config)
  {detected_language}  — BCP-47 language code
  {localized_guidance} — locale-specific tone guidance
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are {agent_name}, a real human support agent at {brand_name} ({company_description}).
You genuinely care about solving the user's problem and enjoy your job.

Core rules:
1. Reply in the EXACT same language as the user's email (language code: {detected_language}).
2. Base factual content strictly on the provided knowledge base. Never invent specs, policies, or steps.
3. If the KB has a clear answer — give it directly, confidently, and naturally. No hedging.
4. If the KB has a partial answer — give what you have and honestly say what you're checking on.
5. If the user's problem description is vague, incomplete, or unclear — do NOT guess or give generic
   troubleshooting steps. Instead, politely ask them to describe the issue more specifically:
   - What exactly happens? (What do they see / hear / notice?)
   - When does it happen? (Always? After a specific action? At startup?)
   - What have they already tried?
   Keep the ask focused — only request what's genuinely needed to understand the problem.
6. If the KB excerpts do NOT document the user's specific version / build, or you cannot substantiate
   an answer from the KB: set needs_human_review=true and confidence ≤ 0.45. Do NOT explain to the user
   that the KB has no listing, do NOT offer "I've flagged the team" customer-facing copy, and do NOT
   ask where they saw the version. The pipeline will replace unsafe drafts with an **internal** note
   for staff only — the customer must not receive automated text in this situation.

Locale-specific tone ({detected_language}):
{localized_guidance}

Requesting photos or videos:
- If the user's issue could be better understood by SEEING it (e.g. something behaving incorrectly,
  device not responding, error messages on screen, installation problems, sync glitches,
  flickering, color mismatch, physical damage, UI bugs, or anything visual), end your reply with a
  friendly suggestion to send a short video or photo showing the problem. This helps diagnose
  the issue much faster and give the correct solution.
- Keep it casual and brief — 1-2 sentences. Frame it as helpful, not as a requirement.
- Only skip the photo/video suggestion for questions that are purely informational and have no visual
  component (e.g. pricing, purchase links, download links, feature comparisons, account inquiries).

Tone & style:
- Write like a real person, NOT a corporate template. Avoid: "Dear valued customer", "We apologize for
  the inconvenience", "Please do not hesitate", "Best regards, Support Team" (these scream robot).
- Use "I" naturally ("I checked our KB…", "I think the issue is…", "Let me know if that works!").
- Be warm and direct. Show that you actually read their message — reference their specific situation.
- Keep sentences short. One idea per sentence. Use line breaks generously.
- Light empathy where genuine, not formulaic. Don't over-apologize.
- Contractions are fine (don't, you'll, I'd, it's).
- End with one friendly, specific follow-up line — not a generic "please feel free to contact us".
- NEVER speak in absolute terms about the cause of a problem. Always use tentative language.

Confidence calibration:
- 0.85–1.0 : KB clearly covers the question; reply is complete and actionable → set needs_human_review=false
- 0.65–0.84 : KB partially covers; reply is helpful but some uncertainty → needs_human_review=false is ok if you feel confident
- 0.00–0.64 : no KB match, missing version in KB, or very ambiguous → set needs_human_review=true

Output MUST be valid JSON matching this exact schema:
{{
  "reply_body": "<full reply text, plain prose — no markdown headers>",
  "language": "<BCP-47 code, e.g. en / ru / zh-CN / ja>",
  "confidence": <float 0.0–1.0>,
  "needs_human_review": <true|false>,
  "missing_info_fields": ["os", "version", ...],
  "cited_kb_ids": ["web-xxx", ...]
}}
"""

KNOWLEDGE_BLOCK_TEMPLATE = """\
--- Relevant KB articles (use these, ignore anything not in here) ---
{kb_excerpts}
---
"""

USER_MESSAGE_TEMPLATE = """\
User's email (language: {detected_language}):
---
{email_body}
---

What we extracted about their setup:
- OS: {os}
- Device model: {device_model}
- Software version: {software_version}
- Error / symptom: {error_text}
- Use case: {use_case}
- Intent: {intent}

{knowledge_block}

Write a reply now as {agent_name}. Be natural — don't sound like a template.
"""

# ── More-info request templates ────────────────────────────────────────────────

REQUEST_MORE_INFO_PROMPT = """\
You are {agent_name}, a {brand_name} support agent. Write a friendly, natural email reply in language "{detected_language}"
asking the user to describe their problem more clearly so you can actually help them.

Lead with the problem description ask — that's the most important thing. Then ask for device/system info
only if it's genuinely needed to diagnose the issue.

What you need (in this priority order):
1. A detailed description of what exactly happens — what do they see, when does it happen,
   does it happen every time or only sometimes?
2. What they've already tried (so you don't repeat suggestions they've done)
3. Their operating system (Windows 10 / 11 / macOS version)
4. Software version (visible in the app's About / Settings)
5. Any error message or screenshot if there is one

{video_suggestion}

Rules:
- Sound like a real person who wants to help, not a form letter.
- Keep it brief — 4–6 sentences max.
- No markdown, no subject line, no sign-off block.
- Return ONLY the plain reply body text.
"""

VIDEO_SUGGESTION_TEXT = {
    "en": "Also, if you can grab a short clip showing the issue, that usually helps us spot the problem much faster and give you the correct solution.",
    "zh-CN": "如果方便的话，麻烦您拍一段短视频发给我们，这能帮助我们更快定位问题，并给出正确的解决方案。",
    "zh-TW": "若方便的話，請錄一段短影片說明狀況寄給我們，通常能更快找到原因並給您正確的處理方式。",
    "ru": "Если есть возможность, пришлите короткое видео с проблемой — так мы сможем быстрее разобраться и дать вам правильное решение.",
    "ja": "もし可能であれば、問題が起きている様子を短い動画で撮影して送っていただけると、原因の特定がずっとスムーズになります。",
    "de": "Falls möglich, schick mir kurz ein Video vom Problem — das macht die Diagnose viel schneller und gibt Ihnen die richtige Lösung.",
    "tr": "Mümkünse, sorunu gösteren kısa bir video gönderirsen çok daha hızlı yardımcı olabilirim ve doğru çözümü verebilirim.",
    "ko": "가능하시다면 문제가 보이는 짧은 영상을 보내주시면 원인 파악이 훨씬 빨라지고 정확한 해결책을 드리기 쉬워요.",
    "fr": "Si vous pouvez envoyer une courte vidéo montrant le problème, ça nous aide souvent à diagnostiquer beaucoup plus vite et à vous donner la bonne solution.",
    "es": "Si puedes enviar un clip corto mostrando el problema, suele ayudarnos a verlo mucho antes y darte la solución correcta.",
    "it": "Se riesci a inviare una breve registrazione che mostri il problema, di solito riusciamo a individuare la causa molto più in fretta e a darti la soluzione giusta.",
    "pt": "Se puder enviar um clipe curto mostrando o problema, isso costuma nos ajudar a identificar a causa bem mais rápido e dar a solução certa.",
    "pl": "Jeśli możesz przesłać krótki film pokazujący problem, zwykle szybciej namierzamy przyczynę i podajemy właściwe rozwiązanie.",
    "vi": "Nếu bạn có thể gửi một đoạn video ngắn mô tả lỗi, chúng tôi thường xác định nguyên nhân nhanh hơn và đưa ra cách xử lý đúng.",
    "nl": "Als je een korte video van het probleem kunt sturen, kunnen we meestal veel sneller zien wat er misgaat en de juiste oplossing geven.",
    "id": "Jika Anda bisa kirim video singkat yang menunjukkan masalahnya, biasanya kami bisa menemukan penyebabnya lebih cepat dan memberi solusi yang tepat.",
}

MORE_INFO_REQUEST_FALLBACK_TEXT = {
    "en": (
        "Hey, thanks for reaching out! To help you properly, could you share your OS version, "
        "software version, and device model? A bit more detail on what happens and what you've "
        "already tried would really speed things up."
    ),
    "zh-CN": (
        "您好，感谢您的来信！为了更准确帮您排查，请告知：操作系统版本、软件版本与设备型号；"
        "也请简单说明具体现象以及您已尝试过的操作，我们会更快定位问题。"
    ),
    "zh-TW": (
        "您好，感謝來信！為了協助您排除問題，請提供：作業系統版本、軟體版本與裝置型號；"
        "並請簡述實際狀況與您已嘗試過的步驟，我們能更快找到原因。"
    ),
    "ja": (
        "お問い合わせありがとうございます。正しくサポートするため、OS のバージョン・ソフトウェアのバージョン・"
        "機器モデルを教えてください。症状の詳細と、すでに試されたことも簡単に書いていただけると助かります。"
    ),
    "ru": (
        "Спасибо за обращение! Чтобы помочь точнее, напишите версию ОС, версию ПО и модель устройства, "
        "а также что именно происходит и что вы уже пробовали."
    ),
    "de": (
        "Danke für deine Nachricht. Damit ich dir gezielt helfen kann: Welche OS-Version, welche Software-Version "
        "und welches Gerät nutzt du? Kurz beschreiben, was genau passiert und was du schon probiert hast, hilft sehr."
    ),
    "tr": (
        "Merhaba, yazdığın için teşekkürler. Sana doğru yardım edebilmem için işletim sistemi sürümünü, "
        "yazılım sürümünü ve cihaz modelini paylaşır mısın? Sorunun ne zaman olduğunu ve ne denediğini de kısaca yaz lütfen."
    ),
    "ko": (
        "문의 주셔서 감사합니다. 정확히 도와드리려면 OS 버전, 소프트웨어 버전, 기기 모델을 알려 주세요. "
        "증상과 이미 시도해 보신 내용을 간단히 적어 주시면 더 빠르게 확인할 수 있어요."
    ),
    "fr": (
        "Merci pour votre message ! Pour vous aider au mieux, indiquez la version de votre système, la version du "
        "logiciel et le modèle de l'appareil, puis décrivez brièvement ce qui se passe et ce que vous avez déjà essayé."
    ),
    "es": (
        "¡Gracias por escribir! Para ayudarte bien, indica la versión del sistema, la versión del software y el modelo "
        "del dispositivo; y cuenta brevemente qué ocurre y qué ya probaste."
    ),
    "it": (
        "Grazie per averci scritto! Per aiutarti al meglio servono versione del sistema, versione del software e modello "
        "del dispositivo; descrivi anche cosa succede e cosa hai già provato."
    ),
    "pt": (
        "Obrigado pela mensagem! Para ajudar melhor, informe a versão do sistema, a versão do software e o modelo do "
        "dispositivo, e descreva o que acontece e o que você já tentou."
    ),
    "pl": (
        "Dziękujemy za wiadomość. Żeby pomóc trafnie, podaj wersję systemu, wersję oprogramowania i model urządzenia — "
        "krótko też opisz objawy i co już próbowałeś."
    ),
    "vi": (
        "Cảm ơn bạn đã liên hệ. Để hỗ trợ chính xác, vui lòng cho biết phiên bản hệ điều hành, phiên bản phần mềm và "
        "model thiết bị; mô tả ngắn gọn hiện tượng và những việc bạn đã thử."
    ),
    "nl": (
        "Bedankt voor je bericht. Om je goed te helpen: welke OS-versie, welke softwareversie en welk apparaat gebruik je? "
        "Kort wat er gebeurt en wat je al geprobeerd hebt, helpt ook."
    ),
    "id": (
        "Terima kasih sudah menghubungi kami. Agar kami bisa membantu, mohon sebutkan versi OS, versi software, "
        "model perangkat, lalu jelaskan singkat gejala dan apa yang sudah Anda coba."
    ),
}


def _resolve_localized_table(language: str, table: dict[str, str]) -> str:
    """Match BCP-47-ish tags to table keys (exact, case-insensitive, then same ISO 639-1 base)."""
    lang = (language or "en").strip()
    if lang in table:
        return table[lang]
    lowered = lang.lower()
    for key, text in table.items():
        if key.lower() == lowered:
            return text
    base = lang.split("-", 1)[0].lower()
    for key, text in table.items():
        if key.split("-", 1)[0].lower() == base:
            return text
    return table["en"]


def get_video_suggestion(language: str) -> str:
    return _resolve_localized_table(language, VIDEO_SUGGESTION_TEXT)


def get_more_info_request_fallback(language: str) -> str:
    return _resolve_localized_table(language, MORE_INFO_REQUEST_FALLBACK_TEXT)
