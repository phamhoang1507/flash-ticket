#!/usr/bin/env bash

set -Eeuo pipefail

exec > >(tee -a /var/log/flash-ticket-mongo-build.log /dev/console) 2>&1

finish_build() {
  TASK_EXIT_CODE=$?

  trap - EXIT

  if [[ "$TASK_EXIT_CODE" -eq 0 ]]; then
    printf '%s\n' "FLASH_TICKET_MONGO_AMI_BUILD_OK"
  else
    printf '%s\n' "FLASH_TICKET_MONGO_AMI_BUILD_FAILED exit_code=${TASK_EXIT_CODE}"
  fi

  sync

  shutdown -h now
}

trap finish_build EXIT

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

cat > /etc/apt/sources.list.d/ubuntu.sources <<'EOF'
Types: deb
URIs: https://archive.ubuntu.com/ubuntu/
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: https://security.ubuntu.com/ubuntu/
Suites: noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

apt-get \
  -o DPkg::Lock::Timeout=300 \
  -o Acquire::Retries=5 \
  update

apt-get \
  -o DPkg::Lock::Timeout=300 \
  -o Acquire::Retries=5 \
  install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    jq \
    openssh-server \
    ec2-instance-connect

curl \
  --fail \
  --silent \
  --show-error \
  --retry 5 \
  https://pgp.mongodb.com/server-8.0.asc \
  | gpg \
      --batch \
      --yes \
      --dearmor \
      --output /usr/share/keyrings/mongodb-server-8.0.gpg

cat > /etc/apt/sources.list.d/mongodb-org-8.0.list <<'EOF'
deb [arch=amd64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse
EOF

apt-get \
  -o DPkg::Lock::Timeout=300 \
  -o Acquire::Retries=5 \
  update

apt-get \
  -o DPkg::Lock::Timeout=300 \
  -o Acquire::Retries=5 \
  install -y mongodb-org

systemctl disable --now mongod

cat > /etc/mongod.conf <<'EOF'
storage:
  dbPath: /var/lib/mongodb
  wiredTiger:
    engineConfig:
      cacheSizeGB: 0.25

systemLog:
  destination: file
  logAppend: true
  path: /var/log/mongodb/mongod.log

net:
  port: 27017
  bindIp: 127.0.0.1

processManagement:
  timeZoneInfo: /usr/share/zoneinfo

security:
  authorization: enabled
EOF

cat > /etc/apt/apt.conf.d/99flash-ticket-private <<'EOF'
APT::Periodic::Enable "0";
EOF

systemctl disable --now apt-daily.timer apt-daily-upgrade.timer

mongod --version
mongosh --version

dpkg-query -W \
  -f='${Package}: ${Status}\n' \
  mongodb-org \
  ec2-instance-connect

apt-get clean

rm -f /etc/ssh/ssh_host_*

cloud-init clean --logs --machine-id