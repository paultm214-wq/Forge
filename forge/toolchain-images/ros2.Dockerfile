# syntax=docker/dockerfile:1
FROM ros:jazzy-ros-base-noble
USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv build-essential cmake \
    && rm -rf /var/lib/apt/lists/* && (id 1000 >/dev/null 2>&1 || useradd --uid 1000 --create-home runner) \
    && python3 -m venv --system-site-packages /opt/forge-ros-venv && mkdir -p /opt/forge-tools
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    /opt/forge-ros-venv/bin/pip install --no-cache-dir pytest==8.4.2 colcon-common-extensions==0.3.0 build==1.3.0 \
    && /opt/forge-ros-venv/bin/python -c 'import json,platform;open("/opt/forge-versions.json","w").write(json.dumps({"python":platform.python_version(),"ros":"jazzy","ubuntu":"24.04","colcon-common-extensions":"0.3.0"}))'
COPY --chmod=644 locked_dependencies.py /opt/forge-tools/locked_dependencies.py
ENV PATH=/opt/forge-ros-venv/bin:$PATH HOME=/tmp/home ROS_LOCALHOST_ONLY=1 ROS_LOG_DIR=/tmp/ros-logs
USER 1000:1000
WORKDIR /work
