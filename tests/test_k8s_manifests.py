"""G8a: the Kubernetes manifests keep the properties the README promises.

CI also renders every overlay and validates it against the Kubernetes
schemas (kubeconform); this checks the parts a schema can't: one replica,
no overlap during upgrades, non-root pods, probes on the right paths,
deny-by-default networking, and no secret values in the manifests.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[1] / "deploy" / "k8s"
SECRET_NAMES = {"BOT_SECRETS_KEY", "BOT_SECRETS_KEY_PREVIOUS", "OPENAI_API_KEY", "DATABASE_URL",
                "QDRANT_API_KEY", "EMAIL_PASSWORD", "AUTH_OIDC_CLIENT_SECRET",
                "AUTH_LOCAL_BOOTSTRAP_PASSWORD", "META_APP_SECRET", "META_CLIENT_SECRET",
                "POSTGRES_PASSWORD"}


def _docs():
    for path in sorted(K8S.rglob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if doc:
                yield path, doc


def _kind(kind):
    return [(p, d) for p, d in _docs() if d["kind"] == kind]


def _workloads():
    return [(p, d) for p, d in _docs() if d["kind"] in ("Deployment", "StatefulSet")]


def _by_name(kind, name):
    (doc,) = [d for _, d in _kind(kind) if d["metadata"]["name"] == name]
    return doc


def test_app_is_one_replica_replaced_not_rolled():
    app = _by_name("Deployment", "emly-app")
    assert app["spec"]["replicas"] == 1
    assert app["spec"]["strategy"]["type"] == "Recreate"


@pytest.mark.parametrize("path,doc", _workloads(), ids=lambda v: getattr(v, "name", None) or v["metadata"]["name"])
def test_every_pod_is_locked_down(path, doc):
    pod = doc["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] != 0
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    for c in pod["containers"]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]
        assert "readinessProbe" in c and "livenessProbe" in c
        assert c["resources"]["limits"]["memory"]


def test_app_probes_use_livez_and_readyz():
    (c,) = _by_name("Deployment", "emly-app")["spec"]["template"]["spec"]["containers"]
    assert c["livenessProbe"]["httpGet"]["path"] == "/api/livez"
    assert c["readinessProbe"]["httpGet"]["path"] == "/api/readyz"
    assert c["startupProbe"]["httpGet"]["path"] == "/api/livez"


def test_qdrant_telemetry_is_off():
    (c,) = _by_name("StatefulSet", "emly-qdrant")["spec"]["template"]["spec"]["containers"]
    assert {"name": "QDRANT__TELEMETRY_DISABLED", "value": "true"} in c["env"]


def test_networking_is_deny_by_default():
    deny = _by_name("NetworkPolicy", "emly-default-deny")
    assert deny["spec"]["podSelector"] == {} and set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    qdrant = _by_name("NetworkPolicy", "emly-qdrant")
    (rule,) = qdrant["spec"]["ingress"]
    assert rule["from"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "emly-app"}}}]
    assert qdrant["spec"]["egress"] == []


def test_no_secret_values_in_manifests():
    assert _kind("Secret") == []
    for _, cm in _kind("ConfigMap"):
        assert not set(cm.get("data", {})) & SECRET_NAMES
    for path, doc in _workloads():
        for c in doc["spec"]["template"]["spec"]["containers"]:
            for env in c.get("env", []):
                assert env["name"] not in SECRET_NAMES, f"{path}: {env['name']} must come from a file"


def test_config_is_production_and_single_worker():
    cm = _by_name("ConfigMap", "emly-config")["data"]
    assert cm["MODE"] == "prod" and cm["WEB_CONCURRENCY"] == "1"
    assert cm["FORWARDED_ALLOW_IPS"] != "*"


def test_readme_documents_the_manifests():
    readme = (K8S.parents[1] / "README.md").read_text()
    assert "deploy/k8s" in readme and "### Kubernetes" in readme
