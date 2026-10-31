#!/usr/bin/env bash
# The only imperative entry point of the platform. See platform/README.md.
set -euo pipefail

# Pinned k3s release. Chart versions are pinned in platform/apps/*/app.yaml.
K3S_VERSION=v1.36.5+k3s1
