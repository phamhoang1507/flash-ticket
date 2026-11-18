#!/usr/bin/env bash

set -Eeuo pipefail

exec > >(tee -a /var/log/flash-ticket-mongodb-node.log /dev/console) 2>&1

TASK_IMDS_TOKEN="$(
  curl \
    --fail \
    --silent \
    --show-error \
    --connect-timeout 3 \
    --max-time 10 \
    --request PUT \
    --header "X-aws-ec2-metadata-token-ttl-seconds: 300" \
    http://169.254.169.254/latest/api/token
)"

TASK_PRIVATE_IP="$(
  curl \
    --fail \
    --silent \
    --show-error \
    --connect-timeout 3 \
    --max-time 10 \
    --header "X-aws-ec2-metadata-token: ${TASK_IMDS_TOKEN}" \
    http://169.254.169.254/latest/meta-data/local-ipv4
)"

install -d \
  -o mongodb \
  -g mongodb \
  -m 0750 \
  /var/lib/mongodb \
  /var/log/mongodb

cat > /etc/mongod.conf <<EOF
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
  bindIp: 127.0.0.1,${TASK_PRIVATE_IP}

processManagement:
  timeZoneInfo: /usr/share/zoneinfo

security:
  authorization: enabled
EOF

cat > /etc/ssh/sshd_config.d/00-flash-ticket.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowUsers ubuntu
EOF

sshd -t

systemctl restart ssh
systemctl enable --now mongod

systemctl is-active --quiet mongod

printf '%s\n' "FLASH_TICKET_MONGODB_NODE_READY"