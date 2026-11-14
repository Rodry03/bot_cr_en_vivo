import asyncio
import contextlib
import html
import json
import logging
import os
import re
import secrets
import tempfile
from io import BytesIO
from pathlib import Path

import pdfplumber
from docx import Document
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from groq import APIStatusError, AsyncGroq, GroqError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from PIL import Image

from archivo_fotos import LUGARES, extraer_imagenes_pdf, fotos_para_lugar
from tarjeta import CATEGORIAS, generar_tarjeta

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx registra a nivel INFO cada petición con su URL completa, y las de Telegram
# llevan el token del bot (https://api.telegram.org/bot<TOKEN>/...). Se suben a
# WARNING para que el token no acabe en `docker logs`. httpcore solo escribe en
# DEBUG, pero se fija igual por si alguien baja el nivel global.
QUIET_HTTP_LOGGERS = ("httpx", "httpcore")
for _http_logger in QUIET_HTTP_LOGGERS:
    logging.getLogger(_http_logger).setLevel(logging.WARNING)
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

GROQ_MODEL = "openai/gpt-oss-120b"

# Registros de tono que copy_prompt.txt puede detectar (ver bloque "ANTES DE ESCRIBIR" ahí).
DEFAULT_TONE = "festivo"
ALLOWED_TONES = {"festivo", "sobrio", "alerta", "politico", "mejora"}
TONE_DISPLAY = {
    "festivo": "festivo",
    "sobrio": "sobrio",
    "alerta": "alerta",
    "politico": "político",
    "mejora": "mejora",
}
MAX_CARD_METADATA = 20
MAX_PDF_IMAGES = 8

# Modelo de Whisper en Groq usado para transcribir notas de voz reenviadas (mp4/m4a/mp3/ogg/wav).
# Groq acepta estos contenedores directamente, así que no hace falta reextraer el audio con
# ffmpeg antes de subirlo (confirmado en la documentación de Groq y probado en vivo).
WHISPER_MODEL = "whisper-large-v3"
AUDIO_EXTENSIONS = {".mp4", ".m4a", ".mp3", ".ogg", ".wav"}

# Modelo de visión de Gemini usado para "leer" carteles de eventos en fotos.
# Solo describe la imagen en texto neutro; la voz del copy la pone Groq (ver generate_copy).
GEMINI_VISION_MODEL = "gemini-2.5-flash"

GEMINI_VISION_PROMPT = """Eres un transcriptor neutro de carteles/imágenes de eventos.

Describe en texto plano y ordenado TODOS los datos textuales que aparezcan en la imagen: \
qué evento es, fecha, hora, lugar, organizador, teléfonos, precios, plazas, edad mínima/requisitos \
y cualquier otro dato concreto visible.

Reglas estrictas:
- No inventes ni completes datos que no estén escritos en la imagen.
- No omitas ningún dato relevante que sí aparezca.
- No redactes un copy ni le des estilo o tono: solo transcribe/describe la información de forma \
neutra y estructurada, como si fuera una ficha de datos.
- Si algún dato no es legible o no aparece, simplemente no lo incluyas (no lo rellenes)."""

DOCUMENT_TOO_LONG_MESSAGE = (
    "⚠️ El documento es demasiado largo para procesarlo de una vez. "
    "Prueba a mandarme solo la parte relevante, o un documento más corto."
)

CARD_METADATA_INSTRUCTIONS = """Extrae metadatos para una tarjeta de noticias. No redactes el copy ni detectes el tono.
Devuelve únicamente un objeto JSON válido con exactamente estas cinco claves:
- "titular": titular fiel a la nota, con un máximo de 8 palabras.
- "resaltado": el lugar, entidad o elemento concreto más reconocible que aparezca literalmente en el titular (por ejemplo, "río Águeda", "Plaza Mayor" o "Carnaval del Toro"). Nunca elijas un verbo ni una palabra genérica como "Limpieza", "Nueva" u "Obras". Si no hay un elemento concreto, elige la palabra de mayor peso informativo. Copia exactamente su escritura del titular.
- "subtitulo": fragmento literal de la nota, sin reescribir ni añadir información, con un máximo de 15 palabras.
- "categoria": elige exactamente una categoría de esta lista y aplica estas definiciones:
  - MEDIO AMBIENTE: ríos, naturaleza, limpiezas, residuos, zonas verdes o animales.
  - OBRAS: construcción, reformas, asfaltado o cortes de calles causados por obras.
  - AVISO: cortes de agua o luz, alertas o cambios urgentes de horario.
  - AYUNTAMIENTO: plenos, presupuestos, trámites o subvenciones.
  - CULTURA: conciertos, exposiciones, teatro o fiestas culturales.
  - DEPORTES: competiciones, clubes o instalaciones deportivas.
  - SOCIEDAD: asociaciones, sucesos, solidaridad y el resto de temas locales que no encajen mejor en otra categoría.
- "lugar": clave obligatoria en todos los JSON. Elige exactamente una etiqueta de esta lista si la noticia trata claramente de ese sitio: rio-agueda = río, canal o ribera del Águeda; puente-romano = el puente romano cuando sea el tema de la noticia. Si el puente solo se menciona como referencia de ubicación, no elijas puente-romano; para una noticia sobre la limpieza del canal o la ribera, elige rio-agueda. Las demás etiquetas son: plaza-mayor = Plaza Mayor; ayuntamiento = edificio consistorial; catedral = catedral; castillo = castillo; muralla = muralla; dehesa = dehesa. Si ninguna encaja con claridad, usa el valor JSON null (sin comillas). Nunca inventes una etiqueta ni omitas la clave.
Si no puedes completar con certeza un campo, devuélvelo como null."""


def is_document_too_long_error(exc: Exception) -> bool:
    """Detects Groq's 413 'request too large' error (texto + prompt exceden el límite
    de tokens por request/minuto), a distinguir del 429 genérico de rate limiting
    (ese sí es transitorio y no tiene que ver con el tamaño del documento)."""
    return isinstance(exc, APIStatusError) and exc.status_code == 413


CardMetadata = dict[str, object]


def _empty_card_metadata() -> CardMetadata:
    return {
        "titular": None,
        "resaltado": None,
        "subtitulo": None,
        "categoria": None,
        "lugar": None,
    }


def _normalized_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _literal_subtitle_from_note(press_release: str) -> str | None:
    first_sentence = re.split(r"(?<=[.!?])\s+", press_release.strip(), maxsplit=1)[0]
    words = list(re.finditer(r"\S+", first_sentence))
    if not words:
        return None
    return first_sentence[: words[min(len(words), 15) - 1].end()]


def _trim_card_title(titular: str, resaltado: str | None) -> str:
    palabras = list(re.finditer(r"\S+", titular))
    if len(palabras) <= 8:
        return titular

    inicio = 0
    coincidencia = re.search(re.escape(resaltado), titular) if resaltado else None
    if coincidencia:
        indices_resaltados = [
            indice
            for indice, palabra in enumerate(palabras)
            if palabra.start() < coincidencia.end() and coincidencia.start() < palabra.end()
        ]
        if indices_resaltados and indices_resaltados[-1] >= 8:
            primero, ultimo = indices_resaltados[0], indices_resaltados[-1]
            palabras_resaltadas = ultimo - primero + 1
            inicio = max(0, primero - (8 - palabras_resaltadas) // 2)
            inicio = min(inicio, len(palabras) - 8, primero)
            if inicio + 8 <= ultimo:
                inicio = ultimo - 7

    return titular[palabras[inicio].start() : palabras[inicio + 7].end()]


def _parse_card_metadata(data: dict[str, object], press_release: str) -> CardMetadata:
    metadata = _empty_card_metadata()

    titular = data.get("titular")
    resaltado = data.get("resaltado")
    resaltado_texto = resaltado.strip() if isinstance(resaltado, str) else None
    if isinstance(titular, str) and titular.strip():
        titular_texto = titular.strip()
        if len(titular_texto.split()) > 8:
            titular_texto = _trim_card_title(titular_texto, resaltado_texto)
            logger.warning("Titular de tarjeta recortado al máximo de 8 palabras.")
        metadata["titular"] = titular_texto
    else:
        logger.warning("Groq devolvió un titular de tarjeta ausente o inválido.")

    if (
        resaltado_texto
        and metadata["titular"]
        and resaltado_texto in metadata["titular"]
    ):
        metadata["resaltado"] = resaltado_texto
    else:
        logger.warning("Groq devolvió un resaltado de tarjeta ausente o inválido.")

    subtitulo = data.get("subtitulo")
    if (
        isinstance(subtitulo, str)
        and subtitulo.strip()
        and len(subtitulo.split()) <= 15
        and _normalized_text(subtitulo) in _normalized_text(press_release)
    ):
        metadata["subtitulo"] = subtitulo.strip()
    else:
        metadata["subtitulo"] = _literal_subtitle_from_note(press_release)
        logger.warning("Groq devolvió un subtítulo no literal; uso un fragmento de la nota.")

    categoria = data.get("categoria")
    if isinstance(categoria, str) and categoria.strip().upper() in CATEGORIAS:
        metadata["categoria"] = categoria.strip().upper()
    else:
        logger.warning("Groq devolvió una categoría de tarjeta ausente o inválida.")

    lugar = data.get("lugar")
    if isinstance(lugar, str) and lugar.strip().lower() in LUGARES:
        metadata["lugar"] = lugar.strip().lower()
    elif lugar is not None:
        logger.warning("Groq devolvió un lugar que no está en la lista cerrada; se ignora.")

    return metadata


async def generate_copy(
    press_release: str, temperature: float = 0.7
) -> tuple[str, str]:
    """Calls Groq to generate an Instagram copy and detect its tone from the press release text.

    Returns (copy, tono), with the original two-key JSON format.
    """
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": COPY_SYSTEM_PROMPT,
            },
            {"role": "user", "content": press_release},
        ],
        temperature=temperature,
        max_tokens=1024,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content.strip()

    try:
        data = json.loads(raw)
        copy_text = str(data["copy"]).strip()
        if not copy_text:
            raise ValueError("la clave 'copy' del JSON viene vacía")
        tone = str(data.get("tono", "")).strip().lower()
        if tone not in ALLOWED_TONES:
            tone = DEFAULT_TONE
        return copy_text, tone
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "Groq no devolvió el JSON {tono, copy} esperado, uso el texto en crudo y tono por defecto: %s",
            exc,
        )
        return raw, DEFAULT_TONE


async def generate_card_metadata(press_release: str) -> CardMetadata:
    """Generates only the five fields needed to render a news card."""
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": CARD_METADATA_INSTRUCTIONS},
            {"role": "user", "content": press_release},
        ],
        temperature=0.3,
        max_tokens=1024,
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Groq devolvió una respuesta vacía para los datos de la tarjeta.")

    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {
        "titular",
        "resaltado",
        "subtitulo",
        "categoria",
        "lugar",
    }:
        raise ValueError("Groq no devolvió exactamente los cinco campos de tarjeta.")

    metadata = _parse_card_metadata(data, press_release)
    if not _has_complete_card_metadata(metadata):
        raise ValueError("Groq no devolvió datos suficientes para crear la tarjeta.")
    return metadata


def _store_card_metadata(
    context: ContextTypes.DEFAULT_TYPE,
    metadata: CardMetadata,
    pdf_path: str | None = None,
    press_release: str = "",
    draft: str = "",
    tone: str = DEFAULT_TONE,
) -> str:
    card_token = secrets.token_hex(8)
    records = context.user_data.get("card_metadata")
    if not isinstance(records, dict):
        records = {}
        context.user_data["card_metadata"] = records
    record = dict(metadata)
    record["_press_release"] = press_release
    record["_draft"] = draft
    record["_tone"] = tone
    if pdf_path:
        record["_pdf_path"] = pdf_path
    else:
        record["_pdf_path"] = None
    record["_pdf_images"] = []
    record["_archive_photos"] = None
    record["_archive_index"] = -1
    record["_current_photo"] = False
    records[card_token] = record
    while len(records) > MAX_CARD_METADATA:
        oldest_token = next(iter(records))
        _cleanup_card_assets(records.pop(oldest_token))
    return card_token


def _has_complete_card_metadata(metadata: CardMetadata) -> bool:
    return all(
        isinstance(metadata.get(field), str) and bool(metadata[field].strip())
        for field in ("titular", "resaltado", "subtitulo", "categoria")
    )


def _get_card_metadata(
    context: ContextTypes.DEFAULT_TYPE, card_token: str
) -> CardMetadata | None:
    records = context.user_data.get("card_metadata")
    if not isinstance(records, dict) or not card_token:
        return None
    metadata = records.get(card_token)
    return metadata if isinstance(metadata, dict) else None


def _draft_keyboard(
    card_token: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✅ Aprobar", callback_data=f"approve:{card_token}"),
            InlineKeyboardButton("🔄 Regenerar", callback_data=f"regenerate:{card_token}"),
            InlineKeyboardButton("🗑 Descartar", callback_data=f"discard:{card_token}"),
        ]
    ]
    rows.append(
        [
            InlineKeyboardButton(
                "🖼️ Necesito imagen",
                callback_data=f"need_image:{card_token}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _remove_local_file(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("No se pudo eliminar el archivo temporal %s: %s", path, exc)


def _cleanup_card_assets(metadata: CardMetadata) -> None:
    _remove_local_file(metadata.get("_pdf_path") if isinstance(metadata.get("_pdf_path"), str) else None)
    images = metadata.get("_pdf_images")
    if isinstance(images, list):
        for image_path in images:
            if isinstance(image_path, str):
                _remove_local_file(image_path)


def _delete_card_metadata(context: ContextTypes.DEFAULT_TYPE, card_token: str) -> None:
    records = context.user_data.get("card_metadata")
    if isinstance(records, dict):
        record = records.pop(card_token, None)
        if isinstance(record, dict):
            _cleanup_card_assets(record)


def _get_pdf_temp_dir(context: ContextTypes.DEFAULT_TYPE) -> Path:
    temporary_directory = context.bot_data.get("pdf_temp_dir")
    if not isinstance(temporary_directory, tempfile.TemporaryDirectory):
        temporary_directory = tempfile.TemporaryDirectory(prefix="bot-cr-pdf-")
        context.bot_data["pdf_temp_dir"] = temporary_directory
    return Path(temporary_directory.name)


async def _cleanup_pdf_temp_dir(application: Application) -> None:
    temporary_directory = application.bot_data.pop("pdf_temp_dir", None)
    if isinstance(temporary_directory, tempfile.TemporaryDirectory):
        temporary_directory.cleanup()


def _render_card_jpeg(metadata: CardMetadata, photo: str | Path | None = None) -> bytes:
    titular = metadata.get("titular")
    resaltado = metadata.get("resaltado")
    subtitulo = metadata.get("subtitulo")
    categoria = metadata.get("categoria")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (titular, resaltado, subtitulo, categoria)
    ):
        raise ValueError("Faltan datos válidos para crear la tarjeta.")
    png = generar_tarjeta(
        titular,
        resaltado,
        subtitulo,
        categoria,
        foto=photo,
    )
    with Image.open(BytesIO(png)) as image:
        output = BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


async def _send_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card_token: str,
    metadata: CardMetadata,
    photo: str | Path | None = None,
    with_keyboard: bool = True,
) -> None:
    jpeg_bytes = await asyncio.to_thread(_render_card_jpeg, metadata, photo)
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=InputFile(BytesIO(jpeg_bytes), filename="tarjeta.jpg"),
        reply_markup=_card_keyboard(card_token) if with_keyboard else None,
    )


def _cached_pdf_images(metadata: CardMetadata) -> list[str]:
    images = metadata.get("_pdf_images")
    if not isinstance(images, list):
        return []
    return [path for path in images if isinstance(path, str) and Path(path).is_file()]


async def _send_pdf_image_choices(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card_token: str,
    image_paths: list[str],
) -> None:
    for index, image_path in enumerate(image_paths):
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(BytesIO(Path(image_path).read_bytes()), filename=f"imagen-{index + 1}.jpg"),
            caption=f"Imagen {index + 1} del PDF",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Usar esta imagen", callback_data=f"pdf_foto:{card_token}:{index}")]]
            ),
        )


def _card_keyboard(card_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Otra foto", callback_data=f"otra_foto:{card_token}"),
                InlineKeyboardButton("🎨 Sin foto", callback_data=f"sin_foto:{card_token}"),
            ]
        ]
    )


def describe_poster_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Calls Gemini Vision to transcribe a poster image into plain, neutral text. Runs synchronously."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=GEMINI_VISION_MODEL,
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            GEMINI_VISION_PROMPT,
        ],
    )
    return (response.text or "").strip()


PDF_MAX_PAGES = 5
PDF_TRUNCATED_NOTICE = (
    "ℹ️ El documento tenía más de 5 páginas; he usado solo las primeras 5 "
    "para generar el copy."
)


def extract_text_from_pdf(path: str) -> tuple[str, bool]:
    """Extracts and concatenates the text of the first PDF_MAX_PAGES pages of a PDF. Runs synchronously.

    Trunca a las primeras PDF_MAX_PAGES páginas como heurística para reducir el riesgo de
    pasarse del límite de tokens de Groq (ver DOCUMENT_TOO_LONG_MESSAGE, que sigue siendo
    la red de seguridad si aun así se supera). Devuelve (texto, truncado).
    """
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages[:PDF_MAX_PAGES]
        truncated = len(pdf.pages) > PDF_MAX_PAGES
        text = "\n".join(page.extract_text() or "" for page in pages)
        return text, truncated


def extract_text_from_docx(path: str) -> str:
    """Extracts and concatenates the text of every paragraph of a DOCX. Runs synchronously."""
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _langfuse_transcription_span():
    """Best-effort manual Langfuse span for the Whisper call.

    GroqInstrumentor (openinference-instrumentation-groq) solo parchea chat.completions,
    no audio.transcriptions, así que aquí no hay instrumentación automática. Si Langfuse
    no está configurado o falla, se devuelve un context manager que no hace nada.
    """
    try:
        from langfuse import get_client

        return get_client().start_as_current_observation(
            name="groq-whisper-transcription",
            as_type="generation",
            model=WHISPER_MODEL,
        )
    except Exception:
        return contextlib.nullcontext()


async def transcribe_audio(file_path: str) -> str:
    """Calls Groq Whisper to transcribe an audio file into plain Spanish text."""
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    with _langfuse_transcription_span() as span:
        text = await client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(os.path.basename(file_path), file_bytes),
            language="es",
            response_format="text",
        )
        if span is not None and hasattr(span, "update"):
            with contextlib.suppress(Exception):
                span.update(output=text)

    return text.strip()


async def handle_extracted_text(
    status_msg,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    notice: str = "",
    pdf_path: str | None = None,
) -> None:
    """Routes freshly extracted text (from any input type) to the normal draft flow,
    or to the multi-fuente accumulator if that mode is active for this chat.

    notice, si se pasa (p. ej. PDF_TRUNCATED_NOTICE), se muestra junto al resultado."""
    if context.user_data.get("multi_mode"):
        _remove_local_file(pdf_path)
        await accumulate_multi_source(status_msg, context, text, notice=notice)
    else:
        await generate_and_show_draft(
            status_msg, context, text, notice=notice, pdf_path=pdf_path
        )


async def accumulate_multi_source(
    status_msg, context: ContextTypes.DEFAULT_TYPE, text: str, notice: str = ""
) -> None:
    """Stores one extracted source while modo multi-fuente is active, instead of generating a copy."""
    sources = context.user_data.setdefault("multi_sources", [])
    sources.append(text)
    message = f"✅ Fuente {len(sources)} guardada. Mándame más fuentes o pulsa /generar."
    if notice:
        message += f"\n\n{notice}"
    await status_msg.edit_text(message)


async def generate_and_show_draft(
    status_msg,
    context: ContextTypes.DEFAULT_TYPE,
    press_release: str,
    notice: str = "",
    pdf_path: str | None = None,
) -> None:
    """Generates a copy from the press release text and shows it with the approve/regenerate/discard buttons.

    El borrador, el tono y la nota se guardan en el registro del token de esta noticia,
    para que los botones de cada mensaje actúen sobre su propia noticia."""
    await status_msg.edit_text("⏳ Generando copy…")

    try:
        draft, tone = await generate_copy(press_release)
    except Exception as exc:
        if is_document_too_long_error(exc):
            logger.warning("Documento demasiado largo para Groq (413): %s", exc)
            _remove_local_file(pdf_path)
            await status_msg.edit_text(DOCUMENT_TOO_LONG_MESSAGE)
            return
        logger.error("Error al llamar a Groq: %s", exc)
        _remove_local_file(pdf_path)
        await status_msg.edit_text(
            "❌ No se pudo generar el copy. "
            "Comprueba tu <code>GROQ_API_KEY</code> o inténtalo de nuevo en unos segundos.",
            parse_mode="HTML",
        )
        return

    card_token = _store_card_metadata(
        context,
        _empty_card_metadata(),
        pdf_path,
        press_release=press_release,
        draft=draft,
        tone=tone,
    )

    message = (
        f"📝 <b>Borrador de copy:</b>\n"
        f"Tono detectado: <b>{TONE_DISPLAY[tone]}</b>\n\n"
        f"<pre>{html.escape(draft)}</pre>"
    )
    if notice:
        message += f"\n\n{html.escape(notice)}"

    await status_msg.edit_text(
        message,
        reply_markup=_draft_keyboard(card_token),
        parse_mode="HTML",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    await update.message.reply_text(
        "👋 ¡Hola! Soy el asistente de <b>Ciudad Rodrigo en Vivo</b>.\n\n"
        "Pégame una nota de prensa (o mándamela como PDF/DOCX) y te la convierto "
        "en un copy listo para Instagram.\n\n"
        "Podrás <b>aprobar</b> el borrador, pedir una <b>variante</b> o <b>descartarlo</b> "
        "con los botones que aparecerán bajo cada copy.\n\n"
        "Si una noticia tiene varias fuentes (p. ej. nota del gobierno + comunicado de la "
        "oposición), usa /multi para acumularlas y /generar para hacer un único copy que las "
        "contraste (o /cancelar para descartarlo).",
        parse_mode="HTML",
    )


async def handle_press_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Treats any non-command text message as a press release and generates a copy draft
    (or accumulates it as a source if modo multi-fuente está activo)."""
    press_release = update.message.text
    status_msg = await update.message.reply_text("📨 Nota de prensa recibida…")
    await handle_extracted_text(status_msg, context, press_release)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Treats an attached PDF/DOCX/audio as a press release: extracts or transcribes its content and generates a copy draft."""
    document = update.message.document
    file_name = document.file_name or ""
    ext = Path(file_name).suffix.lower()
    mime_type = document.mime_type or ""
    is_audio = (
        ext in AUDIO_EXTENSIONS
        or mime_type.startswith("audio/")
        or mime_type == "video/mp4"
    )

    if is_audio:
        await handle_audio_document(update, context, document, ext)
        return

    if ext not in (".pdf", ".docx"):
        await update.message.reply_text("De momento solo acepto pdf, docx o audio (nota de voz reenviada).")
        return

    status_msg = await update.message.reply_text("⏳ Descargando documento…")

    try:
        text, truncated, pdf_path = await extract_text_from_pdf_or_docx_document(
            document, ext, _get_pdf_temp_dir(context) if ext == ".pdf" else None
        )
    except Exception as exc:
        logger.error("Error al leer el documento: %s", exc)
        await status_msg.edit_text(
            "❌ No se pudo leer el documento. Comprueba que no esté dañado e inténtalo de nuevo."
        )
        return

    text = text.strip()
    if not text:
        _remove_local_file(pdf_path)
        await status_msg.edit_text(
            "⚠️ No encontré texto en ese documento. Si es un PDF escaneado (solo imagen), "
            "de momento no puedo leerlo — pega el texto directamente."
        )
        return

    notice = PDF_TRUNCATED_NOTICE if truncated else ""
    await handle_extracted_text(
        status_msg, context, text, notice=notice, pdf_path=pdf_path
    )


async def extract_text_from_pdf_or_docx_document(
    document,
    ext: str,
    temp_dir: Path | None = None,
) -> tuple[str, bool, str | None]:
    """Downloads a Telegram PDF/DOCX document and extracts its text, reusing the existing extractors.

    Devuelve (texto, truncado, ruta_temporal_pdf). DOCX se elimina al terminar;
    los PDF se conservan hasta que se solicitan las imágenes de archivo.
    """
    tmp_path = None
    keep_file = False
    try:
        tg_file = await document.get_file()
        with tempfile.NamedTemporaryFile(
            suffix=ext, dir=temp_dir, delete=False
        ) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        loop = asyncio.get_running_loop()
        if ext == ".pdf":
            text, truncated = await loop.run_in_executor(
                None, extract_text_from_pdf, tmp_path
            )
            keep_file = True
            return text, truncated, tmp_path
        text = await loop.run_in_executor(None, extract_text_from_docx, tmp_path)
        return text, False, None
    finally:
        if tmp_path and not keep_file:
            _remove_local_file(tmp_path)


async def handle_audio_document(update: Update, context: ContextTypes.DEFAULT_TYPE, document, ext: str) -> None:
    """Treats an attached audio file (mp4/m4a/mp3/ogg/wav) as a press release: transcribes it with Groq Whisper and generates a copy draft."""
    status_msg = await update.message.reply_text("🎙️ Descargando audio…")

    tmp_path = None
    try:
        tg_file = await document.get_file()
        with tempfile.NamedTemporaryFile(suffix=ext or ".mp4", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        await status_msg.edit_text("⏳ Transcribiendo audio… puede tardar unos segundos")
        text = await transcribe_audio(tmp_path)
    except Exception as exc:
        logger.error("Error al transcribir el audio: %s", exc)
        await status_msg.edit_text(
            "❌ No he podido transcribir el audio. Prueba a reenviarlo de nuevo o mándame el texto directamente."
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
            "⚠️ No he detectado voz en ese audio. Prueba con otro archivo o pega el texto directamente."
        )
        return

    await handle_extracted_text(status_msg, context, text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Treats a photo (event poster) as a press release: describes it via Gemini Vision and generates a copy draft."""
    status_msg = await update.message.reply_text("🖼️ Descargando imagen…")

    try:
        photo = update.message.photo[-1]  # última = mayor resolución
        tg_file = await photo.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        logger.error("Error al descargar la foto: %s", exc)
        await status_msg.edit_text(
            "❌ No se pudo descargar la imagen. Inténtalo de nuevo."
        )
        return

    await status_msg.edit_text("🔎 Leyendo el cartel…")

    loop = asyncio.get_running_loop()
    try:
        description = await loop.run_in_executor(
            None, describe_poster_image, image_bytes
        )
    except Exception as exc:
        logger.error(
            "Error al llamar a Gemini Vision (modelo=%s). Si el mensaje menciona "
            "'region', 'location', 'billing' o 'PERMISSION_DENIED', probablemente sea "
            "un bloqueo de disponibilidad regional o de facturación (común en la UE): %s",
            GEMINI_VISION_MODEL,
            exc,
        )
        await status_msg.edit_text(
            "❌ No he podido procesar la imagen ahora mismo. Inténtalo de nuevo en unos minutos."
        )
        return

    description = description.strip()
    if not description:
        await status_msg.edit_text(
            "⚠️ No pude extraer información del cartel. Prueba con una foto más nítida "
            "o pega el texto directamente."
        )
        return

    await handle_extracted_text(status_msg, context, description)


async def multi_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /multi: activates modo multi-fuente, or reports the current progress if already active."""
    if context.user_data.get("multi_mode"):
        n = len(context.user_data.get("multi_sources", []))
        await update.message.reply_text(
            f"ℹ️ Ya estás en modo multi-fuente, con {n} fuente(s) acumulada(s). "
            "Mándame más fuentes, o pulsa /generar o /cancelar."
        )
        return

    context.user_data["multi_mode"] = True
    context.user_data["multi_sources"] = []
    await update.message.reply_text(
        "🗂️ <b>Modo multi-fuente activado.</b>\n"
        "Mándame las fuentes una a una (texto, PDF, DOCX, foto o audio). "
        "Cuando las tengas todas, pulsa /generar para juntarlas en un solo copy, "
        "o /cancelar para descartarlas.",
        parse_mode="HTML",
    )


async def multi_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /generar: combines all accumulated sources into one press release and generates a single copy."""
    if not context.user_data.get("multi_mode"):
        await update.message.reply_text(
            "ℹ️ No estás en modo multi-fuente. Actívalo con /multi si quieres combinar varias fuentes."
        )
        return

    sources = context.user_data.get("multi_sources", [])
    if not sources:
        await update.message.reply_text(
            "⚠️ No has añadido ninguna fuente todavía. Mándame al menos una antes de /generar, "
            "o usa /cancelar para salir del modo multi-fuente."
        )
        return

    combined = "\n\n".join(
        f"FUENTE {i}:\n{text}" for i, text in enumerate(sources, start=1)
    )

    context.user_data["multi_mode"] = False
    context.user_data["multi_sources"] = []

    status_msg = await update.message.reply_text(
        f"📨 {len(sources)} fuente(s) recibidas, generando un copy conjunto…"
    )
    await generate_and_show_draft(status_msg, context, combined)


async def multi_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /cancelar: discards all accumulated sources and exits modo multi-fuente."""
    if not context.user_data.get("multi_mode"):
        await update.message.reply_text("ℹ️ No estás en modo multi-fuente, no hay nada que cancelar.")
        return

    context.user_data["multi_mode"] = False
    context.user_data["multi_sources"] = []
    await update.message.reply_text("🗑️ Modo multi-fuente cancelado. He descartado todo lo acumulado.")


async def handle_unsupported_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches any command other than /start, /multi, /generar, /cancelar, without breaking multi-fuente state."""
    if context.user_data.get("multi_mode"):
        n = len(context.user_data.get("multi_sources", []))
        await update.message.reply_text(
            f"⚠️ Ese comando no lo entiendo. Sigues en modo multi-fuente con {n} fuente(s) acumulada(s): "
            "mándame más fuentes, o usa /generar o /cancelar."
        )
    else:
        await update.message.reply_text("No reconozco ese comando.")


async def _archive_photos_for_card(metadata: CardMetadata) -> list[str]:
    cached = metadata.get("_archive_photos")
    if isinstance(cached, list):
        return [
            path
            for path in cached
            if isinstance(path, str) and Path(path).is_file()
        ]
    lugar = metadata.get("lugar")
    try:
        photos = await asyncio.to_thread(
            fotos_para_lugar, lugar if isinstance(lugar, str) else None
        )
    except (OSError, ValueError) as exc:
        logger.error("No se pudo consultar el archivo de fotos: %s", exc)
        metadata["_archive_photos"] = []
        return []
    photo_paths = [str(path) for path in photos]
    metadata["_archive_photos"] = photo_paths
    return photo_paths


async def _send_archive_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card_token: str,
    metadata: CardMetadata,
    index: int,
) -> bool:
    photos = await _archive_photos_for_card(metadata)
    if index < 0 or index >= len(photos):
        return False
    await _send_card(context, chat_id, card_token, metadata, photos[index])
    metadata["_archive_index"] = index
    metadata["_current_photo"] = True
    return True


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles draft and generated-card inline button callbacks."""
    query = update.callback_query
    if query is None:
        return
    callback_data = query.data or ""
    callback_parts = callback_data.split(":")
    action = callback_parts[0]
    card_token = callback_parts[1] if len(callback_parts) > 1 else ""

    if action in {"need_image", "otra_foto", "sin_foto", "pdf_foto"}:
        card_metadata = _get_card_metadata(context, card_token)
        if card_metadata is None:
            await query.answer(
                "Esta noticia es anterior al último reinicio, vuelve a enviarla",
                show_alert=True,
            )
            return

        if action == "sin_foto":
            if query.message is None:
                await query.answer(
                    "No puedo modificar este mensaje.",
                    show_alert=True,
                )
                return
            await query.answer("Tarjeta confirmada sin foto.")
            await query.edit_message_reply_markup(reply_markup=None)
            if card_metadata.get("_current_photo"):
                try:
                    await _send_card(
                        context,
                        query.message.chat.id,
                        card_token,
                        card_metadata,
                        with_keyboard=False,
                    )
                except (OSError, TypeError, ValueError, TelegramError) as exc:
                    logger.error("No se pudo generar la tarjeta sin foto: %s", exc)
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="❌ No se pudo generar la tarjeta sin foto.",
                    )
            # Solo se limpian los temporales de la tarjeta: el registro se conserva
            # para que Aprobar/Regenerar del borrador sigan funcionando.
            _cleanup_card_assets(card_metadata)
            card_metadata["_pdf_path"] = None
            card_metadata["_pdf_images"] = []
            return

        if query.message is None:
            await query.answer(
                "No puedo enviar la tarjeta desde este mensaje.",
                show_alert=True,
            )
            return

        if action == "need_image" and not _has_complete_card_metadata(card_metadata):
            press_release = card_metadata.get("_press_release")
            if not isinstance(press_release, str) or not press_release.strip():
                await query.answer(
                    "No encuentro la nota original para preparar la tarjeta.",
                    show_alert=True,
                )
                return
            try:
                card_metadata.update(await generate_card_metadata(press_release))
            except (GroqError, ValueError, KeyError) as exc:
                logger.error("No se pudieron generar los datos de la tarjeta: %s", exc)
                await query.answer(
                    "No se pudieron generar los datos de la tarjeta. Prueba de nuevo.",
                    show_alert=True,
                )
                return

        if not _has_complete_card_metadata(card_metadata):
            await query.answer(
                "No hay datos válidos para crear la tarjeta de esta noticia.",
                show_alert=True,
            )
            return

        if action == "pdf_foto":
            image_paths = _cached_pdf_images(card_metadata)
            try:
                image_index = int(callback_parts[2])
            except (IndexError, ValueError):
                image_index = -1
            if image_index < 0 or image_index >= len(image_paths):
                await query.answer("Esa imagen del PDF ya no está disponible.", show_alert=True)
                return
            await query.answer()
            try:
                await _send_card(
                    context,
                    query.message.chat.id,
                    card_token,
                    card_metadata,
                    image_paths[image_index],
                )
            except (OSError, TypeError, ValueError, TelegramError) as exc:
                logger.error("No se pudo generar la tarjeta con la imagen del PDF: %s", exc)
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text="❌ No se pudo generar la tarjeta con esa imagen.",
                )
                return
            card_metadata["_current_photo"] = True
            _cleanup_card_assets({"_pdf_images": image_paths})
            card_metadata["_pdf_images"] = []
            return

        if action == "otra_foto":
            next_index = card_metadata.get("_archive_index", -1)
            next_index = next_index + 1 if isinstance(next_index, int) else 0
            await query.answer()
            try:
                sent = await _send_archive_card(
                    context,
                    query.message.chat.id,
                    card_token,
                    card_metadata,
                    next_index,
                )
            except (OSError, TypeError, ValueError, TelegramError) as exc:
                logger.error("No se pudo enviar otra foto de archivo: %s", exc)
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text="❌ No se pudo enviar otra foto de archivo.",
                )
                return
            if not sent:
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text="No hay más fotos de archivo para este lugar.",
                )
            return

        await query.answer()
        pdf_path = card_metadata.get("_pdf_path")
        pdf_images = _cached_pdf_images(card_metadata)
        if not pdf_images and isinstance(pdf_path, str) and Path(pdf_path).is_file():
            try:
                extracted_paths = await asyncio.to_thread(
                    extraer_imagenes_pdf,
                    pdf_path,
                    _get_pdf_temp_dir(context),
                    PDF_MAX_PAGES,
                    MAX_PDF_IMAGES,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("No se pudieron extraer imágenes del PDF: %s", exc)
                extracted_paths = []
            finally:
                _remove_local_file(pdf_path)
                card_metadata["_pdf_path"] = None
            pdf_images = [str(path) for path in extracted_paths]
            card_metadata["_pdf_images"] = pdf_images

        if pdf_images:
            try:
                await _send_pdf_image_choices(
                    context,
                    query.message.chat.id,
                    card_token,
                    pdf_images,
                )
            except (OSError, TelegramError) as exc:
                logger.error("No se pudieron enviar las imágenes del PDF: %s", exc)
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text="❌ No se pudieron enviar las imágenes del PDF.",
                )
            return

        try:
            sent = await _send_archive_card(
                context, query.message.chat.id, card_token, card_metadata, 0
            )
            if not sent:
                card_metadata["_current_photo"] = False
                await _send_card(
                    context, query.message.chat.id, card_token, card_metadata
                )
        except (OSError, TypeError, ValueError, TelegramError) as exc:
            logger.error("No se pudo generar la tarjeta: %s", exc)
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="❌ No se pudo generar la tarjeta. Inténtalo de nuevo más tarde.",
            )
        return

    if action == "discard":
        await query.answer()
        await query.message.delete()
        _delete_card_metadata(context, card_token)
        return

    if action not in {"approve", "regenerate"}:
        await query.answer()
        return

    record = _get_card_metadata(context, card_token)
    if record is None:
        await query.answer(
            "Esta noticia es anterior al último reinicio, vuelve a enviarla",
            show_alert=True,
        )
        return

    if action == "approve":
        draft = record.get("_draft")
        if not isinstance(draft, str) or not draft:
            await query.answer(
                "No encontré el borrador de esta noticia. Vuelve a enviarla.",
                show_alert=True,
            )
            return
        tone = record.get("_tone")
        if tone not in ALLOWED_TONES:
            tone = DEFAULT_TONE
        await query.answer()
        await query.edit_message_text(
            f"✅ <b>Copy aprobado</b>\n"
            f"Tono detectado: <b>{TONE_DISPLAY[tone]}</b>\n\n"
            f"<pre>{html.escape(draft)}</pre>",
            parse_mode="HTML",
        )
        _delete_card_metadata(context, card_token)
        return

    press_release_to_regenerate = record.get("_press_release")
    if not isinstance(press_release_to_regenerate, str) or not press_release_to_regenerate:
        await query.answer(
            "No encontré la nota original. Vuélvela a pegar en el chat.",
            show_alert=True,
        )
        return
    await query.answer()

    if action == "regenerate":
        # Remove buttons while generating to prevent double-taps
        await query.edit_message_text("⏳ Regenerando copy…")

        try:
            # Slightly higher temperature so the new draft varies from the previous one
            draft, tone = await generate_copy(
                press_release_to_regenerate, temperature=0.9
            )
        except Exception as exc:
            if is_document_too_long_error(exc):
                logger.warning("Documento demasiado largo para Groq (413): %s", exc)
                await query.edit_message_text(DOCUMENT_TOO_LONG_MESSAGE)
                return
            logger.error("Error al regenerar con Groq: %s", exc)
            await query.edit_message_text(
                "❌ No se pudo regenerar el copy. Inténtalo de nuevo."
            )
            return

        # Misma noticia, mismo token: se actualiza su borrador y se conservan la nota,
        # el PDF y los datos de tarjeta (dependen de la nota, no del copy).
        record["_draft"] = draft
        record["_tone"] = tone

        await query.edit_message_text(
            f"📝 <b>Borrador de copy:</b>\n"
            f"Tono detectado: <b>{TONE_DISPLAY[tone]}</b>\n\n"
            f"<pre>{html.escape(draft)}</pre>",
            reply_markup=_draft_keyboard(card_token),
            parse_mode="HTML",
        )


def setup_langfuse_tracing() -> None:
    """Activa el tracing de Langfuse para las llamadas a Groq y Gemini.

    Instrumentación NO invasiva: parchea los SDKs de Groq y google-genai a nivel de
    cliente (vía OpenInference), así que generate_copy(), generate_card_metadata() y
    describe_poster_image()
    no se tocan. Cualquier fallo aquí (credenciales ausentes/erróneas, red, host mal
    configurado...) se registra en el log y el bot sigue funcionando sin observabilidad.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        logger.info(
            "Langfuse: LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY no configuradas. "
            "Tracing desactivado, el bot funciona igual."
        )
        return

    try:
        from langfuse import get_client
        from openinference.instrumentation import TraceConfig
        from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor
        from openinference.instrumentation.groq import GroqInstrumentor

        langfuse = get_client()
        GroqInstrumentor().instrument()
        # hide_input_images: las fotos de carteles superan el límite de base64 que
        # Langfuse intenta decodificar como media, lo que solo generaba un log de error
        # inofensivo. No hace falta ver la imagen en la traza (basta con el texto/modelo/
        # tokens/coste), así que se oculta en origen en vez de dejar el ruido en el log.
        GoogleGenAIInstrumentor().instrument(config=TraceConfig(hide_input_images=True))

        if langfuse.auth_check():
            logger.info("Langfuse: tracing activo (Groq + Gemini instrumentados).")
        else:
            logger.warning(
                "Langfuse: no se pudo autenticar con las credenciales/host configurados "
                "(revisa LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY/LANGFUSE_HOST). "
                "El bot sigue funcionando pero no se enviarán trazas."
            )
    except Exception as exc:
        logger.warning(
            "Langfuse: no se pudo inicializar el tracing (%s). "
            "El bot sigue funcionando sin observabilidad.",
            exc,
        )


def _build_application(token: str) -> Application:
    app = (
        Application.builder()
        .token(token)
        .post_shutdown(_cleanup_pdf_temp_dir)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("multi", multi_start))
    app.add_handler(CommandHandler("generar", multi_generate))
    app.add_handler(CommandHandler("cancelar", multi_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_press_release))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.COMMAND, handle_unsupported_command))
    return app


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN no está definido en el archivo .env")

    setup_langfuse_tracing()
    app = _build_application(token)
    logger.info("Bot arrancado. Escuchando mensajes (long-polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
