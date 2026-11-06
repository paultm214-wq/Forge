# syntax=docker/dockerfile:1
FROM forge-sandbox-kotlin-jvm:latest
ARG ANDROID_SDK_LICENSE_ACCEPTED=no
USER root
ENV ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk
RUN --mount=type=secret,id=system_ca \
    test "$ANDROID_SDK_LICENSE_ACCEPTED" = yes \
    && if [ -f /run/secrets/system_ca ]; then export CURL_CA_BUNDLE=/run/secrets/system_ca; fi; \
    test "$ANDROID_SDK_LICENSE_ACCEPTED" = yes \
    && curl -fsSL https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip -o /tmp/android.zip \
    && echo '7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee  /tmp/android.zip' | sha256sum -c - \
    && mkdir -p /opt/android-sdk/cmdline-tools && unzip -q /tmp/android.zip -d /tmp/android-tools \
    && mv /tmp/android-tools/cmdline-tools /opt/android-sdk/cmdline-tools/latest && rm -rf /tmp/android.zip /tmp/android-tools
COPY --chmod=644 sdk_proxy.py /opt/forge-tools/sdk_proxy.py
COPY templates/android/ /tmp/template/
RUN --mount=type=secret,id=proxy_ca \
    cp /etc/ssl/certs/java/cacerts /tmp/forge-cacerts \
    && if [ -f /run/secrets/proxy_ca ]; then keytool -importcert -noprompt -alias forge-proxy -file /run/secrets/proxy_ca -keystore /tmp/forge-cacerts -storepass changeit; fi \
    && export JAVA_TOOL_OPTIONS='-Djavax.net.ssl.trustStore=/tmp/forge-cacerts -Djavax.net.ssl.trustStorePassword=changeit' \
    && ANDROID_SDK_LICENSE_ACCEPTED="$ANDROID_SDK_LICENSE_ACCEPTED" python /opt/forge-tools/sdk_proxy.py \
    && cd /tmp/template && gradle -I /opt/forge-tools/java_proxy.init.gradle --no-daemon --max-workers=2 --gradle-user-home /opt/forge-android-gradle-home assembleDebug testDebugUnitTest \
    && rm -rf /tmp/template /tmp/forge-cacerts && chmod -R a+rX /opt/android-sdk /opt/forge-android-gradle-home \
    && python -c 'import json;p="/opt/forge-versions.json";d=json.load(open(p));d.update(android_sdk="19.0",api="35",build_tools="35.0.0",agp="8.9.2");open(p,"w").write(json.dumps(d))'
USER 1000:1000
