output "bucket_name" {
  value       = aws_s3_bucket.state.id
  description = "Name of the state bucket."
}

output "bucket_arn" {
  value       = aws_s3_bucket.state.arn
  description = "ARN of the state bucket."
}
