# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_SYMLINKS=1 \
    HF_HOME=/models/huggingface \
    HUGGINGFACE_HUB_CACHE=/models/huggingface \
    TRANSFORMERS_CACHE=/models/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY docker/bge_m3/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-create model cache directory to avoid permission issues
RUN mkdir -p /models/huggingface

# Preload the model weights into the image layer to speed up cold starts
RUN python -c "import os; from FlagEmbedding import BGEM3FlagModel; os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS', '1'); BGEM3FlagModel('BAAI/bge-m3', use_fp16=False)"

COPY docker/bge_m3/app /app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
