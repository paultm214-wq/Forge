# syntax=docker/dockerfile:1
FROM golang:1.24.7-trixie AS tools
ENV GOBIN=/out CGO_ENABLED=0 GOMAXPROCS=2 GOFLAGS=-p=2 GOTOOLCHAIN=local
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export SSL_CERT_FILE=/run/secrets/system_ca; fi; \
    go install helm.sh/helm/v3/cmd/helm@v3.18.6 \
    && go install sigs.k8s.io/kustomize/kustomize/v5@v5.6.0 \
    && go install github.com/yannh/kubeconform/cmd/kubeconform@v0.6.7
FROM forge-sandbox-extra-base:latest
USER root
COPY --from=tools /out/ /usr/local/bin/
COPY --chmod=644 verified_downloads.py /opt/forge-tools/verified_downloads.py
COPY --chmod=644 kubernetes-schemas.json /opt/forge-tools/kubernetes-schemas.json
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca SSL_CERT_FILE=/run/secrets/system_ca; fi; \
    pip install --no-cache-dir PyYAML==6.0.2 \
    && python /opt/forge-tools/verified_downloads.py /opt/forge-tools/kubernetes-schemas.json /opt/forge-schemas \
    && python -c 'import json,subprocess;p="/opt/forge-versions.json";d=json.load(open(p));d.update(kubernetes="1.32.0",helm=subprocess.check_output(["helm","version","--short"],text=True).strip(),kustomize=subprocess.check_output(["kustomize","version"],text=True).strip(),kubeconform="0.6.7",schema_revision=json.load(open("/opt/forge-tools/kubernetes-schemas.json"))["revision"]);open(p,"w").write(json.dumps(d))'
USER 1000:1000
