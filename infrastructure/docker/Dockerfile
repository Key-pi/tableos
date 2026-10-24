FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependencies in their own layer, from the pinned lock file (AA-038): every
# build installs the same versions. Regenerate it after changing
# pyproject.toml (docs/engineering/DEPLOYMENT.md).
COPY requirements.lock ./
RUN pip install -r requirements.lock

# The code runs from /app (the development compose also mounts the working
# copy here, so code changes need no rebuild; restart the service instead).
COPY . .

# Production runs as this unprivileged user (docker-compose.prod.yml).
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/staticfiles \
    && chown -R app /app/staticfiles

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
