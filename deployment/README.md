# Deployment notes

Run `provision_host.sh inigo` on the NUC to verify host prerequisites, permissions, and directory layout. Copy `compose.env.example` to the deployment Compose directory and use a deployment-local `.env`; never commit model paths, device IDs, or machine-specific values.

The archive workflow is deliberately two-stage: `scripts/inspect_vault.py` validates and inventories the ZIP, then `scripts/import_vault.py` extracts to a temporary staging directory and refuses to merge into a non-empty destination. Run `scripts/init_vault_git.py` and verify the initial commit before starting write-capable services.

The default no-setup URL is `http://192.168.1.117/`. Caddy also provides the encrypted `https://tiny-brain.home.arpa` route, but clients using that route must resolve the hostname and trust the Caddy internal CA. The IP-based HTTP route is intentionally limited to the trusted home LAN and is unencrypted; do not expose it through the internet or an untrusted network. Syncthing's GUI stays on localhost; pair devices separately and preserve conflict files. The application does not treat Syncthing as an authorization boundary.

Before enabling automatic Inbox writes, benchmark the model and embeddings, record checksums and versions, and run verification benchmarks and tests. `backup.sh` writes independent vault, operational-data, Compose, and Git bundle backups.
