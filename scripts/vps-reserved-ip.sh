#!/usr/bin/env bash
# Make the droplet leave the internet from its DigitalOcean Reserved IP.
#
# Why: DigitalOcean recycles droplet addresses, and Kite refuses an IP that another Zerodha account has
# already whitelisted ("already linked to another account"). A Reserved IP (Networking -> Reserved IPs,
# free while assigned) gives a fresh address without rebuilding the droplet. Inbound traffic to the
# Reserved IP reaches the droplet at once, but OUTBOUND traffic keeps using the droplet's own address
# until the default route is moved to the anchor gateway. This script does that, verifies the egress
# address against an echo service, and persists the route with a systemd oneshot unit that runs after
# the network is up on every boot (independent of how netplan/cloud-init lay out the config).
#
#   sudo bash scripts/vps-reserved-ip.sh [RESERVED_IP]   # after assigning the Reserved IP in the DO console
#   sudo bash scripts/vps-reserved-ip.sh --revert        # back to the droplet's own address
set -euo pipefail
[ "$(id -u)" = "0" ] || { echo "run with sudo"; exit 1; }
META=http://169.254.169.254/metadata/v1
UNIT=/etc/systemd/system/tradebot-egress.service
meta() { curl -s --max-time 3 "$META/$1" 2>/dev/null || true; }
is_ip() { [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
egress() { curl -4 -s --max-time 10 https://api.ipify.org || curl -4 -s --max-time 10 https://icanhazip.com || true; }

IFACE="$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')"; IFACE="${IFACE:-eth0}"
CUR_GW="$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1); exit}')"
PUBLIC_GW="$(meta interfaces/public/0/ipv4/gateway)"; is_ip "$PUBLIC_GW" || PUBLIC_GW="$CUR_GW"
ANCHOR_GW="$(meta interfaces/public/0/anchor_ipv4/gateway)"
ANCHOR_IP="$(meta interfaces/public/0/anchor_ipv4/address)"
echo "interface $IFACE, current default gateway $CUR_GW, public gateway $PUBLIC_GW, anchor $ANCHOR_IP via $ANCHOR_GW"

if [ "${1:-}" = "--revert" ]; then
  is_ip "$PUBLIC_GW" || { echo "cannot determine the public gateway"; exit 1; }
  systemctl disable --now tradebot-egress.service 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload
  ip route replace default via "$PUBLIC_GW" dev "$IFACE"
  echo "reverted; egress now: $(egress)"
  exit 0
fi

RESERVED="${1:-}"
is_ip "$RESERVED" || RESERVED="$(meta reserved_ip/ipv4/ip_address)"
is_ip "$RESERVED" || RESERVED="$(meta floating_ip/ipv4/ip_address)"
is_ip "$RESERVED" || { echo "no Reserved IP found in metadata: assign one to this droplet in the DO console, or pass it: sudo bash $0 209.38.x.x"; exit 1; }
is_ip "$ANCHOR_GW" || { echo "no anchor gateway in metadata (is this a DigitalOcean droplet with a Reserved IP assigned?)"; ip -4 addr show "$IFACE"; exit 1; }
ip -4 addr show "$IFACE" | grep -q "$ANCHOR_IP" || echo "warning: anchor IP $ANCHOR_IP is not configured on $IFACE yet; continuing"

echo "reserved IP $RESERVED; egress before: $(egress)"

# 1. live change, verified before anything is persisted
ip route replace default via "$ANCHOR_GW" dev "$IFACE"
sleep 1
NOW="$(egress)"
if [ "$NOW" != "$RESERVED" ]; then
  echo "egress is '$NOW', expected $RESERVED; reverting the live route to $PUBLIC_GW"
  ip route replace default via "$PUBLIC_GW" dev "$IFACE"
  exit 1
fi
echo "egress after: $NOW (ok)"

# 2. persist: a oneshot unit re-applies the route after the network is up on every boot
cat > "$UNIT" <<UNITEOF
[Unit]
Description=tradebot: route outbound traffic via the DigitalOcean Reserved IP ($RESERVED)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip route replace default via $ANCHOR_GW dev $IFACE

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable tradebot-egress.service >/dev/null 2>&1
echo
echo "done: this droplet leaves from $RESERVED now and after every reboot (tradebot-egress.service)."
echo "next:  tradebot doctor --no-data            -> egress:ipv4 must say 'matches'"
echo "       sudo systemctl start tradebot-check.timer"
echo "       systemctl list-timers --no-pager | grep tradebot"
echo "       ssh to $RESERVED from now on"
