FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install '.[embeddings]'
USER 1000:1000
EXPOSE 8000
CMD ["tiny-brain"]
