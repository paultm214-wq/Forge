# syntax=docker/dockerfile:1
FROM node:22.21.0-bookworm-slim AS node
FROM forge-sandbox-python-app:latest
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update && apt-get install -y --no-install-recommends chromium fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/system_ca PIP_CERT=/run/secrets/system_ca; fi; \
    mkdir -p /opt/node-tools /opt/axe && cd /opt/node-tools \
    && npm install --ignore-scripts --no-audit --no-fund --save-exact typescript@5.9.3 vitest@3.2.4 tsx@4.20.6 jsdom@26.1.0 @types/node@22.18.12 \
        vite@7.1.12 react@19.2.0 react-dom@19.2.0 vue@3.5.22 axe-core@4.10.3 pg@8.16.3 \
    && cp node_modules/axe-core/axe.min.js node_modules/axe-core/LICENSE /opt/axe/ \
    && pip install --no-cache-dir playwright==1.56.0 \
    && node -e 'const fs=require("fs"),v={node:process.versions.node};for(const p of ["typescript","vitest","tsx","jsdom","vite","react","vue","pg","axe-core"])v[p]=JSON.parse(fs.readFileSync("node_modules/"+p+"/package.json")).version;fs.writeFileSync("versions.json",JSON.stringify(v));fs.writeFileSync("/opt/forge-versions.json",JSON.stringify({...JSON.parse(fs.readFileSync("/opt/forge-versions.json")),...v}))' \
    && chromium --version > /opt/chromium-version.txt
ENV FORGE_CHROMIUM=/usr/bin/chromium
USER 1000:1000
