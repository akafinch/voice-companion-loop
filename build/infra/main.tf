# Voice Companion Loop on standard LKE. Bracketed [Sn] point to README.md, "Sources"
# (as-of 2026-11-02 unless noted there).
# Generate and plan only: `apply` is the operator's, and it bills.

locals {
  kubeconfig = yamldecode(base64decode(linode_lke_cluster.this.kubeconfig)) # base64-encoded [S1]

  # Each GPU pool carries this label and a NoSchedule taint of the same key and value, so only GPU
  # services land there, each pinned to its class. The key has no prefix: LKE rejects a prefixed
  # node-pool label key when the cluster is created, which `plan` cannot catch (README.md, "Open points").
  gpu_class_key = "gpu-class"
  gpu_class = {
    speech   = "ada"       # RTX 4000 Ada: transcription, alone
    language = "blackwell" # RTX PRO 6000 Blackwell: the chat model and speech synthesis, sharing one card
  }

  # The device plugin advertises each GPU this many times, so the chat model and speech synthesis
  # each take one slot of the Blackwell. Time-slicing partitions neither memory nor compute [S8].
  gpu_time_slices = 2

  # Hostnames from the NodeBalancers' IPv4 addresses: they resolve publicly and Let's Encrypt
  # issues for them, so no domain is needed [S14].
  page_host  = "${replace(linode_nodebalancer.page.ipv4, ".", "-")}.ip.linodeusercontent.com"
  relay_host = "${replace(linode_nodebalancer.relay.ipv4, ".", "-")}.ip.linodeusercontent.com"

  allowed_v4 = [for c in var.allowed_cidrs : c if !strcontains(c, ":")]
  allowed_v6 = [for c in var.allowed_cidrs : c if strcontains(c, ":")]

  # Exact chart pins; each `app` must match the adpconf version (checked by preconditions below).
  charts = {
    gpu_plugin = { repo = "https://nvidia.github.io/k8s-device-plugin", chart = "nvidia-device-plugin", version = "0.20.1", app = "0.20.1" }            # [S8]
    cfw_crd    = { repo = "https://linode.github.io/cloud-firewall-controller", chart = "cloud-firewall-crd", version = "0.2.0", app = "0.2.0" }        # [S9]
    cfw        = { repo = "https://linode.github.io/cloud-firewall-controller", chart = "cloud-firewall-controller", version = "0.2.1", app = "0.2.1" } # [S9]
    stunner    = { repo = "https://l7mp.io/stunner", chart = "stunner", version = "1.2.1", app = "1.2.1" }                                              # [S10]
    traefik    = { repo = "https://traefik.github.io/charts", chart = "traefik", version = "42.1.3", app = "3.7.16" }                                   # [S11]
  }

  stunner_namespace = "stunner-system"
  front_selector    = { "companion-front" = "traefik" }
}

# ─── Cluster: standard LKE, three pools ──────────────────────────────────────────────

resource "linode_lke_cluster" "this" {
  label       = var.cluster_label
  region      = var.region
  k8s_version = var.k8s_version
  tags        = [var.cluster_label]
  # `tier` omitted: standard is the default, and the attribute needs api_version v4beta [S1].

  # The provider treats pool order as significant [S1]; keep system first.
  pool {
    type  = var.system_pool_type
    count = var.system_pool_count
  }

  pool {
    type   = var.speech_pool_type
    count  = var.speech_pool_count
    labels = { (local.gpu_class_key) = local.gpu_class.speech }
    taint {
      key    = local.gpu_class_key
      value  = local.gpu_class.speech
      effect = "NoSchedule"
    }
  }

  pool {
    type   = var.language_pool_type
    count  = var.language_pool_count
    labels = { (local.gpu_class_key) = local.gpu_class.language }
    taint {
      key    = local.gpu_class_key
      value  = local.gpu_class.language
      effect = "NoSchedule"
    }
  }
  # No pool `firewall_id`: the node firewall is the cloud-firewall-controller's, which attaches
  # recycled nodes too [S9]. See README.md, "Open points".
}

# ─── Front doors: two NodeBalancers, adopted and preserved by the front's Services ────

resource "linode_nodebalancer" "page" {
  label  = "${var.cluster_label}-page"
  region = var.region
  tags   = [var.cluster_label]
}

resource "linode_nodebalancer" "relay" {
  label  = "${var.cluster_label}-relay"
  region = var.region
  tags   = [var.cluster_label]
}

# Edge firewall: 443 from the allow-list, 80 from anywhere for HTTP-01 only (Traefik redirects
# everything else on 80 to 443). The Services name it by firewall-id, or the cloud controller
# detaches and may delete it [S13].
resource "linode_firewall" "edge" {
  label           = "${var.cluster_label}-edge"
  inbound_policy  = "DROP"
  outbound_policy = "ACCEPT"
  nodebalancers   = [linode_nodebalancer.page.id, linode_nodebalancer.relay.id] # [S12]
  tags            = [var.cluster_label]

  inbound {
    label    = "https-allow-list"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "443"
    ipv4     = length(local.allowed_v4) > 0 ? local.allowed_v4 : null
    ipv6     = length(local.allowed_v6) > 0 ? local.allowed_v6 : null
  }

  inbound {
    label    = "acme-http-01"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "80"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }
}

# ─── Generated secrets, passed to Helm as sensitive values → cluster Secrets ──────────

resource "random_password" "turn_shared_secret" {
  length  = 32
  special = false
}

resource "random_password" "voicestudio_admin_key" {
  length  = 32
  special = false
}

# ─── Cluster add-ons (upstream charts) ────────────────────────────────────────────────
# Preconditions compare "<app>." with "<version>.", so that 0.2 cannot match 0.20.

resource "helm_release" "gpu_plugin" {
  name             = "nvidia-device-plugin"
  namespace        = "nvidia-device-plugin"
  create_namespace = true
  repository       = local.charts.gpu_plugin.repo
  chart            = local.charts.gpu_plugin.chart
  version          = local.charts.gpu_plugin.version

  values = [yamlencode({
    # The chart's default affinity requires NFD/GFD labels LKE nodes do not carry, and its default
    # tolerations do not cover this module's taint [S8]. Pin it to the GPU pools instead.
    tolerations = [{ key = local.gpu_class_key, operator = "Exists", effect = "NoSchedule" }]
    affinity = {
      nodeAffinity = {
        requiredDuringSchedulingIgnoredDuringExecution = {
          nodeSelectorTerms = [{
            matchExpressions = [{ key = local.gpu_class_key, operator = "In", values = values(local.gpu_class) }]
          }]
        }
      }
    }
    # The sharing block the measured cluster ran, as one config. `default` names it explicitly: left
    # empty, the config manager would fall back to whichever config happens to be called "default" [S8].
    config = {
      default = "default"
      map = {
        default = <<-EOT
          version: v1
          sharing:
            timeSlicing:
              resources:
                - name: nvidia.com/gpu
                  replicas: ${local.gpu_time_slices}
        EOT
      }
    }
  })]

  lifecycle {
    precondition {
      condition     = startswith("${local.charts.gpu_plugin.app}.", "${var.gpu_plugin_version}.")
      error_message = "The device-plugin chart pin does not match adpconf's gpu-plugin version."
    }
  }
}

# The CRD chart first; the README installs it before the controller [S9].
resource "helm_release" "cloud_firewall_crd" {
  name       = "cloud-firewall-crd"
  namespace  = "kube-system"
  repository = local.charts.cfw_crd.repo
  chart      = local.charts.cfw_crd.chart
  version    = local.charts.cfw_crd.version
}

resource "helm_release" "cloud_firewall" {
  name       = "cloud-firewall"
  namespace  = "kube-system"
  repository = local.charts.cfw.repo
  chart      = local.charts.cfw.chart
  version    = local.charts.cfw.version
  depends_on = [helm_release.cloud_firewall_crd]

  # Added to the controller's default rules, which carry LKE's required ones [S9]. The node
  # firewall itself (lke-<cluster-id>) is the controller's, not Terraform's.
  values = [yamlencode({
    firewall = {
      inbound = [{
        label       = "relay-udp"
        action      = "ACCEPT"
        description = "TURN relay UDP NodePort, allow-list only"
        protocol    = "UDP"
        ports       = tostring(var.relay_udp_node_port)
        addresses = merge(
          length(local.allowed_v4) > 0 ? { ipv4 = local.allowed_v4 } : {},
          length(local.allowed_v6) > 0 ? { ipv6 = local.allowed_v6 } : {},
        )
      }]
    }
  })]

  lifecycle {
    precondition {
      condition     = startswith("${local.charts.cfw.app}.", "${var.firewall_controller_version}.")
      error_message = "The cloud-firewall-controller chart pin does not match adpconf's node-firewall version."
    }
  }
}

# STUNner's control plane: gateway operator and auth service, with the Gateway API CRDs [S10].
resource "helm_release" "stunner" {
  name             = "stunner"
  namespace        = local.stunner_namespace
  create_namespace = true
  repository       = local.charts.stunner.repo
  chart            = local.charts.stunner.chart
  version          = local.charts.stunner.version

  lifecycle {
    precondition {
      condition     = startswith("${local.charts.stunner.app}.", "${var.stunner_version}.")
      error_message = "The STUNner chart pin does not match adpconf's turn-relay version."
    }
  }
}

# The HTTPS front. Its own LoadBalancer Service is disabled: the project chart owns the two
# Services that adopt the NodeBalancers, or the cloud controller would create a third that
# outlives `destroy` [S13].
resource "helm_release" "traefik" {
  name             = "traefik"
  namespace        = "traefik"
  create_namespace = true
  repository       = local.charts.traefik.repo
  chart            = local.charts.traefik.chart
  version          = local.charts.traefik.version

  values = [yamlencode({
    service = { enabled = false }
    deployment = {
      replicas  = 1 # certificates live in the pod; Let's Encrypt allows 5 per hostname per week
      podLabels = local.front_selector
    }
    # One pod at a time, so two pods never race for the same HTTP-01 challenge.
    updateStrategy = { type = "Recreate", rollingUpdate = null }
    ports = {
      # Routers with no entry points attach to all of them [S11], past the allow-list. Port 80 only
      # redirects (the HTTP-01 challenge still answers: "Redirection is fully compatible with
      # the HTTP-01 challenge" [S11]), and websecure is the only default entry point.
      web       = { http = { redirections = { entryPoint = { to = "websecure", scheme = "https", permanent = true } } } }
      websecure = { asDefault = true }
    }
    certificatesResolvers = {
      letsencrypt = {
        acme = {
          email         = var.acme_email
          storage       = "/data/acme.json" # an emptyDir while persistence is off [S11]
          httpChallenge = { entryPoint = "web" }
        }
      }
    }
  })]

  lifecycle {
    precondition {
      condition     = startswith("${local.charts.traefik.app}.", "${var.traefik_version}.")
      error_message = "The Traefik chart pin does not match adpconf's https-front version."
    }
  }
}

# ─── The project chart (deploy/chart): everything else ────────────────────────────────

resource "helm_release" "companion" {
  name             = "companion"
  namespace        = "companion"
  create_namespace = true
  chart            = "${path.module}/../../deploy/chart"
  # Models take many minutes to load; the reader watches readiness in kubectl rather than
  # holding `apply` open.
  wait = false

  depends_on = [
    helm_release.gpu_plugin,
    helm_release.cloud_firewall,
    helm_release.stunner,
    helm_release.traefik,
  ]

  # The values contract with deploy/chart; build/infra/README.md lists it.
  values = [yamlencode({
    images = {
      bot    = var.bot_image
      speech = var.speech_image
      vllm   = var.vllm_image
    }
    models = {
      chat          = var.chat_model
      transcription = var.transcription_model
    }
    placement = {
      key      = local.gpu_class_key
      speech   = local.gpu_class.speech
      language = local.gpu_class.language
    }
    hosts = {
      page  = local.page_host
      relay = local.relay_host
    }
    front = {
      namespace           = helm_release.traefik.namespace # a Service selects pods in its own namespace only
      podSelector         = local.front_selector
      entryPoint          = "websecure"
      certResolver        = "letsencrypt"
      pageNodeBalancerId  = linode_nodebalancer.page.id
      relayNodeBalancerId = linode_nodebalancer.relay.id
      firewallId          = linode_firewall.edge.id
    }
    relay = {
      udpNodePort = var.relay_udp_node_port
      authUrl     = "http://stunner-auth.${local.stunner_namespace}:8088" # [S10]
    }
  })]

  set_sensitive = [
    { name = "secrets.turnSharedSecret", value = random_password.turn_shared_secret.result },
    { name = "secrets.voiceStudioAdminKey", value = random_password.voicestudio_admin_key.result },
  ]
}
