# Cloud slicer runtime

The production endpoint remains `https://slicer.alf-broadcast.co.uk/mcp`. The Worker uses one configured Windsor Slicer Codespace and its port 8000; consumer repositories are immutable input sources, not runtime environments.

## Codespace settings

Set these Codespaces secrets/environment values before starting the service:

- `SLICER_MCP_BEARER_TOKEN`: private origin credential.
- `SLICER_ALLOWED_REPOSITORIES`: exact comma-separated `owner/repository` allowlist.

The devcontainer installs Bambu Studio `v02.08.02.61`, OpenSCAD, and the Python MCP dependencies on creation. `.tools` and the Python environment persist over ordinary stop/start. `postStartCommand` starts Streamable HTTP on port 8000 only when the origin bearer exists. `/health` requires the same bearer.

## Worker configuration

The Worker requires `ACCESS_ISSUER`, `ACCESS_AUDIENCE`, optional `ACCESS_ADDITIONAL_AUDIENCES`, `CODESPACE_OWNER`, `CODESPACE_NAME`, `CODESPACE_PORT`, `GITHUB_CODESPACES_TOKEN`, and `ORIGIN_BEARER_TOKEN`. It validates the signed Access JWT before contacting GitHub, starts a stopped Codespace, opens only the configured port, probes the origin, and then proxies the request with the private origin bearer. No static public MCP client bearer is supported.

The Cloudflare Access MCP application, Managed OAuth/DCR, and `alf-broadcast.co.uk` WAF exceptions are provisioned centrally in `windsor-app`. Do not add a second zone-level Terraform state here.

## Direct smoke

Use the consumer repository's full commit SHA and a manifest model key:

```sh
SLICER_MCP_BEARER_TOKEN='...' \
  .venv/bin/python scripts/slicer/codespace-mcp-smoke.py \
  --url http://127.0.0.1:8000/mcp \
  --repository owner/repository \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --model example-part
```

For a diagnostic fixture, add `--expect-category FLOATING_REGION`; the smoke succeeds only when that structured fatal category is returned. The positive smoke retrieves the `.3mf` and verifies its byte size and SHA-256. Neither smoke submits a print.

The production Worker workflow validates on changes and keeps deployment disabled until `SLICER_CUTOVER_APPROVED=true` is explicitly configured after the direct acceptance gate.
