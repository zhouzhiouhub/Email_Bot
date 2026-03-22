"""
KB gap: model admits no documented version/build or interrogates the user.
Replace LLM draft with **internal operator text only** — never send that text to the customer.
Reviewer must use Edit (or mail client). Approve-without-edit skips SMTP and closes the thread.
"""
from __future__ import annotations

import re

from models.schemas import ReplyOutput

# Shown only on DingTalk / review UI — NOT customer-facing.
_OPERATOR_KB_GAP: dict[str, str] = {
    "en": (
        "[Internal — do NOT send to customer]\n"
        "The knowledge base does not cover this version/build (or the draft was unsafe).\n"
        "Compose the real reply via Edit in review, or reply from your mailbox.\n"
        "If you click Approve without editing, no email will be sent to the customer."
    ),
    "zh-CN": (
        "[内部 — 请勿发给用户]\n"
        "知识库未覆盖用户提及的版本/构建（或草稿不适宜自动发出）。\n"
        "请通过审核「编辑」撰写正文，或直接在邮箱中回复用户。\n"
        "若未修改正文直接点「批准」，系统将不向用户发信。"
    ),
    "zh-TW": (
        "[內部 — 請勿寄給使用者]\n"
        "知識庫未涵蓋使用者提及的版本/組建（或草稿不適合自動寄出）。\n"
        "請於審核「編輯」撰寫正文，或改由信箱直接回覆使用者。\n"
        "若未修改正文即按「核准」，系統將不會對使用者寄信。"
    ),
    "ko": (
        "[내부용 — 고객에게 보내지 마세요]\n"
        "지식 베이스에 해당 버전/빌드가 없거나(또는 초안이 자동 발송에 부적합합니다).\n"
        "검토의 「편집」에서 답을 작성하거나 메일로 직접 회신하세요.\n"
        "수정 없이 「승인」만 하면 고객에게 메일이 발송되지 않습니다."
    ),
    "fr": (
        "[Interne — NE PAS envoyer au client]\n"
        "La base de connaissances ne couvre pas cette version / ce build (ou le brouillon est inadapté à l’envoi auto).\n"
        "Rédigez la vraie réponse via « Modifier » dans la revue, ou depuis votre messagerie.\n"
        "Si vous approuvez sans modifier, aucun e-mail ne sera envoyé au client."
    ),
    "es": (
        "[Interno — NO enviar al cliente]\n"
        "La base de conocimiento no cubre esta versión/compilación (o el borrador no es apto para envío automático).\n"
        "Escriba la respuesta real con «Editar» en la revisión o desde su correo.\n"
        "Si aprueba sin cambios, no se enviará correo al cliente."
    ),
    "it": (
        "[Interno — NON inviare al cliente]\n"
        "La knowledge base non copre questa versione/build (o la bozza non è adatta all’invio automatico).\n"
        "Scrivi la risposta tramite «Modifica» nella revisione o dalla tua casella di posta.\n"
        "Con «Approva» senza modifiche, non verrà inviata alcuna email al cliente."
    ),
    "pt": (
        "[Interno — NÃO enviar ao cliente]\n"
        "A base de conhecimento não cobre esta versão/build (ou o rascunho é inadequado para envio automático).\n"
        "Escreva a resposta em «Editar» na revisão ou pelo seu e-mail.\n"
        "Se aprovar sem alterações, nenhum e-mail será enviado ao cliente."
    ),
    "pl": (
        "[Wewnętrzne — NIE wysyłać do klienta]\n"
        "Baza wiedzy nie obejmuje tej wersji/builda (lub wersja robocza jest nieodpowiednia do auto-wysyłki).\n"
        "Napisz właściwą odpowiedź przez «Edytuj» w recenzji lub z klienta poczty.\n"
        "Po «Zatwierdź» bez zmian wiadomość do klienta nie zostanie wysłana."
    ),
    "vi": (
        "[Nội bộ — KHÔNG gửi cho khách]\n"
        "Cơ sở tri thức không có phiên bản/build này (hoặc bản nháp không phù hợp để gửi tự động).\n"
        "Soạn trả lời qua «Chỉnh sửa» trong màn duyệt hoặc từ hộp thư.\n"
        "Nếu «Phê duyệt» mà không sửa, hệ thống sẽ không gửi email cho khách."
    ),
    "nl": (
        "[Intern — NIET naar klant sturen]\n"
        "De kennisbank dekt deze versie/build niet (of het concept is ongeschikt voor auto-verzenden).\n"
        "Schrijf het echte antwoord via «Bewerken» in de review of vanuit je mail.\n"
        "Bij «Goedkeuren» zonder wijzigingen wordt er geen e-mail naar de klant verstuurd."
    ),
    "id": (
        "[Internal — Jangan kirim ke pelanggan]\n"
        "Basis pengetahuan tidak mencakup versi/build ini (atau draf tidak layak dikirim otomatis).\n"
        "Tulis balasan sebenarnya lewat «Edit» di tinjauan atau dari klien email.\n"
        "Jika «Setujui» tanpa mengubah, tidak ada email ke pelanggan."
    ),
    "ru": (
        "[Только для сотрудников — НЕ отправлять клиенту]\n"
        "В базе знаний нет этой версии/сборки (или черновик небезописен для автоотправки).\n"
        "Напишите ответ через «Изменить» в проверке или из почты.\n"
        "При «Утвердить» без правок письмо клиенту не уйдёт."
    ),
    "ja": (
        "[社内用 — お客様に送らない]\n"
        "ナレッジに当該バージョン/ビルドが無い（または自動送信が不適切）です。\n"
        "審査の「編集」で文面を作成するか、メールで直接ご返信ください。\n"
        "未編集のまま「承認」した場合、お客様へメールは送りません。"
    ),
    "de": (
        "[Intern — NICHT an Kunden senden]\n"
        "Die Knowledge Base deckt diese Version/diesen Build nicht ab (oder der Entwurf ist ungeeignet).\n"
        "Bitte die echte Antwort über «Bearbeiten» schreiben oder aus dem Mailprogramm.\n"
        "Bei «Genehmigen» ohne Änderung wird keine E-Mail an den Kunden verschickt."
    ),
    "tr": (
        "[Sadece ekip — müşteriye gönderme]\n"
        "Bilgi tabanı bu sürüm/yapıyı kapsamıyor (veya taslak otomatik gönderime uygun değil).\n"
        "İncelemede Düzenle ile yanıtı yazın veya postadan gönderin.\n"
        "Düzenlemeden Onaylarsanız müşteriye posta gitmez."
    ),
}

_FROZEN_OPERATOR_BODIES = frozenset(_OPERATOR_KB_GAP.values())


def _operator_text_for_language(language: str) -> str:
    lang = (language or "en").strip()
    if lang in _OPERATOR_KB_GAP:
        return _OPERATOR_KB_GAP[lang]
    low = lang.lower()
    if low.startswith("zh"):
        if "tw" in low or low.replace("_", "-") == "zh-tw":
            return _OPERATOR_KB_GAP["zh-TW"]
        return _OPERATOR_KB_GAP["zh-CN"]
    base = low.split("-")[0]
    return _OPERATOR_KB_GAP.get(base, _OPERATOR_KB_GAP["en"])


_INTERROGATION_PATTERNS = (
    re.compile(r"请问您是在哪里"),
    re.compile(r"在哪里看到"),
    re.compile(r"在哪.{0,6}看到"),
    re.compile(r"为何特别需要"),
    re.compile(r"能否告知您?(为何|为什么)"),
    re.compile(r"where did you (see|find|hear about)\b", re.I),
    re.compile(r"why do you (need|want) (this|that)\b", re.I),
    re.compile(r"which (page|site|link)\b.{0,40}\?", re.I),
)

_ADMISSION_PATTERNS = (
    re.compile(r"查阅.{0,20}(了)?(所有)?文档.{0,35}(并未|没有|未).{0,10}找到"),
    re.compile(r"并未找到.{0,40}(关于|该|此|有关)?.{0,12}(版本|记录|信息)"),
    re.compile(r"没有找到.{0,30}(关于|该)?.{0,12}(特定)?.{0,8}(版本|记录)"),
    re.compile(r"看来您正在寻找.{0,25}版本.{0,120}(并未|没有|找不到).{0,15}找到"),
    re.compile(r"no(t)?\s+record.{0,50}version", re.I),
    re.compile(r"couldn'?t find.{0,50}(this|that|the).{0,20}version", re.I),
    re.compile(r"I('ve| have) (checked|looked).{0,60}(don'?t see|no record|couldn'?t find|not find)", re.I),
    re.compile(r"don'?t have.{0,40}(any|a) (record|documentation).{0,40}version", re.I),
    # English: "not listed in our knowledge base" / can't confirm download
    re.compile(r"isn'?t specifically listed in.{0,40}knowledge base", re.I),
    re.compile(r"not specifically listed in.{0,40}knowledge base", re.I),
    re.compile(r"not.{0,20}listed in.{0,30}knowledge base", re.I),
    re.compile(r"can'?t confirm.{0,30}availability", re.I),
    re.compile(r"can'?t.{0,20}(give|offer|provide).{0,20}download link", re.I),
    re.compile(r"I looked into this.{0,150}knowledge base", re.I),
    re.compile(r"I looked into this.{0,80}isn'?t specifically listed", re.I),
)


def _interrogates_missing_version(text: str) -> bool:
    return any(p.search(text) for p in _INTERROGATION_PATTERNS)


def _admits_no_kb_for_version(text: str) -> bool:
    return any(p.search(text) for p in _ADMISSION_PATTERNS)


def should_escalate_kb_gap_draft(reply_body: str) -> bool:
    if not reply_body or len(reply_body.strip()) < 12:
        return False
    if _interrogates_missing_version(reply_body):
        return True
    if _admits_no_kb_for_version(reply_body):
        return True
    return False


def apply_kb_gap_handoff(reply: ReplyOutput) -> tuple[ReplyOutput, bool]:
    """
    If unsafe draft, replace with internal operator instructions.
    Returns (reply, True) if replaced — caller must not SMTP this to the customer as-is.
    """
    if not should_escalate_kb_gap_draft(reply.reply_body):
        return reply, False
    internal = _operator_text_for_language(reply.language)
    return (
        ReplyOutput(
            reply_body=internal,
            language=reply.language,
            confidence=min(reply.confidence, 0.42),
            needs_human_review=True,
            missing_info_fields=[],
            cited_kb_ids=[],
        ),
        True,
    )


def is_operator_kb_gap_reply(text: str) -> bool:
    """Skip polish; detect approve-without-edit (no SMTP)."""
    return text.strip() in _FROZEN_OPERATOR_BODIES


# Backward-compatible name used in graph / dev tester
is_escalation_handoff_reply = is_operator_kb_gap_reply
