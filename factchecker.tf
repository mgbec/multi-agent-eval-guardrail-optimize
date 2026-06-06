# ============================================================================
# Fact Checker Agent Runtime - Independent Agent
# ============================================================================

resource "aws_bedrockagentcore_agent_runtime" "factchecker" {
  agent_runtime_name = "${replace(var.stack_name, "-", "_")}_${var.factchecker_name}"
  description        = "Fact Checker agent runtime for ${var.stack_name}"
  role_arn           = aws_iam_role.factchecker_execution.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.factchecker.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = var.network_mode
  }

  environment_variables = {
    AWS_REGION         = data.aws_region.current.region
    AWS_DEFAULT_REGION = data.aws_region.current.region
    BEDROCK_MODEL_ID   = var.bedrock_model_id
  }

  tags = {
    Name        = "${var.stack_name}-factchecker-runtime"
    Environment = "production"
    Module      = "BedrockAgentCore"
    Agent       = "FactChecker"
  }

  depends_on = [
    null_resource.trigger_build_factchecker,
    aws_iam_role_policy.factchecker_execution,
    aws_iam_role_policy_attachment.factchecker_execution_managed
  ]
}
