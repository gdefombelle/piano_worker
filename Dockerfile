FROM python:3.12.3-slim

WORKDIR /app

# deps système minimales + Poetry
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libpq-dev \
 && pip install --no-cache-dir poetry \
 && rm -rf /var/lib/apt/lists/*

# Copie d'abord les manifests (cache build)
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install --without dev --no-root

# Puis le code
COPY . .

# CMD par défaut (sera écrasé par tes args dans docker run)
CMD ["poetry","run","celery","-A","worker.piano_tasks:app","worker","--loglevel=info"]
