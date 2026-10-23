FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependencies in their own layer: rebuilt only when pyproject.toml changes.
COPY pyproject.toml ./
RUN python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('/tmp/requirements.txt', 'w').write('\n'.join(deps))" \
    && pip install -r /tmp/requirements.txt

# The code runs from /app (docker-compose also mounts the working copy here,
# so code changes need no rebuild; restart the service instead).
COPY . .

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
