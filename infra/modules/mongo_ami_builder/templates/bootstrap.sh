#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

install -d -m 0755 /var/lib/flash-ticket
touch /var/log/flash-ticket-mongo-build.log
chmod 0644 /var/log/flash-ticket-mongo-build.log
exec > >(tee -a /var/log/flash-ticket-mongo-build.log) 2>&1

write_status() {
  printf '%s\n' "$1" > /var/lib/flash-ticket/mongo-build.status.tmp
  mv /var/lib/flash-ticket/mongo-build.status.tmp /var/lib/flash-ticket/mongo-build.status
}

build_failed() {
  local result=$?
  trap - ERR
  write_status FAILED
  printf 'MongoDB AMI preparation failed (exit %s). Builder remains running for inspection.\n' "$result"
  exit "$result"
}

trap build_failed ERR
write_status BUILDING
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

# Stop future timer launches; an already running apt service is allowed to finish.
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer

# Every package source uses HTTPS because the builder permits outbound TCP 443 only.
install -d -m 0755 /etc/apt/sources.list.d
if [[ -f /etc/apt/sources.list ]]; then
  mv /etc/apt/sources.list /etc/apt/sources.list.flash-ticket-disabled
fi
find /etc/apt/sources.list.d -maxdepth 1 -type f \
  \( -name '*.list' -o -name '*.sources' \) -delete

cat > /etc/apt/sources.list.d/ubuntu.sources <<'SOURCES'
Types: deb
URIs: https://archive.ubuntu.com/ubuntu
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: https://security.ubuntu.com/ubuntu
Suites: noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
SOURCES

cat > /etc/apt/apt.conf.d/80flash-ticket-retries <<'APT'
Acquire::Retries "5";
Acquire::https::Timeout "60";
DPkg::Lock::Timeout "300";
APT

# This AMI runs in a private data subnet without outbound Internet access.
cat > /etc/apt/apt.conf.d/99flash-ticket-offline <<'APT'
APT::Periodic::Enable "0";
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
APT

# Canonical's Ubuntu EC2 AMI normally includes the SSM snap already.
if [[ -x /usr/bin/amazon-ssm-agent ]]; then
  systemctl enable --now amazon-ssm-agent.service
elif command -v snap > /dev/null 2>&1; then
  if ! snap list amazon-ssm-agent > /dev/null 2>&1; then
    snap install amazon-ssm-agent --classic
  fi
  snap start --enable amazon-ssm-agent
else
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl
  curl --fail --show-error --silent --location --retry 5 \
    https://s3.us-east-1.amazonaws.com/amazon-ssm-us-east-1/latest/debian_amd64/amazon-ssm-agent.deb \
    --output /tmp/amazon-ssm-agent.deb
  dpkg --install /tmp/amazon-ssm-agent.deb
  rm /tmp/amazon-ssm-agent.deb
  systemctl enable --now amazon-ssm-agent.service
fi

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg jq ec2-instance-connect openssh-server

curl --fail --show-error --silent --location --retry 5 \
  https://pgp.mongodb.com/server-8.0.asc \
  | gpg --dearmor --yes --output /usr/share/keyrings/mongodb-server-8.0.gpg
chmod 0644 /usr/share/keyrings/mongodb-server-8.0.gpg

cat > /etc/apt/sources.list.d/mongodb-org-8.0.list <<'MONGODB'
deb [arch=amd64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse
MONGODB

apt-get update
apt-get install -y --no-install-recommends mongodb-org
systemctl disable --now mongod.service

cat > /etc/mongod.conf <<'MONGOD'
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
MONGOD
chmod 0644 /etc/mongod.conf
chown -R mongodb:mongodb /var/lib/mongodb /var/log/mongodb

# The controller schedules this only after SSM has verified READY and package versions.
# The detached systemd unit continues running after the SSM agent is stopped.
cat > /usr/local/sbin/flash-ticket-finalize-ami <<'FINALIZE'
#!/usr/bin/env bash
set -Eeuo pipefail
exec >> /var/log/flash-ticket-mongo-finalize.log 2>&1

[[ "$(cat /var/lib/flash-ticket/mongo-build.status)" == READY ]]
systemctl disable --now mongod.service

if systemctl cat amazon-ssm-agent.service > /dev/null 2>&1; then
  systemctl disable --now amazon-ssm-agent.service
fi
if command -v snap > /dev/null 2>&1 && snap list amazon-ssm-agent > /dev/null 2>&1; then
  snap stop --disable amazon-ssm-agent
fi

rm -rf /var/lib/amazon/ssm /var/log/amazon/ssm
apt-get clean
rm -rf /var/lib/apt/lists/*
cloud-init clean --logs --machine-id
rm -f /etc/ssh/ssh_host_*
rm -f /var/lib/systemd/random-seed
rm -f /root/.bash_history /home/ubuntu/.bash_history
sync
shutdown -h now
FINALIZE
chmod 0755 /usr/local/sbin/flash-ticket-finalize-ami

mongod --version
dpkg-query --show --showformat='${Package} ${Version}\n' \
  mongodb-org-server ec2-instance-connect openssh-server
write_status READY
printf 'MongoDB AMI preparation is READY. Waiting for controller validation through SSM.\n'
