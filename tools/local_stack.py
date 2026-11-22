#!/usr/bin/env python3
"""Prepare and run the Pipesor local runtime stack.

Implements the local profile of pipesor-docs/docs/development/runtime-manifest.md:
signing keys per workload, stack credentials and one env file per service, all under
an operator-owned 0700 directory. Uses only the standard library and the openssl CLI.
"""
import argparse
import json
import os
import secrets
import stat
import string
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "local-stack"
CONFIG = Path(os.environ.get("PIPESOR_LOCAL_HOME", Path.home() / ".config" / "pipesor-local"))

PORTS = {
    "ingestion": 8081, "pipeline": 8082, "log-event": 8083, "nlp": 8084,
    "correlation": 8085, "recommendation": 8086, "rag": 8087,
}
WORKLOADS = tuple(PORTS)
# Inbound trust per target: caller -> roles (platform identity contract v1, route catalog).
TRUST = {
    "ingestion": {"pipeline": ["Pipeline.MachineScope.Check", "Pipeline.GitHubFacts.Read", "Pipeline.CaptureScope.Write"]},
    "pipeline": {
        "correlation": ["Correlation.OperationalReferences.Check"],
        "log-event": ["Evidence.Check"],
        "recommendation": ["Recommendation.DiagnosisEntitlements.Resolve"],
    },
    "log-event": {"correlation": ["Correlation.EvidenceMembership.Check"]},
    "correlation": {"nlp": ["Nlp.ProcessedEvidence.Send"]},
    "recommendation": {"correlation": ["Correlation.Contexts.Deliver"]},
    "rag": {"recommendation": ["Rag.Query"]},
    "nlp": {},
}
HUMAN_APIS = ("ingestion", "pipeline", "log-event", "recommendation")
OUTBOUND = {
    "ingestion": [], "pipeline": ["ingestion"], "log-event": ["pipeline"], "nlp": ["correlation"],
    "correlation": ["pipeline", "log-event", "recommendation"],
    "recommendation": ["rag", "pipeline"], "rag": [],
}
SERVICE_BUS = ("Endpoint=sb://127.0.0.1;SharedAccessKeyName=RootManageSharedAccessKey;"
               "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;")
SUBSCRIPTIONS = {"pipeline": "pipeline", "log-event": "log-event", "nlp": "nlp"}
DASHBOARD_ORIGIN = "http://127.0.0.1:5173"


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise SystemExit(f"{path} must be a directory owned by the current user")
    os.chmod(path, 0o700)
    return path


def write_private(path, text):
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def password(length=32):
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"Aa1-{core}"  # satisfies SQL Server complexity rules


def generate_key(path):
    if path.exists():
        return False
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(path)], check=True, capture_output=True)
    os.chmod(path, 0o600)
    return True


def tenant_id(state):
    if state.get("tenantId"):
        return state["tenantId"]
    authority = CONFIG / "authority.json"
    if authority.exists():
        scopes = json.loads(authority.read_text()).get("machineScopes") or []
        if scopes and scopes[0].get("tenantId"):
            return scopes[0]["tenantId"]
    return str(uuid.uuid4())


def github_settings():
    """GitHub App settings for Ingestion, taken from the operator's existing private capture properties."""
    source = CONFIG / "ingestion-capture.properties"
    values = {}
    if source.exists():
        for line in source.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    settings = {
        "PIPESOR_GITHUB_AUTHORITY_FILE": str(CONFIG / "authority.json"),
        "PIPESOR_GITHUB_CLIENT_SECRET_FILE": str(CONFIG / "github-client-secret"),
        "PIPESOR_GITHUB_CALLBACK_URL": f"http://127.0.0.1:{PORTS['ingestion']}/api/v1/auth/github/callback",
        "PIPESOR_GITHUB_CUSTODY_DIRECTORY": str(CONFIG / "custody"),
    }
    for env, key in (("PIPESOR_GITHUB_APP_ID", "ingestion.local-capture.app"),
                     ("PIPESOR_GITHUB_CAPTURE_DOMAIN", "ingestion.local-capture.domain"),
                     ("PIPESOR_GITHUB_PRIVATE_KEY_FILE", "ingestion.local-capture.signing-key-file"),
                     ("PIPESOR_GITHUB_PRIVATE_KEY_SHA256", "ingestion.local-capture.signing-key-sha256")):
        if values.get(key):
            settings[env] = values[key]
    return settings


def service_env(workload, state, keys):
    port = PORTS[workload]
    lines = {
        "SPRING_PROFILES_ACTIVE": "local",
        "PIPESOR_ENVIRONMENT": "local",
        "SERVER_ADDRESS": "127.0.0.1",
        "SERVER_PORT": str(port),
        "PIPESOR_TENANT_ID": state["tenantId"],
        "PIPESOR_WORKLOAD_ID": workload,
        "PIPESOR_SIGNING_KEY_FILE": str(keys / f"{workload}.pem"),
        "PIPESOR_SECURITY_AUDIENCE": f"urn:pipesor:api:{workload}",
    }
    if workload in JAVA_SERVICES:
        lines["PIPESOR_DATABASE_URL"] = "jdbc:postgresql://127.0.0.1:55432/pipesor"
    elif workload == "rag":
        lines["PIPESOR_DATABASE_URL"] = "postgresql+psycopg://127.0.0.1:55432/pipesor"
    if workload in JAVA_SERVICES or workload == "rag":
        lines["PIPESOR_DATABASE_USERNAME"] = "pipesor"
        lines["PIPESOR_DATABASE_PASSWORD"] = state["postgresPassword"]
    if workload not in JAVA_SERVICES:
        del lines["SPRING_PROFILES_ACTIVE"]
    if workload == "ingestion":
        lines["PIPESOR_SESSION_SIGNING_KEY_FILE"] = str(keys / "session.pem")
        lines.update(github_settings())
        lines["PIPESOR_SERVICE_BUS_CONNECTION_STRING"] = SERVICE_BUS
        lines["PIPESOR_SERVICE_BUS_TOPIC"] = "workflow-evidence"
    if workload == "rag":
        lines.update({
            "PIPESOR_RAG_AUTHENTICATION_MODE": "platform",
            "PIPESOR_RAG_STORAGE_BACKEND": "postgresql",
            "PIPESOR_RAG_EMBEDDING_BACKEND": "azure_openai",
            "PIPESOR_RAG_AZURE_OPENAI_ENDPOINT": "https://ai-pipesor-dev-04f339b7.services.ai.azure.com",
            "PIPESOR_RAG_AZURE_OPENAI_DEPLOYMENT": "pipesor-dev-embeddings",
            "PIPESOR_RAG_EMBEDDING_MODEL_VERSION": "1",
        })
    if workload == "recommendation":
        lines["PIPESOR_INFERENCE_ENDPOINT"] = "https://ai-pipesor-dev-04f339b7.services.ai.azure.com"
        lines["PIPESOR_INFERENCE_DEPLOYMENT"] = "pipesor-dev-diagnostics"
    if workload in SUBSCRIPTIONS:
        lines["PIPESOR_SERVICE_BUS_CONNECTION_STRING"] = SERVICE_BUS
        lines["PIPESOR_SERVICE_BUS_TOPIC"] = "workflow-evidence"
        lines["PIPESOR_SERVICE_BUS_SUBSCRIPTION"] = SUBSCRIPTIONS[workload]
    if workload in HUMAN_APIS:
        lines["PIPESOR_SECURITY_SESSION_JWKSURI"] = f"http://127.0.0.1:{PORTS['ingestion']}/.well-known/jwks.json"
        lines["PIPESOR_DASHBOARD_ORIGIN"] = DASHBOARD_ORIGIN
    for index, (caller, roles) in enumerate(sorted(TRUST[workload].items())):
        prefix = f"PIPESOR_SECURITY_TRUSTEDWORKLOADS_{index}_"
        lines[prefix + "ID"] = caller
        lines[prefix + "JWKSURI"] = f"http://127.0.0.1:{PORTS[caller]}/.well-known/jwks.json"
        lines[prefix + "ROLES"] = ",".join(roles)
    for target in OUTBOUND[workload]:
        lines[f"PIPESOR_ENDPOINTS_{target.upper().replace('-', '')}"] = f"http://127.0.0.1:{PORTS[target]}"
    return "".join(f"{key}={shell_quote(value)}\n" for key, value in lines.items())


def shell_quote(value):
    """Single-quote values so `set -a; . file` keeps characters such as ';' intact."""
    if "'" in value or "\n" in value:
        raise SystemExit("Env values must not contain quotes or newlines")
    return f"'{value}'"


def init(_args):
    private_dir(CONFIG)
    keys = private_dir(CONFIG / "keys")
    envs = private_dir(CONFIG / "env")
    state_path = CONFIG / "local-stack.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state.setdefault("postgresPassword", password())
    state.setdefault("mssqlPassword", password())
    state["tenantId"] = tenant_id(state)
    write_private(state_path, json.dumps(state, indent=2) + "\n")
    write_private(CONFIG / "stack.env",
                  f"PIPESOR_LOCAL_POSTGRES_PASSWORD={state['postgresPassword']}\n"
                  f"PIPESOR_LOCAL_MSSQL_PASSWORD={state['mssqlPassword']}\n")
    created = [name for name in (*WORKLOADS, "session") if generate_key(keys / f"{name}.pem")]
    for workload in WORKLOADS:
        write_private(envs / f"{workload}.env", service_env(workload, state, keys))
    print(f"Local configuration ready in {CONFIG} (new keys: {', '.join(created) or 'none'}).")
    print(f"Tenant: {state['tenantId']}. Service env files: {envs}/<workload>.env")


JAVA_SERVICES = {
    "ingestion": "services/pipesor-ingestion-normalization-service",
    "pipeline": "services/pipesor-pipeline-service",
    "log-event": "services/pipesor-log-event-service",
    "correlation": "services/pipesor-evidence-correlation-engine",
    "recommendation": "services/pipesor-recommendation-service",
}
STATE = Path(os.environ.get("PIPESOR_LOCAL_STATE", Path.home() / ".local" / "state" / "pipesor-local"))


def env_values(workload):
    values = {}
    for line in (CONFIG / "env" / f"{workload}.env").read_text().splitlines():
        key, value = line.split("=", 1)
        values[key] = value[1:-1] if value.startswith("'") and value.endswith("'") else value
    return values


def service_jar(workload):
    target = ROOT.parent / JAVA_SERVICES[workload] / "target"
    jars = [jar for jar in target.glob("*-SNAPSHOT.jar") if not jar.name.endswith("-plain.jar")]
    if len(jars) != 1:
        raise SystemExit(f"Build {JAVA_SERVICES[workload]} first: ./mvnw -B -ntp -DskipTests package")
    return jars[0]


def running(workload):
    pid_file = STATE / f"{workload}.pid"
    if not pid_file.exists():
        return None
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except OSError:
        pid_file.unlink(missing_ok=True)
        return None
    return pid


def services(action, names):
    private_dir(STATE)
    selected = names or list(JAVA_SERVICES)
    for workload in selected:
        if workload not in JAVA_SERVICES:
            raise SystemExit(f"Unknown Java service {workload}")
    for workload in selected:
        pid = running(workload)
        if action == "start":
            if pid:
                print(f"{workload}: already running (pid {pid})")
                continue
            log = open(STATE / f"{workload}.log", "ab")
            process = subprocess.Popen(["java", "-jar", str(service_jar(workload))], env={**os.environ, **env_values(workload)},
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            (STATE / f"{workload}.pid").write_text(str(process.pid))
            print(f"{workload}: started (pid {process.pid}, log {STATE / (workload + '.log')})")
        elif action == "stop":
            if pid:
                os.kill(pid, 15)
                (STATE / f"{workload}.pid").unlink(missing_ok=True)
                print(f"{workload}: stopped")
        else:
            print(f"{workload}: {'running pid ' + str(pid) if pid else 'stopped'} (port {PORTS[workload]})")
    return 0


def compose(*arguments):
    env_file = CONFIG / "stack.env"
    if not env_file.exists():
        raise SystemExit("Run `python3 tools/local_stack.py init` first")
    command = ["docker", "compose", "--env-file", str(env_file), "-f", str(STACK / "compose.yaml"), *arguments]
    return subprocess.run(command, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create keys, credentials and per-service env files")
    commands.add_parser("up", help="start PostgreSQL/pgvector and the Service Bus emulator")
    commands.add_parser("down", help="stop the stack (volumes are kept)")
    commands.add_parser("status", help="show stack containers")
    for action in ("start", "stop", "ps"):
        sub = commands.add_parser(f"services-{action}", help=f"{action} the Java services built in sibling checkouts")
        sub.add_argument("names", nargs="*", help="workloads (default: all Java services)")
    args = parser.parse_args(argv)
    if args.command.startswith("services-"):
        return services(args.command.removeprefix("services-"), args.names)
    if args.command == "init":
        return init(args)
    if args.command == "up":
        return compose("up", "-d", "--wait")
    if args.command == "down":
        return compose("down")
    return compose("ps")


if __name__ == "__main__":
    sys.exit(main())
