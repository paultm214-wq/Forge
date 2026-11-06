# syntax=docker/dockerfile:1
FROM rust:1.90.0-slim-trixie AS rust
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export SSL_CERT_FILE=/run/secrets/system_ca; fi; rustup target add wasm32-wasip1
FROM forge-sandbox-extra-base:latest
USER root
COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends clang-19=1:19.1.7-3+b1 lld-19=1:19.1.7-3+b1 libclang-rt-19-dev-wasm32=1:19.1.7-3 wasi-libc=0.0~git20241209.574b88d-2 \
    && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /usr/local/rustup /usr/local/cargo
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; pip install --no-cache-dir wasmtime==38.0.0 \
    && python -c 'import json;p="/opt/forge-versions.json";d=json.load(open(p));d.update(wasmtime="38.0.0",rust="1.90.0",clang="19.1.7",wasi="Preview 1");open(p,"w").write(json.dumps(d))'
COPY --chmod=644 wasi_runner.py /opt/forge-tools/wasi_runner.py
USER 1000:1000
