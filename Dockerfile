FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        git \
        bash \
        proxychains4 \
        procps \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs gh \
    && npm install -g @anthropic-ai/claude-code@2.1.204 @openai/codex@0.142.5 @larksuite/cli@1.0.64 cc-connect@1.4.1 \
    && ln -sf /usr/bin/lark-cli /usr/local/bin/larkcli \
    && groupadd -g 1000 claude \
    && useradd -m -u 1000 -g 1000 -s /bin/bash claude \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app

COPY deploy/docker/claude-proxied /usr/local/bin/claude-proxied
COPY deploy/docker/cc-connect /usr/local/bin/cc-connect
RUN chmod 0755 /usr/local/bin/claude-proxied /usr/local/bin/cc-connect

CMD ["python", "main.py"]
