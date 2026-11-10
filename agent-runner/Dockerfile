# The image of an agent run (design decision 7): OpenCode and the OpenSpec CLI, pinned, with git
# and the entrypoint that does everything deterministic around the model. Built by
# .gitea/workflows/images.yaml as gitea.sdlc.internal/platform/agent-runner:<commit>.
FROM docker.io/library/node:24.22.2-trixie-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates jq \
  && rm -rf /var/lib/apt/lists/*
# OpenSpec stays at the version the target projects' /opsx commands were generated with.
RUN npm install -g --no-fund --no-audit opencode-ai@1.19.0 @fission-ai/openspec@1.15.0 \
  && npm cache clean --force
COPY entrypoint.sh /usr/local/bin/agent-run
ARG COMMIT=unknown
ENV AGENT_RUNNER_COMMIT=$COMMIT
USER node
WORKDIR /work
ENTRYPOINT ["agent-run"]
