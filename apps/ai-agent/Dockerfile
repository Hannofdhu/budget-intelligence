FROM node:24-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl fd-find file git jq procps python3 ripgrep unzip \
    && rm -rf /var/lib/apt/lists/*
RUN ln -s /usr/bin/fdfind /usr/local/bin/fd
WORKDIR /opt/budget-ai
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && ln -s /opt/budget-ai/node_modules/.bin/pi /usr/local/bin/pi
COPY launch.mjs relay.mjs budget-api.mjs ./
RUN ln -s /opt/budget-ai/budget-api.mjs /usr/local/bin/budget-api \
    && chmod 0555 /opt/budget-ai/*.mjs
USER node
WORKDIR /workspace
ENTRYPOINT ["node", "/opt/budget-ai/launch.mjs"]
