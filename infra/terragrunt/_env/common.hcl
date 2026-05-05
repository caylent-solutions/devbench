# Shared inputs that apply to every leaf. All values are env-overridable so the
# repo holds team-wide DEFAULTS but never operator- or environment-specific
# pinned values. Operators may also pin values per-leaf via Terragrunt inputs.
#
# Override env vars (all optional; sensible defaults below):
#   DEVBENCH_VPC_CIDR        - IPv4 CIDR for the singleton VPC (default 10.42.0.0/16)
#   DEVBENCH_SUBNET_CIDR     - IPv4 CIDR for the public subnet (default 10.42.0.0/24)
#   DEVBENCH_AMI_OWNER       - AWS account that owns the AMI (default 099720109477 = Canonical)
#   DEVBENCH_AMI_NAME_FILTER - AMI name glob (default = Ubuntu 24.04 ARM64 LTS)
#   DEVBENCH_INSTANCE_TYPE   - EC2 instance type (default c8g.2xlarge = Graviton4 8 vCPU / 16 GiB)
#   DEVBENCH_EBS_SIZE_GB     - root EBS volume size (default 256)

locals {
  vpc_cidr        = get_env("DEVBENCH_VPC_CIDR", "10.42.0.0/16")
  subnet_cidr     = get_env("DEVBENCH_SUBNET_CIDR", "10.42.0.0/24")
  ami_owner       = get_env("DEVBENCH_AMI_OWNER", "099720109477")
  ami_name_filter = get_env("DEVBENCH_AMI_NAME_FILTER", "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*")

  default_instance_type = get_env("DEVBENCH_INSTANCE_TYPE", "c8g.2xlarge")
  default_ebs_size_gb   = tonumber(get_env("DEVBENCH_EBS_SIZE_GB", "256"))

  # Secret-name template; the leaf substitutes the operator's slug at apply time.
  github_token_secret_pattern   = "devbench-remote/%s/github-token"
  github_ssh_key_secret_pattern = "devbench-remote/%s/github-ssh-key"
}
