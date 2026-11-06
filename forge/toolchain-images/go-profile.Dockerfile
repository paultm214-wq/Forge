# syntax=docker/dockerfile:1
FROM golang:1.24.7-bookworm AS go
FROM forge-sandbox-python-app:latest
USER root
COPY --from=go /usr/local/go /usr/local/go
ENV PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=local
RUN python -c 'import json,subprocess;p="/opt/forge-versions.json";d=json.load(open(p));d["go"]=subprocess.check_output(["go","version"],text=True).strip();open(p,"w").write(json.dumps(d))'
USER 1000:1000
