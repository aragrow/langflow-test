#!/usr/bin/env bash
# ============================================================
# DEVELOPMENT SCRIPT — DO NOT USE IN PRODUCTION
# ============================================================
# This script starts Langflow for local development only.
#
# Environment variables explained:
#
# LANGFLOW_COMPONENTS_PATH
#   Points Langflow to the custom components directory so
#   components like RegexRouter and GoHighLevelContactLookup
#   are loaded automatically on startup.
#
# LANGFLOW_SKIP_AUTH_AUTO_LOGIN=true
#   Bypasses API key authentication for local dev.
#   All API calls (Postman, curl, playground) work without
#   a Bearer token. NEVER set this in production.
#   Required in v1.5–v1.x. Deprecated in v2.0 — when
#   upgrading to v2.0, remove this and configure auth
#   via Langflow Settings → API Keys instead.
#
# GRPC_DNS_RESOLVER=native
#   Forces gRPC to use the system DNS resolver instead of
#   C-ares. Required on macOS to reach Google's Gemini API
#   (generativelanguage.googleapis.com). Without this,
#   all Gemini calls fail with DNS resolution errors.
#
# DO_NOT_TRACK=true
#   Disables anonymous usage telemetry sent to Langflow/DataStax.
# ============================================================

source .venv/bin/activate

# Kill any running Langflow instances before starting
echo "Stopping any running Langflow processes..."
pkill -f "langflow.__main__" 2>/dev/null
pkill -f "langflow run" 2>/dev/null
sleep 2  # give processes time to fully exit

export LANGFLOW_COMPONENTS_PATH="/Users/david/Documents/python/virtual/langflow-test/artifact/components"
export LANGFLOW_SKIP_AUTH_AUTO_LOGIN=true  # deprecated in v2.0 but required in v1.5
export GRPC_DNS_RESOLVER=native
export DO_NOT_TRACK=true

LF_VERSION=$(langflow --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
LF_PORT=7860

echo ""
echo "============================================================"
echo "  Langflow version : ${LF_VERSION:-unknown}"
echo "  UI  : http://127.0.0.1:${LF_PORT}"
echo "  API : http://127.0.0.1:${LF_PORT}/api/v1"
echo "  Components path  : $LANGFLOW_COMPONENTS_PATH"
echo "============================================================"
echo ""

langflow run --port "$LF_PORT"
