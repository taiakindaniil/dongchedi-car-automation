FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pin the Python wrapper to match the browser binaries baked into the
# base image. Without this, pip would upgrade playwright to whatever
# the latest is and the wrapper would point at a missing browser path.
RUN pip install --upgrade pip && pip install "playwright==1.48.0"

COPY pyproject.toml README.md ./
COPY src ./src

# `--no-deps` is intentional: we already pinned playwright above and
# everything else is plain Python, so a regular install pulls them in.
RUN pip install .

COPY config ./config

RUN mkdir -p /app/data

CMD ["python", "-m", "avto_bot", "serve"]
