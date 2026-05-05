variable "bucket_name" {
  description = "S3 bucket name for terraform state. Must be globally unique."
  type        = string
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
}
