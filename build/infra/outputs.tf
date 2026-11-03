# Connection surfaces; values populate after `apply`.

output "url" {
  description = "Open this to talk to the companion (allow-listed addresses only)."
  value       = "https://${local.page_host}/"
}

output "relay" {
  description = "The TURN relay: UDP on every node's public address at this NodePort, and TLS on 443 at this hostname."
  value = {
    hostname      = local.relay_host
    tls_url       = "turns:${local.relay_host}:443?transport=tcp"
    udp_node_port = var.relay_udp_node_port
    auth_url      = "http://stunner-auth.${local.stunner_namespace}:8088"
  }
}

output "cluster" {
  description = "The LKE cluster."
  value = {
    id            = linode_lke_cluster.this.id
    api_endpoints = linode_lke_cluster.this.api_endpoints
    pool_ids      = [for p in linode_lke_cluster.this.pool : p.id]
  }
}

output "kubeconfig" {
  description = "Base64-encoded kubeconfig: terraform output -raw kubeconfig | base64 -d > kubeconfig.yaml"
  value       = linode_lke_cluster.this.kubeconfig
  sensitive   = true
}

output "nodebalancers" {
  description = "Terraform-owned NodeBalancers, adopted by the front's Services."
  value = {
    page  = { id = linode_nodebalancer.page.id, ipv4 = linode_nodebalancer.page.ipv4, hostname = local.page_host }
    relay = { id = linode_nodebalancer.relay.id, ipv4 = linode_nodebalancer.relay.ipv4, hostname = local.relay_host }
  }
}

output "firewalls" {
  description = "The edge firewall is Terraform's; the node firewall is the controller's and survives destroy (free)."
  value = {
    edge_id    = linode_firewall.edge.id
    node_label = "lke-${linode_lke_cluster.this.id}"
  }
}

output "releases" {
  description = "Helm releases, by namespace."
  value = {
    for r in [helm_release.gpu_plugin, helm_release.cloud_firewall, helm_release.stunner, helm_release.traefik, helm_release.companion] :
    r.name => { namespace = r.namespace, version = r.version }
  }
}
