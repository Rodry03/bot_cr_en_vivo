# bot_crenvivo

## Qué es el proyecto

Bot de Telegram para la cuenta de Instagram **"Ciudad Rodrigo en Vivo"** (noticias locales). Convierte notas de prensa en copys para Instagram.

Flujo: el usuario reenvía al bot una nota de prensa (texto pegado, PDF o DOCX) → el bot genera un borrador de copy con la voz de la cuenta → lo devuelve con botones inline de **aprobar / regenerar / descartar**.

## Stack

- Python, `python-telegram-bot` v21 (async, long-polling).
- **Groq** como LLM para generar el copy. Modelo actual: `openai/gpt-oss-120b` (migrado desde `llama-3.3-70b-versatile`, que Groq deprecó).
- `pdfplumber` y `python-docx` para extraer texto de PDF/DOCX.
- El "cerebro" de la voz está en `copy_prompt.txt` (system prompt con reglas de tono, estructura, emoji obligatorio y bloque de 2-3 hashtags). **Este fichero es un activo muy trabajado — no modificarlo sin que el usuario lo pida.**
- El bot se despliega en un servidor remoto vía Docker (imagen en Docker Hub). El detalle de despliegue lo gestiona el usuario aparte; desde el código, lo relevante es que corre containerizado.

## Arquitectura clave

Hay varias "puertas de entrada" (handler de texto, handler de documentos PDF/DOCX) que desembocan todas en una función común `generate_copy()` + envío del borrador con botones. **Cualquier nueva entrada debe reutilizar esa función común, no duplicarla.**

## Estado actual

Funcionando y estable con: texto, PDF y DOCX.

Se probó una función de fotos con OCR (Tesseract) y se **descartó**: no leía bien datos críticos (horarios, teléfonos) y generaba copys con información inventada. Todo el código de Tesseract ya se ha revertido.

## Próximo paso (siguiente sesión)

Añadir una función de **"foto → copy"** para carteles de eventos, pero usando un **modelo de visión (Google Gemini Flash)** en lugar de OCR.

Diseño acordado:

- Nuevo handler que escucha fotos (`filters.PHOTO`).
- La imagen se manda a la API de Gemini (modelo Flash multimodal), que **describe** el cartel como texto plano y neutro (evento, fecha, lugar, datos) — no le pone voz ni estilo.
- Ese texto descriptivo se pasa a la **misma** función `generate_copy()` de siempre, para que `copy_prompt.txt` (vía Groq) le ponga la voz.
- Es decir: **Gemini traduce imagen → texto, Groq pone la voz.** No meter reglas de copy en el prompt de Gemini.
- La Gemini API key irá en `.env` como variable nueva, aparte de la de Groq.
- Mismo patrón que el handler de documentos: reutilizar la función común de generar copy + botones.
