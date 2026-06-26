#!/bin/bash
set -euo pipefail

# Redis 6
dnf install -y redis6
systemctl enable redis6 --now

# Ollama
curl -fsSL https://ollama.ai/install.sh | sh
systemctl enable ollama --now

# Neo4j (Community)
rpm --import https://debian.neo4j.com/neotechnology.gpg.key
cat > /etc/yum.repos.d/neo4j.repo <<'NEO4J_REPO'
[neo4j]
name=Neo4j RPM Repository
baseurl=https://yum.neo4j.com/stable/5
enabled=1
gpgcheck=1
NEO4J_REPO
dnf install -y neo4j
systemctl enable neo4j --now

# Qdrant (Docker-less native binary)
curl -fsSL https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-musl.tar.gz | tar xz -C /usr/local/bin/
cat > /etc/systemd/system/qdrant.service <<'QDRANT_SVC'
[Unit]
Description=Qdrant vector database
After=network.target

[Service]
ExecStart=/usr/local/bin/qdrant
Restart=on-failure
WorkingDirectory=/data/qdrant

[Install]
WantedBy=multi-user.target
QDRANT_SVC
mkdir -p /data/qdrant
systemctl daemon-reload
systemctl enable qdrant --now

# Data directory & deploy script
mkdir -p /data/lumina-invest
chown ec2-user:ec2-user /data/lumina-invest

cat > /data/deploy.sh <<'DEPLOY'
#!/bin/bash
set -euo pipefail
cd /data/lumina-invest
zip -r /tmp/lumina-source.zip . --exclude '.git/*' --exclude '__pycache__/*' --exclude '*.pyc'
aws s3 cp /tmp/lumina-source.zip s3://lumina-source-${account_id}/source.zip --region ${region}
DEPLOY
chmod +x /data/deploy.sh
chown ec2-user:ec2-user /data/deploy.sh
