"""Restore a native Dialogflow CX JSON_PACKAGE into an explicitly confirmed agent."""

import os
from pathlib import Path


def main() -> None:
    """Restore the configured native agent package using Application Default Credentials."""
    target_agent = os.environ.get("CX_TARGET_AGENT")
    confirmation = os.environ.get("CX_CONFIRM_RESTORE")
    package_path = Path(
        os.environ.get("CX_AGENT_PACKAGE", "cx_agent/claims-processing-agent.cx-package")
    )
    if not target_agent:
        raise SystemExit("Set CX_TARGET_AGENT to projects/.../locations/.../agents/....")
    if confirmation != target_agent:
        raise SystemExit(
            "Restore replaces resources in the target agent. "
            "Set CX_CONFIRM_RESTORE to the exact CX_TARGET_AGENT value to proceed."
        )
    if not package_path.is_file():
        raise SystemExit(
            f"Native JSON_PACKAGE not found at {package_path}; export an agent first."
        )
    try:
        from google.cloud.dialogflowcx_v3 import AgentsClient
    except ImportError as error:
        raise SystemExit(
            "Install the cloud extra with `pip install -e .[cloud]` before importing."
        ) from error

    client = AgentsClient()
    operation = client.restore_agent(
        request={
            "name": target_agent,
            "agent_content": package_path.read_bytes(),
            "restore_option": "KEEP",
        }
    )
    operation.result()
    print(f"Restored agent package to {target_agent}")


if __name__ == "__main__":
    main()
