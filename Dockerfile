# ===============================
# Étape 1 : Build avec UV
# ===============================
FROM --platform=linux/amd64 python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app

# 👉 Copie workspace root
COPY pyproject.toml uv.lock ./

# 👉 Copie tout le repo (packages + workers + services)
COPY src ./src

# 👉 Aller dans CE worker
WORKDIR /app/src/workers/piano_worker

# Installer les deps dans /app/.venv
RUN uv sync --no-dev


# ===============================
# Étape 2 : Image finale
# ===============================
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 👉 root working directory
WORKDIR /app

# Copier l’environnement entier construit
COPY --from=builder /app /app

# 👉 On exécute Celery DANS le worker
WORKDIR /app/src/workers/piano_worker

EXPOSE 8010

# 👉 Lancement via la venv du workspace
CMD ["/app/.venv/bin/celery","-A", "worker.piano_tasks","worker","-Q", "i2i_tasks_queue","--loglevel=info", "-P", "solo"]