# Ciudad Rodrigo en Vivo — Bot de Telegram

Bot de Telegram que convierte notas de prensa en copys listos para publicar en Instagram, pensado para la gestión de la cuenta de noticias locales **Ciudad Rodrigo en Vivo**.

Se le puede enviar el contenido de cuatro formas intercambiables:
- Pegando el texto de la nota de prensa directamente en el chat.
- Adjuntando el documento como **PDF** o **DOCX** (el bot extrae el texto automáticamente).
- Enviando una **foto de un cartel** (el bot la transcribe a texto plano con Gemini Vision antes de redactar).
- Adjuntando una **nota de voz/audio** reenviada (`.mp4`, `.m4a`, `.mp3`, `.ogg`, `.wav`) — el bot la transcribe con Whisper (Groq) antes de redactar.

El bot clasifica automáticamente el **tono** de cada noticia antes de escribir (festivo, sobrio, alerta, político o mejora vecinal — ver [copy_prompt.txt](copy_prompt.txt)) y muestra el tono detectado junto al borrador, de modo que noticias trágicas, avisos de emergencia o notas políticas no salgan con el registro festivo por defecto.

El borrador se envía con tres opciones:

- ✅ **Aprobar** — da el borrador por bueno.
- 🔄 **Regenerar** — pide una variante distinta a partir de la misma nota.
- 🗑 **Descartar** — descarta el borrador.

El copy se muestra en un bloque de código de Telegram para poder copiarlo de un toque sin arrastrar las etiquetas de estado.

## Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v21 — integración con la API de Telegram (long polling).
- [Groq](https://groq.com/) (`openai/gpt-oss-120b`) — generación del copy y clasificación de tono (respuesta en JSON).
- [Groq Whisper](https://console.groq.com/docs/speech-to-text) (`whisper-large-v3`) — transcripción de notas de voz/audio.
- [Google Gemini](https://ai.google.dev/) (`gemini-2.5-flash`, vía SDK `google-genai`) — transcripción neutra de fotos de carteles a texto plano (no le pone voz ni tono).
- [pdfplumber](https://github.com/jsvine/pdfplumber) — extracción de texto de PDF.
- [python-docx](https://python-docx.readthedocs.io/) — extracción de texto de DOCX.
- [Langfuse](https://langfuse.com/) — observabilidad (opcional) de las llamadas a Groq y Gemini: input, output, modelo, tokens, coste y latencia.

El "cerebro" de la voz y de la clasificación de tono vive en [copy_prompt.txt](copy_prompt.txt), no en el código.

## Puesta en marcha

1. Clona el repositorio e instala las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

2. Crea tu propio `.env` a partir de la plantilla y rellena los valores:

   ```bash
   cp .env.example .env
   ```

   Necesitarás:
   - `TELEGRAM_BOT_TOKEN` — token del bot, obtenido de [@BotFather](https://t.me/BotFather).
   - `GROQ_API_KEY` — API key de [Groq](https://console.groq.com/) (genera el copy y transcribe el audio).
   - `GEMINI_API_KEY` — API key de [Google AI Studio](https://aistudio.google.com/) (solo necesaria para procesar fotos de carteles).
   - `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` — opcionales, para observabilidad. Si se dejan vacías, el bot funciona igual sin tracing.

3. Arranca el bot:

   ```bash
   python bot.py
   ```

También se puede desplegar containerizado con el `Dockerfile` incluido (no requiere dependencias del sistema adicionales: ni PDF ni audio necesitan binarios externos).

El estilo del copy generado y las reglas de cada registro de tono se pueden ajustar sin tocar código editando [copy_prompt.txt](copy_prompt.txt).
