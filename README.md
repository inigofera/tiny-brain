# Tiny Brain

Tiny Brain is a local, safety-first interface to an Obsidian vault. Markdown remains the source of truth; SQLite, FTS5, embeddings, audit records, and model state are derived or operational data.

## Current implementation

The repository contains:

- Safe relative-path validation and symlink/traversal protection.
- YAML frontmatter, heading, tag, Markdown-link, and Obsidian-wikilink parsing.
- Deterministic heading-aware chunks with bounded oversized-section splitting.
- WAL SQLite schema with FTS5, relationships, embeddings, sessions, proposals, audit, and indexing state.
- Incremental and full vault indexing with last-known-good failure behavior.
- Lexical retrieval, optional local FastEmbed semantic retrieval, reciprocal-rank fusion, bounded link expansion, context budgets, and source citations.
- Local llama.cpp OpenAI-compatible client.
- Conservative memory gate and provenance-bearing AI Inbox entries.
- Proposal-only normal writes, hash validation, three-way merge conflict handling, atomic replacement, re-indexing, JSON/SQLite audit, and focused Git commits.
- FastAPI JSON API and server-rendered browser pages for chat, search, notes, changes, and status.
- Compose deployment for app, llama.cpp, Caddy internal HTTPS, and Syncthing.

The code is intentionally usable in read-only mode before a model is installed. Normal writes require a Git repository and deployment author identity.

## Local development

```powershell
python -m pip install -e ".[test]"
python -m pytest
python scripts/inspect_vault.py obsidian-20261007T153549Z-1-001.zip
```

For a temporary local vault:

```powershell
$env:TINY_BRAIN_VAULT_ROOT = "$PWD\vault"
$env:TINY_BRAIN_DATABASE = "$PWD\data\database.sqlite"
python scripts/index_vault.py $env:TINY_BRAIN_VAULT_ROOT $env:TINY_BRAIN_DATABASE
python -m tiny_brain.main
```

## Deployment outline

1. Perform the host discovery listed in `final_plan.md`; do not assume Docker, firewall, Caddy, Syncthing, storage, or model files exist.
2. Create `/srv/tiny-brain/{vault,data,models,staging,compose}` on the NUC with the discovered UID/GID.
3. Copy the archive to staging, inspect it with `scripts/inspect_vault.py`, reject unsafe entries, and extract only after inspection.
4. Initialize Git in the canonical vault, configure a deployment author, and create the initial import commit before enabling writes.
5. Copy `deployment/compose.env.example` to the deployment compose directory, review paths and pin a verified llama.cpp image digest/release.
6. Put the verified Spark Q6 GGUF in the model directory and record its SHA-256.
7. Start the stack with Docker Compose, trust Caddy's internal CA on client devices, and run the initial index from the app container.
8. Pair Syncthing separately. Keep its GUI bound to localhost; do not publish it through Caddy.
9. Benchmark Q6 and the two embedding runtimes on representative notes before enabling automatic Inbox writes.

## Security and operations

This first release deliberately has no application authentication. Anyone who can reach the trusted LAN HTTPS endpoint can use the write-capable approval workflow. Caddy's internal TLS encrypts traffic and authenticates the service certificate; it does not identify users. Do not expose the Caddy ports to the public internet.

The model has no arbitrary shell, filesystem, Git, delete, move, or bulk-rewrite tool. Application code validates every path and mutation. If a write succeeds but indexing or Git fails, the proposal is `recovery_required` and must be resolved before claiming success.

Back up the vault, SQLite database, JSON audit directory, Git repository, Compose configuration, Caddy data, Syncthing configuration, and model checksums separately. The index can be rebuilt from Markdown; the vault cannot be reconstructed from the index.
