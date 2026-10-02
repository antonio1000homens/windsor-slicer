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

Manifest version 1 supports `openscad` (`.scad` to `.stl`) and `copy` (committed `.stl` or `.3mf` to the same output type). Version 1 entries have only `source`, `generator`, and `output`; commands, scripts, custom arguments, environment fields, absolute paths, and traversal are rejected. Version 2 keeps those fields and may add named, strictly validated slicing variants:

```yaml
version: 2
models:
  power-supply-tray:
    source: hardware/enclosure/power_supply_tray.scad
    generator: openscad
    output: power_supply_tray.stl
    variants:
      standard:
        support_mode: off
      petg-supported:
        filament_profile: "Bambu PETG Basic @BBL H2D 0.4 nozzle"
        bed_type: "Textured PEI Plate"
        support_mode: tree-auto
```

Variant keys are limited to machine/process/filament profile, bed type, orientation, and `support_mode`. Support modes are `off`, `normal-auto`, and `tree-auto`. Windsor maps the automatic modes to the pinned Bambu settings `normal(auto)` and `tree(auto)` and changes only the generated job's flattened `process.json`; `off` explicitly disables support while retaining the profile's support type as an inactive value. Arbitrary Bambu settings and CLI arguments are not accepted.

For a manifest-defined model, pass `variant="petg-supported"` to `slicer_slice`, `slicer_validate_for_print`, or `slicer_prepare_print`. For example, call `slicer_validate_for_print(path="runtime/inputs/<workspace>/power_supply_tray.stl", workspace="<workspace>", support_mode="off")` for the baseline, then `slicer_prepare_print(path="runtime/inputs/<workspace>/power_supply_tray.stl", workspace="<workspace>", variant="petg-supported")` for the intended print profile. Per-field precedence is explicit MCP request value, named variant value, then existing runtime/default profile behavior. The result includes the selected variant and resolved profiles/support policy. A support-enabled result is ready only when the sliced 3MF includes generated support extrusion moves. Without a variant or support mode, existing behavior is retained.

Support-free and intended-print validation answer different questions. For example, run a baseline with `support_mode="off"` to preserve any unsupported-region warning, then validate or prepare the declared `tree-auto` variant. Windsor does not silently enable support during default validation. `slicer_capabilities` reports the supported modes and manifest-variant capability.

Requests provide an exact `owner/repository`, a full 40-character commit SHA, and a model key. Configure `SLICER_ALLOWED_REPOSITORIES` as a comma-separated allowlist in the Codespace environment. Consumer files are fetched into `runtime/repos`, checked out as detached worktrees in `runtime/workspaces`, and generated or copied into `runtime/inputs`. Only staged files are accepted by Bambu Studio.

## Generated-model and sliced-artifact retrieval

The MCP keeps generated source models and printer-specific outputs in separate storage roots rather than copying files between them:

```text
slicer_prepare_workspace
  -> slicer_generate_model
  -> slicer_get_model          # canonical generated/staged STL or 3MF
  -> slicer_slice / slicer_validate_for_print / slicer_prepare_print
  -> slicer_get_artifact       # sliced printer/profile-specific 3MF
```

`slicer_get_model` accepts only `.stl` or `.3mf` files resolved below `runtime/inputs/`. `slicer_get_artifact` remains restricted to `.3mf` files below `artifacts/slicer/`. Both calls return the repository-relative path, filename, size and SHA-256 digest. Set `include_base64=true` only when the caller needs the bytes; inline transfers are bounded by `SLICER_MCP_MAX_ARTIFACT_BYTES` (10 MiB by default).

The current MCP server uses structured tool results and does not expose a host-backed native downloadable-file reference. Bounded base64 is therefore the portable transfer mechanism today. Retrieval, hashing and size-limit logic is shared internally so a future native file-reference field can be added without changing either storage trust boundary.

Consumer repositories may choose to version canonical generated STLs. Sliced 3MF files encode machine/nozzle/process/filament/bed choices and are normally treated as transient build/print artifacts rather than committed source.

## Local development

```sh
python3 -m venv .venv
.venv/bin/pip install -r mcp_servers/slicer/requirements.txt
SLICER_ALLOWED_REPOSITORIES=owner/repository .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/test_slicer_worker.js
```

The Codespace devcontainer installs OpenSCAD and the pinned Bambu Studio runtime. On start, the authenticated MCP listens on port 8000 only when `SLICER_MCP_BEARER_TOKEN` is configured. The Codespace must also receive `SLICER_ALLOWED_REPOSITORIES` as a Codespaces secret or environment variable.

## Bambu profile selection

`slicer_slice`, `slicer_validate_for_print`, and `slicer_prepare_print` accept optional per-job `machine_profile`, `process_profile`, and `filament_profile` overrides. Each setting resolves in this order: explicit request, corresponding `SLICER_*_PROFILE` runtime environment value, then the built-in fallback. The current Codespace defaults are `Bambu Lab H2D 0.4 nozzle`, `0.20mm Standard @BBL H2D`, and `Bambu PLA Basic @BBL H2D` for machine, process, and filament respectively. `slicer_capabilities` reports the effective runtime defaults and the built-in fallbacks.

To select PETG, call `slicer_list_profiles(profile_type="filament", query="PETG")` (or list all filament profiles without a query), choose the exact installed PETG profile name from the response, then pass that name as `filament_profile` to the desired prepare, validation, or slice call. Type-specific profile listings are not truncated, while an unfiltered all-types listing remains bounded. Profile names are discovered from the installed Bambu Studio profile tree; PETG is not hard-coded. This changes material for that job and does not require editing `devcontainer.json` or rebuilding the Codespace. The selected build plate must also support the material: the live H2D acceptance test used `filament_profile="Bambu PETG Basic @BBL H2D 0.4 nozzle"` together with `bed_type="Textured PEI Plate"`. If `bed_type` is omitted, Bambu Studio may use its implicit Cool Plate selection, which rejects PETG.

## MCP authentication

ChatGPT authenticates through Cloudflare Access Managed OAuth. The Worker verifies `Cf-Access-Jwt-Assertion` against `ACCESS_ISSUER`, `ACCESS_AUDIENCE`, and optional `ACCESS_ADDITIONAL_AUDIENCES` using Cloudflare JWKS. Cloudflare Access policy controls users; the Worker has no email allowlist. It removes external identity credentials and sends only `Authorization: Bearer <ORIGIN_BEARER_TOKEN>` to the Codespace. That secret must equal the Codespace's `SLICER_MCP_BEARER_TOKEN`.

The production Worker deployment is gated until direct Codespace acceptance passes. The shared Access application and zone WAF rules are owned by `windsor-app`; this repository owns Worker-side validation only.


## Live MCP activity

The Codespace MCP writes a sanitized, bounded activity stream containing MCP tool
requests/results and the child-process commands/results they trigger.

Watch it live from a Codespace terminal:

```sh
tail -f /tmp/windsor-slicer-activity.log
```

The default Codespace settings rotate at 5 MiB and keep two backups, so the
activity history is capped at roughly 15 MiB. Rotation deletes the oldest file
automatically. Foreground/manual MCP runs also emit the same activity to stderr;
the background Codespace service disables that duplicate stderr copy because it
already writes the rotating activity file.

Useful overrides are `SLICER_MCP_ACTIVITY_LOG`,
`SLICER_MCP_ACTIVITY_MAX_BYTES`, `SLICER_MCP_ACTIVITY_BACKUPS`,
`SLICER_MCP_ACTIVITY_STDERR`, and `SLICER_MCP_ACTIVITY_FIELD_CHARS`.
Sensitive token/authorization/password fields are redacted and large values,
including inline base64 artifacts, are bounded or omitted.
