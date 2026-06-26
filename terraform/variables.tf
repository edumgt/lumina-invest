variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-2"
}

variable "account_id" {
  description = "AWS account ID"
  type        = string
  default     = "086015456585"
}

variable "docdb_master_password" {
  description = "DocumentDB master password (provide via TF_VAR_docdb_master_password)"
  type        = string
  sensitive   = true
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL for deployment notifications"
  type        = string
  sensitive   = true
  default     = ""
}
