from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def test_worker_has_access_jwt_auth_and_no_static_client_token(self):
        worker = (ROOT / "cloud/slicer-worker/src/index.js").read_text()
        self.assertIn('request.headers.get("Cf-Access-Jwt-Assertion")', worker)
        self.assertIn("ACCESS_ISSUER", worker)
        self.assertIn("ACCESS_AUDIENCE", worker)
        self.assertNotIn("MCP_CLIENT_TOKEN", worker)
        handler = worker[worker.index("async fetch(request, env)"):]
        self.assertLess(handler.index("await accessPayload(request, env)"), handler.index("ensureReady(env)"))
        for header in ("authorization", "cookie", "cf-access-", "x-auth-request-"):
            self.assertIn(header, worker)

    def test_worker_deploy_is_disabled_until_explicit_cutover_gate(self):
        workflow = (ROOT / ".github/workflows/slicer-worker.yml").read_text()
        self.assertIn("vars.SLICER_CUTOVER_APPROVED == 'true'", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        deploy = (ROOT / "scripts/slicer/deploy-cloud-slicer-worker.sh").read_text()
        self.assertIn("/windsor-slicer/cloud-slicer/", deploy)
        self.assertNotIn("/led/cloud-slicer/", deploy)
        self.assertNotIn("MCP_CLIENT_TOKEN", deploy)
        self.assertIn('--var "CODESPACE_NAME:$CODESPACE_NAME"', deploy)
        self.assertIn('CODESPACE_NAME: ${{ vars.CODESPACE_NAME }}', workflow)

    def test_oidc_policy_is_repository_and_parameter_scoped(self):
        template = (ROOT / "infrastructure/cloud-slicer-deploy-role.yaml").read_text()
        self.assertIn("windsor-slicer-github-cloud-slicer-deploy-role", template)
        self.assertIn("parameter/windsor-slicer/cloud-slicer/github-codespaces-token", template)
        self.assertIn("parameter/windsor-slicer/cloud-slicer/origin-bearer-token", template)
        self.assertIn("parameter/windsor-slicer/cloud-slicer/cloudflare-api-token", template)
        self.assertIn("1389803549", template)


if __name__ == "__main__":
    unittest.main()
