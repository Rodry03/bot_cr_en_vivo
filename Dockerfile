FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py copy_prompt.txt tarjeta.py ./
COPY assets/tarjeta-noticia/tarjeta-noticia/fuentes/ ./assets/tarjeta-noticia/tarjeta-noticia/fuentes/

CMD ["python", "bot.py"]
