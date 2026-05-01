terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70"
    }
  }
}

locals {
  name_prefix = replace(var.owner_email, "/[^a-z0-9-]/", "-")
}

data "aws_ami" "ubuntu_arm" {
  most_recent = true
  owners      = [var.ami_owner]

  filter {
    name   = "name"
    values = [var.ami_name_filter]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# IAM role for SSM agent + per-instance scoped permissions.
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name_prefix        = "${local.name_prefix}-devbench-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  secret_arn_prefix = "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret"
  github_token_arn  = "${local.secret_arn_prefix}:${var.github_token_secret_name}-*"
  github_ssh_arn    = "${local.secret_arn_prefix}:${var.github_ssh_key_secret_name}-*"
}

data "aws_iam_policy_document" "secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [local.github_token_arn, local.github_ssh_arn]
  }
}

resource "aws_iam_role_policy" "secrets" {
  name   = "github-secrets-read"
  role   = aws_iam_role.instance.name
  policy = data.aws_iam_policy_document.secrets.json
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = "${local.name_prefix}-devbench-"
  role        = aws_iam_role.instance.name
  tags        = var.tags
}

resource "aws_instance" "this" {
  ami                         = data.aws_ami.ubuntu_arm.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  key_name                    = var.key_name
  monitoring                  = true
  associate_public_ip_address = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.ebs_size_gb
    encrypted   = true
    tags        = merge(var.tags, { Name = "${local.name_prefix}-devbench-root" })
  }

  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    linux_user  = var.linux_user
    owner_email = var.owner_email
  })

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-devbench"
  })
}
