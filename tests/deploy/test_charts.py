"""The deployment, checked before anything is applied: every chart build/infra/main.tf installs is
rendered with `helm template` and the values Terraform passes it, then read for what must hold on
standard LKE. Needs helm and the network: the upstream charts come from their repositories, at the
pins main.tf itself sets, so a version bump there is what these tests render.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MAIN_TF = (ROOT / "build/infra/main.tf").read_text()
VARIABLES_TF = (ROOT / "build/infra/variables.tf").read_text()
VALUES = Path(__file__).with_name("values")  # what main.tf passes each chart, mirrored

# main.tf's helm_release resources, by their key in its `charts` local: release name and namespace.
RELEASES = {
    "gpu_plugin": ("nvidia-device-plugin", "nvidia-device-plugin"),
    "cfw_crd": ("cloud-firewall-crd", "kube-system"),
    "cfw": ("cloud-firewall", "kube-system"),
    "stunner": ("stunner", "stunner-system"),
    "traefik": ("traefik", "traefik"),
}

WORKLOADS = {"Pod", "Deployment", "DaemonSet", "StatefulSet", "ReplicaSet", "Job", "CronJob"}
NODEBALANCER = "service.beta.kubernetes.io/linode-loadbalancer-"

# LKE's rule for node-pool label keys, which `terraform plan` does not check: no prefix allowed
# (build/infra/README.md, "Open points").
LKE_LABEL_KEY = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

# The NVIDIA device plugin chart's MPS control daemon is host-PID and privileged. It is exempt because
# it schedules only onto nodes labelled nvidia.com/mps.capable=true, which LKE's nodes never carry, so
# it never runs. The check asserts that selector, so the exemption cannot go stale silently.
MPS_DAEMON = ("nvidia-device-plugin-mps-control-daemon", {"nvidia.com/mps.capable": "true"})


# ── Rendering ────────────────────────────────────────────────────────────────────────────────────


def chart_pins() -> dict[str, dict[str, str]]:
    """main.tf's `charts` local: each chart's repo, name, version and app version."""
    block = re.search(r"^  charts = \{\n(.*?)^  \}", MAIN_TF, re.M | re.S)[1]
    return {
        key: dict(re.findall(r'(\w+) = "([^"]*)"', pins))
        for key, pins in re.findall(r"^\s*(\w+)\s*= \{(.*?)\}", block, re.M)
    }


def helm_template(release: str, namespace: str, chart: str, *flags: str, values: Path) -> list[dict]:
    kube_version = re.search(r'variable "k8s_version" \{.*?default\s*= "([^"]+)"', VARIABLES_TF, re.S)[1]
    command = ["helm", "template", release, chart, *flags, "--namespace", namespace]
    command += ["--kube-version", kube_version, "--include-crds"]
    if values.exists():
        command += ["--values", str(values)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.fixture(scope="session")
def rendered() -> dict[str, list[dict]]:
    """Every chart's manifests, CRDs included: the upstream ones by their `charts` key, and the
    project chart as "companion"."""
    if not shutil.which("helm"):
        pytest.fail("these tests render the charts with helm, which is not installed")
    pins = chart_pins()
    assert pins.keys() == RELEASES.keys(), "main.tf's charts are not the ones these tests render"
    charts = {
        key: helm_template(
            release, namespace, pins[key]["chart"], "--repo", pins[key]["repo"],
            "--version", pins[key]["version"], values=VALUES / f"{key}.yaml",
        )
        for key, (release, namespace) in RELEASES.items()
    }
    charts["companion"] = helm_template(
        "companion", "companion", str(ROOT / "deploy/chart"), values=VALUES / "companion.yaml"
    )
    return charts


@pytest.fixture(scope="session")
def values() -> dict:
    return yaml.safe_load((VALUES / "companion.yaml").read_text())


def everything(rendered) -> list[dict]:
    return [doc for docs in rendered.values() for doc in docs]


def one(docs: list[dict], kind: str, name: str) -> dict:
    found = [doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name]
    assert len(found) == 1, f"{len(found)} of {kind} {name}"
    return found[0]


def pod_spec(workload: dict) -> dict:
    spec = workload["spec"]
    if workload["kind"] == "Pod":
        return spec
    if workload["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["spec"]


# ── The values Terraform passes ──────────────────────────────────────────────────────────────────


def test_values_mirror_main_tf():
    """The test values hold exactly main.tf's contract with the project chart, the chart reads
    nothing outside it, and every release main.tf gives values has its mirror here."""
    companion = MAIN_TF.split('resource "helm_release" "companion"')[1]
    contract, group = set(re.findall(r'name = "(secrets\.\w+)"', companion)), None
    for line in companion.split("values = [yamlencode({")[1].split("})]")[0].splitlines():
        if opened := re.match(r"\s*(\w+) = \{$", line):
            group = opened[1]
        elif item := re.match(r"\s*(\w+)\s*=", line):
            contract.add(f"{group}.{item[1]}")

    def keys(tree: dict) -> set[str]:
        return {f"{group}.{key}" for group, items in tree.items() for key in items}

    assert keys(yaml.safe_load((VALUES / "companion.yaml").read_text())) == contract
    assert keys(yaml.safe_load((ROOT / "deploy/chart/values.yaml").read_text())) == contract
    templates = "".join(path.read_text() for path in (ROOT / "deploy/chart/templates").glob("*.yaml"))
    assert set(re.findall(r"\.Values\.(\w+\.\w+)", templates)) <= contract

    # A mirror for each release main.tf passes values, named after its `charts` key.
    given = {
        re.search(r"local\.charts\.(\w+)\.chart", release)[1] if "local.charts" in release else "companion"
        for release in MAIN_TF.split('resource "helm_release"')[1:]
        if "values = [" in release
    }
    assert {path.stem for path in VALUES.glob("*.yaml")} == given


def test_placement_key_is_one_lke_accepts(values):
    """placement.key carries no prefix and matches LKE's rule for node-pool label keys."""
    key = re.search(r'gpu_class_key = "([^"]+)"', MAIN_TF)[1]
    assert values["placement"]["key"] == key
    assert LKE_LABEL_KEY.match(key)


# ── Pods ─────────────────────────────────────────────────────────────────────────────────────────


def test_no_pod_is_host_networked_or_privileged(rendered):
    for workload in (doc for doc in everything(rendered) if doc["kind"] in WORKLOADS):
        spec = pod_spec(workload)
        name = f"{workload['kind']} {workload['metadata']['name']}"
        if workload["metadata"]["name"] == MPS_DAEMON[0]:
            assert spec["nodeSelector"] == MPS_DAEMON[1]
            continue
        assert not spec.get("hostNetwork"), name
        for container in spec.get("initContainers", []) + spec["containers"]:
            assert not (container.get("securityContext") or {}).get("privileged"), name
    # STUNner's operator makes the relay's own pods from this template, so it is read instead.
    assert one(rendered["stunner"], "Dataplane", "default")["spec"]["hostNetwork"] is False


def test_nothing_claims_a_volume(rendered):
    """A claimed volume would outlive the cluster, and bill."""
    for doc in everything(rendered):
        assert doc["kind"] not in {"PersistentVolumeClaim", "PersistentVolume"}, doc["metadata"]["name"]
        if doc["kind"] in WORKLOADS:
            assert not doc["spec"].get("volumeClaimTemplates"), doc["metadata"]["name"]
            for volume in pod_spec(doc).get("volumes", []):
                # A generic ephemeral volume makes a claim too.
                assert not {"persistentVolumeClaim", "ephemeral"} & volume.keys(), volume["name"]


def test_gpu_services_are_cluster_internal_on_their_class(rendered):
    """Chat and speech synthesis select and tolerate gpu-class=blackwell, transcription
    gpu-class=ada, each taking one GPU. Every Deployment is replaced, not rolled, and nothing has a
    liveness probe."""
    companion = rendered["companion"]
    for name, gpu_class in {"chat": "blackwell", "speech": "blackwell", "transcription": "ada"}.items():
        assert one(companion, "Service", name)["spec"]["type"] == "ClusterIP"
        spec = pod_spec(one(companion, "Deployment", name))
        assert spec["nodeSelector"] == {"gpu-class": gpu_class}
        assert spec["tolerations"] == [
            {"key": "gpu-class", "operator": "Equal", "value": gpu_class, "effect": "NoSchedule"}
        ]
        (container,) = spec["containers"]
        assert container["resources"]["limits"] == {"nvidia.com/gpu": 1}
        assert "readinessProbe" in container
    deployments = [doc for doc in companion if doc["kind"] == "Deployment"]
    assert {doc["metadata"]["name"] for doc in deployments} == {"bot", "chat", "speech", "transcription"}
    for deployment in deployments:
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}
        assert all("livenessProbe" not in c for c in pod_spec(deployment)["containers"])


def test_device_plugin_time_slices_each_gpu_pool_two_ways(rendered, values):
    plugin = rendered["gpu_plugin"]
    key = values["placement"]["key"]
    spec = pod_spec(one(plugin, "DaemonSet", "nvidia-device-plugin"))
    assert {"key": key, "operator": "Exists", "effect": "NoSchedule"} in spec["tolerations"]
    affinity = spec["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    (term,) = affinity["nodeSelectorTerms"]
    (match,) = term["matchExpressions"]
    assert (match["key"], match["operator"]) == (key, "In")
    assert set(match["values"]) == {values["placement"]["speech"], values["placement"]["language"]}

    containers = spec["initContainers"] + spec["containers"]
    config_managers = [c for c in containers if c.get("command") == ["config-manager"]]
    assert config_managers
    for container in config_managers:
        assert {"name": "DEFAULT_CONFIG", "value": "default"} in container["env"]
    config = yaml.safe_load(one(plugin, "ConfigMap", "nvidia-device-plugin-configs")["data"]["default"])
    assert config["sharing"]["timeSlicing"]["resources"] == [{"name": "nvidia.com/gpu", "replicas": 2}]


# ── The front and the relay ──────────────────────────────────────────────────────────────────────


def test_exactly_two_load_balancers_each_adopting_a_terraform_nodebalancer(rendered, values):
    """Any other LoadBalancer Service would get a NodeBalancer that outlives `terraform destroy`."""
    front = values["front"]
    balancers = [
        doc for doc in everything(rendered)
        if doc["kind"] == "Service" and doc["spec"].get("type") == "LoadBalancer"
    ]
    adopted = {
        "companion-page": front["pageNodeBalancerId"],
        "companion-relay": front["relayNodeBalancerId"],
    }
    assert sorted(doc["metadata"]["name"] for doc in balancers) == sorted(adopted)
    for service in balancers:
        assert service["metadata"]["namespace"] == front["namespace"]
        assert service["metadata"]["annotations"] == {
            NODEBALANCER + "nodebalancer-id": adopted[service["metadata"]["name"]],
            NODEBALANCER + "preserve": "true",
            NODEBALANCER + "firewall-id": front["firewallId"],
        }
        # The cloud controller reads the IDs as integers, from strings of digits.
        assert adopted[service["metadata"]["name"]].isdigit() and front["firewallId"].isdigit()
        assert service["spec"]["selector"] == front["podSelector"]
        ports = {(port["port"], port["targetPort"]) for port in service["spec"]["ports"]}
        assert ports == {(80, "web"), (443, "websecure")}

    # Both select Traefik's pod, by ports it names.
    traefik = one(rendered["traefik"], "Deployment", "traefik")
    assert front["podSelector"].items() <= traefik["spec"]["template"]["metadata"]["labels"].items()
    (container,) = pod_spec(traefik)["containers"]
    assert {"web", "websecure"} <= {port["name"] for port in container["ports"]}


def test_relay_gateways_are_a_fixed_nodeport_and_cluster_internal(rendered, values):
    gateways = {doc["metadata"]["name"]: doc for doc in everything(rendered) if doc["kind"] == "Gateway"}
    assert gateways.keys() == {"relay-udp", "relay-tcp"}

    udp = gateways["relay-udp"]
    (listener,) = udp["spec"]["listeners"]
    assert listener["protocol"] == "TURN-UDP"
    assert udp["metadata"]["annotations"]["stunner.l7mp.io/service-type"] == "NodePort"
    nodeports = json.loads(udp["metadata"]["annotations"]["stunner.l7mp.io/nodeport"])
    assert nodeports == {listener["name"]: values["relay"]["udpNodePort"]}

    tcp = gateways["relay-tcp"]
    assert [listener["protocol"] for listener in tcp["spec"]["listeners"]] == ["TURN-TCP"]
    assert tcp["metadata"]["annotations"] == {"stunner.l7mp.io/service-type": "ClusterIP"}


def test_routes_attach_only_to_websecure(rendered, values):
    """Each route names the HTTPS entry point alone, beside the Service it names; the page's route
    has no middleware. Traefik redirects port 80 to 443, and websecure is its default entry point."""
    companion = rendered["companion"]
    routes = [doc for doc in everything(rendered) if doc["kind"] in {"IngressRoute", "IngressRouteTCP"}]
    names = {(doc["kind"], doc["metadata"]["name"]) for doc in routes}
    assert names == {("IngressRoute", "page"), ("IngressRouteTCP", "relay")}
    for route in routes:
        assert route["spec"]["entryPoints"] == ["websecure"] == [values["front"]["entryPoint"]]
        assert route["spec"]["tls"]["certResolver"] == values["front"]["certResolver"]

    (page,) = one(companion, "IngressRoute", "page")["spec"]["routes"]
    assert page["match"] == f"Host(`{values['hosts']['page']}`)"
    assert "middlewares" not in page
    assert page["services"] == [{"name": "bot", "port": 8080}]
    assert one(companion, "Service", "bot")["spec"]["ports"][0]["port"] == 8080

    relay = one(companion, "IngressRouteTCP", "relay")["spec"]
    assert relay["tls"]["passthrough"] is False
    (route,) = relay["routes"]
    assert route["match"] == f"HostSNI(`{values['hosts']['relay']}`)"
    # STUNner names the TURN-TCP Gateway's Service after the Gateway.
    assert route["services"] == [{"name": "relay-tcp", "port": 3478}]
    assert one(companion, "Gateway", "relay-tcp")["spec"]["listeners"][0]["port"] == 3478

    (container,) = pod_spec(one(rendered["traefik"], "Deployment", "traefik"))["containers"]
    assert "--entryPoints.web.http.redirections.entryPoint.to=:443" in container["args"]
    assert "--entryPoints.websecure.asDefault=true" in container["args"]
    assert not [arg for arg in container["args"] if "allowACMEByPass" in arg]


def test_node_firewall_admits_the_relay_udp_port(rendered, values):
    firewall = one(rendered["cfw"], "CloudFirewall", "primary")
    assert firewall["spec"].get("defaultRules", True), "LKE's required rules are the defaults"
    udp = [rule for rule in firewall["spec"]["ruleset"]["inbound"] if rule["protocol"] == "UDP"]
    port = str(values["relay"]["udpNodePort"])
    assert [(rule["action"], rule["ports"]) for rule in udp] == [("ACCEPT", port)]
    assert udp[0]["addresses"] == {"ipv4": ["203.0.113.7/32"]}  # the allow-list, nothing wider


def test_custom_resources_are_ones_the_add_ons_install(rendered):
    """Each custom resource the project chart renders is served by a CRD the add-on charts install:
    STUNner's UDPRoute, for one, not the Gateway API's, whose CRD STUNner's chart leaves out."""
    served = {
        (crd["spec"]["group"], version["name"], crd["spec"]["names"]["kind"])
        for crd in everything(rendered) if crd["kind"] == "CustomResourceDefinition"
        for version in crd["spec"]["versions"] if version["served"]
    }
    for doc in rendered["companion"]:
        group, _, version = doc["apiVersion"].rpartition("/")
        if group not in {"", "apps"}:  # the core and apps groups are Kubernetes' own
            assert (group, version, doc["kind"]) in served, f"{doc['apiVersion']} {doc['kind']}"
