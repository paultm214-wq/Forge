# lab-service-chart

Toolchain: infra

Create a Helm chart and equivalent Kustomize overlay for an internal service on port 8080. Target Kubernetes 1.32.0. Require nonroot, read-only filesystem, explicit CPU/memory requests and limits, and a pinned container image. Test invalid replicas and privileged-container rejection. Export rendered manifests and change report; do not apply them.
