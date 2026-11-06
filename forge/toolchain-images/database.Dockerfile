# syntax=docker/dockerfile:1
ARG BASE=forge-sandbox-python-app:latest
FROM ${BASE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-17 postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    pip install --no-cache-dir 'psycopg[binary]==3.2.12' \
    && python -c 'import json,subprocess;p="/opt/forge-versions.json";d=json.load(open(p));d["postgres"]=subprocess.check_output(["/usr/lib/postgresql/17/bin/postgres","--version"],text=True).strip();open(p,"w").write(json.dumps(d))'
USER 1000:1000
