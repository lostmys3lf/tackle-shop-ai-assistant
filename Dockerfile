FROM python:3.13-slim

# Install uv dari image resmi astral-sh
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

# Atur direktori kerja
WORKDIR /app

# Atur environment variables untuk Python dan uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Salin definisi dependency terlebih dahulu untuk caching layer
COPY pyproject.toml uv.lock ./

# Install dependensi tanpa dev dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Salin kode aplikasi, prompt LLM, migrasi Alembic, dan seed awal
COPY app/ ./app/
COPY prompts/ ./prompts/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY db/ ./db/

# Install project
RUN uv sync --frozen --no-dev

# Buat user sistem non-root untuk menjalankan service API
RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

# Port yang diekspos
EXPOSE 8000

# Perintah menjalankan FastAPI dengan Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
