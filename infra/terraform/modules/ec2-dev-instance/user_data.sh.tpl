#!/bin/bash
# Cloud-init bootstrap for a per-user devbench EC2.
# Minimal: create the linux user, install ansible/git/awscli, signal ready.
# The full ansible playbook (and Secrets Manager fetch for git auth) is pushed
# from the operator's machine via `make ec2-bootstrap` once SSM is online.
# Fail fast on any error.
set -euxo pipefail

LINUX_USER='${linux_user}'
OWNER_EMAIL='${owner_email}'

# 1. Create user (idempotent).
if ! id -u "$LINUX_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$LINUX_USER"
fi
install -d -o "$LINUX_USER" -g "$LINUX_USER" -m 0700 "/home/$LINUX_USER/.ssh"

# 2. Authorized keys: AWS injects the operator's key pair into /home/ubuntu/.ssh.
if [ -f /home/ubuntu/.ssh/authorized_keys ]; then
    install -o "$LINUX_USER" -g "$LINUX_USER" -m 0600 \
        /home/ubuntu/.ssh/authorized_keys "/home/$LINUX_USER/.ssh/authorized_keys"
fi

# 3. Passwordless sudo.
echo "$LINUX_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$LINUX_USER"
chmod 0440 "/etc/sudoers.d/90-$LINUX_USER"

# 4. Apt prerequisites for the SSM-pushed ansible playbook.
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends git ansible python3-pip unzip curl ca-certificates

# 5. AWS CLI v2 (apt's awscli is v1; we need v2 for the Secrets Manager calls).
if ! command -v aws >/dev/null 2>&1 || ! aws --version 2>&1 | grep -q 'aws-cli/2'; then
    TMPDIR=$(mktemp -d)
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "$TMPDIR/awscli.zip"
    unzip -q "$TMPDIR/awscli.zip" -d "$TMPDIR"
    "$TMPDIR/aws/install" --update
    rm -rf "$TMPDIR"
fi

# 6. Mark cloud-init ready for SSM playbook push.
echo "cloud-init ready for SSM bootstrap: user=$LINUX_USER owner=$OWNER_EMAIL ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > /var/log/devbench-cloudinit-ready.done
