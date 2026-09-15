#!/usr/bin/env bash
# Make the droplet leave the internet from its DigitalOcean Reserved IP.
#
# Why: DigitalOcean recycles droplet addresses, and Kite refuses an IP that another Zerodha account has
# already whitelisted ("already linked to another account"). A Reserved IP (Networking -> Reserved IPs,
# free while assigned) gives a fresh address without rebuilding the droplet. Inbound traffic to the
# Reserved IP reaches the droplet at once, but OUTBOUND traffic keeps using the droplet's own address
# until the default route is moved to the anchor gateway; this script does that, verifies the egress
# address, and persists it across reboots (netplan). It reverts itself if the verification fails.
#
#   sudo bash scripts/vps-reserved-ip.sh            # after assigning the Reserved IP in the DO console
#   sudo bash scripts/vps-reserved-ip.sh --revert   # back to the droplet's own address
set -euo pipefail
[ "$(id -u)" = "0" ] || { echo "run with sudo"; exit 1; }
META=http://169.254.169.254/metadata/v1
IFACE="$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')"
IFACE="${IFACE:-eth0}"
CUR_GW="$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1); exit}')"
echo "interface $IFACE, current default gateway $CUR_GW"

ANCHOR_GW="$(curl -s $META/interfaces/public/0/anchor_ipv4/gateway)"
ANCHOR_IP="$(curl -s $META/interfaces/public/0/anchor_ipv4/address)"
PUBLIC_GW="$(curl -s $META/interfaces/public/0/ipv4/gateway)"
RESERVED="$(curl -s $META/reserved_ip/ipv4/ip_address 2>/dev/null || true)"
[ -n "$RESERVED" ] && [[ "$RESERVED" =~ ^[0-9.]+$ ]] || RESERVED="$(curl -s $META/floating_ip/ipv4/ip_address 2>/dev/null || true)"

egress() { curl -4 -s --max-time 10 https://api.ipify.org || curl -4 -s --max-time 10 https://icanhazip.com || true; }

NETPLAN="$(grep -l -E "(via|gateway4): *($PUBLIC_GW|$ANCHOR_GW)\b" /etc/netplan/*.yaml 2>/dev/null | head -1 || true)"
[ -n "$NETPLAN" ] || { echo "no netplan file carries the default gateway; edit /etc/netplan by hand (see docs/VPS.md 6a)"; exit 1; }

if [ "${1:-}" = "--revert" ]; then
  echo "reverting default route to the public gateway $PUBLIC_GW"
  ip route replace default via "$PUBLIC_GW" dev "$IFACE"
  sed -i -E "s/((via|gateway4): *)$ANCHOR_GW\b/\1$PUBLIC_GW/" "$NETPLAN"
  netplan apply
  echo "egress now: $(egress)"
  exit 0
fi

[[ "$RESERVED" =~ ^[0-9.]+$ ]] || { echo "no Reserved IP is assigned to this droplet (metadata reports none). Assign one in the DO console first."; exit 1; }
echo "reserved IP $RESERVED, anchor IP $ANCHOR_IP, anchor gateway $ANCHOR_GW"
echo "egress before: $(egress)"

# 1. live change, verified before anything is persisted
ip route replace default via "$ANCHOR_GW" dev "$IFACE"
sleep 1
NOW="$(egress)"
if [ "$NOW" != "$RESERVED" ]; then
  echo "egress is '$NOW', expected $RESERVED; reverting the live route"
  ip route replace default via "$PUBLIC_GW" dev "$IFACE"
  exit 1
fi
echo "egress after: $NOW (ok)"

# 2. persist: stop cloud-init from rewriting the network config, point netplan's default route at the anchor gateway
cp -n "$NETPLAN" "/root/$(basename "$NETPLAN").before-reserved-ip" || true
echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
sed -i -E "s/((via|gateway4): *)$PUBLIC_GW\b/\1$ANCHOR_GW/" "$NETPLAN"
netplan apply
sleep 1
FINAL="$(egress)"
if [ "$FINAL" != "$RESERVED" ]; then
  echo "netplan apply left egress at '$FINAL'; restoring the previous netplan file"
  cp "/root/$(basename "$NETPLAN").before-reserved-ip" "$NETPLAN"
  netplan apply
  ip route replace default via "$PUBLIC_GW" dev "$IFACE"
  exit 1
fi
echo
echo "done: this droplet now leaves from $RESERVED and will keep doing so after a reboot."
echo "next:  1. whitelist $RESERVED in the Kite developer console (if not already done)"
echo "       2. set kite.whitelisted_ip: $RESERVED in config.yaml (the research session commits it)"
echo "       3. tradebot doctor --no-data   -> egress:ipv4 must say 'matches'"
echo "       4. sudo systemctl start tradebot-check.timer"
echo "       5. ssh to $RESERVED from now on; the old address stays reachable but is no longer special"
