# Account-wide values. All required-from-env so the repo holds no operator- or
# account-specific data. Make targets and the operator runbook spell out which
# env vars must be set before any `make ec2-*` invocation.
#
#   AWS_ACCOUNT_ID         -- AWS account id (required)
#   AWS_REGION             -- AWS region    (required)
#   DEVBENCH_OWNER_EMAIL   -- operator email used as state-key prefix + Owner tag (required)

locals {
  account_id          = get_env("AWS_ACCOUNT_ID")
  default_region      = get_env("AWS_REGION")
  default_owner_email = get_env("DEVBENCH_OWNER_EMAIL")
}
