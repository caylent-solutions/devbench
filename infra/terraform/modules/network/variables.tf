variable "vpc_cidr" {
  description = "CIDR block for the devbench-remote VPC."
  type        = string
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet within the VPC."
  type        = string
}

variable "availability_zone" {
  description = "AZ to place the public subnet in."
  type        = string
}

variable "tags" {
  description = "Common resource tags. Caller must provide Project, ManagedBy, Owner."
  type        = map(string)
}
