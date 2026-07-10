FROM python:3.13-slim

WORKDIR /app

# CJK/Unicode TrueType font so non-Latin CVs (e.g. Mandarin) render in the PDF
# export instead of showing missing-glyph boxes. reportlab embeds a subset.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# Install deps first for better layer caching.
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

# App code (hookai package + server.py). .env is excluded via .dockerignore;
# secrets are injected at runtime via docker-compose env_file.
COPY . .

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
