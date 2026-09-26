"""MCP entry point for the repository-independent slicer service."""

from __future__ import annotations

import hmac
import os
import time
from typing import Any

import uvicorn
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_servers.slicer.service import (
    DEFAULT_FILAMENT,
    DEFAULT_MACHINE,
    DEFAULT_PROCESS,
    SlicerService,
    SlicerServiceError,
)


class StaticBearerVerifier(TokenVerifier):
    def __init__(self, expected_token: str, resource_url: str):
        self.expected_token = expected_token
        self.resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="windsor-cloud-slicer",
            scopes=["slicer:use"],
            expires_at=int(time.time()) + 3600,
            resource=self.resource_url,
            subject="cloudflare-proxy",
        )


def _build_mcp() -> MCPServer:
    kwargs: dict[str, Any] = {}
    token = os.environ.get("SLICER_MCP_BEARER_TOKEN", "").strip()
    if token:
        resource_url = os.environ.get(
            "SLICER_MCP_RESOURCE_URL",
            "https://slicer.alf-broadcast.co.uk/mcp",
        ).strip()
        issuer_url = os.environ.get(
            "SLICER_MCP_ISSUER_URL",
            "https://slicer.alf-broadcast.co.uk",
        ).strip()
        kwargs["token_verifier"] = StaticBearerVerifier(token, resource_url)
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=["slicer:use"],
            validate_token_resource=True,
        )

    return MCPServer(
        "Slicer Service",
        version="0.2.0",
        instructions=(
            "Prepare immutable allowlisted repository workspaces, generate or stage "
            "manifest-defined models, and slice them with Bambu Studio. Never start a printer job."
        ),
        **kwargs,
    )


mcp = _build_mcp()
service = SlicerService()


def _safe_call(callable_, *args, **kwargs) -> dict[str, Any]:
    try:
        return callable_(*args, **kwargs)
    except SlicerServiceError as error:
        return {"ok": False, "error": str(error)}


@mcp.tool()
def slicer_prepare_workspace(
    repository: str,
    commit: str,
) -> dict[str, Any]:
    """Prepare an isolated worktree at one immutable repository commit."""
    return _safe_call(service.prepare_workspace, repository, commit)


@mcp.tool()
def slicer_generate_model(
    workspace: str,
    model: str,
) -> dict[str, Any]:
    """Generate or stage one model declared in the consumer manifest."""
    return _safe_call(
        service.generate_model,
        workspace,
        model,
    )


@mcp.tool()
def slicer_capabilities() -> dict[str, Any]:
    """Report available slicer providers, defaults and remote-access posture."""
    return service.capabilities()


@mcp.tool()
def slicer_inspect_model(path: str) -> dict[str, Any]:
    """Inspect one STL/3MF under the repository's allowed input roots."""
    return _safe_call(service.inspect_model, path)


@mcp.tool()
def slicer_list_profiles(profile_type: str | None = None) -> dict[str, Any]:
    """List available H2D-oriented Bambu machine/process/filament profiles."""
    return _safe_call(service.list_profiles, profile_type)


@mcp.tool()
def slicer_slice(
    path: str,
    workspace: str | None = None,
    machine_profile: str = DEFAULT_MACHINE,
    process_profile: str = DEFAULT_PROCESS,
    filament_profile: str = DEFAULT_FILAMENT,
    orient: bool = False,
    bed_type: str | None = None,
) -> dict[str, Any]:
    """Slice one model with the shared BambuStudio pipeline."""
    return _safe_call(
        service.slice_model,
        workspace=workspace,
        path_value=path,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profile=filament_profile,
        orient=orient,
        bed_type=bed_type,
    )


@mcp.tool()
def slicer_validate_for_print(
    path: str,
    workspace: str | None = None,
    machine_profile: str = DEFAULT_MACHINE,
    process_profile: str = DEFAULT_PROCESS,
    filament_profile: str = DEFAULT_FILAMENT,
    orient: bool = False,
    bed_type: str | None = None,
) -> dict[str, Any]:
    """Run a real slice and return a printability result without printing."""
    return _safe_call(
        service.validate_for_print,
        workspace=workspace,
        path_value=path,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profile=filament_profile,
        orient=orient,
        bed_type=bed_type,
    )


@mcp.tool()
def slicer_prepare_print(
    path: str,
    workspace: str | None = None,
    machine_profile: str = DEFAULT_MACHINE,
    process_profile: str = DEFAULT_PROCESS,
    filament_profile: str = DEFAULT_FILAMENT,
    orient: bool = False,
    bed_type: str | None = None,
) -> dict[str, Any]:
    """Generate a validated pre-sliced artifact; never start the printer."""
    return _safe_call(
        service.prepare_print,
        workspace=workspace,
        path_value=path,
        machine_profile=machine_profile,
        process_profile=process_profile,
        filament_profile=filament_profile,
        orient=orient,
        bed_type=bed_type,
    )


@mcp.tool()
def slicer_get_artifact(
    path: str,
    include_base64: bool = False,
) -> dict[str, Any]:
    """Return print-artifact metadata and optionally bounded base64 file data."""
    return _safe_call(
        service.get_artifact,
        path,
        include_base64=include_base64,
    )


@mcp.tool()
def slicer_get_diagnostics(
    log_path: str,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Return a bounded sanitized tail of a slicer diagnostic log."""
    return _safe_call(
        service.get_diagnostics,
        log_path,
        max_chars=max_chars,
    )


async def _health(request: Request) -> JSONResponse:
    expected = os.environ.get("SLICER_MCP_BEARER_TOKEN", "").strip()
    if expected:
        supplied = request.headers.get("authorization", "")
        prefix = "Bearer "
        token = supplied[len(prefix):] if supplied.startswith(prefix) else ""
        if not hmac.compare_digest(token, expected):
            return JSONResponse(
                {"ok": False, "error": "unauthorized"},
                status_code=401,
            )
    provider = service.provider.capabilities()
    return JSONResponse(
        {
            "ok": True,
            "service": "windsor-slicer-mcp",
            "provider_available": bool(provider.get("available")),
        }
    )


def build_http_app(path: str):
    app = mcp.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=True,
    )
    app.routes.append(Route("/health", _health, methods=["GET"]))
    return app


def main() -> None:
    transport = os.environ.get("SLICER_MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
        return
    if transport == "streamable-http":
        host = os.environ.get("SLICER_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("SLICER_MCP_PORT", "8000"))
        path = os.environ.get("SLICER_MCP_PATH", "/mcp")
        uvicorn.run(
            build_http_app(path),
            host=host,
            port=port,
            log_level=os.environ.get("SLICER_MCP_LOG_LEVEL", "info"),
        )
        return
    raise SystemExit(
        "SLICER_MCP_TRANSPORT must be 'stdio' or 'streamable-http'"
    )


if __name__ == "__main__":
    main()
