# syntax=docker/dockerfile:1
FROM forge-sandbox-extra-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jdk-headless=21.0.13+9-1~deb13u2 openjdk-21-jre-headless=21.0.13+9-1~deb13u2 \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export CURL_CA_BUNDLE=/run/secrets/system_ca; fi; \
    curl -fsSL https://downloads.gradle.org/distributions/gradle-8.14.3-bin.zip -o /tmp/gradle.zip \
    && echo 'bd71102213493060956ec229d946beee57158dbd89d0e62b91bca0fa2c5f3531  /tmp/gradle.zip' | sha256sum -c - \
    && unzip -q /tmp/gradle.zip -d /opt && ln -s /opt/gradle-8.14.3/bin/gradle /usr/local/bin/gradle && rm /tmp/gradle.zip
COPY --chmod=644 java_proxy.init.gradle /opt/forge-tools/java_proxy.init.gradle
COPY --chmod=644 locked_dependencies.py /opt/forge-tools/locked_dependencies.py
COPY templates/kotlin-jvm/ /tmp/template/
RUN --mount=type=secret,id=proxy_ca \
    cp /etc/ssl/certs/java/cacerts /tmp/forge-cacerts \
    && if [ -f /run/secrets/proxy_ca ]; then keytool -importcert -noprompt -alias forge-proxy -file /run/secrets/proxy_ca -keystore /tmp/forge-cacerts -storepass changeit; fi \
    && cd /tmp/template && JAVA_TOOL_OPTIONS='-Djavax.net.ssl.trustStore=/tmp/forge-cacerts -Djavax.net.ssl.trustStorePassword=changeit' \
       gradle -I /opt/forge-tools/java_proxy.init.gradle --no-daemon --max-workers=2 --gradle-user-home /opt/forge-gradle-home test installDist \
    && rm -rf /tmp/template /tmp/forge-cacerts && chmod -R a+rX /opt/forge-gradle-home \
    && python -c 'import json;p="/opt/forge-versions.json";d=json.load(open(p));d.update(gradle="8.14.3",kotlin="2.2.21",jdk="21.0.13+9",junit="5.11.4");open(p,"w").write(json.dumps(d))'
USER 1000:1000
