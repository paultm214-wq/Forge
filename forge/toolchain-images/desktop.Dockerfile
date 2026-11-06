# syntax=docker/dockerfile:1
FROM forge-sandbox-python-app:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xauth fonts-dejavu-core libfontconfig1 libdbus-1-3 libglib2.0-0t64 \
    libgl1 libegl1 libxkbcommon0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    pip install --no-cache-dir --only-binary=:all: PySide6==6.9.3 pyinstaller==6.16.0 pyinstaller-hooks-contrib==2025.9 Pillow==12.0.0 \
    && python -c 'import json,importlib.metadata as m;p="/opt/forge-versions.json";d=json.load(open(p));d.update({n:m.version(n) for n in ["PySide6","pyinstaller"]});d["native_platform"]="linux";open(p,"w").write(json.dumps(d))'
ENV QT_QPA_PLATFORM=offscreen LIBGL_ALWAYS_SOFTWARE=1
USER 1000:1000
