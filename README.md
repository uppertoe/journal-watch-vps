# server-[name]

> Replace this heading with your server name and delete this line.

VPS implementation for `[name]`. Uses [vps-base-template](https://github.com/uppertoe/vps-base-template)
for infrastructure — Ansible provisioning, hardening, Docker setup, and Caddy base config.

---

## Create a new server repo from this template

Do this once per server.

1. On GitHub, open [server-instance-template](https://github.com/uppertoe/server-instance-template)
   and click **Use this template → Create a new repository**.
2. Name it `server-[name]` (e.g. `server-mycompany`). Keep it **private**.
3. Clone it locally — the `scaffold` submodule is included automatically:
   ```bash
   git clone --recurse-submodules git@github.com:yourorg/server-[name].git
   cd server-[name]
   ```
4. Edit the repo name in this README, then commit:
   ```bash
   git add README.md && git commit -m "chore: name server repo"
   git push
   ```

That's it — your server repo is ready. Continue below to provision the server.

---

## Provision a new server

Run these **from your local machine** — Ansible SSHs into the VPS on your behalf.

```bash
# 1. Add the server to your local SSH config (~/.ssh/config)
#    The name here ("myserver") should match the hostname in ansible/hosts.
cat >> ~/.ssh/config <<'EOF'

Host myserver
    HostName YOUR_SERVER_IP
    User deploy
    # Use whichever SSH key you want bootstrap to install for deploy.
    IdentityFile ~/.ssh/id_ed25519
EOF

# 2. Configure inventory and environment
#    Keep ansible_ssh_private_key_file aligned with the same key.
#    Bootstrap will install the matching .pub onto the server.
cp ansible/hosts.example ansible/hosts && $EDITOR ansible/hosts
cp .env.example .env && $EDITOR .env

# 3. Install Ansible dependencies (once per machine)
ansible-galaxy collection install -r scaffold/ansible/requirements.yml

# 4. Bootstrap — run once as root on a fresh VPS
#    Most providers give you a root password — --ask-pass makes Ansible prompt for it.
#    If your provider gave you a root SSH key instead, omit --ask-pass.
ansible-playbook -i ansible/hosts scaffold/ansible/bootstrap.yml --ask-pass

# 5. Harden and install Docker — idempotent, safe to re-run any time
#    From here on Ansible uses the deploy user with your SSH key (no password needed).
ansible-playbook -i ansible/hosts scaffold/ansible/site.yml

# 6. Smoke test the fresh VPS
bash scripts/post-provision-smoke-test.sh myserver
```

This smoke test checks SSH access, sudo, Docker, systemd services, SSH hardening,
UFW rules, and deploy-user setup against the real VPS.

---

## Deploy apps to the server

Once provisioned, clone the repo **on the server** so Docker Compose can read it:

```bash
# SSH into the server first, then:
git clone --recurse-submodules git@github.com:yourorg/server-[name].git /opt/deploy
cd /opt/deploy
cp .env.example .env && $EDITOR .env
# Create .env for each app too (see apps/*/env.example)
docker compose up -d
```

To redeploy after changes:
```bash
ssh myserver ./deploy
```

---

## Add an app

Create a folder under `apps/` with three files:

**`apps/myapp/docker-compose.yml`**
```yaml
services:
  myapp:
    image: yourorg/myapp:latest
    restart: unless-stopped
    env_file: apps/myapp/.env
    networks:
      - caddy

networks:
  caddy:
    external: true
    name: caddy
```

**`apps/myapp/.env.example`** — list every required variable (no values)

**`apps/myapp/myapp.caddy`**
```
myapp.{$DOMAIN} {
    reverse_proxy myapp:3000
}
```

Then add one line to the root `docker-compose.yml`:
```yaml
include:
  - apps/myapp/docker-compose.yml
```

Caddy picks up `myapp.caddy` automatically — no changes to `Caddyfile` needed.

---

## Database backups

Databases are backed up to S3-compatible storage via Restic. Each database
gets its own repository and encryption key. Backups run hourly by default, and
repository verification runs weekly by default.

**Configure:**
```bash
cp backup/config.env.example backup/config.env && $EDITOR backup/config.env

# One file per database:
cp backup/services/service.env.example backup/services/myapp.env
$EDITOR backup/services/myapp.env
```

**Deploy:**
```bash
ansible-playbook -i ansible/hosts ansible/backup.yml

# Then rerun the smoke test in strict backup mode:
bash scripts/post-provision-smoke-test.sh myserver --require-backup
```

**Adding a database** = copy `backup/services/service.env.example` to
`backup/services/newservice.env`, fill it in, re-run the playbook.

**Restore:**
```bash
# On the server:
/opt/backup/restore.sh --service myapp --list
/opt/backup/restore.sh --service myapp --target myapp_test   # safe test restore
/opt/backup/restore.sh --service myapp                       # production restore (prompts)
```

**Trigger a backup now:**
```bash
ssh myserver sudo systemctl start backup.service
ssh myserver journalctl -u backup.service -f

# Manual repository verification:
ssh myserver sudo systemctl start backup-verify.service
ssh myserver journalctl -u backup-verify.service -f
```

**Local backup tests before deploying to a real VPS:**
```bash
cd scaffold
molecule test -s backup
bash backup/tests/integration/run_tests.sh
cd ..
```

---

## Local development

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d
docker compose exec caddy caddy trust   # once per machine — adds CA to keychain
```

---

## Security auditing

Run against a real provisioned VPS (not locally):

```bash
ansible-playbook -i ansible/hosts scaffold/ansible/audit-lynis.yml
ansible-playbook -i ansible/hosts scaffold/ansible/audit-openscap.yml
ansible-playbook -i ansible/hosts scaffold/ansible/audit-docker.yml
```

Reports saved to `reports/` (gitignored).

---

## Update the scaffold

```bash
cd scaffold && git pull origin main && cd ..
git add scaffold
git commit -m "chore: update scaffold"
git push
```

On the server: `./deploy`
