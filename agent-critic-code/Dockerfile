FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

COPY . .

# OTEL configuration - service name only; AgentCore configures the exporter
ENV OTEL_SERVICE_NAME="critic-agent"

CMD ["opentelemetry-instrument", "python", "-m", "agent"]
