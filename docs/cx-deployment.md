# Dialogflow CX deployment

The versioned [agent blueprint](../cx_agent/agent-blueprint.yaml) is a design
source, **not** a Google-generated agent export. Google documents the supported
agent export formats as `BLOB` and `JSON_PACKAGE`; `scripts/export_agent.py`
exports a real agent to the latter, and `scripts/import_agent.py` restores a
native package. A real Google agent cannot be exported or restored without a
Google Cloud project, an existing configured agent, IAM permissions, and
Application Default Credentials.

## Configure the agent

1. Create a Dialogflow CX agent in the Conversational Agents console. Use
   `us-central1` (or another supported location) and English.
2. Implement the flow pages, identity gate, playbooks, OpenAPI tool, and
   webhook mapping from `cx_agent/agent-blueprint.yaml` and
   `cx_agent/openapi.yaml`.
3. Configure the standard webhook URL to the deployed service's `/webhook`
   endpoint. Configure OpenAPI tool calls to the `/cx/tools/*` paths. Use HTTPS
   and configure the same bearer token as `CX_WEBHOOK_TOKEN`.
4. Run conversation cases from `cx_agent/conversation-cases.json` in the CX
   simulator before publishing.
5. Export the configured agent and review the resulting native package:

   ```powershell
   $env:CX_SOURCE_AGENT = "projects/PROJECT_ID/locations/us-central1/agents/AGENT_ID"
   $env:CX_AGENT_PACKAGE = "cx_agent/claims-processing-agent.cx-package"
   py scripts/export_agent.py
   ```

6. To restore that package to a target agent, configure all three variables
   below. The exact confirmation variable is intentionally required because
   Google restore replaces the selected agent's existing resources.

   ```powershell
   $env:CX_TARGET_AGENT = "projects/PROJECT_ID/locations/us-central1/agents/TARGET_AGENT_ID"
   $env:CX_CONFIRM_RESTORE = $env:CX_TARGET_AGENT
   $env:CX_AGENT_PACKAGE = "cx_agent/claims-processing-agent.cx-package"
   py scripts/import_agent.py
   ```

Install the optional SDK with `pip install -e ".[cloud]"`. Configure Google
Application Default Credentials outside the repository. The account needs
`dialogflow.agents.export` for export and `dialogflow.agents.restore` for
restore, plus any required project-level access. Never commit credentials,
tokens, or a `.env` file.

## Local mode

Local mode does not contact Google and requires no cloud credentials:

```powershell
py -m pip install -e ".[dev]"
py -m claims_processing_cx.conversation
```

In a real deployment, place the API behind HTTPS and a supported network
boundary, use a dedicated service identity, configure webhook authentication,
and keep the adjudication API inaccessible to unauthenticated callers. The
prototype's in-memory repository and mock identity fixtures are not a
production identity or persistence system.
