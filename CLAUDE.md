# bot_crenvivo

## Qué es el proyecto

Bot de Telegram para la cuenta de Instagram **"Ciudad Rodrigo en Vivo"** (noticias locales). Convierte notas de prensa en copys para Instagram.

Flujo: el usuario reenvía al bot una nota de prensa (texto pegado, PDF, DOCX o una foto de un cartel) → el bot genera un borrador de copy con la voz de la cuenta → lo devuelve con botones inline de **aprobar / regenerar / descartar**.

## Stack

- Python, `python-telegram-bot` v21 (async, long-polling).
- **Groq** como LLM para generar el copy. Modelo actual: `openai/gpt-oss-120b` (migrado desde `llama-3.3-70b-versatile`, que Groq deprecó).
- **Google Gemini** (modelo de visión Flash, vía SDK `google-genai`) como "traductor" imagen → texto plano para las fotos de carteles. No le pone voz ni estilo, solo describe.
- `pdfplumber` y `python-docx` para extraer texto de PDF/DOCX.
- El "cerebro" de la voz está en `copy_prompt.txt` (system prompt con reglas de tono, estructura, emoji obligatorio y bloque de 2-3 hashtags). **Este fichero es un activo muy trabajado — no modificarlo sin que el usuario lo pida.**
- **Langfuse** para observabilidad de las llamadas a Groq y Gemini (instrumentación vía OpenInference, ver sección de Estado actual).
- El bot se despliega containerizado (Docker) en una **VM de GCP**. El detalle de infraestructura lo gestiona el usuario aparte; Claude no tiene acceso SSH ni a la consola de GCP.

## Arquitectura clave

Hay varias "puertas de entrada" (handler de texto, handler de documentos PDF/DOCX, handler de fotos) que desembocan todas en una función común `generate_copy()` + envío del borrador con botones. **Cualquier nueva entrada debe reutilizar esa función común, no duplicarla.**

Para fotos, el patrón es: `handle_photo` → Gemini describe el cartel como texto neutro → ese texto entra en la misma `generate_copy()`/`generate_and_show_draft()` de siempre. Gemini nunca decide tono ni estructura; eso vive solo en `copy_prompt.txt` vía Groq.

## Estado actual (actualizado)

El bot está en producción, corriendo containerizado en un servidor remoto, y funciona con cuatro entradas: texto, PDF, DOCX y fotos (carteles).

- Texto/PDF/DOCX → `generar_copy()` con Groq (`openai/gpt-oss-120b`).
- Fotos → se describen con Google Gemini (modelo de visión Flash) y esa descripción se pasa a `generar_copy()`. La voz vive en Groq (`copy_prompt.txt`), no en Gemini.
- **Observabilidad: recién añadida.** El bot está instrumentado con Langfuse (usando la Langfuse AI skill). Reporta trazas de las llamadas a Groq y Gemini: input, output, modelo, tokens, coste y latencia. Las credenciales (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) están en el `.env`. El tracing es best-effort: si Langfuse falla, el bot sigue generando copys igualmente.
- **Nota técnica:** se evaluó usar `load_dotenv(override=True)` para que el `.env` tuviera prioridad (surgió al resolver un conflicto con una variable de entorno del sistema que pisaba el token), pero se revirtió a `load_dotenv()` a secas — se prefiere que una variable de entorno del sistema que pise el `.env` falle de forma visible en vez de quedar enmascarada en silencio. No reintroducir `override=True` sin que el usuario lo pida explícitamente.

## Próxima mejora (siguiente sesión): ajuste de tono

Añadir la posibilidad de modular el tono del copy según la noticia (ej. un suceso pide un tono más sobrio que una feria).

Idea a explorar: comandos o una forma de indicarle al bot, antes de generar, si quiere un tono más formal/serio o más desenfadado/gracioso, sin tener que editar `copy_prompt.txt`.

Diseño a decidir en la próxima sesión:
- Cómo se le indica el tono al bot (¿comandos tipo `/formal`, `/gracioso`? ¿botones? ¿un parámetro en el mensaje?).
- Cómo se inyecta ese matiz en la generación sin romper la voz base ni tocar `copy_prompt.txt` directamente (probablemente como un fragmento adicional que se añade al prompt de usuario o a un system prompt secundario, no editando el fichero base).

## Ideas de mejora futuras (backlog, para más adelante)

- **Evaluación sistemática de copys**: un set de notas de prensa de ejemplo con sus copys "buenos" y un script que compare si un cambio en `copy_prompt.txt` mejora o empeora el resultado (evals).
- **Historial/registro de copys aprobados** (archivo de lo publicado).
- **Variantes de copy**: generar 2-3 versiones para elegir, con un botón cada una.

**Descartadas por ahora — no reabrir sin motivo:**
- Lectura automática de Gmail.
- Publicación automática en Instagram.
- Enlace → copy (por el tema de derechos/extracción sucia de webs).
