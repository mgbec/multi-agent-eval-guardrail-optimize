# ============================================================================
# Critic Agent Runtime - Evaluates quality of other agents' responses
# ============================================================================

resource "aws_bedrockagentcore_agent_runtime" "critic" {
  agent_runtime_name = "${replace(var.stack_name, "-", "_")}_${var.critic_name}"
  description        = "Critic agent runtime for ${var.stack_name}"
  role_arn           = aws_iam_role.critic_execution.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.critic.repository_url}:${var.image_tag}"
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
    Name        = "${var.stack_name}-critic-runtime"
    Environment = "production"
    Module      = "BedrockAgentCore"
    Agent       = "Critic"
  }

  depends_on = [
    null_resource.trigger_build_critic,
    aws_iam_role_policy.critic_execution,
    aws_iam_role_policy_attachment.critic_execution_managed
  ]
}
