#!/usr/bin/env bash
# Download and verify the speech models. Stellis also does this by itself on first start.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --locked stellis setup
