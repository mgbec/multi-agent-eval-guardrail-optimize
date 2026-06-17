# ============================================================================
# S3 Buckets for Agent Source Code (CDK Asset Equivalent)
# ============================================================================

# Orchestrator Agent Source Bucket
resource "aws_s3_bucket" "orchestrator_source" {
  bucket_prefix = "acma-orch-src-" # Shortened to fit 37 char limit
  force_destroy = true

  tags = {
    Name    = "${var.stack_name}-orchestrator-source"
    Purpose = "Store Orchestrator agent source code for CodeBuild"
  }
}

# Specialist Agent Source Bucket
resource "aws_s3_bucket" "specialist_source" {
  bucket_prefix = "acma-spec-src-" # Shortened to fit 37 char limit
  force_destroy = true

  tags = {
    Name    = "${var.stack_name}-specialist-source"
    Purpose = "Store Specialist agent source code for CodeBuild"
  }
}

# Block public access - Orchestrator
resource "aws_s3_bucket_public_access_block" "orchestrator_source" {
  bucket = aws_s3_bucket.orchestrator_source.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Block public access - Specialist
resource "aws_s3_bucket_public_access_block" "specialist_source" {
  bucket = aws_s3_bucket.specialist_source.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning - Orchestrator
resource "aws_s3_bucket_versioning" "orchestrator_source" {
  bucket = aws_s3_bucket.orchestrator_source.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enable versioning - Specialist
resource "aws_s3_bucket_versioning" "specialist_source" {
  bucket = aws_s3_bucket.specialist_source.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ============================================================================
# Archive and Upload Agent Source Code
# ============================================================================

# Archive agent-orchestrator-code/ directory
data "archive_file" "orchestrator_source" {
  type        = "zip"
  source_dir  = "${path.module}/agent-orchestrator-code"
  output_path = "${path.module}/.terraform/agent-orchestrator-code.zip"
}

# Archive agent-specialist-code/ directory
data "archive_file" "specialist_source" {
  type        = "zip"
  source_dir  = "${path.module}/agent-specialist-code"
  output_path = "${path.module}/.terraform/agent-specialist-code.zip"
}

# Upload Orchestrator source to S3
resource "aws_s3_object" "orchestrator_source" {
  bucket = aws_s3_bucket.orchestrator_source.id
  key    = "agent-orchestrator-code-${data.archive_file.orchestrator_source.output_md5}.zip"
  source = data.archive_file.orchestrator_source.output_path
  etag   = data.archive_file.orchestrator_source.output_md5

  tags = {
    Name  = "agent-orchestrator-source-code"
    Agent = "Orchestrator"
    MD5   = data.archive_file.orchestrator_source.output_md5
  }
}

# Upload Specialist source to S3
resource "aws_s3_object" "specialist_source" {
  bucket = aws_s3_bucket.specialist_source.id
  key    = "agent-specialist-code-${data.archive_file.specialist_source.output_md5}.zip"
  source = data.archive_file.specialist_source.output_path
  etag   = data.archive_file.specialist_source.output_md5

  tags = {
    Name  = "agent-specialist-source-code"
    Agent = "Specialist"
    MD5   = data.archive_file.specialist_source.output_md5
  }
}

# ============================================================================
# Fact Checker Agent Source
# ============================================================================

# Fact Checker Agent Source Bucket
resource "aws_s3_bucket" "factchecker_source" {
  bucket_prefix = "acma-fc-src-"
  force_destroy = true

  tags = {
    Name    = "${var.stack_name}-factchecker-source"
    Purpose = "Store Fact Checker agent source code for CodeBuild"
  }
}

# Block public access - Fact Checker
resource "aws_s3_bucket_public_access_block" "factchecker_source" {
  bucket = aws_s3_bucket.factchecker_source.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning - Fact Checker
resource "aws_s3_bucket_versioning" "factchecker_source" {
  bucket = aws_s3_bucket.factchecker_source.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Archive agent-factchecker-code/ directory
data "archive_file" "factchecker_source" {
  type        = "zip"
  source_dir  = "${path.module}/agent-factchecker-code"
  output_path = "${path.module}/.terraform/agent-factchecker-code.zip"
}

# Upload Fact Checker source to S3
resource "aws_s3_object" "factchecker_source" {
  bucket = aws_s3_bucket.factchecker_source.id
  key    = "agent-factchecker-code-${data.archive_file.factchecker_source.output_md5}.zip"
  source = data.archive_file.factchecker_source.output_path
  etag   = data.archive_file.factchecker_source.output_md5

  tags = {
    Name  = "agent-factchecker-source-code"
    Agent = "FactChecker"
    MD5   = data.archive_file.factchecker_source.output_md5
  }
}

# ============================================================================
# Critic Agent Source
# ============================================================================

resource "aws_s3_bucket" "critic_source" {
  bucket_prefix = "acma-crit-src-"
  force_destroy = true

  tags = {
    Name    = "${var.stack_name}-critic-source"
    Purpose = "Store Critic agent source code for CodeBuild"
  }
}

resource "aws_s3_bucket_public_access_block" "critic_source" {
  bucket = aws_s3_bucket.critic_source.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "critic_source" {
  bucket = aws_s3_bucket.critic_source.id

  versioning_configuration {
    status = "Enabled"
  }
}

data "archive_file" "critic_source" {
  type        = "zip"
  source_dir  = "${path.module}/agent-critic-code"
  output_path = "${path.module}/.terraform/agent-critic-code.zip"
}

resource "aws_s3_object" "critic_source" {
  bucket = aws_s3_bucket.critic_source.id
  key    = "agent-critic-code-${data.archive_file.critic_source.output_md5}.zip"
  source = data.archive_file.critic_source.output_path
  etag   = data.archive_file.critic_source.output_md5

  tags = {
    Name  = "agent-critic-source-code"
    Agent = "Critic"
    MD5   = data.archive_file.critic_source.output_md5
  }
}
