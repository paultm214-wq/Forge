# syntax=docker/dockerfile:1
FROM forge-sandbox-extra-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openscad=2021.01-8 xvfb xauth libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    pip install --no-cache-dir numpy==2.3.4 trimesh==4.9.0 Pillow==12.0.0 \
    && python -c 'import json,importlib.metadata as m;p="/opt/forge-versions.json";d=json.load(open(p));d.update({n:m.version(n) for n in ["numpy","trimesh","Pillow"]});d["openscad"]="2021.01-8";open(p,"w").write(json.dumps(d))'
USER 1000:1000
