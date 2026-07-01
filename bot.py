import asyncio
import html
import logging
import os
import tempfile
from pathlib import Path

import pdfplumber
from docx import Document
from dotenv import load_dotenv
from groq import AsyncGroq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT DE COPY  ← se edita en copy_prompt.txt, no aquí
# ─────────────────────────────────────────────────────────────────────────────
PROMPT_FILE = Path(__file__).parent / "copy_prompt.txt"



def load_copy_system_prompt() -> str:
    """Reads the copy system prompt from copy_prompt.txt so it's easy to iterate on."""
    try:
        return PROMPT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"No se encontró {PROMPT_FILE.name}. Crea ese archivo junto a bot.py con el system prompt."
        ) from exc


COPY_SYSTEM_PROMPT = load_copy_system_prompt()

# ─────────────────────────────────────────────────────────────────────────────

GROQ_MODEL = "llama-3.3-70b-versatile"

DRAFT_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("✅ Aprobar", callback_data="approve"),
        InlineKeyboardButton("🔄 Regenerar", callback_data="regenerate"),
        InlineKeyboardButton("🗑 Descartar", callback_data="discard"),
    ]
])


async def generate_copy(press_release: str, temperature: float = 0.7) -> str:
    """Calls Groq to generate an Instagram copy from the given press release text."""
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": COPY_SYSTEM_PROMPT},
            {"role": "user", "content": press_release},
        ],
        temperature=temperature,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()


def extract_text_from_pdf(path: str) -> str:
    """Extracts and concatenates the text of every page of a PDF. Runs synchronously."""
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def extract_text_from_docx(path: str) -> str:
    """Extracts and concatenates the text of every paragraph of a DOCX. Runs synchronously."""
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


async def generate_and_show_draft(
    status_msg, context: ContextTypes.DEFAULT_TYPE, press_release: str
) -> None:
    """Generates a copy from the press release text and shows it with the approve/regenerate/discard buttons."""
    context.user_data["press_release"] = press_release

    await status_msg.edit_text("⏳ Generando copy…")

    try:
        draft = await generate_copy(press_release)
    except Exception as exc:
        logger.error("Error al llamar a Groq: %s", exc)
        await status_msg.edit_text(
            "❌ No se pudo generar el copy. "
            "Comprueba tu <code>GROQ_API_KEY</code> o inténtalo de nuevo en unos segundos.",
            parse_mode="HTML",
        )
        return

    context.user_data["current_draft"] = draft

    await status_msg.edit_text(
        f"📝 <b>Borrador de copy:</b>\n\n{html.escape(draft)}",
        reply_markup=DRAFT_KEYBOARD,
        parse_mode="HTML",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    await update.message.reply_text(
        "👋 ¡Hola! Soy el asistente de <b>Ciudad Rodrigo en Vivo</b>.\n\n"
        "Pégame una nota de prensa (o mándamela como PDF/DOCX) y te la convierto "
        "en un copy listo para Instagram.\n\n"
        "Podrás <b>aprobar</b> el borrador, pedir una <b>variante</b> o <b>descartarlo</b> "
        "con los botones que aparecerán bajo cada copy.",
        parse_mode="HTML",
    )


async def handle_press_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Treats any non-command text message as a press release and generates a copy draft."""
    press_release = update.message.text
    status_msg = await update.message.reply_text("📨 Nota de prensa recibida…")
    await generate_and_show_draft(status_msg, context, press_release)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Treats an attached PDF/DOCX as a press release: extracts its text and generates a copy draft."""
    document = update.message.document
    file_name = document.file_name or ""
    ext = Path(file_name).suffix.lower()

    if ext not in (".pdf", ".docx"):
        await update.message.reply_text("De momento solo acepto pdf o docx.")
        return

    status_msg = await update.message.reply_text("⏳ Descargando documento…")

    tmp_path = None
    try:
        tg_file = await document.get_file()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        loop = asyncio.get_running_loop()
        extractor = extract_text_from_pdf if ext == ".pdf" else extract_text_from_docx
        text = await loop.run_in_executor(None, extractor, tmp_path)
    except Exception as exc:
        logger.error("Error al leer el documento: %s", exc)
        await status_msg.edit_text(
            "❌ No se pudo leer el documento. Comprueba que no esté dañado e inténtalo de nuevo."
        )
        return
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    text = text.strip()
    if not text:
        await status_msg.edit_text(
            "⚠️ No encontré texto en ese documento. Si es un PDF escaneado (solo imagen), "
            "de momento no puedo leerlo — pega el texto directamente."
        )
        return

    await generate_and_show_draft(status_msg, context, text)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the approve / regenerate / discard inline button callbacks."""
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == "approve":
        draft = context.user_data.get("current_draft", "")
        await query.edit_message_text(
            f"✅ <b>Copy aprobado</b>\n\n{html.escape(draft)}",
            parse_mode="HTML",
        )

    elif action == "regenerate":
        press_release = context.user_data.get("press_release")
        if not press_release:
            await query.answer(
                "No encontré la nota original. Vuélvela a pegar en el chat.",
                show_alert=True,
            )
            return

        # Remove buttons while generating to prevent double-taps
        await query.edit_message_text("⏳ Regenerando copy…")

        try:
            # Slightly higher temperature so the new draft varies from the previous one
            draft = await generate_copy(press_release, temperature=0.9)
        except Exception as exc:
            logger.error("Error al regenerar con Groq: %s", exc)
            await query.edit_message_text(
                "❌ No se pudo regenerar el copy. Inténtalo de nuevo."
            )
            return

        context.user_data["current_draft"] = draft

        await query.edit_message_text(
            f"📝 <b>Borrador de copy:</b>\n\n{html.escape(draft)}",
            reply_markup=DRAFT_KEYBOARD,
            parse_mode="HTML",
        )

    elif action == "discard":
        await query.message.delete()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN no está definido en el archivo .env")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_press_release))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot arrancado. Escuchando mensajes (long-polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
