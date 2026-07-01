# Ciudad Rodrigo en Vivo — Bot de Telegram

Bot de Telegram que convierte notas de prensa en copys listos para publicar en Instagram, pensado para la gestión de una cuenta de noticias locales.

Se le puede enviar el contenido de dos formas intercambiables:
- Pegando el texto de la nota de prensa directamente en el chat.
- Adjuntando el documento como **PDF** o **DOCX** (el bot extrae el texto automáticamente).

El bot genera un borrador de copy con estilo propio (registro variado, emojis temáticos y hashtags) y lo muestra con tres opciones:

- ✅ **Aprobar** — da el borrador por bueno.
- 🔄 **Regenerar** — pide una variante distinta a partir de la misma nota.
- 🗑 **Descartar** — descarta el borrador.

## Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — integración con la API de Telegram (long polling).
- [Groq](https://groq.com/) + Llama 3.3 70B — generación del copy.
- [pdfplumber](https://github.com/jsvine/pdfplumber) — extracción de texto de PDF.
- [python-docx](https://python-docx.readthedocs.io/) — extracción de texto de DOCX.

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
   - `GROQ_API_KEY` — API key de [Groq](https://console.groq.com/).

3. Arranca el bot:

   ```bash
   python bot.py
   ```

El estilo del copy generado se puede ajustar sin tocar código editando [copy_prompt.txt](copy_prompt.txt).
