#!/usr/bin/env bash
# After `terraform destroy`: checks that nothing this deployment created is still billing, and
# exits 1 if something is.
#
# A deleted LKE cluster can leave NodeBalancers and Block Storage volumes behind, and they bill.
# Terraform owns both NodeBalancers, and nothing in the deployment claims a volume, so neither should
# remain; this looks anyway. It matches by label, not tag: the cloud controller rewrites the tags of
# the NodeBalancers it adopts. linode-cli authenticates with LINODE_CLI_TOKEN or its own
# configuration (`linode-cli configure`), not with Terraform's LINODE_TOKEN.
set -euo pipefail

usage="usage: $0 [cluster-label] [cluster-id]
  cluster-label  Terraform's cluster_label (default: voice-companion)
  cluster-id     the cluster's id, from \`terraform -chdir=build/infra output cluster\` before
                 destroying; without it, every LKE node in the account counts as this cluster's"
case "${1:-}" in -h|--help) echo "$usage"; exit 0 ;; esac
command -v linode-cli >/dev/null || { echo "linode-cli is not installed: pip install linode-cli" >&2; exit 2; }

label=${1:-voice-companion}
id=${2:-[0-9]+}  # LKE names its node Linodes lke<cluster-id>-<pool-id>-<suffix>
billing=0

# matching <regex> <linode-cli command...>: the id and label of every item whose label matches.
matching() {
  local regex=$1
  shift
  linode-cli "$@" --text --no-headers --format id,label --all-rows |
    awk -F '\t' -v re="$regex" '$2 ~ re { print "  " $1 "  " $2 }'
}

# check <what> <regex> <linode-cli command...>
check() {
  local what=$1 found
  shift
  found=$(matching "$@")
  if [ -n "$found" ]; then
    echo "STILL BILLING: $what"
    echo "$found"
    billing=1
  else
    echo "gone: $what"
  fi
}

check "the LKE cluster $label" "^$label\$" lke clusters-list
check "the cluster's nodes" "^lke$id-" linodes list
check "the NodeBalancers $label-page and $label-relay" "^$label-(page|relay)\$" nodebalancers list
# The Block Storage CSI driver labels a claim's volume with its PersistentVolume's name, pvc-<uid>,
# after an optional prefix:
# https://github.com/linode/linode-blockstorage-csi-driver/blob/v1.1.5/internal/driver/controllerserver_helper.go
check "volumes made for Kubernetes claims" "pvc" volumes list

# Cloud Firewalls are free. The cloud-firewall-controller's lke-<cluster-id> outlives the cluster.
firewalls=$(matching "^(lke-$id|$label-edge)\$" firewalls list)
if [ -n "$firewalls" ]; then
  echo "left behind, free: firewalls"
  echo "$firewalls"
fi

if [ "$billing" = 1 ]; then
  echo "LKE deletes a cluster's nodes a few minutes after the cluster: if only nodes remain, check again."
fi
exit "$billing"
