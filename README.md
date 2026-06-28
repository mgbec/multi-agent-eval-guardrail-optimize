# Multi-Agent Runtime on Amazon Bedrock AgentCore (Terraform)

This Terraform module deploys a multi-agent system using Amazon Bedrock AgentCore Runtime with enhanced observability.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [What's Included](#whats-included)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Deployment Process](#deployment-process)
- [Authentication Model](#authentication-model)
- [Testing](#testing)
- [Agent Capabilities](#agent-capabilities)
- [Customization](#customization)
- [File Structure](#file-structure)
- [Monitoring and Observability](#monitoring-and-observability)
- [Security](#security)
- [Pricing](#pricing)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)
- [Advanced Topics](#advanced-topics)
- [Next Steps](#next-steps)
- [Resources](#resources)
- [Contributing](#contributing)
- [License](#license)

## Overview

This pattern demonstrates deploying a multi-agent system with four coordinating agents. The Orchestrator routes requests to the Specialist (detailed analysis), the Fact Checker (claim verification), and the Critic (quality evaluation with feedback loops), enabling modular and scalable agent architectures with full distributed tracing.

**Key Features:**
- Four-agent architecture with multi-hop interagent communication and quality feedback loops
- ADOT (AWS Distro for OpenTelemetry) instrumentation for full distributed tracing
- Web search capability via Tavily API (Specialist and Fact Checker)
- Critic agent providing live LLM-as-a-judge quality scoring
- Automated Docker image building via CodeBuild
- S3-based source code management with change detection
- IAM-based security with least-privilege access
- Windows (PowerShell) and Linux/macOS compatible deployment

This makes it ideal for:
- Building complex multi-agent workflows
- Implementing agent specialization patterns
- Creating scalable agent orchestration systems
- Learning agent to agent communication observability

## Architecture

![Multi-Agent Architecture](architecture.png)

### System Components

**Orchestrator Agent**
- Receives initial user requests
- Routes to appropriate specialist agent(s) based on query type
- Contains tools: `call_specialist_agent` and `call_factchecker_agent`
- Has IAM permissions to invoke both downstream agents
- Can invoke multiple agents in a single request for complex queries

**Specialist Agent**
- Independent agent for detailed analysis and research
- Provides comprehensive explanations and data processing
- Invoked by Orchestrator for complex analytical tasks

**Fact Checker Agent**
- Independent agent for claim verification
- Returns structured verdicts (TRUE/FALSE/PARTIALLY TRUE) with confidence levels
- Invoked by Orchestrator when factual accuracy needs verification

**Critic Agent**
- Independent agent for quality evaluation
- Scores responses (1-10) and provides actionable feedback
- Enables quality feedback loops: Orchestrator can retry Specialist with Critic's suggestions
- Creates multi-hop trace patterns (Specialist → Critic → retry Specialist)

### Inter-Agent Communication, but not formal A2A Protocol

> **Note:** This project uses direct `InvokeAgentRuntime` API calls between agents — not the formal [A2A protocol](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-a2a.html) (Google's Agent-to-Agent spec with JSON-RPC, agent cards, and OAuth). See below for the distinction.

**How it works here:** The Orchestrator's tool functions make boto3 `invoke_agent_runtime()` calls to reach the Specialist and Fact Checker. This is simple, direct, and secured via IAM — but it's not the open A2A protocol standard.

**Formal A2A protocol (not used here):** Requires JSON-RPC on port 9000, agent card discovery, OAuth 2.0/SigV4 auth, and the full task lifecycle. This is designed for cross-organization interoperability where agents from different vendors need a standard contract.

**Why I chose the direct approach:**
- Simpler implementation (one boto3 call vs. full protocol stack) and issues deploying agents that worked with official A2A
- Native IAM security without additional OAuth configuration
- Built-in observability via CloudWatch and ADOT
- Lower latency (no extra protocol negotiation hop)
- Appropriate for internal multi-agent systems within the same AWS account

**When you'd want the formal A2A protocol instead:**
- Agents communicate across organizations or trust boundaries
- Dynamic agent discovery is needed (vs. hardcoded ARNs)
- Interoperability with non-AWS agents implementing the same spec
- Building a marketplace where agents from different vendors interact

### Session Propagation (Trace Coherence)

A critical aspect of multi-agent observability is **session propagation** — ensuring all agents in a single user request share the same session ID so traces, evaluations, and observability tools can see the full picture.

**The problem without session propagation:**
```
User → Orchestrator (session: abc-123)
         → Specialist (session: xyz-789)     ← Different session!
         → Fact Checker (session: def-456)   ← Different session!
         → Critic (session: ghi-012)         ← Different session!
```
Each agent gets its own random session ID. Evaluators like `GoalSuccessRate` can't reconstruct the full conversation because they only see one agent's session at a time. Observability dashboards show 4 separate sessions instead of 1 coordinated request.

**The solution — propagate the session ID:**
```
User → Orchestrator (session: abc-123)
         → Specialist (session: abc-123)     ← Same session!
         → Fact Checker (session: abc-123)   ← Same session!
         → Critic (session: abc-123)         ← Same session!
```

**How it works in this project:**
1. The caller passes `runtimeSessionId` when invoking the Orchestrator
2. The Orchestrator's entrypoint captures it via `context.session_id`
3. When the Orchestrator calls downstream agents, it passes that same session ID via `runtimeSessionId`
4. All spans across all agents share the same `session.id` attribute

This enables:
- AgentCore evaluators to score the full multi-agent conversation as one session
- The GenAI Observability dashboard to show the complete request flow
- The optimization service to analyze full multi-hop traces

## What's Included

This Terraform configuration creates:

- **4 S3 Buckets**: Source code storage for all agents with versioning
- **4 ECR Repositories**: Container registries for ARM64 Docker images
- **4 CodeBuild Projects**: Automated image building and pushing
- **5 IAM Roles**: 
  - Orchestrator execution role (with A2A permissions to invoke all downstream agents)
  - Specialist execution role (standard permissions)
  - Fact Checker execution role (standard permissions)
  - Critic execution role (standard permissions)
  - CodeBuild service role
- **4 Agent Runtimes**: 
  - Orchestrator with `SPECIALIST_ARN`, `FACTCHECKER_ARN`, and `CRITIC_ARN` environment variables
  - Specialist (independent runtime with web search)
  - Fact Checker (independent runtime with web search)
  - Critic (independent runtime)
- **ADOT Observability**: OpenTelemetry auto-instrumentation with Strands GenAI spans
- **Build Automation**: Automatic rebuild on code changes (MD5-based detection)
- **Supporting Resources**: S3 lifecycle policies, ECR lifecycle policies, IAM policies

**Total:** ~60 AWS resources deployed and managed by Terraform

## Prerequisites

### Required Tools

1. **Terraform** (>= 1.6)
   - **Recommended**: [tfenv](https://github.com/tfutils/tfenv) for version management
   - **Or download directly**: [terraform.io/downloads](https://www.terraform.io/downloads)
   
   **Note**: `brew install terraform` provides v1.5.7 (deprecated). Use tfenv or direct download for >= 1.6.

2. **AWS CLI** (configured with credentials)
   ```bash
   aws configure
   ```

3. **Python 3.11+** (for testing scripts)
   ```bash
   python --version  # Verify Python 3.11 or later
   pip install boto3
   ```

4. **Docker** (for local testing, optional)

5. **Tavily API Key** (optional, enables web search for Specialist and Fact Checker agents)
   - Get a free key at [app.tavily.com](https://app.tavily.com) (1,000 searches/month, no credit card)
   - Set in `terraform.tfvars` as `tavily_api_key`

### AWS Account Requirements

- AWS Account with appropriate permissions
- Access to Amazon Bedrock AgentCore service
- Permissions to create:
  - S3 buckets
  - ECR repositories
  - CodeBuild projects
  - IAM roles and policies
  - AgentCore Runtime resources

## Quick Start

### 1. Configure Variables

Copy the example variables file and customize:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your preferred values:
- `orchestrator_name`: Name for the orchestrator agent (default: "OrchestratorAgent")
- `specialist_name`: Name for the specialist agent (default: "SpecialistAgent")
- `factchecker_name`: Name for the fact checker agent (default: "FactCheckerAgent")
- `stack_name`: Stack identifier (default: "agentcore-multi-agent")
- `aws_region`: AWS region for deployment
- `network_mode`: PUBLIC or PRIVATE networking
- `bedrock_model_id`: Bedrock model inference profile ID
-  Tavily API key if used

### 2. Initialize Terraform

See [State Management Options](../README.md#state-management-options) in the main README for detailed guidance on local vs. remote state.

**Quick start with local state:**
```bash
terraform init
```

**For team collaboration, use remote state** - see the [main README](../README.md#state-management-options) for setup instructions.

### 3. Deploy

**Method 1: Using Deploy Script (Recommended)**

```bash
chmod +x deploy.sh
./deploy.sh
```

The script validates configuration, shows the plan, and deploys all resources.

**Method 2: Direct Terraform Commands**

```bash
terraform plan
terraform apply
```

**Note**: Deployment includes creating infrastructure, building Docker images sequentially (Agent2 first, then Agent1), and establishing A2A communication. Total deployment time: **~5-10 minutes**

### 4. Verify Deployment

```bash
# View all outputs
terraform output

# Get Agent ARNs
terraform output orchestrator_runtime_arn
terraform output specialist_runtime_arn
```

## Deployment Process

### Sequential Build Process

The deployment follows a strict sequence to ensure proper dependencies:

```
1. S3 Buckets Creation (orchestrator, specialist, factchecker)
2. ECR Repositories Creation (orchestrator, specialist, factchecker)
3. IAM Roles Creation (with A2A permissions for orchestrator)
4. CodeBuild Projects Creation (all three agents)
5. Specialist & Fact Checker Docker Builds (independent, can run in parallel)
6. Orchestrator Docker Build → Orchestrator Runtime Creation (depends on both)
```

**Critical Dependencies:**
- Orchestrator runtime depends on Specialist, Fact Checker, and Critic being created first
- Orchestrator receives `SPECIALIST_ARN`, `FACTCHECKER_ARN`, and `CRITIC_ARN` as environment variables

### Build Triggers

The infrastructure automatically triggers Docker image builds:
- When source code changes (MD5 hash detection)
- When infrastructure changes require rebuild
- Specialist and Fact Checker build independently; Orchestrator builds last

## Authentication Model

This pattern uses **IAM-based authentication with workload identity tokens**:

- **Service Principal**: Agents assume IAM roles via `bedrock-agentcore.amazonaws.com`
- **Workload Identity**: Agents obtain access tokens for secure operations
- **A2A Authorization**: Orchestrator has `InvokeAgentRuntime` permission scoped to Specialist and Fact Checker ARNs only
- **API Access**: Direct AWS API invocation using IAM credentials

**Note**: This is a backend infrastructure pattern with no user authentication layer. For user-facing applications, you would add Cognito or API Gateway authorizers separately.

## Testing

### How It Works

You send a question to the **Orchestrator** agent. Based on what you ask, it decides whether to:
- Answer directly (simple greetings)
- Route to the **Specialist** (for detailed analysis)
- Route to the **Fact Checker** (for verifying claims)
- Route to **both** (for complex questions that need analysis AND verification)

You only need the Orchestrator's ARN — it handles all the routing internally.

### Prerequisites for Testing

Install boto3 (the AWS SDK for Python):

```bash
pip install boto3
```

Ensure your AWS CLI is configured with credentials that have `bedrock-agentcore:InvokeAgentRuntime` permission.

### Quick Test — Ask Your Own Question

Use the included `ask.py` script:

```powershell
# Simple greeting (Orchestrator answers directly)
python ask.py "Hello, who are you?"

# Analysis question (routes to Specialist)
python ask.py "Explain how microservices differ from monolithic architectures"

# Fact-check question (routes to Fact Checker)
python ask.py "Is it true that Python is faster than C++?"

# Complex question (routes to BOTH Specialist and Fact Checker)
python ask.py "Explain serverless computing and verify that Lambda has a 15-minute timeout"
```

You can also pass the ARN directly if you're not in the Terraform directory:
```powershell
python ask.py "Your question" "arn:aws:bedrock-agentcore:us-east-1:123456789:runtime/your-agent-id"
```

### Running the Test Suite

The included test scripts run scenarios automatically:

```powershell
# Full multi-agent test (6 scenarios including Critic)
$ARN = terraform output -raw orchestrator_runtime_arn
python test_multi_agent.py $ARN

# Critic feedback loop tests (5 scenarios)
python test_critic_loop.py              # Quick test (first 2)
python test_critic_loop.py --all        # All 5 scenarios
python test_critic_loop.py --scenario 4 # Specific scenario
```

### Observing the Results

After invoking the agents, you can see what happened:

**1. View agent responses** (immediate — in your terminal output)

The response JSON shows which agent processed your request:
```json
{
  "status": "success",
  "agent": "orchestrator",
  "response": "Based on my analysis and the specialist's input..."
}
```

**2. View A2A application logs** (CloudWatch → Log groups)

Go to CloudWatch Logs and open the Orchestrator's log group:
```
/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_OrchestratorAgent-<id>-DEFAULT
```

Filter for A2A activity:
```
A2A_CALL
```

You'll see entries like:
```
A2A_CALL_START | agent=specialist | query_length=85
A2A_CALL_END | agent=specialist | latency_ms=12340 | response_length=1523
A2A_CALL_START | agent=factchecker | query_length=62
A2A_CALL_END | agent=factchecker | latency_ms=8210 | response_length=312
```

**3. View distributed traces** (CloudWatch → GenAI Observability)

Go to: **CloudWatch** → **Application Signals** → **Generative AI**

This shows a visual dashboard with:
- Each agent as a node
- Invocation counts and latency between agents
- Token usage per call
- Error rates

**4. Query trace spans directly** (CloudWatch → Logs Insights)

Select log group `aws/spans` and run:
```
fields @timestamp, name, durationNano / 1000000 as ms
| filter name like /call_specialist|call_factchecker|InvokeAgentRuntime|invoke_agent/
| sort @timestamp desc
| limit 20
```

This shows the full trace hierarchy — Orchestrator calling Specialist and/or Fact Checker, with exact timing for each hop.

## Agent Capabilities

### Orchestrator Agent

**Tools:**
- `call_specialist_agent`: Invokes Specialist for detailed analysis
  - Parameters: `query` (string)
  - Returns: Detailed analysis from Specialist
- `call_factchecker_agent`: Invokes Fact Checker for claim verification
  - Parameters: `claim` (string)
  - Returns: Verdict with confidence level
- `call_critic_agent`: Invokes Critic to evaluate a response's quality
  - Parameters: `content_to_evaluate` (string)
  - Returns: Score (1-10), verdict, strengths, weaknesses, improvement suggestion

**Use Cases:**
- Complex workflow orchestration across multiple agents
- Multi-step processing (analyze → evaluate → retry if needed)
- Quality feedback loops for important queries
- Intelligent routing based on query type

### Specialist Agent

**Tools:**
- `web_search`: Searches the web via Tavily API for current information
  - Parameters: `query` (string), `max_results` (int, default 5)
  - Returns: Search results with answer, titles, URLs, and content

**Capabilities:**
- Real-time web research for current information
- Domain-specific data analysis
- Detailed information processing
- Expert-level responses with source citations

**Use Cases:**
- Researching unfamiliar topics (e.g., "What is OpenClaw?")
- Getting current information beyond LLM training data
- Comprehensive explanations with sources

### Fact Checker Agent

**Tools:**
- `web_search`: Searches the web to find evidence for or against claims
  - Parameters: `query` (string), `max_results` (int, default 5)
  - Returns: Search results for verification

**Capabilities:**
- Claim evaluation with web-sourced evidence
- Structured verdict responses (TRUE/FALSE/PARTIALLY TRUE/UNVERIFIABLE)
- Confidence assessment (HIGH/MEDIUM/LOW)
- Source-backed reasoning

**Use Cases:**
- Verifying factual claims against current sources
- Assessing accuracy of statements
- Providing evidence-based verdicts with citations

### Critic Agent

**Capabilities:**
- Quality scoring (1-10) of other agents' responses
- Structured feedback (EXCELLENT/GOOD/NEEDS_IMPROVEMENT/POOR)
- Actionable improvement suggestions
- Strengths and weaknesses identification

**Use Cases:**
- Live LLM-as-a-judge quality evaluation
- Quality feedback loops (Specialist → Critic → improved Specialist response)
- Response quality monitoring
- Comparing output quality across model or prompt changes

## Customization

### Modify Agent Code

Agent source code lives in three directories:

```
agent-orchestrator-code/agent.py   # Routing logic, A2A tool definitions
agent-specialist-code/agent.py     # Specialist analysis logic
agent-factchecker-code/agent.py    # Claim verification logic
```

Each agent uses the Strands SDK with a `BedrockModel`. To customize:

1. Edit the agent's `agent.py` (update the system prompt, add tools, change logic)
2. Add any new Python dependencies to the agent's `requirements.txt`
3. Run `terraform apply` — Terraform detects the source code change via MD5 hash and triggers a rebuild automatically

The model is configured via the `bedrock_model_id` variable in `terraform.tfvars` and passed to all agents as the `BEDROCK_MODEL_ID` environment variable.

### Add More Agents

To add a new agent (e.g., Coordinator):
1. Create `coordinator-code/` directory with implementation
2. Add `coordinator.tf` for the runtime resource
3. Update `s3.tf`, `ecr.tf`, `iam.tf`, `codebuild.tf`
4. Create `buildspec-coordinator.yml`
5. Update `main.tf` for build sequence
6. Update `outputs.tf` and `variables.tf`

### Modify Network Configuration

Change from PUBLIC to PRIVATE networking:

```hcl
# terraform.tfvars
network_mode = "PRIVATE"
```

Requires VPC configuration (not included in this module).

## File Structure

```
multi-agent-runtime/
├── agent-orchestrator-code/           # Orchestrator agent source code
│   ├── agent.py                 # Main agent implementation (A2A routing)
│   ├── Dockerfile               # Container definition with ADOT
│   └── requirements.txt         # Python dependencies
├── agent-specialist-code/             # Specialist agent source code
│   ├── agent.py                 # Main agent implementation
│   ├── Dockerfile               # Container definition with ADOT
│   └── requirements.txt         # Python dependencies
├── agent-factchecker-code/            # Fact Checker agent source code
│   ├── agent.py                 # Main agent implementation
│   ├── Dockerfile               # Container definition with ADOT
│   └── requirements.txt         # Python dependencies
├── agent-critic-code/                 # Critic agent source code
│   ├── agent.py                 # Main agent implementation
│   ├── Dockerfile               # Container definition with ADOT
│   └── requirements.txt         # Python dependencies
├── scripts/
│   ├── build-image.ps1          # PowerShell build script (Windows)
│   └── build-image.sh           # Bash build script (Linux/macOS)
├── ask.py                       # Single-question test script
├── test_multi_agent.py          # Full test suite (6 scenarios)
├── test_critic_loop.py          # Critic feedback loop test suite (5 scenarios)
├── monitor_a2a.py               # A2A security monitoring script
├── check_a2a.py                 # A2A call log inspector
├── eval_goal_attainment.py      # Goal attainment evaluation (AgentCore built-in evaluators)
├── optimize_prompts.py          # AgentCore Optimization (prompt recommendations)
├── orchestrator.tf              # Orchestrator runtime configuration
├── specialist.tf                # Specialist runtime configuration
├── factchecker.tf               # Fact Checker runtime configuration
├── critic.tf                    # Critic runtime configuration
├── main.tf                      # Main Terraform configuration & build triggers
├── variables.tf                 # Input variables
├── outputs.tf                   # Output definitions
├── iam.tf                       # IAM roles and policies
├── s3.tf                        # S3 buckets for source code
├── ecr.tf                       # ECR repositories
├── codebuild.tf                 # CodeBuild projects
├── guardrail.tf                 # Bedrock Guardrail (content + prompt attack filtering)
├── secrets.tf                   # Secrets Manager (Tavily API key)
├── versions.tf                  # Terraform and provider versions
├── buildspec-orchestrator.yml   # Orchestrator build specification
├── buildspec-specialist.yml     # Specialist build specification
├── buildspec-factchecker.yml    # Fact Checker build specification
├── buildspec-critic.yml         # Critic build specification
├── terraform.tfvars.example     # Example variable values
├── backend.tf.example           # Example backend configuration
├── deploy.sh                    # Deployment automation script
├── destroy.sh                   # Cleanup automation script
└── README.md                    # This file
```

## Monitoring and Observability

This project includes full distributed tracing via ADOT (AWS Distro for OpenTelemetry).

### Distributed Tracing (ADOT)

All agents are auto-instrumented with OpenTelemetry. Traces flow to the `aws/spans` CloudWatch log group and include:

- **Agent spans**: Full invocation lifecycle per agent
- **LLM call spans**: Model ID, token usage (input/output), time-to-first-token
- **Tool use spans**: Which tools were called, duration, success/failure
- **Agent to agent spans**: Cross-agent invocations with target ARN, latency, HTTP status
- **Session correlation**: All spans in a single request share a trace ID

**View traces in CloudWatch Logs Insights** (log group: `aws/spans`):
```
fields @timestamp, name, attributes.`gen_ai.tool.name`, durationNano / 1000000 as duration_ms
| filter name like /call_specialist|call_factchecker|InvokeAgentRuntime/
| sort @timestamp desc
| limit 50
```

**View in GenAI Observability dashboard:**
CloudWatch → Application Signals → Generative AI

### Application Logs

The Orchestrator emits structured A2A logs to its CloudWatch log group:
```
fields @timestamp, @message
| filter @message like /A2A_CALL/
| sort @timestamp desc
```

### CloudWatch Logs

```bash
# Orchestrator logs
aws logs tail /aws/bedrock-agentcore/runtimes/agentcore_multi_agent_OrchestratorAgent-<id>-DEFAULT --follow

# Specialist logs
aws logs tail /aws/bedrock-agentcore/runtimes/agentcore_multi_agent_SpecialistAgent-<id>-DEFAULT --follow

# Fact Checker logs
aws logs tail /aws/bedrock-agentcore/runtimes/agentcore_multi_agent_FactCheckerAgent-<id>-DEFAULT --follow
```

### AWS Console

Monitor in AWS Console:
- **Bedrock AgentCore**: [Console Link](https://console.aws.amazon.com/bedrock-agentcore/agents)
- **CloudWatch GenAI Observability**: Distributed trace visualization
- **ECR Repositories**: View Docker images
- **CodeBuild**: Monitor build status

## Evaluations

This project includes offline evaluation using AgentCore's built-in evaluators to assess agent quality after invocations complete.

### Running Evaluations

```powershell
# Evaluate the most recent session
python eval_goal_attainment.py

# Invoke, wait for traces, then evaluate in one step
python eval_goal_attainment.py --invoke "Explain serverless computing and verify Lambda has a 15-min timeout"

# Evaluate a specific session by ID
python eval_goal_attainment.py --session-id "your-session-id"

# Evaluate the last 5 sessions
python eval_goal_attainment.py --all-recent

# Look back further in time (default is 2 hours)
python eval_goal_attainment.py --hours 12
python eval_goal_attainment.py --days 7 --all-recent
```

### Built-in Evaluators Used

| Evaluator | What it measures | Level |
|-----------|-----------------|-------|
| `Builtin.Helpfulness` | Was the response useful to the user? | Trace |
| `Builtin.GoalSuccessRate` | Did the agent achieve the user's stated goal? | Trace |
| `Builtin.ToolSelectionAccuracy` | Were the right tools (agents) selected? | Tool call |

### How It Works

1. The script collects span logs from CloudWatch (agent runtime logs + `aws/spans`)
2. Passes them to AgentCore's `Evaluate` API with each built-in evaluator
3. Each evaluator uses an LLM-as-a-judge to score the session
4. Results include a numeric score, label, and explanation

### Evaluations vs. Live Critic

| | Live Critic Agent | Offline Evaluations |
|---|---|---|
| **When** | During the request | After the request completes |
| **Purpose** | Quality gate (can trigger retries) | Assessment and monitoring |
| **Can change outcome** | Yes (retry with feedback) | No (read-only) |
| **Cost impact** | Adds latency + tokens to every request | Runs independently, no user impact |
| **Use case** | Important questions needing quality assurance | Regression testing, trend monitoring, A/B comparison |

### Prerequisites for Evaluations

- CloudWatch Transaction Search must be enabled (one-time account setup)
- Agent must have been invoked with traces recorded
- Spans take ~30 seconds to propagate to CloudWatch after invocation

### Known Limitation: Multi-Agent Evaluation Coherence

> **Important:** AgentCore's built-in evaluators have a known limitation with multi-agent architectures. When the Orchestrator calls downstream agents (Specialist, Fact Checker, Critic), each agent runs in a separate AgentCore Runtime with its own session ID. The Evaluate API requires all spans to belong to a single session, which conflicts with multi-runtime architectures.
>
> **Impact:** Evaluators like `GoalSuccessRate` and `ToolSelectionAccuracy` may return "Session span data is incomplete" errors because they can't see the downstream agent's response data within the Orchestrator's session.
>
> **Workaround in this project:** The `eval_goal_attainment.py` script collects spans by trace ID (shared across all agents via OTEL), unifies them under one session ID, and includes log events from all agent runtime log groups. This enables `Builtin.Helpfulness` to work on most traces but other evaluators may still report incomplete data.
>
> **Root cause:** AgentCore conflates session routing (sticky microVM assignment) with session observability (trace grouping). Passing `runtimeSessionId` to downstream agents creates routing deadlocks, while not passing it creates evaluation gaps. This is a platform limitation, not an implementation error.

## Optimization

> **Current Status: Blocked by Evaluation Limitation**
>
> The Optimization (Recommendations) API requires evaluating traces server-side before generating prompt improvements. Due to the multi-agent session coherence issue described above, the server-side evaluator fails on most sessions with "sessions could not be evaluated" errors.
>
> **What works:**
> - `optimize_prompts.py` successfully submits recommendation requests
> - The API finds traces and attempts evaluation
> - Simple single-agent sessions (no A2A hops) can be evaluated and optimized
>
> **What doesn't work:**
> - Multi-agent sessions (Orchestrator → Specialist/Fact Checker/Critic) fail server-side evaluation
> - The Recommendations API uses `cloudwatchLogs` for server-side span discovery, which cannot be overridden with our client-side trace-ID-based collection workaround
> - Unlike on-demand evaluations (where we collect and unify spans ourselves), the optimization service does its own span discovery and hits the same session boundary problem
>
> **Workaround:** Run optimizations with `--days 1` after generating several simple queries (that don't trigger A2A calls). The 8-10 sessions that CAN be evaluated may be enough for the service to generate a recommendation. Alternatively, wait for AWS to add trace-ID-based session linking in the optimization service.

AgentCore Optimization uses AI to improve your agent's prompts and tool descriptions based on real trace data.

### How It Works

1. Your agents run and generate traces in CloudWatch
2. The Recommendations API analyzes failure patterns against an evaluator (e.g., `Builtin.GoalSuccessRate`)
3. It generates an improved system prompt or tool descriptions
4. You review, apply, and validate the improvement

### Running Optimizations

```powershell
# Optimize the Orchestrator's system prompt (most impactful)
python optimize_prompts.py

# Optimize a specific agent
python optimize_prompts.py --agent specialist
python optimize_prompts.py --agent factchecker
python optimize_prompts.py --agent critic

# Optimize tool descriptions (improves routing accuracy)
python optimize_prompts.py --tools

# Use a different evaluator as the optimization target
python optimize_prompts.py --evaluator Builtin.Helpfulness

# Analyze more trace history
python optimize_prompts.py --days 14
```

Results are saved to `recommendation_result.json` with the improved prompt and an explanation of what was changed.

### Optimization Workflow

```
1. Generate traces    → python test_multi_agent.py <ARN>
2. Run optimization   → python optimize_prompts.py
3. Review result      → cat recommendation_result.json
4. Apply changes      → Update agent.py with new prompt
5. Redeploy           → terraform apply
6. Validate           → python eval_goal_attainment.py --invoke "test question"
7. Compare scores     → Did the new prompt score higher?
```

### What Can Be Optimized

| Target | Script flag | What it improves |
|--------|-------------|-----------------|
| Orchestrator system prompt | `--agent orchestrator` | Routing decisions, quality loop behavior |
| Specialist system prompt | `--agent specialist` | Research depth, web search usage |
| Fact Checker system prompt | `--agent factchecker` | Verdict accuracy, evidence gathering |
| Critic system prompt | `--agent critic` | Scoring calibration, feedback quality |
| Tool descriptions | `--tools` | Reduces wrong-agent routing |

## Security

### IAM Permissions

**Orchestrator Execution Role:**
- Standard AgentCore permissions
- **Critical**: `bedrock-agentcore:InvokeAgentRuntime` for Specialist, Fact Checker, and Critic
- Bedrock model invocation (Converse/ConverseStream)
- AWS Marketplace permissions for model subscription

**Specialist Execution Role:**
- Standard AgentCore permissions
- Bedrock model invocation
- No cross-agent invocation permissions needed

**Fact Checker Execution Role:**
- Standard AgentCore permissions
- Bedrock model invocation
- No cross-agent invocation permissions needed

**Critic Execution Role:**
- Standard AgentCore permissions
- Bedrock model invocation
- No cross-agent invocation permissions needed

**CodeBuild Role:**
- S3 access to all four agent source buckets
- ECR push access to all four repositories
- CloudWatch Logs write access

### Network Security

- Agents run in specified network mode (PUBLIC/PRIVATE)
- ECR repositories have account-level access controls
- S3 buckets block public access
- IAM policies follow least-privilege principle

### Bedrock Guardrail

A Bedrock Guardrail is applied to all agents, filtering both inputs and outputs:

- **Content filtering**: Blocks hate, insults, sexual content, violence, misconduct
- **Prompt attack detection**: Blocks prompt injection attempts on input
- **Sensitive data blocking**: AWS access keys, secret keys, credit cards, SSNs

The guardrail is defined in `guardrail.tf` and applied to all agents via the `BEDROCK_GUARDRAIL_ID` and `BEDROCK_GUARDRAIL_VER` environment variables.

### Secrets Management

The Tavily API key is stored in AWS Secrets Manager (`secrets.tf`):
- Secret name: `agentcore-multi-agent/tavily-api-key`
- Agents retrieve it at runtime via `secretsmanager:GetSecretValue`
- IAM permissions scoped to the specific secret ARN only
- Falls back to `TAVILY_API_KEY` env var for local testing

### Retry Limits

The Critic feedback loop is capped at a maximum of 2 retries (configurable via `MAX_CRITIC_RETRIES` env var). This prevents unbounded token consumption from loops where the Critic repeatedly scores responses below threshold.

### Security Monitoring

Two scripts are included for monitoring A2A communication security:

**`monitor_a2a.py`** — Automated security analysis with findings:
```powershell
python monitor_a2a.py                # Last 1 hour
python monitor_a2a.py --hours 24     # Last 24 hours
python monitor_a2a.py --json         # Machine-readable output
```

Checks performed:
- Unauthorized agent targets (HIGH)
- Prompt injection patterns in payloads (MEDIUM)
- Abnormal payload sizes / data exfiltration (MEDIUM)
- Latency anomalies (LOW/MEDIUM)
- Error rate spikes (HIGH)
- Unusual call frequency (MEDIUM)

Outputs `monitor_checks_explained.json` with detailed descriptions of each check.

**`check_a2a.py`** — Detailed A2A call log inspector:
```powershell
python check_a2a.py                  # Last 7 days
python check_a2a.py --hours 1        # Last 1 hour
python check_a2a.py --days 30        # Last 30 days
python check_a2a.py --output report  # Custom output filename
```

Outputs `a2a_report.json` with full session details, queries, responses, and latencies.

## Pricing

For current pricing information, please refer to:
- [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)
- [Amazon ECR Pricing](https://aws.amazon.com/ecr/pricing/)
- [AWS CodeBuild Pricing](https://aws.amazon.com/codebuild/pricing/)
- [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/)
- [Amazon CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)

**Note**: Actual costs depend on your usage patterns, AWS region, and specific services consumed.

## Troubleshooting

### Common Issues

**Issue**: No aws/span data in CloudWatch
- **Solution**: turn on tracing toggle in AgentCore Runtime (turns off if you recreate runtimes)
- **Check**: Check tracing toggle for all your agent runtimes, even if it was on before

**Issue**: Build fails
- **Solution**: Check CodeBuild logs in CloudWatch
- **Check**: Verify source code is in correct directories

**Issue**: Runtime not created
- **Solution**: Verify ECR image exists and is tagged correctly
- **Check**: Review Terraform state for errors

**Issue**: Runtime not updating with your changes
- **Solution**: Use Terraform taint to force complete rebuild
- **Check**: Agent runtimes are using old logic, ECR images, old environmental variables

### Debug Commands

```bash
# Check Terraform state
terraform show

# Validate configuration
terraform validate

# View specific resource
terraform state show aws_bedrockagentcore_agent_runtime.orchestrator

# Get detailed build logs
PROJECT_NAME=$(terraform output -raw orchestrator_codebuild_project)
aws codebuild batch-get-builds --ids $(aws codebuild list-builds-for-project --project-name $PROJECT_NAME --query 'ids[0]' --output text)
```

## Cleanup

### Automated Cleanup

```bash
chmod +x destroy.sh
./destroy.sh
```

The script shows the destruction plan, requires confirmation, and destroys all resources.

### Manual Cleanup

```bash
terraform destroy
```

**Important**: Verify in AWS Console that all resources are deleted:
- Bedrock AgentCore runtimes
- ECR repositories
- S3 buckets
- CodeBuild projects
- IAM roles

## Advanced Topics

### Adding Custom Tools

1. Define tool schema in agent code
2. Implement tool handler function
3. Register tool with agent
4. Rebuild and deploy

### Implementing Memory

Add session management in agent code:
```python
session_data = {}

def handle_request(input_text, session_id):
    if session_id not in session_data:
        session_data[session_id] = {}
    # Use session_data for context
```

### Multi-Region Deployment

For multi-region:
1. Configure backend for state locking
2. Deploy to each region separately
3. Use Route53 for failover
4. Consider cross-region replication for S3/ECR

## Next Steps

1. **Test the deployment**
   ```bash
   python test_multi_agent.py $(terraform output -raw orchestrator_runtime_arn)
   ```
   On Windows (PowerShell):
   ```powershell
   python test_multi_agent.py (terraform output -raw orchestrator_runtime_arn)
   ```

2. **Customize agents** for your specific use case
   - Add domain-specific tools to agents
   - Implement custom business logic
   - Integrate with external APIs

3. **Explore related patterns**
   - [MCP Server Pattern](../mcp-server-agentcore-runtime/) - MCP protocol with JWT auth
   - [AgentCore Samples](https://github.com/aws-samples/amazon-bedrock-agentcore-samples) - More examples

4. **Add production features**
   - Monitoring and alerting
   - Custom authentication layer (if needed)
   - VPC deployment for private networking
   - CI/CD pipeline integration

## Resources

- [Original Source: awslabs/agentcore-samples multi-agent-runtime](https://github.com/awslabs/agentcore-samples/tree/main/04-infrastructure-as-code/terraform/multi-agent-runtime) — This project is based on this reference implementation
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Agent-to-Agent Communication](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-a2a.html)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## Contributing

We welcome contributions! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT-0 license. See the [LICENSE](../../../LICENSE) file for details.
