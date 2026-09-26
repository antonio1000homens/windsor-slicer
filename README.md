# Windsor Slicer

Windsor Slicer is a reusable Streamable HTTP MCP service for preparing immutable models from allowlisted GitHub repositories and slicing them with Bambu Studio. It preserves the proven Bambu Studio `v02.08.02.61` runtime and H2D profiles. It never submits a printer job.

## Consumer contract

Each consumer repository declares its models in `.windsor-slicer.yaml`:

```yaml
version: 1
models:
  example-part:
    source: cad/part.scad
    generator: openscad
    output: part.stl
```

Version 1 supports `openscad` (`.scad` to `.stl`) and `copy` (committed `.stl` or `.3mf` to the same output type). Entries have only `source`, `generator`, and `output`; commands, scripts, custom arguments, environment fields, absolute paths, and traversal are rejected.

Requests provide an exact `owner/repository`, a full 40-character commit SHA, and a model key. Configure `SLICER_ALLOWED_REPOSITORIES` as a comma-separated allowlist in the Codespace environment. Consumer files are fetched into `runtime/repos`, checked out as detached worktrees in `runtime/workspaces`, and generated or copied into `runtime/inputs`. Only staged files are accepted by Bambu Studio.

## Local development

```sh
python3 -m venv .venv
.venv/bin/pip install -r mcp_servers/slicer/requirements.txt
SLICER_ALLOWED_REPOSITORIES=owner/repository .venv/bin/python -m unittest discover -s tests -p 'test_slicer_*.py' -v
node --test tests/test_slicer_worker.js
```

The Codespace devcontainer installs OpenSCAD and the pinned Bambu Studio runtime. On start, the authenticated MCP listens on port 8000 only when `SLICER_MCP_BEARER_TOKEN` is configured. The Codespace must also receive `SLICER_ALLOWED_REPOSITORIES` as a Codespaces secret or environment variable.

## MCP authentication

ChatGPT authenticates through Cloudflare Access Managed OAuth. The Worker verifies `Cf-Access-Jwt-Assertion` against `ACCESS_ISSUER`, `ACCESS_AUDIENCE`, and optional `ACCESS_ADDITIONAL_AUDIENCES` using Cloudflare JWKS. Cloudflare Access policy controls users; the Worker has no email allowlist. It removes external identity credentials and sends only `Authorization: Bearer <ORIGIN_BEARER_TOKEN>` to the Codespace. That secret must equal the Codespace's `SLICER_MCP_BEARER_TOKEN`.

The production Worker deployment is gated until direct Codespace acceptance passes. The shared Access application and zone WAF rules are owned by `windsor-app`; this repository owns Worker-side validation only.
