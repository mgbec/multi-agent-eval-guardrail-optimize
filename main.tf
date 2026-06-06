# ============================================================================
# Wait for IAM propagation before triggering builds
# ============================================================================

resource "time_sleep" "wait_for_iam" {
  depends_on = [
    aws_iam_role_policy.codebuild,
    aws_iam_role_policy.orchestrator_execution,
    aws_iam_role_policy.specialist_execution,
    aws_iam_role_policy.factchecker_execution
  ]

  create_duration = "30s"
}

# ============================================================================
# Trigger CodeBuild - Sequential Build Process
# Specialist builds first (independent), then Orchestrator (depends on Specialist)
# ============================================================================

# Trigger Specialist Build (Independent - Builds First)
resource "null_resource" "trigger_build_specialist" {
  triggers = {
    build_project   = aws_codebuild_project.specialist_image.id
    image_tag       = var.image_tag
    ecr_repository  = aws_ecr_repository.specialist.id
    source_code_md5 = data.archive_file.specialist_source.output_md5
  }

  provisioner "local-exec" {
    command     = "powershell -ExecutionPolicy Bypass -File ${path.module}/scripts/build-image.ps1 -ProjectName \"${aws_codebuild_project.specialist_image.name}\" -Region \"${data.aws_region.current.region}\" -RepoName \"${aws_ecr_repository.specialist.name}\" -ImageTag \"${var.image_tag}\" -RepoUrl \"${aws_ecr_repository.specialist.repository_url}\""
  }

  depends_on = [
    aws_codebuild_project.specialist_image,
    aws_ecr_repository.specialist,
    aws_iam_role_policy.codebuild,
    aws_s3_object.specialist_source,
    time_sleep.wait_for_iam
  ]
}

# Trigger Orchestrator Build (Depends on Specialist Build Completion)
resource "null_resource" "trigger_build_orchestrator" {
  triggers = {
    build_project   = aws_codebuild_project.orchestrator_image.id
    image_tag       = var.image_tag
    ecr_repository  = aws_ecr_repository.orchestrator.id
    source_code_md5 = data.archive_file.orchestrator_source.output_md5
    # Also rebuild if Specialist build changes
    specialist_build = null_resource.trigger_build_specialist.id
  }

  provisioner "local-exec" {
    command     = "powershell -ExecutionPolicy Bypass -File ${path.module}/scripts/build-image.ps1 -ProjectName \"${aws_codebuild_project.orchestrator_image.name}\" -Region \"${data.aws_region.current.region}\" -RepoName \"${aws_ecr_repository.orchestrator.name}\" -ImageTag \"${var.image_tag}\" -RepoUrl \"${aws_ecr_repository.orchestrator.repository_url}\""
  }

  depends_on = [
    aws_codebuild_project.orchestrator_image,
    aws_ecr_repository.orchestrator,
    aws_iam_role_policy.codebuild,
    aws_s3_object.orchestrator_source,
    null_resource.trigger_build_specialist, # CRITICAL: Wait for Specialist build
    time_sleep.wait_for_iam
  ]
}

# Trigger Fact Checker Build (Independent - Builds in parallel with Specialist)
resource "null_resource" "trigger_build_factchecker" {
  triggers = {
    build_project   = aws_codebuild_project.factchecker_image.id
    image_tag       = var.image_tag
    ecr_repository  = aws_ecr_repository.factchecker.id
    source_code_md5 = data.archive_file.factchecker_source.output_md5
  }

  provisioner "local-exec" {
    command     = "powershell -ExecutionPolicy Bypass -File ${path.module}/scripts/build-image.ps1 -ProjectName \"${aws_codebuild_project.factchecker_image.name}\" -Region \"${data.aws_region.current.region}\" -RepoName \"${aws_ecr_repository.factchecker.name}\" -ImageTag \"${var.image_tag}\" -RepoUrl \"${aws_ecr_repository.factchecker.repository_url}\""
  }

  depends_on = [
    aws_codebuild_project.factchecker_image,
    aws_ecr_repository.factchecker,
    aws_iam_role_policy.codebuild,
    aws_s3_object.factchecker_source,
    time_sleep.wait_for_iam
  ]
}
