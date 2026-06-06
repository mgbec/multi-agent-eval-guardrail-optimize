# ============================================================================
# Orchestrator Agent Outputs
# ============================================================================

output "orchestrator_runtime_id" {
  description = "ID of orchestrator agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_id
}

output "orchestrator_runtime_arn" {
  description = "ARN of orchestrator agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
}

output "orchestrator_runtime_version" {
  description = "Version of orchestrator agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_version
}

output "orchestrator_ecr_repository_url" {
  description = "URL of the ECR repository for orchestrator agent"
  value       = aws_ecr_repository.orchestrator.repository_url
}

output "orchestrator_execution_role_arn" {
  description = "ARN of the orchestrator agent execution role"
  value       = aws_iam_role.orchestrator_execution.arn
}

# ============================================================================
# Specialist Agent Outputs
# ============================================================================

output "specialist_runtime_id" {
  description = "ID of specialist agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.specialist.agent_runtime_id
}

output "specialist_runtime_arn" {
  description = "ARN of specialist agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.specialist.agent_runtime_arn
}

output "specialist_runtime_version" {
  description = "Version of specialist agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.specialist.agent_runtime_version
}

output "specialist_ecr_repository_url" {
  description = "URL of the ECR repository for specialist agent"
  value       = aws_ecr_repository.specialist.repository_url
}

output "specialist_execution_role_arn" {
  description = "ARN of the specialist agent execution role"
  value       = aws_iam_role.specialist_execution.arn
}

# ============================================================================
# Build & Storage Outputs
# ============================================================================

output "orchestrator_codebuild_project_name" {
  description = "Name of the CodeBuild project for orchestrator agent"
  value       = aws_codebuild_project.orchestrator_image.name
}

output "specialist_codebuild_project_name" {
  description = "Name of the CodeBuild project for specialist agent"
  value       = aws_codebuild_project.specialist_image.name
}

output "orchestrator_source_bucket_name" {
  description = "S3 bucket containing orchestrator agent source code"
  value       = aws_s3_bucket.orchestrator_source.id
}

output "specialist_source_bucket_name" {
  description = "S3 bucket containing specialist agent source code"
  value       = aws_s3_bucket.specialist_source.id
}

output "orchestrator_source_code_md5" {
  description = "MD5 hash of orchestrator source code (triggers rebuild when changed)"
  value       = data.archive_file.orchestrator_source.output_md5
}

output "specialist_source_code_md5" {
  description = "MD5 hash of specialist source code (triggers rebuild when changed)"
  value       = data.archive_file.specialist_source.output_md5
}

# ============================================================================
# Testing Information
# ============================================================================

output "test_orchestrator_command" {
  description = "AWS CLI command to test orchestrator agent"
  value       = "aws bedrock-agentcore invoke-agent-runtime --agent-runtime-id ${aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_id} --qualifier DEFAULT --payload '{\"prompt\": \"Hello, how are you?\"}' --region ${data.aws_region.current.region} response.json"
}

output "test_specialist_command" {
  description = "AWS CLI command to test specialist agent"
  value       = "aws bedrock-agentcore invoke-agent-runtime --agent-runtime-id ${aws_bedrockagentcore_agent_runtime.specialist.agent_runtime_id} --qualifier DEFAULT --payload '{\"prompt\": \"Explain cloud computing\"}' --region ${data.aws_region.current.region} response.json"
}

output "test_script_command" {
  description = "Command to test multi-agent communication"
  value       = "python test_multi_agent.py ${aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn}"
}

# ============================================================================
# Fact Checker Agent Outputs
# ============================================================================

output "factchecker_runtime_id" {
  description = "ID of fact checker agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.factchecker.agent_runtime_id
}

output "factchecker_runtime_arn" {
  description = "ARN of fact checker agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.factchecker.agent_runtime_arn
}

output "factchecker_runtime_version" {
  description = "Version of fact checker agent runtime"
  value       = aws_bedrockagentcore_agent_runtime.factchecker.agent_runtime_version
}

output "factchecker_ecr_repository_url" {
  description = "URL of the ECR repository for fact checker agent"
  value       = aws_ecr_repository.factchecker.repository_url
}

output "factchecker_execution_role_arn" {
  description = "ARN of the fact checker agent execution role"
  value       = aws_iam_role.factchecker_execution.arn
}

output "factchecker_codebuild_project_name" {
  description = "Name of the CodeBuild project for fact checker agent"
  value       = aws_codebuild_project.factchecker_image.name
}

output "factchecker_source_bucket_name" {
  description = "S3 bucket containing fact checker agent source code"
  value       = aws_s3_bucket.factchecker_source.id
}

output "factchecker_source_code_md5" {
  description = "MD5 hash of fact checker source code (triggers rebuild when changed)"
  value       = data.archive_file.factchecker_source.output_md5
}
