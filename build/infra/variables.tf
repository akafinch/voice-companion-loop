# ─── Per-environment: the operator sets these (terraform.tfvars, see terraform.tfvars.example) ───

variable "region" {
  description = "One region offering both GPU classes as LKE node pools."
  type        = string
  default     = "us-sea" # both GPU classes verified against an Akamai account as of 2026-10-13; confirm in your own account
}

variable "cluster_label" {
  description = "Prefix for the cluster, NodeBalancer and firewall labels."
  type        = string
  default     = "voice-companion"
}

variable "k8s_version" {
  description = "LKE Kubernetes version, major.minor."
  type        = string
  default     = "1.36" # [S3]
}

variable "system_pool_type" {
  description = "CPU plan for the bot, the relay, the front and the operators."
  type        = string
  default     = "g6-standard-4" # Linode 8GB, 4 vCPU [S4]
}

variable "system_pool_count" {
  description = "System nodes; two let the bot reschedule when one node recycles."
  type        = number
  default     = 2
}

variable "speech_pool_type" {
  description = "RTX 4000 Ada plan for transcription, the only service on it."
  type        = string
  default     = "g2-gpu-rtx4000a1-s" # RTX4000 Ada x1 Small [S4]
}

variable "speech_pool_count" {
  description = "Transcription nodes. One suffices: the demo serves one call at a time."
  type        = number
  default     = 1
}

variable "language_pool_type" {
  description = "RTX PRO 6000 Blackwell plan id for the chat model and speech synthesis, which share its GPU. Absent from the public plan catalog [S4], so it has no default: read it from Cloud Manager."
  type        = string
}

variable "language_pool_count" {
  description = "Language-model nodes."
  type        = number
  default     = 1
}

variable "allowed_cidrs" {
  description = "Addresses admitted to the page and relay on 443 and to the relay's UDP NodePort (IPv4 and/or IPv6 CIDRs)."
  type        = list(string)

  validation {
    condition     = length(var.allowed_cidrs) > 0 && alltrue([for c in var.allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_cidrs needs at least one CIDR, such as 203.0.113.7/32."
  }
}

variable "relay_udp_node_port" {
  description = "The fixed NodePort of the relay's UDP listener."
  type        = number
  default     = 30478

  validation {
    condition     = var.relay_udp_node_port >= 30000 && var.relay_udp_node_port <= 32767
    error_message = "A NodePort lies in 30000-32767."
  }
}

variable "acme_email" {
  description = "Let's Encrypt registration email; Traefik requires one [S11]."
  type        = string
}

variable "bot_image" {
  description = "GHCR reference of the bot image (bot and pages), published by CI. Public: the chart takes no pull secret."
  type        = string
}

variable "speech_image" {
  description = "GHCR reference of the VoiceStudio + VoxCPM2 image, published by CI. Public: the chart takes no pull secret."
  type        = string
}

# ─── adpconf-authoritative: values come from adp.auto.tfvars.json — change adpconf.yaml, not these ───

variable "cluster_engine" {
  description = "adpconf resources.cluster.engine."
  type        = string

  validation {
    # Standard is LKE's default tier; setting `tier` needs api_version v4beta [S1], so it is omitted.
    condition     = var.cluster_engine == "lke-standard"
    error_message = "This module builds standard LKE only (adpconf compute: option C)."
  }
}

variable "chat_model" {
  description = "adpconf resources.chat-model.model."
  type        = string
}

variable "transcription_model" {
  description = "adpconf resources.transcription.model."
  type        = string
}

variable "vllm_image" {
  description = "adpconf libraries.vllm: the default CUDA 13.0 image of that release."
  type        = string
}

variable "stunner_version" {
  description = "adpconf resources.turn-relay.version."
  type        = string
}

variable "traefik_version" {
  description = "adpconf resources.https-front.version."
  type        = string
}

variable "gpu_plugin_version" {
  description = "adpconf resources.gpu-plugin.version."
  type        = string
}

variable "firewall_controller_version" {
  description = "adpconf resources.node-firewall.version."
  type        = string
}
