# syntax=docker/dockerfile:1
FROM forge-sandbox-extra-base:latest
USER root
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    pip install --no-cache-dir pytest==8.4.2 mypy==1.18.2 build==1.3.0 setuptools==80.9.0 wheel==0.45.1 \
        uv==0.9.4 uvicorn==0.38.0 fastapi==0.120.1 flask==3.1.2 django==5.2.7 \
    && python -c 'import json,importlib.metadata as m;p="/opt/forge-versions.json";d=json.load(open(p));d.update({n:m.version(n) for n in ["pytest","mypy","build","uv","fastapi","flask","django"]});open(p,"w").write(json.dumps(d))'
COPY --chmod=644 locked_dependencies.py /opt/forge-tools/locked_dependencies.py
USER 1000:1000
