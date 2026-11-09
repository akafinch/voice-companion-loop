# Provider floors — sources and as-of dates in README.md, "Sources".
terraform {
  # strcontains (main.tf) arrived in Terraform 1.5.0 [S16].
  required_version = ">= 1.5.0"

  required_providers {
    linode = {
      source  = "linode/linode"
      version = "~> 4.8" # v4.8.0 created the measured cluster; v4.9.0 (2026-10-30) adds only LKE Enterprise VXLAN [S1]
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3" # v3.3.0, 2026-09-02; v3 takes `kubernetes = {}` and `set_sensitive = [{}]` [S5][S6]
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9" # v3.9.1, 2026-09-11 [S7]
    }
  }
}

# Authenticates with LINODE_TOKEN from the environment; the token never enters the cluster [S2].
provider "linode" {}

# The cluster does not exist at first plan, so this configuration is unknown until apply.
# helm 3.3 plans helm_release with that unknown connection: `terraform plan` on an empty
# state did so on 2026-11-09.
# ASSUMPTION (unverified): LKE's kubeconfig carries a bearer token in users[0].user.token,
# which only the first apply settles, since the kubeconfig is unknown at plan.
provider "helm" {
  kubernetes = {
    host                   = local.kubeconfig.clusters[0].cluster.server
    cluster_ca_certificate = base64decode(local.kubeconfig.clusters[0].cluster["certificate-authority-data"])
    token                  = local.kubeconfig.users[0].user.token
  }
}
