# Infrastructure

Devbench supports remote per-developer EC2 workstations. The full Terraform + Terragrunt + Ansible subtree lives under [`infra/`](../infra) inside the repo.

- **Operator runbook:** [`infra/docs/operator-runbook.md`](../infra/docs/operator-runbook.md)
- **Architecture:** [`infra/docs/architecture.md`](../infra/docs/architecture.md)
- **Quick reference:** [`infra/README.md`](../infra/README.md)

The Make targets (`ec2-secrets-sync`, `ec2-network-apply`, `ec2-init`, `ec2-plan`, `ec2-apply`, `ec2-bootstrap`, `ec2-ssh`, `ec2-list`, `ec2-start`, `ec2-stop`, `ec2-status`, `ec2-refresh`, `ec2-destroy`, `ec2-doctor`) are documented in the runbook and registered in the top-level [`Makefile`](../Makefile).

Provisioning is two-phased on purpose:

1. `ec2-apply` runs Terraform/Terragrunt -- instance, IAM (incl. scoped Secrets Manager read), networking, cloud-init. Cloud-init only does the user/apt minimum.
2. `ec2-bootstrap` then pushes the ansible playbook over SSH-over-SSM and runs it on the box. The `git-auth` role fetches the operator's GitHub PAT and SSH key from AWS Secrets Manager (uploaded once via `ec2-secrets-sync`) so the devbench repo stays private and no credentials transit on every refresh.

The remote workstation provisioning is independent of the orchestrate / report / watch pipeline; you can run the full devbench loop locally without any AWS infrastructure.
