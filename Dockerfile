# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
# Older npm lockfiles generated on macOS can omit Rollup's Linux optional binary.
# Install exactly the already locked Rollup version for this build architecture.
RUN npm ci && \
    pkg="@rollup/rollup-linux-$(node -p 'process.arch')-gnu" && \
    version="$(node -p 'require("./node_modules/rollup/package.json").version')" && \
    archive="$(npm pack "$pkg@$version" --pack-destination /tmp --silent)" && \
    mkdir -p "node_modules/$pkg" && \
    tar -xzf "/tmp/$archive" --strip-components=1 -C "node_modules/$pkg" && \
    rm "/tmp/$archive"
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS application
LABEL org.opencontainers.image.source="https://github.com/dfar2008/kacrm" \
      org.opencontainers.image.title="KaCRM"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/kacrm/.venv/bin:$PATH \
    PYTHONPATH=/opt/kacrm:/opt/kacrm/hermes \
    HERMES_CLOUD_SKIP_PATCHES=1 \
    HERMES_BIN=/opt/kacrm/.venv/bin/hermes \
    HERMES_PYTHON=/opt/kacrm/.venv/bin/python \
    HERMES_HOME_ROOT=/var/lib/kacrm/profiles \
    HERMES_CLOUD_REQUIRE_DB=1 \
    HOME=/var/lib/kacrm
WORKDIR /opt/kacrm
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git ripgrep openssh-client tini \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv .venv
COPY pyproject.toml LICENSE LICENSING.md ./
COPY hermes_cloud/ ./hermes_cloud/
COPY hermes/ ./hermes/
COPY hermes-patches/ ./hermes-patches/
COPY alembic/ ./alembic/
COPY alembic.ini hermes_prefork.py ./
COPY deploy/container/kacrm_prepare_hermes.py deploy/container/kacrm-agent-requirements.txt ./deploy/container/
# The build script supplies a pristine export of the pinned Hermes commit.
# Apply overlays here, never reset/clean the developer's Hermes checkout.
RUN python deploy/container/kacrm_prepare_hermes.py
RUN pip install 'setuptools==83.0.0' wheel \
    && pip install --no-build-isolation -e . \
    && pip install --no-deps --no-build-isolation -e ./hermes \
    && pip install -r deploy/container/kacrm-agent-requirements.txt \
    && python -c 'import hermes.crm.db, hermes_cloud.app.main, run_agent; from lark_oapi.ws import Client' \
    && groupadd --gid 10001 kacrm \
    && useradd --uid 10001 --gid kacrm --home-dir /var/lib/kacrm --no-create-home kacrm \
    && mkdir -p /var/lib/kacrm/profiles \
    && chown -R kacrm:kacrm /var/lib/kacrm
COPY deploy/container/kacrm_runtime.py ./deploy/container/
COPY --from=frontend /build/dist/ ./frontend/dist/
RUN rm -rf /var/lib/kacrm/.hermes
USER kacrm
EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "-m", "deploy.container.kacrm_runtime"]
CMD ["web"]

# Build pgvector against Debian's PostgreSQL 15 without shipping compilers.
FROM application AS vector-build
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev postgresql-server-dev-15 ca-certificates git \
    && git clone --quiet https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && git checkout --quiet 778dacf20c07caf904557a88705142631818d8cb \
    && make OPTFLAGS="" with_llvm=no \
    && make install with_llvm=no

FROM application AS all-in-one
USER root
RUN mkdir -p /etc/postgresql-common \
    && printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-15 redis-server \
    && rm -rf /var/lib/apt/lists/*
COPY --from=vector-build /usr/lib/postgresql/15/lib/vector.so /usr/lib/postgresql/15/lib/vector.so
COPY --from=vector-build /usr/share/postgresql/15/extension/vector* /usr/share/postgresql/15/extension/
COPY deploy/container/kacrm_all_in_one.py ./deploy/container/
USER kacrm
HEALTHCHECK --interval=15s --timeout=8s --start-period=180s --retries=5 \
    CMD ["python", "-m", "deploy.container.kacrm_all_in_one", "health"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "-m", "deploy.container.kacrm_all_in_one"]
CMD ["serve"]
