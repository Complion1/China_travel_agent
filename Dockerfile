ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

WORKDIR /workspace

RUN groupadd --system --gid 10001 appgroup \
    && useradd --system --uid 10001 --gid appgroup --home-dir /home/appuser appuser

COPY requirements.lock /tmp/requirements.lock
RUN sed '/^playwright==/d; /^pyee==/d' /tmp/requirements.lock > /tmp/runtime-requirements.lock \
    && python -m pip install --requirement /tmp/runtime-requirements.lock

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts

USER appuser

CMD ["python", "-m", "app.web_application"]


FROM runtime AS test

COPY --chown=appuser:appgroup tests ./tests

CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


FROM mcr.microsoft.com/playwright/python:v1.63.0-noble AS browser-test
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp XDG_CACHE_HOME=/tmp/.cache
WORKDIR /workspace
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --requirement /tmp/requirements.lock
COPY pyproject.toml README.md ./
COPY app ./app
COPY tests ./tests
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/browser/web_application"]
