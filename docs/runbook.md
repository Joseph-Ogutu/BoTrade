# Runbook: collector on an Oracle Cloud VM

Scope: the tick collector only (docker-compose.yml, Dockerfile.collector). It uses the public Deriv endpoint, needs no token, no OTP, no App ID and no `.env`. Postgres, strategies, the executor and any authenticated flow are NOT part of this runbook.

Status of this runbook: written and partly tested on a dev machine on 2026-10-31 (UTC; 21:27Z to 22:21Z). Nothing below has been run on an Oracle VM yet. Every VM-specific step is UNVERIFIED until you have run it once and ticked it in section 10. Commands that Oracle may have changed (Always Free shapes, firewall defaults, console menus) must be checked against Oracle's current docs.

What was tested for real (dev machine, Docker 29, 2026-10-31 UTC): image build, container start, healthy status, closed-market handling, automatic reconnect after a server-side disconnect, graceful stop (3 s, exit 0), data surviving stop and start, health failure paths, read-only root filesystem, backup, verify, restore and row-for-row comparison (section 8).

## 1. Decisions you must make first

| Decision | Why it matters |
|---|---|
| Your public IP for SSH (a /32) | The security rule allows SSH only from it. If your IP changes (mobile data and some home ISPs rotate or share addresses) you are locked out until you change the rule in the Oracle console. Prefer a fixed IP. |
| Off-host backup target | The backup copies to an rclone remote. Options: Oracle Object Storage (check current free allowance, UNVERIFIED), another cloud bucket, or another machine you own over SFTP. The remote must NOT be the VM's own disk. |
| Which Oracle shape and region | Always Free eligibility and limits change; check Oracle's docs on the day. The collector needs very little: 256 MB RAM cap, 1 vCPU is enough, disk for data (section 9). The VM needs outbound HTTPS/WSS to `api.derivws.com:443`. |

## 2. Create the VM (Oracle console)

1. Image: Ubuntu LTS. Add your SSH PUBLIC key at creation. Never paste a private key anywhere.
2. Networking: use a VCN with a public subnet and a public IP.
3. Note the public IP. All steps below assume you can `ssh ubuntu@<ip>` (the default image user may be `ubuntu`; check).

## 3. Inbound rule: SSH only, from your IP

In the console: Networking, your VCN, the subnet's Security List (or Network Security Group), Ingress rules.

- Keep exactly ONE ingress rule: TCP, destination port 22, source `<your-ip>/32`.
- Delete or do not add any rule for 0.0.0.0/0. No HTTP, no HTTPS, no ICMP-from-anywhere requirement.
- Egress: leave default (allow all). The collector only makes outbound connections.

The collector exposes no ports (`docker-compose.yml` has no `ports:`), so nothing else needs to be opened.

Host firewall (second layer): Oracle's Ubuntu images are known to ship with pre-configured iptables rules, and enabling `ufw` on top can conflict. UNVERIFIED for your image. Before changing anything, look: `sudo iptables -S`. If you add rules, keep SSH open in a second session and test before you disconnect. The cloud security list above is the primary control.

Verify from a machine NOT on your allowed IP (for example phone hotspot): `nc -vz <vm-ip> 22` must time out. From your IP it must connect.

## 4. Harden the host

Run as the default admin user, in a session you keep open until the end of this section.

```bash
# non-root deploy user, key only
sudo adduser --disabled-password --gecos "" deploy
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys && sudo chmod 600 /home/deploy/.ssh/authorized_keys

# SSH: no root login, no passwords
sudo tee /etc/ssh/sshd_config.d/99-derivbot.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sudo sshd -t && sudo systemctl reload ssh

# automatic security updates
sudo apt-get update && sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

From a NEW terminal confirm `ssh deploy@<ip>` works and `ssh root@<ip>` is refused before closing the first session.

The `deploy` user will run Docker. Membership of the `docker` group is effectively root on the host; that is accepted for a single-purpose VM. Do not give this VM other jobs.

## 5. Install Docker

Follow Docker's official instructions for Ubuntu (apt repository, `docker-ce`, `docker-compose-plugin`); check the page on the day: https://docs.docker.com/engine/install/ubuntu/

```bash
sudo usermod -aG docker deploy      # log out and in again
docker --version && docker compose version
```

## 6. Deploy

As `deploy`:

```bash
sudo install -d -o deploy -g deploy /opt/derivbot && cd /opt/derivbot
git clone <your-repo-url> .          # use a read-only deploy key if the repo is private
mkdir -p data                         # owned by deploy
id -u                                 # note the number; if not 1000 see below
docker compose up -d --build
```

- `data/` must be owned by the same uid the container runs as. The image uses uid `1000` by default. If `id -u` for `deploy` is not 1000, build with `APP_UID=$(id -u) docker compose up -d --build`.
- No `.env` file is needed or read. Do not create one for the collector.
- Pin the base image (one-time, then commit): `docker pull python:3.12-slim && docker inspect --format '{{index .RepoDigests 0}}' python:3.12-slim`, then put that `python@sha256:...` in the `FROM` line of `Dockerfile.collector`. Done in the repo on 2026-10-31 (the digest in `Dockerfile.collector` is the multi-arch index, so it builds on amd64 and arm64). To refresh it later, follow the comment in that file.
- Build needs outbound access to Docker Hub and PyPI. On the dev machine one build attempt failed on IPv6 "network unreachable" and succeeded on retry; if it fails, retry before debugging.

## 7. Check health

```bash
cd /opt/derivbot
docker compose ps                      # STATUS should read "Up ... (healthy)" after about 90 seconds
docker logs --tail 20 derivbot-collector
docker exec derivbot-collector python -m derivbot.collector.healthcheck   # prints "healthy: ok", exit 0
```

What healthy looks like (real output from the dev run):

```
HEARTBEAT ticks_total=414 connects=1 reconnects=0 errors=0 age_1HZ100V=0.6 age_R_100=0.6 age_cryBTCUSD=0.6 age_cryETHUSD=0.6 age_frxEURUSD=never ...
```

- A `HEARTBEAT` line is logged every 10 seconds. `age_X` is seconds since the last tick of symbol X.
- Forex symbols show `age=never` at weekends: their markets are closed. The log shows one line per symbol, `market closed, retrying every 300s`. This is normal and does not make the container unhealthy.
- The container is marked unhealthy if the heartbeat file is stale (process stuck or dead) or an always-open symbol (`1HZ100V`, `R_100`, `cryBTCUSD`, `cryETHUSD`) has had no tick for 120 seconds (`health_max_age_s` in `config/collector.json`).
- If the always-open symbols are silent for 600 seconds (`max_silence_exit_s`) the process exits with code 1 and `restart: unless-stopped` starts it again. A server-side disconnect is handled inside the process: observed once on the dev run, reconnected in about 3 seconds.
- Docker marks a container unhealthy but does not restart it for that alone. Watch for it (section 10 monitoring gap).

| Symptom | Check | Action |
|---|---|---|
| `unhealthy` | `docker logs --tail 50 derivbot-collector` | Look for repeating `connection problem`. Test reachability: `curl -sI https://api.derivws.com` from the VM. Restart: `docker compose restart collector`. |
| `connects` keeps rising, `ticks_total` flat | logs | Network or Deriv issue; the service backs off up to 60 s between attempts. Check the Deriv status page and the VM's outbound route. |
| Container restarting repeatedly | `docker compose ps`, `docker logs` | Exit code 1 means silence watchdog. Exit with a Python traceback means a bug: keep the logs, stop (`docker compose stop`), report. |
| `data/` not writable | `docker logs` mentions permission | `ls -ln data/` and compare uid with `APP_UID`. |
| Disk filling | `df -h`, `du -sh data` | See section 9. |

Stop and start: `docker compose stop` (graceful, flushes buffered ticks, took 3 s), `docker compose start`. To remove the container but keep data: `docker compose down` (data is a bind mount in `./data`, so it stays).

## 8. Daily off-host backup and one tested restore

What gets backed up: `data/ticks.sqlite`, taken with SQLite's online backup API (consistent while the collector is writing), gzip-compressed with a SHA-256 file, verified right after creation, 14 local copies kept (`KEEP_LOCAL`).

### 8.1 One-time setup on the VM (UNVERIFIED until you run it)

```bash
# rclone: install from your package manager or rclone.org, then configure a remote as the deploy user.
sudo apt-get install -y rclone
rclone config            # credentials are stored in /home/deploy/.config/rclone/rclone.conf (keep it mode 600, never commit)
rclone lsd offhost:      # prove the remote works

sudo install -d /etc/derivbot
echo 'OFFHOST_REMOTE=offhost:derivbot-backups' | sudo tee /etc/derivbot/backup.env   # a remote NAME, not a secret
sudo cp /opt/derivbot/deploy/derivbot-backup.service /opt/derivbot/deploy/derivbot-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now derivbot-backup.timer
systemctl list-timers derivbot-backup.timer
```

Run one backup by hand to prove it, then read the result:

```bash
sudo systemctl start derivbot-backup.service
journalctl -u derivbot-backup.service -n 20 --no-pager     # must end with "backup ok: ..."
rclone ls offhost:derivbot-backups                          # the .sqlite.gz and .sha256 must be there
cat /opt/derivbot/data/backups/LAST_OFFHOST_OK              # timestamp, written only after rclone check passed
```

`scripts/backup_daily.sh` fails (non-zero) if the off-host copy or its checksum comparison fails, and writes `LAST_OFFHOST_OK` only on success. rclone was not installed on the dev machine, so the rclone half of this script is UNVERIFIED; its `bash -n` syntax check passed and the unit files passed `systemd-analyze verify` apart from the path not existing on the dev machine.

### 8.2 The restore drill (do this once on the VM, from the OFF-HOST copy)

A backup you have never restored is not a backup. Restore from the off-host copy, not the local one, into a scratch location, and compare with the live data.

```bash
cd /opt/derivbot
mkdir -p data/restore-drill && rclone copy offhost:derivbot-backups data/restore-drill/in --include "*.sqlite.gz*"
B=$(ls -1t data/restore-drill/in/*.sqlite.gz | head -1)
docker compose run --rm --no-deps -v "$PWD/data:/data" collector \
    python -m derivbot.collector.backup verify "/data/restore-drill/in/$(basename $B)"
docker compose run --rm --no-deps -v "$PWD/data:/data" collector \
    python -m derivbot.collector.backup restore "/data/restore-drill/in/$(basename $B)" --to /data/restore-drill/ticks.sqlite
sqlite3 -readonly data/restore-drill/ticks.sqlite "select symbol,count(*),min(epoch),max(epoch) from ticks group by symbol order by symbol"
sqlite3 -readonly data/ticks.sqlite             "select symbol,count(*),min(epoch),max(epoch) from ticks group by symbol order by symbol"
```

Expected: the restored file has the same symbols and counts as the live file at the time of the backup (the live file will have MORE recent rows; compare `min(epoch)` and that restored counts are not larger than live). Then clean up: `rm -rf data/restore-drill`. Record the result in section 10.

The restore command refuses to overwrite an existing file and verifies the checksum and `PRAGMA integrity_check` before writing anything.

### 8.3 Dev-machine drill, real output (2026-10-31T22:18:28Z, local copy, not off-host)

```
create:  created <tmp>/bk/ticks-20261031T221828Z.sqlite.gz rows={'1HZ100V': 180, 'R_100': 90, 'cryBTCUSD': 179, 'cryETHUSD': 179} pruned=0
verify:  ok rows={'1HZ100V': 180, 'R_100': 90, 'cryBTCUSD': 179, 'cryETHUSD': 179}
restore: restored to <tmp>/restored/ticks.sqlite rows={'1HZ100V': 180, 'R_100': 90, 'cryBTCUSD': 179, 'cryETHUSD': 179}
restore again: BACKUP ERROR: refusing to overwrite existing file: <tmp>/restored/ticks.sqlite      (exit 1)
live    : [('1HZ100V', 180, 1793484849, 1793485036), ('R_100', 90, 1793484848, 1793485034), ('cryBTCUSD', 179, 1793484849, 1793485035), ('cryETHUSD', 179, 1793484849, 1793485035)]
restored: [('1HZ100V', 180, 1793484849, 1793485036), ('R_100', 90, 1793484848, 1793485034), ('cryBTCUSD', 179, 1793484849, 1793485035), ('cryETHUSD', 179, 1793484849, 1793485035)]
IDENTICAL
```

Backup size: 6,521 bytes for a 45,056 byte database. The unit tests in `tests/unit/test_backup.py` also cover backup while the database is open for writing, a corrupted backup, a missing checksum, a non-database payload, overwrite refusal and pruning.

## 9. Disk and growth

Measured rate on the dev run: about 1 tick per second for `1HZ100V`, `cryBTCUSD`, `cryETHUSD` and about 1 per 2 seconds for `R_100`, so roughly 300,000 rows per day from the four always-open symbols, plus forex when open (not measured). Size per row in SQLite is not measured yet; estimate 40 to 60 bytes, so roughly 12 to 18 MB per day (UNVERIFIED). After 24 hours run `ls -l data/ticks.sqlite` and replace this estimate with the real number, then size the disk and the off-host retention from it.

Backups are compressed copies: the local 14 copies add roughly 14 times the compressed size. Off-host retention is your decision.

## 10. Verification log (fill in as you go)

| Step | Done (date, by) | Result |
|---|---|---|
| VM created, SSH from allowed IP works | | |
| SSH from a non-allowed IP times out | | |
| `deploy` user works, root login and passwords refused | | |
| Docker installed | | |
| Collector up and `healthy` | | |
| Base image pinned by digest and committed | 2026-10-31, repo (Dockerfile.collector) | digest is the multi-arch index; rebuild verified on amd64 dev machine only |
| 24 hour run: `ls -l data/ticks.sqlite` size recorded in section 9 | | |
| rclone remote works | | |
| Manual backup: `backup ok` in journal and file present off-host | | |
| Restore drill from the off-host copy matches (section 8.2) | | |
| Timer fired on its own once (`journalctl -u derivbot-backup.service`) | | |
| Reboot test: collector comes back by itself and is healthy | | |

## 11. Known gaps (not done, not hidden)

- Nothing here has been run on Oracle. The dev-machine tests prove the container and the backup tooling, not the VM.
- Unhealthy containers are not restarted by Docker. There is no external alerting. Needed before relying on this: an external dead-man's-switch on the heartbeat (HANDBOOK 11.1) and an alert on `LAST_OFFHOST_OK` older than 26 hours. Not built.
- pip installs are version-pinned but not hash-pinned. The base image is pinned by digest.
- Time-of-day and weekends: forex, OTC indices and metals were closed when this was tested (Saturday 2026-10-31 at about 22:00Z. In Nairobi that was already Sunday 2026-11-01, 01:00). Their ticks have never been observed by this collector, so rates, tick formats and gap behaviour for them are UNVERIFIED.
- Public endpoint behaviour could differ from the authenticated demo endpoint (HANDBOOK 2.1). Data collected here is public market data only.
- Deriv's terms on automated collection of public data were not checked (api-notes item 10).
- Restoring requires the container image or `PYTHONPATH=src python -m derivbot.collector.backup` from a checkout; a plain `gunzip` of the `.sqlite.gz` also yields a usable SQLite file if the tooling is unavailable.

## 12. Full stack (trading service, demo only). Written, built and config-checked locally; NOT deployed

Nothing in this section has been run on the VM. Everything below was verified only on the development machine, as noted.

### 12.1 What runs

`docker-compose.yml` defines five services: `postgres` (16), `redis` (7, password, append-only), `migrate` (one shot, `alembic upgrade head`, exits 0), `collector` (public ticks, unchanged) and `bot` (`python -m derivbot.app`). Postgres and Redis sit on an internal network with no route out. No service publishes a port. The admin API listens on 127.0.0.1 inside the bot container only.

`TRADING_MODE=demo` and `ALLOW_REAL_TRADING=false` are written into the compose file and are not read from any env file. Real trading is also locked in code and in the database, so editing the compose file cannot enable it.

### 12.2 First start

1. `bash scripts/init_secrets.sh` creates `./secrets/` (git-ignored): random Postgres, Redis and admin API passwords, and EMPTY files for the Deriv PAT and the Telegram token. It never prints a secret and never overwrites a file.
2. Owner only: fill `config/risk.json` (every `SET_BY_OWNER` value). Until you do, the bot refuses to start and lists the unset names. Checked: the built image exits 1 with exactly that list.
3. Owner only, when the demo credentials exist: paste the PAT into `secrets/deriv_pat_demo` and the bot token into `secrets/telegram_bot_token`, and put `DERIV_APP_ID_DEMO`, `DERIV_ACCOUNT_ID_DEMO` and `TELEGRAM_ALLOWED_CHAT_IDS` in the git-ignored `.env` that compose reads. An empty optional secret means "not provided": no Deriv session, alerts only logged.
4. `docker compose build` then `docker compose up -d`. Order is enforced: postgres and redis healthy, then migrate completes, then bot.

### 12.3 Operating

- Status, kill, release: `scripts/opsctl.sh status`, `scripts/opsctl.sh kill "reason"`, `scripts/opsctl.sh release "reason"`. These run inside the bot container against the loopback API; the token comes from the mounted secret.
- Kill state lives in four places (memory, flag file on the `botstate` volume, Redis, Postgres). Any one saying engaged wins. A restart does not release it.
- Healthchecks: postgres `pg_isready`, redis `PING`, bot `python -m derivbot.healthcheck_bot` (admin API answers and the Redis heartbeat is under 60 s old), collector as before.

### 12.4 Postgres backup and restore

- `scripts/pg_backup.sh` runs `pg_dump --format=custom` inside the container over its local socket (no password handled), checks the archive with `pg_restore --list`, writes a sha256, keeps the newest `KEEP_LOCAL` (14), and if `OFFHOST_REMOTE` is set copies with rclone and runs `rclone check`.
- `scripts/pg_restore_drill.sh <dump>` restores into a scratch database, compares every table's row count with the source, checks the audit triggers survived, and drops the scratch database. Exit 0 only if all match.
- Verified on the dev machine (dev Postgres container, migrated database with one audit row): backup ok, drill OK with identical counts, and a truncated dump makes the drill exit 1. NOT verified: the off-host copy (needs rclone and a remote), the drill on the VM, and timer scheduling for `pg_backup.sh` (the existing systemd timer covers only the SQLite collector backup).
- Do the drill once from the OFF-HOST copy before you rely on any of this.

### 12.5 Continuous integration

`.github/workflows/ci.yml` runs ruff, mypy, the test suite against Postgres and Redis service containers with a 100% line and branch gate on `risk/rules.py` and `risk/stake.py`, pip-audit, the whole-history secret scan and `docker compose config`. `make check` runs the same list locally. The workflow has not run on GitHub (nothing is pushed). `.pre-commit-config.yaml` runs ruff, mypy and the secret scan locally.

### 12.6 Known gaps in the stack

- The bot passes `api_min_stake = None`, so it places no trade until minimum-stake discovery is wired after the demo credentials exist. This is deliberate and fails closed.
- No signal runner starts without a `REAL_DATA_BACKTEST_APPROVED` line in `docs/approvals.md`, which the owner has not written.
- No log shipping, no alert on a missing backup marker, no restart-storm alert. Add before any unattended run.


### 12.7 The deployed instance (demo), how it was set up and how to update it

Set up on 2026-11-05 UTC from the owner's laptop over SSH (key in ~/.ssh on the laptop, never in the repo). Commands used, in order: create the `deploy` user and key-only SSH; install `unattended-upgrades`; create a 4 GB swap file (`scripts/vps_swap.sh` logic); install Docker from Docker's apt repository; send the code with `git archive HEAD | ssh deploy@<host> 'tar -x -C /opt/derivbot'`; copy the token file with `scp`; run `bash scripts/init_secrets.sh`; write `vm.env` (App ID, demo account id, `APP_UID`); then

```bash
docker compose --env-file vm.env -f docker-compose.yml -f docker-compose.lowmem.yml up -d --build
```

Update: re-run `git archive` over the old tree, then the same `up -d --build`. Status: `bash scripts/opsctl.sh status` on the VM (it needs `docker compose` to find the same files, so run it from /opt/derivbot). `vm.env` is not tracked and holds no secret, only the App ID and the demo account id.

Found while bringing the stack up for the first time: the redis service needed `user: 999:1000`, and a variable named `KILL_SWITCH_FILE` was misread as a secret reference (now `KILL_SWITCH_PATH`). Both are fixed and tested.


### 12.8 The dashboard (private, through an SSH tunnel)

The admin API also serves a dashboard at `/ui`. It is a single page with no external files, designed for phone and laptop. It shows the real account balance (VIEW ONLY, trading locked), the demo balance, today's P&L, win rate, open exposure, recent trades, risk rejections, system health and kill switch events. The actions are "Engage kill switch" (type KILL to confirm) and, once it is engaged, "Release kill switch". Release asks for RELEASE typed, a reason, and YOUR PASSWORD AGAIN, and it is refused if it cannot be logged. Check why it was engaged (the banner and the Kill switch events) before you release. The token-only `scripts/opsctl.sh release "reason"` on the VM still exists.

Access, with nothing opened to the internet: the bot publishes port 8787 on the VM's loopback only.

```bash
ssh -i ~/.ssh/<key> -L 8787:127.0.0.1:8787 deploy@<host>     # keep this running
# then open http://127.0.0.1:8787/ui in a browser on the same machine and sign in with the admin token
```

The admin token is the file `/opt/derivbot/secrets/admin_api_token` on the VM. Print it once on the VM with `cat` in your own terminal and paste it into the page; the page keeps it in that tab only. On a phone the same tunnel works with an SSH app that supports port forwarding; a public name with HTTPS and a second factor is a separate, not yet built step.

What the page can see: balances of both accounts through ONE read-only GET to `/trading/v1/options/accounts` with the demo token (cached 30 s). It cannot open a session on the real account and has no write call. Content-Security-Policy: only the page's own script and style by hash, no external resources, no framing.

### 12.9 Postgres backup timer (on the VM)

`deploy/derivbot-pgbackup.service` and `.timer` run `scripts/pg_backup.sh` daily at 03:30 UTC as `deploy`, keep the newest 14 dumps in `/opt/derivbot/data/pg-backups`, and verify each dump. There is NO off-host copy until you set `OFFHOST_REMOTE` and rclone; a lost VM loses these backups too. Install: copy the two files to `/etc/systemd/system/`, then `sudo systemctl daemon-reload && sudo systemctl enable --now derivbot-pgbackup.timer`.


### 12.10 Public HTTPS for the dashboard (optional, compose profile `public`)

`deploy/Caddyfile` plus the `caddy` service put the dashboard on `https://<DASHBOARD_HOST>`. Caddy gets and renews the certificate by itself. It is not started unless `COMPOSE_PROFILES=public` is set, and it is the only service that publishes ports 80 and 443. It proxies to the bot on the internal network, never exposes `/release` (answers 404), redirects `/` to `/ui`, sets HSTS, and OVERWRITES `X-Forwarded-For` with the address it saw, so a client cannot pick the address the lockout counts.

Protection on the public page: a 256 bit random admin token (a long random string, never a password you chose), TLS, a lockout after 10 bad tokens from one address within 10 minutes (blocked 15 minutes, even for the right token), a strict Content-Security-Policy, no inline script or style, and one write action only (kill, type KILL to confirm). NOT present: a second factor, per-user accounts, or alerting on failed logins. Anyone who obtains the token can read balances and engage the kill switch; they cannot release it, change settings, or trade.

Enable on the VM: set `COMPOSE_PROFILES=public` and `DASHBOARD_HOST=bot.josephogutu.com` in `vm.env`, open TCP 80 and 443 in the Oracle security list (ingress from 0.0.0.0/0), then `docker compose --env-file vm.env -f docker-compose.yml -f docker-compose.lowmem.yml up -d`. Docker publishes these ports through its own firewall chains, so the host `iptables` INPUT rules do not gate them: the Oracle security list is the gate. Disable by removing the profile and the two ingress rules.


### 12.11 Password login for the dashboard

Set or change the password on the VM, in your own terminal (the password is typed hidden and never leaves that terminal; only a salted scrypt hash is written, to `secrets/dashboard_password_hash`):

```bash
ssh -t -i ~/.ssh/<key> deploy@<host> 'cd /opt/derivbot && python3 scripts/set_dashboard_password.py'
```

At least 12 characters; a few unrelated words is better than a clever short one. No restart is needed: the bot reads the hash file at each login, and changing it signs every existing session out. Login creates a session cookie (HttpOnly, SameSite=Strict, Secure over HTTPS) valid for 8 hours absolute and 2 hours idle; sessions live in the bot's memory, so a restart signs everyone out. A write through a cookie session (kill) also requires the `X-Requested-With: botrade` header. The release endpoint accepts ONLY the admin token and is blocked from the internet by the proxy. The admin token still works as a fallback and for command line tools. The same lockout applies to wrong passwords (10 per address in 10 minutes, then 15 minutes). If you forget the password: run the script again on the VM.
