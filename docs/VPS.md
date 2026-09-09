# Running Tradebot on a DigitalOcean droplet

Why: Zerodha only accepts API orders from whitelisted static IPs, and the cloud research session's IP
rotates. A small VPS gives one fixed IPv4 that is always on, so stops and targets are enforced by a
timer instead of by whoever is at the laptop. The cloud session keeps doing research and writes theses;
the VPS enforces them and pushes the day's snapshot.

Cost: the $6/month droplet (1 vCPU, 1 GB, 25 GB) is enough for Tradebot plus a few small apps.
Add weekly backups for about $1.20/month. Resizing later keeps the IP; only destroying the droplet
loses it.

## 1. Laptop: an SSH key (once)

```bash
ssh-keygen -t ed25519 -C "aravind-laptop"        # accept the defaults, set a passphrase
cat ~/.ssh/id_ed25519.pub                          # copy this line for step 2
```

## 2. Create the droplet

1. Sign up at https://cloud.digitalocean.com and add a payment method.
2. Create -> Droplets.
3. Region: **Bangalore (BLR1)**. Datacenter: the only option.
4. Image: **Ubuntu 24.04 (LTS) x64**.
5. Size: **Basic** -> CPU options **Regular** -> **$6/mo** (1 GB / 1 vCPU / 25 GB / 1 TB transfer).
   Choose $12/mo (2 GB) if you plan to run several other apps; you can also resize later.
6. Backups: enable, weekly (recommended).
7. Networking: leave **IPv6 off**. Keep the default VPC.
8. Authentication: **SSH Key** -> New SSH Key -> paste the line from step 1.
9. Hostname: `tradebot-blr`. Create Droplet.
10. When it is ready, copy the **public IPv4** from the droplet page. This is the address you will
    whitelist in Kite. Write it down.

Rules for keeping that IP:

- Never use Destroy. Power off, resize, reboot and "Rebuild from snapshot" all keep the IP.
- Restoring a backup to a *new* droplet gives a new IP; use Rebuild on the existing droplet instead.
- Kite allows one whitelist change per calendar week. Treat the droplet as permanent.

## 3. Phase 1 on the droplet (as root, about 5 minutes)

From the laptop, copy the setup script up and run it:

```bash
cd ~/personal/Tradebot && git pull
scp scripts/vps-setup.sh root@<DROPLET_IP>:/root/
ssh root@<DROPLET_IP> bash /root/vps-setup.sh system
```

It updates the OS, sets the clock to Asia/Kolkata, adds 1 GB swap, creates the `trader` user with
your SSH key, disables password and root SSH login, turns on the firewall (SSH only), fail2ban and
automatic security updates, and generates a GitHub deploy key. At the end it prints the deploy key
and the droplet's public IPv4.

## 4. GitHub: add the deploy key

Repo -> Settings -> Deploy keys -> Add deploy key. Title `tradebot-vps`, paste the printed key,
tick **Allow write access** (the VPS pushes a snapshot every evening). Add key.

## 5. Phase 2 on the droplet (as trader, about 5 minutes)

```bash
ssh trader@<DROPLET_IP>
bash ~/vps-setup.sh app
```

It clones the repo (branch `claude/trading-app-crypto-stocks-ub2ix4`), installs Python 3.11 and the
dependencies with `uv`, imports the latest snapshot, sets `TRADEBOT_LIVE=1` and `TRADEBOT_FORCE_IPV4=1`
in `.env`, and installs the systemd units:

| Unit | When (IST, weekdays) | What |
|---|---|---|
| `tradebot-dashboard.service` | always | Dashboard and API on 127.0.0.1:8787 (not public) |
| `tradebot-morning.timer` | 09:05 | `git pull`, import the newest snapshot, Kite equity sync |
| `tradebot-check.timer` | every 10 min, 09:20 to 15:25 | `thesis check --execute`: stops, targets, expiries. Installed but **not started** until the IP is whitelisted |
| `tradebot-eod.timer` | 15:45 | sync, export `data/snapshots/<date>-vps.json`, commit, push |

Then add the keys:

```bash
nano ~/Tradebot/.env        # KITE_API_KEY=..., KITE_API_SECRET=... (Alpaca lines optional)
tradebot doctor             # everything green except broker:kite until a token is saved
curl -4 -s https://ifconfig.me   # must print the droplet IP from step 2
```

## 6. Kite: whitelist the droplet

https://developers.kite.trade -> your app -> static IP settings. Add the droplet's IPv4. Keep the
laptop's IPv4 in the second slot as a fallback. Changes are limited to one per calendar week, so do
this on Monday 14 September or the next open window. Leave the redirect URL as it is.

Once the whitelist is active, hand execution to the droplet and take it away from the laptop:

```bash
sudo systemctl start tradebot-check.timer      # on the droplet
```

From then on do not run `tradebot thesis check --execute` on the laptop. Two machines enforcing the
same exits can sell the same shares twice; one executor at a time.

## 7. Every trading morning (2 minutes)

The Kite access token expires around 06:00 IST and Zerodha requires a human login each day.

```bash
ssh trader@<DROPLET_IP>
tradebot kite-login                          # prints the login URL
# open it on the laptop or phone, log in, approve; the browser lands on a 127.0.0.1 page that fails
# to load: that is expected. Copy request_token=... from the address bar.
tradebot kite-login <request_token> --save
tradebot doctor && tradebot positions --venue kite
```

Nothing else is manual. The 09:05 timer picks up the cloud session's research snapshot; the check
timer enforces exits; the 15:45 timer pushes the day's state. Entering a new thesis is still a
deliberate command: `tradebot thesis enter <id>` over SSH.

## 8. Operating it

```bash
systemctl list-timers --no-pager | grep tradebot          # next runs
journalctl -u tradebot-check -n 50 --no-pager             # what the last checks did
journalctl -u tradebot-eod -n 20 --no-pager
sudo systemctl stop tradebot-check.timer                  # pause automatic exits
tradebot kill                                             # block all new orders (data/KILL); tradebot kill --off to resume
ssh -L 8787:127.0.0.1:8787 trader@<DROPLET_IP>            # then open http://127.0.0.1:8787 on the laptop
```

Updating the code: `cd ~/Tradebot && git pull && uv pip install -e ".[dev]"` (the venv is
`.venv`), then `sudo systemctl restart tradebot-dashboard`.

## 9. Other apps on the same droplet

Run each as its own systemd service or Docker container under a separate user. Keep the firewall
closed except SSH; when an app needs to be reachable from the internet, put Caddy in front of it with
a domain name (automatic HTTPS) and open only ports 80 and 443. Never expose port 8787 directly: the
API can place orders. If you want the cloud research session to place entries itself, the supported
route is `TRADEBOT_API_TOKEN` in `.env` plus Caddy with HTTPS, and that is a separate change.

## 10. Recovery

- Droplet unreachable at 09:15: the laptop is the second whitelisted IP. Run the morning routine
  from HANDOVER.md there. Stops are not enforced while both are offline, so check positions by hand
  in the Kite app.
- Lost the droplet: create a new one, repeat steps 3 to 5 (about 15 minutes), then whitelist the new
  IP in the next weekly window. State comes back from the latest snapshot in `data/snapshots/`.
