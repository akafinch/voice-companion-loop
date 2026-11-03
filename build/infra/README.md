# Infrastructure: Voice Companion Loop on standard LKE

One Terraform root module creates everything that bills, installs the cluster add-ons and deploys
the project chart (`deploy/chart`). Run `apply` yourself: it creates GPU nodes, and they bill by the
hour.

```bash
export LINODE_TOKEN=...                      # never enters the cluster [S2]
cp terraform.tfvars.example terraform.tfvars # fill it in; git ignores the copy
terraform init && terraform plan
```

## What it creates

| Resource | Terraform | Notes |
|---|---|---|
| Cluster | `linode_lke_cluster.this` | Standard LKE `k8s_version` (default 1.36 [S3]), with three pools: system (CPU); speech, one RTX 4000 Ada for transcription; language model, one RTX PRO 6000 Blackwell for the chat model and speech synthesis. Each GPU pool is labelled and tainted `gpu-class`: `ada` or `blackwell`. |
| Page and relay NodeBalancers | `linode_nodebalancer.page`, `.relay` | Created here, then adopted and preserved by the front's two Services in the project chart, so `destroy` reclaims them. |
| Edge firewall | `linode_firewall.edge` | On both NodeBalancers [S12]: 443 from `allowed_cidrs`, 80 from anywhere for the HTTP-01 challenge only. |
| Node firewall | `helm_release.cloud_firewall` (+ CRD chart) | Created by Akamai's cloud-firewall-controller as `lke-<cluster-id>`, with LKE's required rules [S9]. This module adds one rule: the relay's UDP NodePort, open to `allowed_cidrs` only. |
| GPU scheduling | `helm_release.gpu_plugin` | NVIDIA device plugin 0.20.1, on the GPU pools only, advertising each GPU twice (time-slicing). Standard LKE preinstalls the drivers. |
| TURN relay control plane | `helm_release.stunner` | STUNner 1.2.1: the operator, the auth service and the Gateway API CRDs [S10]. |
| HTTPS front | `helm_release.traefik` | Traefik 3.7.16 (chart 42.1.3). Its own Service is off. Port 80 only redirects to `websecure`, which is the sole default entry point [S11]. Let's Encrypt HTTP-01. One replica. |
| The application | `helm_release.companion` | `deploy/chart`, values below. `wait = false`: watch the models load with `kubectl get pods -n companion -w`. |
| Secrets | `random_password.*` | The TURN shared secret and the VoiceStudio admin key, passed as `set_sensitive` so they land in cluster Secrets. |

The speech engine's version and model (VoiceStudio 0.5.6, VoxCPM2) are baked into the speech image
by CI, so this module only takes that image's reference. Make both images public before `apply`: a
GHCR package is private when first published, only public ones pull anonymously, and the chart takes
no pull secret. Making a package public cannot be undone [S17].

## GPU placement

Transcription pins to `gpu-class: ada`. The chat model and speech synthesis pin to
`gpu-class: blackwell`, each taking one of the card's two time-sliced slots. The plugin's one config
is named `default` and set as `config.default` explicitly, not left to the config manager's
fallback [S8]. Time-slicing isolates neither memory nor faults [S8], so the chat model's memory cap,
set in `deploy/chart`, decides what speech synthesis gets.

## Values contract with `deploy/chart`

The module passes exactly these values; the chart reads them and nothing else from Terraform.
The chart renders the front's two LoadBalancer Services into `front.namespace` (Traefik's), since a
Service selects pods only in its own namespace; everything else lands in `companion`.

`images.{bot,speech,vllm}` · `models.{chat,transcription}` · `placement.{key,speech,language}` ·
`hosts.{page,relay}` · `front.{namespace,podSelector,entryPoint,certResolver,pageNodeBalancerId,relayNodeBalancerId,firewallId}` ·
`relay.{udpNodePort,authUrl}` · `secrets.{turnSharedSecret,voiceStudioAdminKey}` (sensitive)

## `adp.auto.tfvars.json`

Holds the values the project's configuration owns: the LKE engine, the two models, the vLLM image,
and the add-on versions. Terraform loads it automatically. Preconditions fail the plan if a chart
pin in `main.tf` stops matching its version. Change these values at their source, not here.

## Reader caveats

- Both GPU classes, and the RTX PRO 6000 Blackwell as an LKE node pool in `us-sea`, were verified
  against an Akamai account as of 2026-10-13. Confirm in your own account. The Blackwell plan id is
  not in the public catalog [S4], so `language_pool_type` has no default.
- The pool layout (transcription alone on the Ada, the chat model and speech synthesis sharing the
  Blackwell) and the chat model were chosen on measurements made on standard LKE in Seattle on
  27 October 2026, on one RTX 4000 Ada and one RTX PRO 6000 Blackwell node. Confirm the GPU plans in
  your own account, and re-measure before relying on the timings.
- The `<a-b-c-d>.ip.linodeusercontent.com` hostnames were verified for Seattle NodeBalancer
  addresses as of 2026-10-13 [S14]. Confirm for your region.

## Teardown

`terraform destroy` removes the releases, the cluster, both NodeBalancers and the edge firewall.
Three things remain, none of which bill: the controller's `lke-<cluster-id>` firewall, the issued
certificates, and the images on GHCR.

## Open points

- **LKE's API and its reference disagree on prefixed label keys.** The reference documents pool label
  keys such as `example.com/my-app` [S15]. On 2026-10-27 a cluster create with the pool label key
  `vcl-probe/gpu` returned, after `plan` had passed and before anything was created:
  `[400] [node_pools[0].labels.vcl-probe/gpu] label must be alphanumeric, "-", "_" or ".", and must start and end with an alphanumeric character.`
  The same create with `vcl-probe-gpu` succeeded. The module follows the API: `gpu-class` has no
  prefix, and its taints use the same key, which the reference's pattern allows [S15]. Taints are
  cited, not measured: the measured cluster had none.
- **ASSUMPTION (unverified):**
  - The helm provider 3.3 plans `helm_release` while its connection is still unknown, before the
    cluster exists.
  - LKE's kubeconfig carries a bearer token at `users[0].user.token`.

  `terraform plan` on an empty state settles both. If it fails, write the decoded kubeconfig to a
  file in a first apply targeting the cluster.
- **ASSUMPTION (unverified):** the edge firewall is attached here *and* named by the Services'
  `firewall-id` annotation. The assumption is that the cloud controller treats an already-attached
  firewall as done [S13].
- **Docs disagree on pool `firewall_id`.** The provider's docs (v4.9.0) say an omitted pool
  `firewall_id` associates "the default firewall" [S1]. The API reference marked it "Beta, LKE
  Enterprise" as of 2026-10-13. The module sets no pool firewall and relies on the controller.

## Sources (as-of 2026-11-02 unless noted)

- S1. linode/linode: v4.8.0 (2026-10-20) created the measured cluster; v4.9.0 (2026-10-30) adds only
  VXLAN for LKE Enterprise: https://github.com/linode/terraform-provider-linode/releases ·
  `linode_lke_cluster` (pool order, base64 `kubeconfig`, `tier` needs v4beta, `labels`, `taint`):
  https://registry.terraform.io/providers/linode/linode/latest/docs/resources/lke_cluster
- S2. linode provider authentication: https://registry.terraform.io/providers/linode/linode/latest/docs
- S3. LKE supports Kubernetes v1.36: https://techdocs.akamai.com/cloud-computing/changelog/jun-26-2026-lke-now-supports-kubernetes-v136 (as-of 2026-10-13)
- S4. Public plan catalog (`g6-standard-4`, `g2-gpu-rtx4000a1-s`; no Blackwell plan): https://api.linode.com/v4/linode/types
- S5. hashicorp/helm v3.3.0, released 2026-09-02: https://github.com/hashicorp/terraform-provider-helm/releases ·
  provider configuration: https://registry.terraform.io/providers/hashicorp/helm/latest/docs
- S6. `helm_release` (local chart paths, `wait`): https://registry.terraform.io/providers/hashicorp/helm/latest/docs/resources/release ·
  `set_sensitive` as a list of objects: https://registry.terraform.io/providers/hashicorp/helm/latest/docs/guides/v3-upgrade-guide
- S7. hashicorp/random v3.9.1: https://github.com/hashicorp/terraform-provider-random/releases
- S8. NVIDIA device plugin v0.20.1:
  - `config.default` and time-slicing's missing isolation: https://github.com/NVIDIA/k8s-device-plugin/blob/v0.20.1/README.md
  - chart `values.yaml` (affinity, tolerations, `config`) and `templates/validation.yml`: https://github.com/NVIDIA/k8s-device-plugin/tree/v0.20.1/deployments/helm/nvidia-device-plugin
  - an explicit default wins, else the fallbacks run: https://github.com/NVIDIA/k8s-device-plugin/blob/v0.20.1/cmd/config-manager/main.go (lines 360–393)
- S9. cloud-firewall-controller (default rules, install order): https://github.com/linode/cloud-firewall-controller ·
  controller chart 0.2.1 (`firewall.inbound`) and CRD chart 0.2.0: https://linode.github.io/cloud-firewall-controller/index.yaml
- S10. STUNner 1.2.1: https://github.com/l7mp/stunner/blob/v1.2.1/docs/INSTALL.md ·
  https://github.com/l7mp/stunner/blob/v1.2.1/docs/AUTH.md · chart (Service `stunner-auth`, port 8088): https://l7mp.io/stunner/stunner-1.2.1.tgz
- S11. Traefik:
  - routers attach to all entry points by default: https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/router/
  - `asDefault`, redirections: https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/
  - ACME email required, redirection compatible with HTTP-01: https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/
  - chart 42.1.3 `values.yaml` and `templates/_podtemplate.tpl` (redirect and `asDefault` arguments; an emptyDir while persistence is off): https://github.com/traefik/traefik-helm-chart/tree/v42.1.3/traefik
- S12. `linode_firewall` (`nodebalancers`): https://registry.terraform.io/providers/linode/linode/latest/docs/resources/firewall ·
  `linode_nodebalancer` (`ipv4`): https://registry.terraform.io/providers/linode/linode/latest/docs/resources/nodebalancer
- S13. Cloud controller NodeBalancer adoption and firewalls (as-of 2026-10-13): https://github.com/linode/linode-cloud-controller-manager/blob/v0.9.10/cloud/linode/loadbalancers.go ·
  https://github.com/linode/linode-cloud-controller-manager/blob/v0.9.10/cloud/linode/services/firewalls.go
- S14. NodeBalancer hostname form (as-of 2026-10-13): public DNS resolution of the dashed-IPv4 name for Seattle addresses, no CAA record for either form, and prior Let's Encrypt issuance in https://crt.sh
- S15. Linode API v4.215.0, node-pool `labels` and `taints` key rules: https://techdocs.akamai.com/linode-api/reference/post-lke-cluster ·
  https://github.com/linode/linode-api-docs (`openapi.json`)
- S16. Terraform 1.5.0 added `strcontains`: https://github.com/hashicorp/terraform/blob/v1.5/CHANGELOG.md
- S17. GHCR visibility: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry ·
  https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility
