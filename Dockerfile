FROM python:3.12-slim

# System deps for weasyprint, qrcode/PIL, postgres client
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    libglib2.0-0 \
    shared-mime-info \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml requirements.txt ./

# Install dependencies (non-editable, then copy source)
RUN pip install --no-cache-dir pip setuptools wheel hatchling \
    && pip install --no-cache-dir "bcrypt==4.0.1" \
    && pip install --no-cache-dir -c requirements.txt .

# Copy application source
COPY . .

# Re-install in editable mode now that source is present
RUN pip install --no-cache-dir -c requirements.txt -e .

# Copy entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Release version shown by /api/v1/health (docker build --build-arg APP_VERSION=v1.2.0 .)
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

# Run as an unprivileged user (a compromised app must not be root in the container)
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
# Platforms such as Sevalla tell the app which port to use through $PORT
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
