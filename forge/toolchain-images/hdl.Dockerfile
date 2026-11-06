# syntax=docker/dockerfile:1
FROM forge-sandbox-extra-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends iverilog=12.0-2+b1 verilator=5.032-1+b2 yosys=0.52-2 \
    && rm -rf /var/lib/apt/lists/* \
    && python -c 'import json;p="/opt/forge-versions.json";d=json.load(open(p));d.update(iverilog="12.0-2+b1",verilator="5.032-1+b2",yosys="0.52-2");open(p,"w").write(json.dumps(d))'
USER 1000:1000
