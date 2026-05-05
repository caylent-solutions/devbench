# COPY-ME template for a new operator's per-user EC2.
#
# Two install locations are supported -- pick ONE:
#
# (A) Out-of-repo (RECOMMENDED -- lives in your home dir, never staged in any
#     devbench checkout, survives `git clean`):
#       mkdir -p ~/.devbench/instances/<your-slug>
#       cp infra/terragrunt/instances/_template/terragrunt.hcl \
#          ~/.devbench/instances/<your-slug>/
#       export DEVBENCH_INSTANCES_DIR=$HOME/.devbench/instances
#       export DEVBENCH_DEVBENCH_REPO=$(pwd)   # absolute path to this checkout
#
# (B) In-repo (gitignored under infra/terragrunt/instances/<your-slug>/):
#       cp -r infra/terragrunt/instances/_template \
#             infra/terragrunt/instances/<your-slug>
#
# Then in either case:
#   1. Add your AWS console key pair (Name = your email).
#   2. Edit owner_email, linux_user, key_name below. Optionally override
#      instance_type / ebs_size_gb.
#   3. From devbench repo root:
#        export DEVBENCH_OWNER_EMAIL=<your-email>
#        export DEVBENCH_LINUX_USER=<your-linux-username>
#        export DEVBENCH_KEY_NAME=<your-key-pair-name>
#        aws sso login --profile <your-profile>
#        make ec2-init && make ec2-apply
#
# Every input is overridable via Terragrunt inputs, env vars, or by editing
# this file directly. No operator-specific values live in the devbench repo.

# Locate the Terragrunt root (in-repo) regardless of where this leaf is stored.
# When stored OUT of the devbench checkout, set DEVBENCH_DEVBENCH_REPO to the
# repo's absolute path; the helper picks up the root from there.
locals {
  devbench_repo  = get_env("DEVBENCH_DEVBENCH_REPO", "")
  in_repo_root   = "${get_repo_root()}/infra/terragrunt/root.hcl"
  external_root  = "${local.devbench_repo}/infra/terragrunt/root.hcl"
  resolved_root  = local.devbench_repo == "" ? local.in_repo_root : local.external_root
}

include "root" {
  path   = local.resolved_root
  expose = true
}

dependency "network" {
  config_path = "${dirname(local.resolved_root)}/network"
  mock_outputs = {
    vpc_id            = "vpc-mock"
    subnet_id         = "subnet-mock"
    security_group_id = "sg-mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init"]
}

terraform {
  source = "${get_repo_root()}/infra/terraform/modules/ec2-dev-instance"
}

inputs = {
  # ---- per-operator: edit these three ----
  owner_email = get_env("DEVBENCH_OWNER_EMAIL", "operator@example.com")
  linux_user  = get_env("DEVBENCH_LINUX_USER", "operator")
  key_name    = get_env("DEVBENCH_KEY_NAME", "operator@example.com")

  # ---- optional overrides ----
  instance_type = get_env("DEVBENCH_INSTANCE_TYPE", include.root.inputs.default_instance_type)
  ebs_size_gb   = tonumber(get_env("DEVBENCH_EBS_SIZE_GB", tostring(include.root.inputs.default_ebs_size_gb)))

  # ---- inherited from network singleton ----
  subnet_id         = dependency.network.outputs.subnet_id
  security_group_id = dependency.network.outputs.security_group_id

  # ---- inherited from common.hcl (AMI lookup happens inside the module) ----
  ami_owner       = include.root.inputs.ami_owner
  ami_name_filter = include.root.inputs.ami_name_filter

  # ---- Secrets Manager names: scoped per operator email ----
  github_token_secret_name   = format(include.root.inputs.github_token_secret_pattern, get_env("DEVBENCH_OWNER_EMAIL", "operator@example.com"))
  github_ssh_key_secret_name = format(include.root.inputs.github_ssh_key_secret_pattern, get_env("DEVBENCH_OWNER_EMAIL", "operator@example.com"))

  tags = {
    Project   = "devbench-remote"
    ManagedBy = "terragrunt"
    Owner     = get_env("DEVBENCH_OWNER_EMAIL", "operator@example.com")
  }
}
