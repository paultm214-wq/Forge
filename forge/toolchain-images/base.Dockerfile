# syntax=docker/dockerfile:1
FROM python:3.12.12-slim-trixie
ENV DEBIAN_FRONTEND=noninteractive HOME=/tmp/home
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip git build-essential \
    && rm -rf /var/lib/apt/lists/* && useradd --uid 1000 --create-home runner
RUN mkdir -p /opt/forge-tools && python -c 'import json,platform;open("/opt/forge-versions.json","w").write(json.dumps({"python":platform.python_version()}))'
USER 1000:1000
WORKDIR /work
