# Singleton VPC + public subnet + IGW + zero-ingress / all-egress security group
# for the entire devbench-remote workload. Apply this once per account.

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

terraform {
  source = "${get_repo_root()}/infra/terraform/modules/network"
}

inputs = {
  vpc_cidr          = include.root.inputs.vpc_cidr
  subnet_cidr       = include.root.inputs.subnet_cidr
  availability_zone = "${include.root.inputs.region}a"
  tags = {
    Project   = "devbench-remote"
    ManagedBy = "terragrunt"
    Owner     = "shared"
  }
}
