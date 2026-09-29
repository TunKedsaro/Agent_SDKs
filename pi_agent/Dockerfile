FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/jupyter \
    && /opt/jupyter/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/jupyter/bin/pip install --no-cache-dir jupyterlab ipykernel

COPY pi_agent/package.json pi_agent/package-lock.json ./
RUN npm ci

EXPOSE 8888

CMD ["/opt/jupyter/bin/jupyter", "lab", \
     "--ip=0.0.0.0", \
     "--port=8888", \
     "--no-browser", \
     "--allow-root", \
     "--IdentityProvider.token=pi-agent-local"]