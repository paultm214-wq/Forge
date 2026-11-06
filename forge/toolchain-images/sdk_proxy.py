"""Install only the requested SDK components after explicit license consent."""

import os
import subprocess
import urllib.parse

if os.environ.get("ANDROID_SDK_LICENSE_ACCEPTED") != "yes":
    raise SystemExit("explicit Android SDK license acceptance required")
args = [
    "/opt/android-sdk/cmdline-tools/latest/bin/sdkmanager",
    "--sdk_root=/opt/android-sdk",
]
proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
if proxy:
    endpoint = urllib.parse.urlparse(proxy)
    args += [
        "--proxy=http",
        "--proxy_host=" + endpoint.hostname,
        "--proxy_port=" + str(endpoint.port or 80),
    ]
subprocess.run(
    [*args, "platform-tools", "platforms;android-35", "build-tools;35.0.0"],
    input="y\n" * 100,
    text=True,
    check=True,
    timeout=1200,
)
