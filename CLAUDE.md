# bot_crenvivo

## Qué es el proyecto

Bot de Telegram para la cuenta de Instagram **"Ciudad Rodrigo en Vivo"** (noticias locales). Convierte notas de prensa en copys para Instagram.

Flujo: el usuario reenvía al bot una nota de prensa (texto pegado, PDF, DOCX, una foto de un cartel o una nota de voz/audio) → el bot clasifica automáticamente el tono de la noticia y genera un borrador de copy con la voz de la cuenta → lo devuelve con botones inline de **aprobar / regenerar / descartar**.

## Stack

- Python, `python-telegram-bot` v21 (async, long-polling).
- **Groq** como LLM para generar el copy. Modelo actual: `openai/gpt-oss-120b` (migrado desde `llama-3.3-70b-versatile`, que Groq deprecó). Devuelve JSON (`response_format=json_object`) con dos claves: `tono` y `copy` — ver detección de tono más abajo.
- **Groq Whisper** (`whisper-large-v3`) para transcribir notas de voz/audio reenviadas (mp4/m4a/mp3/ogg/wav), en español (`language="es"`). Groq acepta esos contenedores directamente: no hace falta ffmpeg ni tocar el Dockerfile.
- **Google Gemini** (modelo de visión Flash, vía SDK `google-genai`) como "traductor" imagen → texto plano para las fotos de carteles. No le pone voz ni estilo, solo describe.
- `pdfplumber` y `python-docx` para extraer texto de PDF/DOCX.
- El "cerebro" de la voz y de la clasificación de tono está en `copy_prompt.txt` (system prompt con los 5 registros de tono, reglas de estructura, emoji obligatorio y bloque de 2-3 hashtags). **Este fichero es un activo muy trabajado — no modificarlo sin que el usuario lo pida.**
- **Langfuse** para observabilidad de las llamadas a Groq (chat y Whisper) y Gemini (instrumentación automática vía OpenInference para chat/Gemini; span manual para Whisper, ya que el instrumentador de Groq no cubre `audio.transcriptions`).
- El bot se despliega containerizado (Docker) en una **VM de GCP**. El detalle de infraestructura lo gestiona el usuario aparte; Claude no tiene acceso SSH ni a la consola de GCP.

## Arquitectura clave

Hay varias "puertas de entrada" (handler de texto, handler de documentos PDF/DOCX, handler de fotos, handler de audio) que desembocan todas en una función común `generate_and_show_draft()` → `generate_copy()` + envío del borrador con botones. **Cualquier nueva entrada debe reutilizar esa función común, no duplicarla.**

Para fotos, el patrón es: `handle_photo` → Gemini describe el cartel como texto neutro → ese texto entra en la misma `generate_copy()`/`generate_and_show_draft()` de siempre. Gemini nunca decide tono ni estructura; eso vive solo en `copy_prompt.txt` vía Groq.

Para audio, el patrón es análogo: `handle_document` detecta que el documento es audio (por extensión o mime_type) y lo desvía a `handle_audio_document` → `transcribe_audio()` (Groq Whisper) transcribe a texto plano → ese texto entra en la misma `generate_and_show_draft()` de siempre. Whisper tampoco decide tono ni estructura.

`generate_copy()` devuelve una tupla `(copy, tono)`; el tono es uno de `festivo/sobrio/alerta/politico/mejora` (`ALLOWED_TONES` en bot.py), con fallback a `"festivo"` si el JSON no parsea. Se muestra en Telegram como línea "Tono detectado: X" fuera del bloque `<pre>` del copy (para que el botón de copiar de Telegram solo copie el copy limpio).

## Estado actual (actualizado)

El bot está en producción, corriendo containerizado en un servidor remoto, y funciona con cinco entradas: texto, PDF, DOCX, fotos (carteles) y audio (notas de voz reenviadas).

- Texto/PDF/DOCX/audio transcrito/foto descrita → `generate_copy()` con Groq (`openai/gpt-oss-120b`), que clasifica el tono y redacta en un único paso (JSON `{tono, copy}`).
- Fotos → se describen con Google Gemini (modelo de visión Flash) y esa descripción se pasa a `generate_copy()`. La voz vive en Groq (`copy_prompt.txt`), no en Gemini.
- Audio → se transcribe con Groq Whisper (`whisper-large-v3`, `language="es"`) y esa transcripción se pasa a `generate_copy()`. No requiere ffmpeg: Groq acepta mp4/m4a/mp3/ogg/wav directamente.
- **Detección automática de tono**: 5 registros — festivo (por defecto), sobrio (tragedias/heridos/fallecimientos), alerta (avisos de emergencia/servicio público), político (notas de gobierno/oposición con conflicto real, informa de forma atribuida sin tomar partido) y mejora (obras/inversiones/servicios municipales positivos y sin conflicto, limpiando el autobombo). La frontera político/mejora es explícita: autobombo sin que nadie lo conteste va a "mejora"; conflicto/crítica/oposición real va a "político".
- **Observabilidad**: el bot está instrumentado con Langfuse (usando la Langfuse AI skill). Reporta trazas de las llamadas a Groq (chat y Whisper) y Gemini: input, output, modelo, tokens, coste y latencia. Las credenciales (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) están en el `.env`. El tracing es best-effort: si Langfuse falla, el bot sigue generando copys igualmente.
- **Nota técnica:** se evaluó usar `load_dotenv(override=True)` para que el `.env` tuviera prioridad (surgió al resolver un conflicto con una variable de entorno del sistema que pisaba el token), pero se revirtió a `load_dotenv()` a secas — se prefiere que una variable de entorno del sistema que pise el `.env` falle de forma visible en vez de quedar enmascarada en silencio. No reintroducir `override=True` sin que el usuario lo pida explícitamente.

No queda ningún "siguiente paso" fijado de antemano — el proyecto está en modo mantenimiento/mejoras puntuales. Ver el backlog más abajo para ideas futuras sin comprometer con ninguna todavía.

## Ideas de mejora futuras (backlog, para más adelante)

- **Evaluación sistemática de copys**: un set de notas de prensa de ejemplo con sus copys "buenos" y un script que compare si un cambio en `copy_prompt.txt` mejora o empeora el resultado (evals).
- **Historial/registro de copys aprobados** (archivo de lo publicado).
- **Variantes de copy**: generar 2-3 versiones para elegir, con un botón cada una.

**Descartadas por ahora — no reabrir sin motivo:**
- Lectura automática de Gmail.
- Publicación automática en Instagram.
- Enlace → copy (por el tema de derechos/extracción sucia de webs).
