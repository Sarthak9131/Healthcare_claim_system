"""Export an existing Dialogflow CX agent in its native JSON_PACKAGE format."""

import os
from pathlib import Path


def main() -> None:
    """Export the configured CX agent with Application Default Credentials."""
    source_agent = os.environ.get("CX_SOURCE_AGENT")
    package_path = Path(
        os.environ.get("CX_AGENT_PACKAGE", "cx_agent/claims-processing-agent.cx-package")
    )
    if not source_agent:
        raise SystemExit("Set CX_SOURCE_AGENT to projects/.../locations/.../agents/....")
    try:
        from google.cloud.dialogflowcx_v3 import AgentsClient
    except ImportError as error:
        raise SystemExit(
            "Install the cloud extra with `pip install -e .[cloud]` before exporting."
        ) from error

    client = AgentsClient()
    operation = client.export_agent(
        request={"name": source_agent, "data_format": "JSON_PACKAGE"}
    )
    response = operation.result()
    content = response.agent_content
    if not content:
        raise RuntimeError("The export completed without inline agent content.")
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(content)
    print(f"Exported native JSON_PACKAGE to {package_path}")


if __name__ == "__main__":
    main()
