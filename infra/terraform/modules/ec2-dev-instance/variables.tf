variable "owner_email" {
  description = "Operator email -- used for tagging, naming prefix, and ansible playbook extra-var substitution."
  type        = string
}

variable "linux_user" {
  description = "Linux account to create on the box. SSH-over-SSM connects as this user."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9_-]{0,30}$", var.linux_user))
    error_message = "linux_user must be a valid POSIX username (lowercase, starts with letter, <= 31 chars)."
  }
}

variable "key_name" {
  description = "Name of the AWS console-managed key pair to install in ~/.ssh/authorized_keys."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. Default is c8g.2xlarge (8 vCPU, 16 GiB Graviton4)."
  type        = string
}

variable "ami_owner" {
  description = "AWS account ID that owns the AMI (Canonical = 099720109477)."
  type        = string
}

variable "ami_name_filter" {
  description = "AMI name glob (e.g. ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*)."
  type        = string
}

variable "subnet_id" {
  description = "Subnet to place the instance in (output of the network module)."
  type        = string
}

variable "security_group_id" {
  description = "Security group to attach (output of the network module)."
  type        = string
}

variable "ebs_size_gb" {
  description = "Root EBS volume size in GiB."
  type        = number
}

variable "github_token_secret_name" {
  description = "AWS Secrets Manager secret name holding the operator's GitHub PAT."
  type        = string
}

variable "github_ssh_key_secret_name" {
  description = "AWS Secrets Manager secret name holding the operator's GitHub SSH private key."
  type        = string
}

variable "tags" {
  description = "Common tags. Caller must include Owner, Project, ManagedBy."
  type        = map(string)
}
