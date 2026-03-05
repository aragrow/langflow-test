#!/usr/bin/env bash
source venv/bin/activate
export LANGFLOW_COMPONENTS_PATH="/Users/david/Documents/python/virtual/langflow-test/artifact/components"

echo $LANGFLOW_COMPONENTS_PATH
langflow run
