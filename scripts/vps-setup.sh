#!/usr/bin/env bash
# Tradebot VPS setup for a fresh Ubuntu 24.04 droplet (DigitalOcean or any KVM host). Two phases:
#
#   phase 1, as root:    bash vps-setup.sh system      # hardening, user "trader", deploy key
#   phase 2, as trader:  bash vps-setup.sh app         # clone, bootstrap, .env, systemd timers
#
# Between the phases, add the printed deploy key to GitHub (repo Settings -> Deploy keys, allow write).
# Full walkthrough: docs/VPS.md
set -euo pipefail

PHASE="${1:-}"
APP_USER="${APP_USER:-trader}"
REPO_SSH="${REPO_SSH:-git@github.com:Mechagodz-glitch/Tradebot.git}"
BRANCH="${BRANCH:-claude/trading-app-crypto-stocks-ub2ix4}"
APP_DIR="/home/$APP_USER/Tradebot"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

phase_system() {
  [ "$(id -u)" -eq 0 ] || { echo "run phase 'system' as root"; exit 1; }

  say "packages and unattended security updates"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q && apt-get upgrade -yq
  apt-get install -yq git curl ufw fail2ban unattended-upgrades ca-certificates
  dpkg-reconfigure -f noninteractive unattended-upgrades

  say "timezone Asia/Kolkata (systemd timers use the server clock)"
  timedatectl set-timezone Asia/Kolkata

  if ! swapon --show | grep -q '^/swapfile'; then
    say "1 GB swap"
    fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi

  if ! id "$APP_USER" >/dev/null 2>&1; then
    say "user $APP_USER (sudo without password; SSH keys only)"
    adduser --disabled-password --gecos "" "$APP_USER"
    usermod -aG sudo "$APP_USER"
    echo "$APP_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$APP_USER"
    chmod 440 "/etc/sudoers.d/90-$APP_USER"
  fi
  install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
  if [ -f /root/.ssh/authorized_keys ]; then
    cp /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"
    chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh/authorized_keys"
    chmod 600 "/home/$APP_USER/.ssh/authorized_keys"
  fi

  say "sshd: no passwords, no root login"
  cat > /etc/ssh/sshd_config.d/90-tradebot.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
X11Forwarding no
MaxAuthTries 4
SSHD
  # DigitalOcean cloud-init may leave a file that re-enables passwords
  [ -f /etc/ssh/sshd_config.d/50-cloud-init.conf ] && sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config.d/50-cloud-init.conf
  sshd -t && systemctl reload ssh

  say "firewall: only SSH inbound"
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow OpenSSH >/dev/null
  ufw --force enable >/dev/null
  systemctl enable --now fail2ban >/dev/null

  say "deploy key for GitHub (read/write so the VPS can push snapshots)"
  if [ ! -f "/home/$APP_USER/.ssh/id_ed25519" ]; then
    sudo -u "$APP_USER" ssh-keygen -t ed25519 -N "" -C "tradebot-vps" -f "/home/$APP_USER/.ssh/id_ed25519" >/dev/null
  fi
  sudo -u "$APP_USER" bash -c "ssh-keyscan -t ed25519 github.com >> /home/$APP_USER/.ssh/known_hosts 2>/dev/null"
  cp "$0" "/home/$APP_USER/vps-setup.sh" && chown "$APP_USER:$APP_USER" "/home/$APP_USER/vps-setup.sh"

  echo
  echo "=================================================================================="
  echo "phase 1 done. Add this deploy key to GitHub -> repo Settings -> Deploy keys (tick 'Allow write access'):"
  echo
  cat "/home/$APP_USER/.ssh/id_ed25519.pub"
  echo
  echo "then log in as $APP_USER (root login is now disabled) and run:  bash ~/vps-setup.sh app"
  echo "public IPv4 to whitelist in Kite:  $(curl -4 -s https://ifconfig.me || echo '<curl -4 https://ifconfig.me>')"
  echo "=================================================================================="
}

phase_app() {
  [ "$(id -un)" = "$APP_USER" ] || { echo "run phase 'app' as $APP_USER"; exit 1; }

  if [ ! -d "$APP_DIR/.git" ]; then
    say "clone $REPO_SSH ($BRANCH)"
    git clone -q -b "$BRANCH" "$REPO_SSH" "$APP_DIR"
  fi
  cd "$APP_DIR"
  git config user.name "tradebot-vps"
  git config user.email "tradebot-vps@users.noreply.github.com"
  git config pull.rebase true

  say "python environment (uv) and dependencies"
  ./scripts/bootstrap.sh
  export PATH="$HOME/.local/bin:$PATH"

  say ".env flags for live execution from this whitelisted IPv4"
  touch .env && chmod 600 .env
  grep -q '^TRADEBOT_LIVE=' .env || echo 'TRADEBOT_LIVE=1' >> .env
  grep -q '^TRADEBOT_FORCE_IPV4=' .env || echo 'TRADEBOT_FORCE_IPV4=1' >> .env

  say "systemd services and timers"
  sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  # the executor timer (tradebot-check) is installed but NOT started: start it only after this IP is whitelisted in Kite
  sudo systemctl enable --now tradebot-dashboard.service tradebot-morning.timer tradebot-eod.timer
  sudo systemctl enable tradebot-check.timer
  systemctl list-timers --no-pager | grep tradebot || true

  echo
  echo "=================================================================================="
  echo "phase 2 done. Next:"
  echo "  1. nano $APP_DIR/.env   -> KITE_API_KEY, KITE_API_SECRET (Alpaca keys optional)"
  echo "  2. tradebot doctor       -> everything except broker:kite should be green until a token is saved"
  echo "  3. whitelist this IPv4 in the Kite developer console: $(curl -4 -s https://ifconfig.me || true)"
  echo "  4. AFTER the whitelist is active:  sudo systemctl start tradebot-check.timer   (stops/targets then run from here; stop running thesis check --execute on the laptop)"
  echo "  5. each trading morning: tradebot kite-login -> log in -> tradebot kite-login <request_token> --save"
  echo "  logs: journalctl -u tradebot-check -n 50    pause: sudo systemctl stop tradebot-check.timer  or  tradebot kill"
  echo "=================================================================================="
}

case "$PHASE" in
  system) phase_system ;;
  app) phase_app ;;
  *) echo "usage: bash vps-setup.sh system|app   (see docs/VPS.md)"; exit 1 ;;
esac
