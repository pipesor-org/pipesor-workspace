import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(home):
    with mock.patch.dict(os.environ, {"PIPESOR_LOCAL_HOME": str(home)}):
        spec = importlib.util.spec_from_file_location("local_stack", ROOT / "tools" / "local_stack.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def env(text):
    return dict(line.split("=", 1) for line in text.splitlines())


class LocalStackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "pipesor-local"
        self.stack = load(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        with mock.patch.object(self.stack, "generate_key", return_value=True):
            self.stack.init(None)

    def test_init_creates_private_files_and_one_env_per_workload(self):
        self.init()
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        for name in ("stack.env", "local-stack.json", *(f"env/{w}.env" for w in self.stack.WORKLOADS)):
            self.assertEqual(stat.S_IMODE((self.home / name).stat().st_mode), 0o600, name)

    def test_init_is_idempotent_and_keeps_credentials(self):
        self.init()
        first = json.loads((self.home / "local-stack.json").read_text())
        self.init()
        self.assertEqual(first, json.loads((self.home / "local-stack.json").read_text()))

    def test_init_reuses_the_existing_pilot_tenant(self):
        self.home.mkdir(mode=0o700)
        (self.home / "authority.json").write_text(json.dumps({"machineScopes": [{"tenantId": "t-1"}]}))
        self.init()
        self.assertEqual(env((self.home / "env/pipeline.env").read_text())["PIPESOR_TENANT_ID"], "t-1")

    def test_trust_follows_the_route_catalog(self):
        self.init()
        pipeline = env((self.home / "env/pipeline.env").read_text())
        callers = {pipeline[f"PIPESOR_SECURITY_TRUSTEDWORKLOADS_{i}_ID"]: pipeline[f"PIPESOR_SECURITY_TRUSTEDWORKLOADS_{i}_ROLES"]
                   for i in range(3)}
        self.assertEqual(callers, {
            "correlation": "Correlation.OperationalReferences.Check",
            "log-event": "Evidence.Check",
            "recommendation": "Recommendation.DiagnosisEntitlements.Resolve",
        })
        self.assertNotIn("PIPESOR_SECURITY_TRUSTEDWORKLOADS_0_ID", env((self.home / "env/nlp.env").read_text()))

    def test_only_human_apis_trust_sessions_and_only_consumers_get_subscriptions(self):
        self.init()
        for workload in self.stack.WORKLOADS:
            values = env((self.home / f"env/{workload}.env").read_text())
            self.assertEqual("PIPESOR_SECURITY_SESSION_JWKSURI" in values, workload in self.stack.HUMAN_APIS, workload)
            self.assertEqual(values.get("PIPESOR_SERVICE_BUS_SUBSCRIPTION"), self.stack.SUBSCRIPTIONS.get(workload), workload)
            self.assertTrue(values["PIPESOR_SIGNING_KEY_FILE"].endswith(f"keys/{workload}.pem"))
            for key, value in values.items():
                if key.endswith("JWKSURI") or key.startswith("PIPESOR_ENDPOINTS_"):
                    self.assertTrue(value.startswith("http://127.0.0.1:"), key)

    def test_service_bus_topology_matches_the_manifest(self):
        config = json.loads((ROOT / "local-stack" / "servicebus-config.json").read_text())
        topic, = config["UserConfig"]["Namespaces"][0]["Topics"]
        self.assertEqual(topic["Name"], "workflow-evidence")
        filters = {s["Name"]: s["Rules"][0]["Properties"]["SqlFilter"]["SqlExpression"] for s in topic["Subscriptions"]}
        self.assertEqual(filters, {
            "pipeline": "eventType = 'execution.snapshot'",
            "log-event": "eventType = 'protected_workflow_evidence'",
            "nlp": "eventType = 'protected_workflow_evidence'",
        })
        self.assertTrue(all(s["Properties"]["MaxDeliveryCount"] == 10 for s in topic["Subscriptions"]))


if __name__ == "__main__":
    unittest.main()
