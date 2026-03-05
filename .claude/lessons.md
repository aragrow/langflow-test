Virtual environment for LangFlow Desktop App: Where LangFlow is installed


/Users/david/.langflow/.langflow-venv/lib/python3.12/site-packages/langflow


Path where the Langflow log is located:
/Users/david/.langflow/logs

tail -n 10 /Users/david/Library/Caches/langflow/langflow.log           


 tail -f ~/Library/Caches/langflow/langflow.log | grep -Ei "(error|exec)" -A2 -B1


/Users/david/Library/Logs/com.LangflowDesktop/langflow.log




export LANGFLOW_COMPONENTS_PATH='["/Users/david/dev/langflow-test/langflow_components"]'