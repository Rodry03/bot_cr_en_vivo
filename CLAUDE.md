# bot_crenvivo

## Qué es el proyecto

Bot de Telegram para la cuenta de Instagram **"Ciudad Rodrigo en Vivo"** (noticias locales). Convierte notas de prensa en copys para Instagram.

Flujo: el usuario reenvía al bot una nota de prensa (texto pegado, PDF, DOCX, una foto de un cartel o una nota de voz/audio) → el bot clasifica automáticamente el tono de la noticia y genera un borrador de copy con la voz de la cuenta → lo devuelve con botones inline de **aprobar / regenerar / descartar** y **🖼️ Necesito imagen**, que genera bajo demanda una tarjeta de noticia (imagen 1080×1350) para el post.

## Stack

- Python, `python-telegram-bot` v21 (async, long-polling).
- **Groq** como LLM para generar el copy. Modelo actual: `openai/gpt-oss-120b` (migrado desde `llama-3.3-70b-versatile`, que Groq deprecó). Devuelve JSON (`response_format=json_object`) con dos claves: `tono` y `copy` — ver detección de tono más abajo.
- **Groq Whisper** (`whisper-large-v3`) para transcribir notas de voz/audio reenviadas (mp4/m4a/mp3/ogg/wav), en español (`language="es"`). Groq acepta esos contenedores directamente: no hace falta ffmpeg ni tocar el Dockerfile.
- **Google Gemini** (modelo de visión Flash, vía SDK `google-genai`) como "traductor" imagen → texto plano para las fotos de carteles. No le pone voz ni estilo, solo describe.
- `pdfplumber` y `python-docx` para extraer texto de PDF/DOCX.
- **Pillow** para dibujar las tarjetas de noticia en local ([tarjeta.py](tarjeta.py), fuente Montserrat en `assets/tarjeta-noticia/tarjeta-noticia/fuentes/`). **`assets/tarjeta-noticia/tarjeta-noticia/plantilla.html` es solo la referencia visual del diseño original: el bot no la usa y editarla no cambia las tarjetas.** El render real lo hace `generar_tarjeta()` en tarjeta.py con Pillow, y ahí es donde se cambia el diseño.
- **PyMuPDF** (`fitz`) para extraer fotos incrustadas en PDFs y **PyYAML** para el índice del archivo de fotos.
- El "cerebro" de la voz y de la clasificación de tono está en `copy_prompt.txt` (system prompt con los 5 registros de tono, reglas de estructura, emoji obligatorio y bloque de 2-3 hashtags). **Este fichero es un activo muy trabajado — no modificarlo sin que el usuario lo pida.**
- **Langfuse** para observabilidad de las llamadas a Groq (chat y Whisper) y Gemini (instrumentación automática vía OpenInference para chat/Gemini; span manual para Whisper, ya que el instrumentador de Groq no cubre `audio.transcriptions`).
- El bot se despliega containerizado (Docker) en una **VM de GCP**. El detalle de infraestructura lo gestiona el usuario aparte; Claude no tiene acceso SSH ni a la consola de GCP.

## Arquitectura clave

Hay varias "puertas de entrada" (handler de texto, handler de documentos PDF/DOCX, handler de fotos, handler de audio) que desembocan todas en una función común `generate_and_show_draft()` → `generate_copy()` + envío del borrador con botones. **Cualquier nueva entrada debe reutilizar esa función común, no duplicarla.**

Para fotos, el patrón es: `handle_photo` → Gemini describe el cartel como texto neutro → ese texto entra en la misma `generate_copy()`/`generate_and_show_draft()` de siempre. Gemini nunca decide tono ni estructura; eso vive solo en `copy_prompt.txt` vía Groq.

Para audio, el patrón es análogo: `handle_document` detecta que el documento es audio (por extensión o mime_type) y lo desvía a `handle_audio_document` → `transcribe_audio()` (Groq Whisper) transcribe a texto plano → ese texto entra en la misma `generate_and_show_draft()` de siempre. Whisper tampoco decide tono ni estructura.

`generate_copy()` devuelve una tupla `(copy, tono)`; el tono es uno de `festivo/sobrio/alerta/politico/mejora` (`ALLOWED_TONES` en bot.py), con fallback a `"festivo"` si el JSON no parsea. Se muestra en Telegram como línea "Tono detectado: X" fuera del bloque `<pre>` del copy (para que el botón de copiar de Telegram solo copie el copy limpio).

### Token por noticia

Cada borrador lleva un token (`secrets.token_hex(8)`) en el `callback_data` de todos sus botones. El registro del token, en `context.user_data["card_metadata"]`, guarda la nota original (`_press_release`), el borrador (`_draft`), el tono (`_tone`), la ruta temporal del PDF si la hay y el estado de la tarjeta. Se conservan como mucho 20 (`MAX_CARD_METADATA`); al echar el más antiguo se borran sus temporales. **Aprobar, Regenerar, Descartar y los botones de tarjeta actúan siempre sobre el registro de su propio token**, nunca sobre "la última noticia" del usuario. Regenerar reutiliza el mismo token y actualiza `_draft`/`_tone`. Si el token no existe (p. ej. tras un reinicio, porque `user_data` vive en memoria), se muestra la alerta "Esta noticia es anterior al último reinicio, vuelve a enviarla".

### Tarjetas de noticia: llamada separada del copy

**Regla: copy y tarjeta van en llamadas separadas a Groq.** `generate_copy()` solo devuelve `{tono, copy}` y no sabe nada de tarjetas. Solo cuando se pulsa **🖼️ Necesito imagen** se llama a `generate_card_metadata()` (prompt propio `CARD_METADATA_INSTRUCTIONS` en bot.py, no en `copy_prompt.txt`), que devuelve los cinco campos `titular` (≤10 palabras; nunca se recorta: si se pasa, se reintenta una vez y, si sigue largo, se usa entero cuando `titular_cabe()` de tarjeta.py confirma que entra en 3 líneas), `resaltado` (literal dentro del titular), `subtitulo` (frase completa con el dato principal, ≤20 palabras; literal o condensada, pero sus nombres, títulos y números tienen que estar en la nota), `categoria` (una de `CATEGORIAS` en tarjeta.py) y `lugar` (lista cerrada `LUGARES` de archivo_fotos.py, o null). `_parse_card_metadata()` valida y corrige. Un error se comunica con una alerta sin afectar al copy. No fusionar las dos llamadas.

Orden de búsqueda de la foto de fondo:
1. Si la noticia vino de un PDF, se ofrecen sus fotos grandes incrustadas (primeras 5 páginas, máximo 8) con un botón "✅ Usar esta imagen" cada una.
2. Si no, la primera foto del archivo asociada al `lugar`; **🔄 Otra foto** pasa a la siguiente.
3. Si no hay ninguna, o se pulsa **🎨 Sin foto**, sale la tarjeta sin foto (fondo oscuro + icono grande de la categoría).

### Archivo de fotos

[archivo_fotos.py](archivo_fotos.py) lee `archivo/indice.yaml` (lista de `{archivo, lugares}`) y valida nombres y lugares contra `LUGARES`. **Las fotos (`archivo/*.jpg`) no están en git**: viven solo en la VM, en `/home/deploy/bot-cr-archivo`, montado en el contenedor como `/app/archivo` en solo lectura. `archivo/indice.yaml` sí se versiona como registro, pero la copia que manda es la de la VM. En un clon limpio faltan las fotos, así que el índice falla al validar y el bot cae a la tarjeta sin foto (se registra el error, no se cae). Para añadir fotos: `python agregar_foto.py <foto> --lugar <lugar>` (genera un JPEG de calidad 85 y como máximo 1350 px de alto, y actualiza el índice) y luego `scp` de la foto y del índice al volumen (ver README). No hace falta redesplegar.

### Modo multi-fuente (/multi)

`/multi` activa la acumulación: cada entrada (texto/PDF/DOCX/foto/audio) pasa por `handle_extracted_text()`, que en este modo la guarda en `multi_sources` en vez de generar el copy. `/generar` junta las fuentes como `FUENTE 1: … FUENTE 2: …` y las pasa a `generate_and_show_draft()` (un solo copy que las contrasta, útil para gobierno vs. oposición). `/cancelar` descarta. Cualquier otro comando avisa sin romper el modo.

## Estado actual (actualizado)

El bot está en producción, corriendo containerizado en un servidor remoto, y funciona con cinco entradas: texto, PDF, DOCX, fotos (carteles) y audio (notas de voz reenviadas).

- Texto/PDF/DOCX/audio transcrito/foto descrita → `generate_copy()` con Groq (`openai/gpt-oss-120b`), que clasifica el tono y redacta en un único paso (JSON `{tono, copy}`).
- Fotos → se describen con Google Gemini (modelo de visión Flash) y esa descripción se pasa a `generate_copy()`. La voz vive en Groq (`copy_prompt.txt`), no en Gemini.
- Audio → se transcribe con Groq Whisper (`whisper-large-v3`, `language="es"`) y esa transcripción se pasa a `generate_copy()`. No requiere ffmpeg: Groq acepta mp4/m4a/mp3/ogg/wav directamente.
- **Detección automática de tono**: 5 registros — festivo (por defecto), sobrio (tragedias/heridos/fallecimientos), alerta (avisos de emergencia/servicio público), político (notas de gobierno/oposición con conflicto real, informa de forma atribuida sin tomar partido) y mejora (obras/inversiones/servicios municipales positivos y sin conflicto, limpiando el autobombo). La frontera político/mejora es explícita: autobombo sin que nadie lo conteste va a "mejora"; conflicto/crítica/oposición real va a "político".
- **Tarjetas de noticia** bajo demanda (botón 🖼️ Necesito imagen), con fotos del PDF, del archivo por lugar o sin foto. Ver "Tarjetas de noticia" arriba.
- **Modo multi-fuente** (`/multi`, `/generar`, `/cancelar`) para combinar varias fuentes en un solo copy.
- **Observabilidad**: el bot está instrumentado con Langfuse (usando la Langfuse AI skill). Reporta trazas de las llamadas a Groq (chat y Whisper) y Gemini: input, output, modelo, tokens, coste y latencia. Las credenciales (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) están en el `.env`. El tracing es best-effort: si Langfuse falla, el bot sigue generando copys igualmente.
- **Nota técnica:** se evaluó usar `load_dotenv(override=True)` para que el `.env` tuviera prioridad (surgió al resolver un conflicto con una variable de entorno del sistema que pisaba el token), pero se revirtió a `load_dotenv()` a secas — se prefiere que una variable de entorno del sistema que pise el `.env` falle de forma visible en vez de quedar enmascarada en silencio. No reintroducir `override=True` sin que el usuario lo pida explícitamente.

## Despliegue

- **Cada push a `main` despliega solo** a producción (GitHub Actions, [.github/workflows/deploy.yml](.github/workflows/deploy.yml)): `py_compile` de bot.py → build y push de la imagen `rodrs03/bot-cr-en-vivo:latest` a Docker Hub → SSH a la VM de GCP como usuario `deploy` → `docker run` del contenedor `bot-cr` con `--restart unless-stopped`. Por eso **no se hace push sin avisar antes al usuario**.
- En la VM: el entorno está en `/home/deploy/.env` (el deploy falla si no existe) y el archivo de fotos en `/home/deploy/bot-cr-archivo`, montado como `-v /home/deploy/bot-cr-archivo:/app/archivo:ro`. El deploy comprueba 5 s después que el contenedor sigue vivo; si no, muestra los últimos 100 logs y falla.
- El Dockerfile copia solo `bot.py`, `copy_prompt.txt`, `tarjeta.py`, `archivo_fotos.py` y las fuentes. Un módulo nuevo hay que añadirlo ahí.
- La CI tiene un job `test` (instala requirements.txt y ejecuta `python -m unittest discover -s tests`). `build-and-push` y `deploy` dependen de él: si falla un test, no se publica la imagen ni se despliega. Los tests no usan claves ni red: Groq y Gemini van mockeados, y en la CI no hay `.env`. Un test futuro que necesite una clave real debe llevar `@unittest.skipUnless(os.environ.get("CLAVE"), ...)` para saltarse en la CI en vez de fallar.
- Ejecutar los tests en local antes de hacer commit: `.\.venv\Scripts\python.exe -m unittest discover -s tests` (son de unittest, no de pytest; el `python` del PATH es de otro proyecto).

No queda ningún "siguiente paso" fijado de antemano — el proyecto está en modo mantenimiento/mejoras puntuales. Ver el backlog más abajo para ideas futuras sin comprometer con ninguna todavía.

## Ideas de mejora futuras (backlog, para más adelante)

- **Evaluación sistemática de copys**: un set de notas de prensa de ejemplo con sus copys "buenos" y un script que compare si un cambio en `copy_prompt.txt` mejora o empeora el resultado (evals).
- **Historial/registro de copys aprobados** (archivo de lo publicado).
- **Variantes de copy**: generar 2-3 versiones para elegir, con un botón cada una.

**Descartadas por ahora — no reabrir sin motivo:**
- Lectura automática de Gmail.
- Publicación automática en Instagram.
- Enlace → copy (por el tema de derechos/extracción sucia de webs).
