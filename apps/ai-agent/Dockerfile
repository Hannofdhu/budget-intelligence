FROM node:24-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl fd-find file fonts-noto-cjk git jq procps python3 python3-venv ripgrep unzip \
    && rm -rf /var/lib/apt/lists/*
RUN ln -s /usr/bin/fdfind /usr/local/bin/fd
WORKDIR /opt/budget-ai
COPY analysis-requirements.txt ./
RUN python3 -m venv /opt/budget-python \
    && /opt/budget-python/bin/python -m pip install --no-cache-dir --only-binary=:all: \
        --require-hashes -r analysis-requirements.txt \
    && /opt/budget-python/bin/python -m pip check
ENV PATH="/opt/budget-python/bin:${PATH}" \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/budget-matplotlib \
    MATPLOTLIBRC=/opt/budget-ai \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && ln -s /opt/budget-ai/node_modules/.bin/pi /usr/local/bin/pi
COPY launch.mjs relay.mjs budget-api.mjs analysis_smoke.py matplotlibrc ./
RUN ln -s /opt/budget-ai/budget-api.mjs /usr/local/bin/budget-api \
    && chmod 0555 /opt/budget-ai/*.mjs
USER node
WORKDIR /workspace
ENTRYPOINT ["node", "/opt/budget-ai/launch.mjs"]
