output "instance_id" {
  value       = aws_instance.this.id
  description = "EC2 instance ID."
}

output "private_ip" {
  value       = aws_instance.this.private_ip
  description = "Instance private IP within the VPC."
}

output "iam_role_arn" {
  value       = aws_iam_role.instance.arn
  description = "Instance IAM role ARN."
}

output "iam_role_name" {
  value       = aws_iam_role.instance.name
  description = "Instance IAM role name."
}

output "linux_user" {
  value       = var.linux_user
  description = "Linux user created on the instance for SSH-over-SSM."
}

output "owner_email" {
  value       = var.owner_email
  description = "Operator email tag value."
}
