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
RUN python -m pip install --requirement /tmp/requirements.lock

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts

USER appuser

# The end-to-end CLI is not implemented yet. Keep the runtime target honest by
# validating only the six production packages that currently exist.
CMD ["python", "-c", "import app.requirements, app.llm_runtime, app.requirement_extraction, app.workflow_contracts, app.requirement_subgraph, app.baidu_map; print('modules 01-06 import successfully')"]


FROM runtime AS test

COPY --chown=appuser:appgroup tests ./tests

CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
