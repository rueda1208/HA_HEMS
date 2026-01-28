FROM ghcr.io/home-assistant/devcontainer:2-addons

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip python-is-python3 systemd systemd-sysv apparmor apparmor-utils curl \
    && rm -rf /var/lib/apt/lists/* && \
    # Node.js LTS (version 20.x) for SonarQube
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    # Poetry
    curl -sSL https://install.python-poetry.org | python3 - && \
    echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.bashrc

ENV PATH="/root/.local/bin:$PATH"