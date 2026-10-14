---
title: Tiny Brain - Local AI Second Brain
status: finalized
date: 2026-10-07
target_host: 192.168.1.117
target_os: Arch Linux
deployment: Docker Compose
---

# Tiny Brain: Final Implementation Plan

## 1. Purpose and Scope

Tiny Brain is a private, local AI assistant that provides a conversational interface to an Obsidian vault. It is not intended to be a general-purpose hosted chatbot or an autonomous system with unrestricted access to the machine. Its purpose is to help the user find, understand, connect, and carefully update information in a personal knowledge base.

The system will run on the home server at `192.168.1.117`, using the Linux account `inigo`. It will use Docker Compose for deployment, Spark-X2.5-1.7B through llama.cpp for conversational reasoning, SQLite for local application state and search metadata, and the Obsidian vault as the canonical source of knowledge.

The first usable release includes the complete planned workflow, delivered in independently testable phases:

1. Import and inspect the vault.
2. Synchronize the vault with other Obsidian devices.
3. Index Markdown notes, metadata, links, tags, and sections.
4. Search with lexical and semantic retrieval.
5. Answer questions with grounded source citations.
6. Expose a constrained tool system to Spark.
7. Classify durable information from conversations.
8. Automatically append only conservative, high-confidence items to an AI Inbox.
9. Create proposals for all other supported note mutations.
10. Review, approve, reject, merge, and revert changes through a browser UI.
11. Maintain SQLite and JSON audit records.
12. Create focused Git commits for approved changes.
13. Re-index changed notes without scanning the entire vault each time.
14. Run entirely locally after setup, with no runtime dependency on external AI services.

This is a full-scope plan delivered in phases, not a plan to postpone writes, memory, audit, or the UI indefinitely. Each phase has a testable exit condition, and the system is not considered ready for first use until all release criteria pass.

## 2. Confirmed Decisions

### 2.1 Target host

- Host: `192.168.1.117`
- SSH user: `inigo`
- Operating system: Arch Linux
- CPU: Intel Core i7-8550U, four cores and eight threads
- Memory: 16 GB RAM
- GPU: integrated Intel graphics
- Initial inference mode: CPU-only
- Primary constraint: response latency and memory pressure matter more than maximum context size.

The first deployment must inspect the actual host before assuming that Docker, Compose, Caddy, Syncthing, firewall rules, storage paths, or model files already exist.

### 2.2 Deployment

- Deployment technology: Docker Compose.
- Deployment root: `/srv/tiny-brain`.
- Persistent vault path: `/srv/tiny-brain/vault`.
- Persistent application data path: `/srv/tiny-brain/data`.
- Model cache path: `/srv/tiny-brain/models`.
- Compose/deployment configuration path: `/srv/tiny-brain/compose`.
- Temporary archive staging path: `/srv/tiny-brain/staging`.

The repository contains source code and reproducible configuration. Persistent vault and model data are mounted into containers and are not built into application images. The `.env` file may contain local deployment settings, but secrets and machine-specific values must not be committed.

### 2.3 Vault source

The initial vault seed is:

`c:\src\tiny-brain\obsidian-20261007T153549Z-1-001.zip`

The archive must be inspected and extracted without changing its contents. The deployment must identify the actual vault root inside the archive because the archive may contain a wrapper directory.

The complete vault is preserved on the NUC. The first release indexes Markdown notes and their relationships; it does not semantically index binary attachments. Non-Markdown files remain available in the vault for Obsidian and synchronization.

### 2.4 Synchronization

Syncthing synchronizes the NUC vault copy with the user's other Obsidian devices. The NUC is the canonical working copy for Tiny Brain's indexing and AI workflows.

This means:

- The assistant reads and writes `/srv/tiny-brain/vault`.
- The NUC is authoritative for assistant-created changes.
- Other devices may still edit notes, so the application must detect concurrent changes.
- Syncthing conflicts must be preserved and surfaced rather than silently overwritten.
- The indexer must ignore temporary files, Syncthing metadata, and generated service directories.
- The assistant must never use synchronization behavior as a substitute for application-level change validation.

The NUC authority is a workflow convention, not permission to destroy a newer user edit. A proposal must always verify the current file before applying.

### 2.5 Source of truth

The Markdown vault remains the canonical knowledge source. The following are derived or operational data:

- SQLite metadata.
- FTS5 indexes.
- Vector embeddings.
- Parsed frontmatter and link tables.
- Conversation state.
- Pending proposals.
- Audit records.
- Model metadata.
- Retrieval diagnostics.

The vault must remain readable and useful in Obsidian if Tiny Brain is stopped, removed, or unavailable. No knowledge must exist only in SQLite.

### 2.6 Chat model

- Primary model family: Spark-X2.5-1.7B.
- Initial quantization: Q6_K GGUF.
- Runtime: llama.cpp.
- Required llama.cpp support: `b10828` or later.
- API boundary: llama.cpp runs as a separate internal service using an OpenAI-compatible HTTP API.
- Initial execution mode: CPU-only.
- Q4_K_M and Q8_0 are deferred benchmark alternatives.

The model file must be mounted read-only into the llama.cpp container. The application must communicate with llama.cpp over the internal Compose network and must not manage arbitrary model files through the browser or model tool interface.

### 2.7 Context window

Spark may advertise a very large context capability, but Tiny Brain must not attempt to load the entire vault into the prompt. The initial context budget is bounded to a measured range of approximately 8K to 32K tokens. The final value is selected from NUC benchmarks.

The context assembler must:

- Prefer complete semantic sections over arbitrary fragments.
- Avoid duplicate chunks.
- Include heading paths and note identifiers.
- Reserve space for system instructions, the user request, tool calls, and the answer.
- Truncate by section boundaries where possible.
- Report when relevant context was omitted because of the budget.

### 2.8 Embeddings

The chat model is not responsible for embeddings. The initial embedding model is `BAAI/bge-small-en-v1.5`, because the vault is expected to be primarily English.

The implementation must benchmark two local execution approaches on representative vault content:

1. Lightweight ONNX/FastEmbed-style runtime.
2. Standard sentence-transformers runtime.

The selected runtime must be recorded in the deployment manifest with its model version, embedding dimension, normalization behavior, measured resource usage, and offline behavior.

Model and package downloads are allowed during setup. Once the system is running, the application must not call hosted embedding APIs, hosted language models, telemetry services, or other external AI services.

### 2.9 Search and vector storage

The first release uses hybrid retrieval:

- SQLite FTS5 for exact and lexical search.
- SQLite plus `sqlite-vec` for semantic nearest-neighbor search.
- Reciprocal-rank fusion to combine lexical and semantic candidates.
- Bounded graph expansion through wikilinks, backlinks, shared tags, and neighboring sections.

No dedicated vector database is used. The first release does not introduce Postgres, Redis, Elasticsearch, Milvus, Weaviate, Kafka, Kubernetes, or another infrastructure platform.

### 2.10 Browser UI

The first release uses a small server-rendered web interface implemented with FastAPI and Jinja2, with lightweight browser-side JavaScript for streaming and interactions. There is no separate React or Vite application in the first release.

The UI must support:

- Chat input and streamed responses.
- Source citations and retrieved excerpts.
- Search results.
- Safe note inspection.
- Pending proposal review.
- Approval and rejection.
- Conflict display.
- Revert workflow.
- Audit history.
- Memory classification visibility.
- Indexing and service health.

### 2.11 Network and access

The service is intended for the trusted home LAN only.

- Caddy is the LAN-facing entry point.
- Caddy uses its internal certificate authority.
- The service must not be exposed to the public internet.
- Internal service ports must not be exposed unnecessarily.
- The browser endpoint uses a local HTTPS hostname such as `tiny-brain.home.arpa`.
- Client devices must be configured to trust the Caddy internal CA.

By decision, the first release has no application authentication. Anyone who can reach the trusted LAN endpoint can use the write-capable interface. This is an explicit and significant risk. The zero-setup URL is `http://192.168.1.117/`, which is intentionally unencrypted and must remain limited to the trusted home LAN. Caddy also provides `https://tiny-brain.home.arpa` for clients that can resolve the hostname and trust the Caddy internal CA. Caddy's internal TLS encrypts the HTTPS route and authenticates the service certificate, but it does not identify the user or authorize operations.

The deployment documentation must make this limitation prominent. The architecture must leave room to add authentication later without changing the core tool and mutation services.

### 2.12 Conversation retention

Only the current conversation session is retained in SQLite.

The application may retain minimal operational metadata after a session ends, such as:

- Stable source message identifier.
- Timestamp.
- Classification result.
- Confidence.
- Provenance identifier.
- Retrieval and change identifiers.

The application must not retain full historical chat transcripts or full tool payloads indefinitely. Approved note changes and Inbox entries retain enough provenance to explain why they exist without turning the application database into a second permanent vault.

### 2.13 Memory extraction

Memory extraction runs after every user message. Each message may be classified as:

- `ephemeral`
- `working_memory`
- `user_fact`
- `decision`
- `project_information`
- `idea`
- `task`
- `reference`
- `permanent_knowledge`

The automatic write gate is conservative:

- High-confidence user facts, decisions, and project information may be appended to `_AI/Inbox.md`.
- Ephemeral and working memory are never written to the vault automatically.
- Ideas, tasks, references, and low-confidence candidates remain session state or a reviewable internal result.
- A candidate must be deduplicated against recent Inbox content before it can be appended.
- Every automatic Inbox append includes a dated readable entry and a stable provenance identifier.
- Full machine metadata remains in SQLite and JSON audit data instead of creating noisy visible Markdown comments.

The model may classify a message, but application code enforces the write gate. The model cannot bypass the gate by emitting a tool call directly.

### 2.14 Write approval

Read tools are automatic. General write operations are not.

The following operations create pending changes:

- Create a note.
- Append to a normal note.
- Update frontmatter.
- Add or remove tags.
- Add or change links.
- Propose an edit to existing content.

Only the dedicated, conservative `_AI/Inbox.md` path may append automatically, and only when the memory gate accepts the candidate.

The following operations do not exist in the first release:

- Automatic deletion.
- Automatic note moves.
- Bulk rewrites.
- Arbitrary file writes.
- Arbitrary shell commands.
- Silent rewriting of permanent historical facts.

Before proposing a change to an existing note, the assistant must inspect the current note and understand its structure. The application must validate the target path and operation independently of the model's system prompt.

### 2.15 Audit and provenance

Every AI modification must have both:

1. A structured SQLite record for querying in the UI.
2. A JSON audit record for portable inspection and recovery.

Each record should contain, as applicable:

- Change identifier.
- Timestamp.
- Session identifier.
- Source message identifier.
- Operation.
- Target relative path.
- Reason.
- Memory category and confidence.
- Base content hash.
- Current content hash at approval time.
- Proposed content or patch.
- Before content or recovery reference.
- After content or resulting hash.
- Approval state.
- Conflict state.
- Indexing result.
- Git commit identifier.
- Error information.

The system must be able to answer why a piece of information was added and whether it came from a conversation, a source note, or an explicit user-approved operation.

### 2.16 Git

Git is required before first use.

The deployment must:

1. Initialize or provision a local Git repository for the NUC vault.
2. Configure a deployment-supplied author identity.
3. Create an initial commit before AI writes are enabled.
4. Create a focused commit after every approved AI modification, once the file write and re-index have succeeded.
5. Store the resulting commit identifier in the audit record.

A remote repository is optional and is not required for the first release. Git configuration is deployment data, not a model capability. The model must never receive a general Git or shell command tool.

The write pipeline must account for the fact that a filesystem write, SQLite transaction, index update, and Git commit cannot be one atomic transaction. It must use explicit operation states such as `proposed`, `approved`, `write_applied`, `indexed`, `committed`, `failed`, and `recovery_required`.

### 2.17 Conflict policy

Every proposal stores the exact base content and base hash from which it was generated.

On approval, the application compares the current note with the stored base:

- If the hash is unchanged, apply the proposed change atomically.
- If the hash changed, perform a three-way merge using base content, current content, and proposed content.
- If the merge is clean, show the merged result and continue through the approval/apply workflow.
- If the merge conflicts, keep the proposal pending and display a conflict diff.
- Never overwrite a newer user or Syncthing edit automatically.
- Require regeneration or explicit manual resolution before a conflicted proposal can be applied.

Syncthing conflict files are preserved. The indexer and UI must surface them as operational conflicts rather than treating them as ordinary knowledge notes.

### 2.18 Performance target

The target for warm retrieval and streaming chat is a usable response under 10 seconds on the NUC.

The benchmark must separate:

- Cold startup time.
- Warm startup time.
- Vault retrieval latency.
- Embedding query latency.
- Time to first token.
- Completion token rate.
- Full answer latency.
- Tool-call reliability.
- Peak RAM.
- CPU utilization.
- Context size.
- Failure and timeout rates.

The target is measured, not assumed. If the target is missed, the deployment must document the measured result and tuning decision before first use.

## 3. High-Level Architecture

```text
												 Trusted home LAN
															 |
															 v
										+------------------------+
										| Caddy internal HTTPS   |
										| tiny-brain.home.arpa   |
										+-----------+------------+
																|
																v
										+------------------------+
										| FastAPI application    |
										| server-rendered UI     |
										| chat, search, changes  |
										+----+---------+---------+
												 |         |
						 OpenAI API |         | local storage
												 |         |
												 v         v
								+------------+  +----------------+
								| llama.cpp |  | SQLite         |
								| Spark Q6  |  | FTS5           |
								+------------+  | sqlite-vec     |
																| audit/state    |
																+-------+--------+
																				|
																				v
															+-------------------+
															| Obsidian vault    |
															| Markdown source   |
															+---------+---------+
																				^
																				|
															+---------+---------+
															| Syncthing devices |
															+-------------------+
```

The application is the policy boundary. Spark reasons and requests tools, but the application validates every tool request, performs path checks, enforces permissions, writes audit records, and controls the approval workflow.

### 3.1 Compose services

#### `app`

Responsibilities:

- FastAPI HTTP server.
- Server-rendered browser UI.
- Chat orchestration.
- Retrieval pipeline.
- Model tool loop.
- Vault watcher and indexing queue.
- Memory extraction.
- Proposed changes and approvals.
- SQLite access.
- Git operation orchestration.
- Health and status endpoints.

The application mounts the vault and data directories with only the permissions it needs. It must not be given access to the host filesystem outside the deployment root and must not mount the Docker socket.

#### `llama`

Responsibilities:

- Load the Spark-X2.5 Q6 GGUF model.
- Serve the OpenAI-compatible local inference API.
- Run CPU-only with bounded context and configured threads.

The service is not directly exposed to the LAN. It is reachable only from the application service over the Compose network.

#### `caddy`

Responsibilities:

- LAN-facing HTTPS endpoint.
- Internal CA and local certificate.
- Reverse proxy to the application.
- No public internet exposure.

Caddy must not proxy arbitrary paths to arbitrary internal services.

#### `syncthing`

Responsibilities:

- Synchronize the complete vault with the user's other Obsidian devices.

The Syncthing UI/API must not be exposed through the Tiny Brain public route. Its access and device pairing must be configured separately and documented.

### 3.2 Persistent filesystem layout

```text
/srv/tiny-brain/
	compose/
		compose.yaml
		Caddyfile
		.env
	vault/
		... complete Obsidian vault ...
	data/
		database.sqlite
		audit/
			YYYY/MM/change-*.json
		backups/
		logs/
	models/
		chat/Spark-X2.5-1.7B-Q6_K.gguf
		embeddings/...
	staging/
		... temporary archive extraction ...
```

The exact ownership and container UID/GID mapping must be discovered on the NUC. The vault must remain accessible to Syncthing and the application without granting either service unnecessary host privileges.

## 4. Initial Host and Vault Provisioning

### 4.1 Host discovery

Before building application features, inspect the target host over SSH and record:

- Arch Linux version and kernel.
- CPU model, available threads, RAM, and swap.
- Disk capacity, free space, and filesystem type.
- Docker Engine version.
- Docker Compose version.
- Existing containers.
- Existing listeners and firewall rules.
- Existing Caddy installation or container.
- Existing Syncthing installation or container.
- Available LAN hostname and DNS behavior.
- User/group IDs needed for mounted volume permissions.
- Git version and configured identity.
- Whether the host can access the LAN peers used for Syncthing.

Provisioning must be idempotent. Re-running it must not destroy the vault, reset the database, overwrite an existing Git repository, or replace user configuration without an explicit operation.

### 4.2 Archive inspection

The archive import must:

1. Validate that the archive can be read.
2. List entries before extraction.
3. Reject archive path traversal.
4. Identify the vault root.
5. Count Markdown notes.
6. Measure total archive and extracted size.
7. Detect duplicate paths and suspicious entries.
8. Record attachments and non-Markdown content.
9. Inspect representative frontmatter, tags, aliases, headings, wikilinks, and templates.
10. Extract into staging first.
11. Copy into the canonical vault only after inspection succeeds.

The archive must not be modified during inspection.

### 4.3 Git initialization

Before the application can execute AI write tools:

1. Initialize or provision the vault repository if one does not exist.
2. Add an appropriate `.gitignore` for application metadata while preserving desired Obsidian files.
3. Configure the author identity from deployment configuration.
4. Review the initial file list.
5. Create the initial import commit.
6. Record the commit identifier in deployment state.

The system must never silently discard an existing repository or rewrite history.

### 4.4 Syncthing configuration

Syncthing setup must document:

- Device IDs.
- Folder ID.
- Folder path.
- Folder type on each device.
- Ignore patterns for temporary files and service metadata.
- Conflict file behavior.
- Whether remote devices may send changes to the NUC.
- Backup expectations.

## 5. Vault Parsing and Indexing

### 5.1 Supported input

The first release parses Markdown files under the vault root. It preserves other files but does not semantically embed binary attachments.

The parser must handle:

- YAML frontmatter.
- Markdown headings.
- Obsidian wikilinks.
- Wikilink aliases and heading targets where practical.
- Standard Markdown links.
- Relative links.
- Tags in frontmatter and body where practical.
- Code blocks without falsely treating their contents as links or headings.
- Empty notes.
- Notes without frontmatter.
- Unicode content already present in the vault.

The implementation should preserve source text and offsets rather than rewriting notes during indexing.

### 5.2 Exclusions

The indexer must exclude from knowledge chunks:

- `.obsidian` service metadata.
- Tiny Brain's own generated directories.
- Temporary editor files.
- Syncthing metadata.
- Git internals.
- Conflict files, unless surfaced explicitly as operational conflicts.

The complete vault remains preserved even when a file is excluded from indexing.

### 5.3 Frontmatter extraction

Extract and store:

- `title`.
- `tags`.
- `aliases`.
- `created`.
- `modified`.
- `status`.
- Other frontmatter fields as structured JSON.

Unknown fields must be preserved. The parser must not normalize or rewrite user frontmatter merely because a note was indexed.

### 5.4 Heading-aware chunking

Notes are naturally hierarchical. Chunks should correspond to meaningful sections:

```text
Note
|-- Overview
|-- Hardware
|-- Model selection
|-- Software architecture
|-- Experiments
`-- Decisions
```

Each section record stores:

- Note ID.
- Relative path.
- Display title.
- Heading path.
- Heading level.
- Section order.
- Content.
- Character offsets.
- Approximate token count.
- Previous and next section identifiers.
- Content hash.
- Embedding model/version.

The chunker must keep sections intact when they fit the budget and split oversized sections only when necessary. Split sections use a small overlap while retaining the original heading path. Unchanged content must produce deterministic chunks.

### 5.5 Incremental indexing

The indexer has two modes:

1. Full initial scan.
2. Incremental changed-note scan.

For a changed file:

1. Receive a filesystem event.
2. Debounce duplicate events.
3. Confirm the path is in the vault and allowed.
4. Hash the current content.
5. Skip if the hash is unchanged.
6. Parse the Markdown.
7. Remove or replace only that note's old sections, links, tags, FTS rows, and embeddings in one transaction.
8. Generate embeddings for changed sections.
9. Commit the index transaction.
10. Mark the job complete and expose status to the UI.

If parsing or embedding fails, preserve the last known good index, record the failure, and show the note as stale rather than deleting searchable data.

### 5.6 Watcher behavior

The watcher must be resilient to editor save patterns that emit multiple events. It must handle file creation, modification, rename, and deletion conservatively.

- A deletion removes derived index records only after the file is confirmed absent and the event is not temporary.
- The AI has no delete tool.
- An externally observed deletion is recorded if it affects indexed data.
- A move is treated as delete plus create only when the filesystem state is stable.
- A symlink is not followed.
- A path outside the vault root is ignored.
- Temporary and conflict files are not treated as ordinary notes.

## 6. Database Design

SQLite is the operational database. It must use WAL mode, foreign keys, explicit migrations, busy timeouts, and parameterized queries.

### 6.1 `notes`

Stores one row per indexed Markdown note:

- Stable note ID.
- Normalized relative path.
- Display title.
- Frontmatter JSON.
- Alias data.
- Tags data.
- Content hash.
- Byte size.
- Modified time.
- Last successful index time.
- Index status.
- Conflict status.
- Parse error if applicable.

Relative paths are stable identities. Absolute paths are derived from the configured vault root and are never accepted from model input.

### 6.2 `sections`

Stores heading-aware chunks with note ID, heading path, heading level, order, content, offsets, token estimate, content hash, and embedding status.

### 6.3 `fts_sections`

An FTS5 virtual table containing searchable section content and selected metadata such as title, path, heading path, aliases, and tags.

### 6.4 `section_embeddings`

Stores or references vector data for each section, together with model name, runtime, dimension, normalization mode, content hash, and generation timestamp. Incompatible dimensions or model versions must never be mixed silently.

### 6.5 `links` and `backlinks`

Stores outgoing links, resolved targets, unresolved targets, aliases, headings, and source sections. Backlink queries are derived from outgoing links or maintained as a synchronized index.

### 6.6 `tags`

Stores normalized tags and note relationships while retaining the original display spelling.

### 6.7 `sessions` and `messages`

Stores current-session messages, roles, timestamps, tool references, retrieval references, and memory references. Full historical transcripts are not retained indefinitely.

### 6.8 `retrieval_events`

Stores bounded diagnostics such as query ID, candidate IDs, rank reasons, citation IDs, latency, and model configuration. This table must not become an unbounded transcript archive.

### 6.9 `memory_candidates`

Stores category, confidence, claim, subject, related notes, source message ID, gate decision, reason, duplicate match, and resulting Inbox change ID.

### 6.10 `change_proposals`

Stores operation, target path, description, reason, base hash, base content or reference, proposed content or patch, current hash, state, conflict data, timestamps, and Git commit ID.

### 6.11 `audit_events`

Stores append-only modification history. Portable JSON copies are written under date-based paths in the data directory.

### 6.12 `index_jobs`

Stores queued, running, completed, skipped, and failed indexing jobs with paths, causes, attempts, errors, and timestamps.

### 6.13 `model_metadata`

Stores model checksums, quantization, llama.cpp version, embedding model/runtime, context configuration, and benchmark results.

## 7. Retrieval Pipeline

The retrieval path is:

```text
User question
		|
		+--> query normalization
		|
		+--> FTS5 lexical search
		|
		+--> query embedding
		|        |
		|        `--> sqlite-vec search
		|
		+--> reciprocal-rank fusion
		|
		+--> bounded graph expansion
		|
		+--> deduplication and reranking
		|
		+--> token-budgeted context assembly
		|
		`--> citation-labeled context for Spark
```

### 7.1 Lexical search

FTS5 provides exact and lexical search with BM25 ranking. The query layer must safely handle technical terms, phrase queries, aliases, tags, titles, and heading paths.

Examples that must remain searchable include:

- `Spark-X2.5`.
- `i7-8550U`.
- `Docker`.
- `Kubernetes`.
- `Proxmox`.

### 7.2 Semantic search

The embedding provider generates local query vectors using the selected model and runtime. It searches the sqlite-vec index for nearest sections and records the model version used.

If the embedding model changes, the application must schedule controlled re-embedding and must not combine incompatible vectors.

### 7.3 Hybrid ranking

The first ranking strategy is:

1. Retrieve lexical candidates.
2. Retrieve vector candidates.
3. Normalize candidate identity by section and note.
4. Apply reciprocal-rank fusion.
5. Apply bounded exact title, alias, or phrase boosts.
6. Add bounded graph-expanded candidates.
7. Apply lower rank or decay to graph-only candidates.
8. Deduplicate sections and notes.
9. Select context under the configured token budget.

Every selected context item includes the reason it was selected: lexical match, semantic match, backlink, outgoing link, shared tag, or neighboring section.

### 7.4 Graph expansion

After initial retrieval, expansion may consider:

- Outgoing wikilinks.
- Backlinks.
- Shared tags.
- Same-note neighboring sections.
- Related sections from the same project path where configured.

Graph expansion must not flood context with an entire connected component. Expanded items compete for the same budget as direct matches.

### 7.5 Context assembly and citations

Each context block should be labeled with a stable citation ID:

```text
[SOURCE S1]
Path: Projects/NUC AI Server.md
Title: NUC AI Server
Heading: Model decisions
Relationship: lexical match
Content:
...
[/SOURCE S1]
```

The model must use only returned sources for factual vault claims, identify uncertainty, and cite source IDs in its answer. The application validates every citation against the retrieval result and renders it through a safe note route.

## 8. Chat Agent and Tools

### 8.1 System prompt rules

Spark's system prompt establishes that it is the local assistant for the user's Obsidian knowledge base. It must instruct Spark to:

1. Prefer retrieved information over assumptions.
2. Cite notes used to answer factual questions.
3. Never invent notes, paths, tags, decisions, or relationships.
4. Search before answering questions about the vault when context is insufficient.
5. Inspect a note before proposing a mutation.
6. Prefer existing notes over duplicate new notes.
7. Prefer appending dated history over rewriting established facts.
8. Distinguish ideas, tasks, facts, and decisions.
9. Never delete, move, or bulk rewrite notes.
10. Request or create a proposal for normal writes.
11. Use existing naming, organization, and linking conventions.
12. Explain uncertainty instead of guessing.

The prompt is not a security boundary. Application code enforces the dangerous rules independently.

### 8.2 Read tools

```text
search_notes(query: string, limit: integer)
read_note(path: string)
find_backlinks(path: string, limit: integer)
find_related_notes(path: string, limit: integer)
find_notes_by_tag(tag: string, limit: integer)
find_recent_notes(limit: integer)
```

Read tools return structured results with safe paths, stable IDs, content limits, and citation metadata. They never return arbitrary host paths.

### 8.3 Controlled write tools

```text
create_note(path: string, content: string)
append_to_note(path: string, content: string)
update_frontmatter(path: string, changes: object)
propose_edit(path: string, description: string, proposed_content: string)
```

These tools do not directly apply normal writes. They create validated proposals. The application decides whether a request is eligible for the automatic Inbox path or must wait for approval.

### 8.4 Tool enforcement

For every tool call, the application must:

- Validate the schema.
- Enforce maximum string and content sizes.
- Normalize and validate the relative path.
- Reject absolute paths and traversal.
- Reject symlinks that escape the vault.
- Enforce the operation allowlist.
- Enforce the target file-type policy.
- Inspect the current file when required.
- Record the tool result and policy decision.
- Limit tool-loop iterations.
- Return controlled errors to Spark.

There is no `run_command`, `write_file`, `delete_file`, or arbitrary shell tool.

## 9. Memory Workflow

After every user message, the application performs a separate memory extraction pass. The extractor returns structured candidates, not direct file writes.

Example candidate:

```json
{
	"category": "decision",
	"confidence": 0.94,
	"subject": "NUC model",
	"claim": "Use Spark-X2.5 Q6 as the initial chat model",
	"related_paths": ["Projects/NUC AI Server.md"],
	"source_message_id": "..."
}
```

The extractor must be allowed to return no candidate when a message contains no durable information.

### 9.1 Deterministic gate

The application gate evaluates:

- Category.
- Confidence threshold.
- Duplicate similarity.
- Whether the statement is explicit or speculative.
- Whether a related note exists.
- Whether the candidate would overwrite or contradict existing content.
- Whether the target is exactly `_AI/Inbox.md`.

Only high-confidence durable facts, decisions, and project information may enter the automatic Inbox append path. Ideas, tasks, references, and uncertain candidates remain visible in session state or a review queue, but do not become automatic vault content.

### 9.2 Inbox format

Automatic entries should be dated and readable:

```markdown
## 2026-10-07

### Decision: initial chat model

The initial chat model for the NUC will be Spark-X2.5-1.7B Q6_K.

Provenance: `memory-...`
```

Machine details such as classifier version, confidence, source message identifier, and gate decision remain in SQLite and JSON records.

### 9.3 Deduplication

Before appending, compare the candidate with:

- Recent Inbox entries.
- The proposed target note if one exists.
- Lexical matches for the subject and claim.
- Semantic matches for equivalent claims.

If the candidate is likely duplicated or contradicts existing content, do not append automatically. Convert it to a reviewable proposal or report the ambiguity.

## 10. Controlled Mutation Workflow

The normal mutation workflow is:

```text
User statement or explicit request
		|
		v
Search for relevant notes
		|
		v
Read candidate note
		|
		v
Generate structured proposal
		|
		v
Validate path, operation, and base hash
		|
		v
Show diff and provenance in UI
		|
		+--> Reject -> record rejection, no file change
		|
		`--> Approve
						 |
						 v
			 Verify current hash
						 |
						 +--> unchanged -> apply
						 |
						 +--> changed -> three-way merge
						 |                 |
						 |                 +--> clean -> review/apply
						 |                 `--> conflict -> remain pending
						 |
						 v
			 Atomic file replacement
						 |
						 v
			 Re-index changed note
						 |
						 v
			 Create focused Git commit
						 |
						 v
			 Complete audit record
```

### 10.1 Proposal states

Proposals must support at least:

- `pending`
- `approved`
- `rejected`
- `stale`
- `conflict`
- `write_applied`
- `indexed`
- `committed`
- `failed`
- `recovery_required`
- `reverted`

### 10.2 Approval sequence

1. Load the proposal.
2. Confirm it is still pending.
3. Resolve the target path safely.
4. Read current content.
5. Compare the current hash with the proposal base hash.
6. Apply directly if unchanged.
7. Otherwise calculate a three-way merge.
8. Stop and show a conflict if the merge is not clean.
9. Write the result atomically.
10. Re-read and hash the resulting file.
11. Re-index the affected note.
12. Write the SQLite audit event.
13. Write the JSON audit record.
14. Create a focused Git commit.
15. Mark the proposal complete and expose the commit ID.

If a filesystem write succeeds but indexing or Git fails, the operation must enter `recovery_required` and be shown in the UI. It must not be reported as fully successful.

### 10.3 Atomic writes

Writes use a temporary file in the same filesystem, flush and close it, preserve appropriate permissions, and atomically replace the target. The application must not leave a partially written Markdown note after an interruption.

### 10.4 Revert

Revert is an audited operation. It uses stored before-state or a Git reference, verifies that the current file has not changed unexpectedly, applies the same atomic-write and conflict protections, re-indexes the note, and creates a new Git commit. Revert never deletes audit history.

## 11. Web UI

### 11.1 Chat page

The chat page shows:

- Current-session conversation.
- Streaming assistant response.
- Source citations.
- Retrieval excerpts.
- Tool activity in a compact inspectable form.
- Memory extraction result after each message.
- Pending proposal notifications.
- Health errors when model or index services are unavailable.

### 11.2 Search page

The search page supports natural-language and exact-term queries. Each result shows note title, path, heading path, match reason, tags, and whether the match was lexical, semantic, or graph-derived.

### 11.3 Note page

The note page shows current Markdown content, frontmatter, backlinks, outgoing links, tags, index status, and recent AI changes. It must never accept an arbitrary filesystem path from the browser.

### 11.4 Changes page

The changes page shows proposal ID, target note, operation, reason, provenance, base version, current version, before/after diff, approval status, merge/conflict status, Git commit ID, and valid approve, reject, regenerate, and revert actions.

### 11.5 Audit page

The audit page allows filtering by date, operation, target path, source, approval state, and Git commit. It shows enough information to explain a change without exposing full retained conversation history.

### 11.6 Status page

The status page shows application health, llama.cpp health, model/runtime versions, migration status, index queue state, full-scan time, pending jobs, Syncthing conflicts, Git state, Caddy certificate guidance, and the LAN/no-auth warning.

## 12. API Surface

The initial API may include:

```text
GET  /health
GET  /status
POST /chat
GET  /sessions/{session_id}
POST /search
GET  /notes/{path}
GET  /notes/{path}/backlinks
GET  /notes/{path}/related
GET  /notes/{path}/history
GET  /changes
GET  /changes/{change_id}
POST /changes/{change_id}/approve
POST /changes/{change_id}/reject
POST /changes/{change_id}/regenerate
POST /changes/{change_id}/revert
GET  /audit
GET  /index/status
POST /index/rebuild
```

The API must use Pydantic schemas, safe relative path types, bounded request sizes, structured error responses, and streaming responses that do not leak internal stack traces or absolute paths.

## 13. Configuration

Configuration must come from environment variables or deployment configuration, never hard-coded paths or secrets.

Expected settings include:

```text
TINY_BRAIN_VAULT_ROOT=/srv/tiny-brain/vault
TINY_BRAIN_DATA_ROOT=/srv/tiny-brain/data
TINY_BRAIN_MODEL_ROOT=/srv/tiny-brain/models
TINY_BRAIN_DATABASE=/srv/tiny-brain/data/database.sqlite
TINY_BRAIN_LLAMA_BASE_URL=http://llama:8080/v1
TINY_BRAIN_CHAT_MODEL=Spark-X2.5-1.7B-Q6_K.gguf
TINY_BRAIN_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
TINY_BRAIN_EMBEDDING_RUNTIME=selected-during-benchmark
TINY_BRAIN_CONTEXT_TOKENS=selected-during-benchmark
TINY_BRAIN_MAX_TOOL_LOOPS=bounded-value
TINY_BRAIN_LAN_HOST=tiny-brain.home.arpa
TINY_BRAIN_AUTO_INBOX_ENABLED=true
TINY_BRAIN_AUTO_INBOX_CONFIDENCE=high
TINY_BRAIN_RUNTIME_OFFLINE=true
```

The repository contains `.env.example` only. The real `.env` remains deployment-local and must not be committed, copied into an image, logged, or sent to the model.

## 14. Planned Repository Files

The implementation is expected to use a structure similar to:

```text
tiny-brain/
|-- final_plan.md
|-- plan.md
|-- README.md
|-- compose.yaml
|-- Dockerfile
|-- pyproject.toml
|-- .env.example
|-- .gitignore
|-- deployment/
|   |-- Caddyfile
|   |-- compose.env.example
|   `-- scripts/
|-- src/
|   `-- tiny_brain/
|       |-- main.py
|       |-- config.py
|       |-- db.py
|       |-- migrations/
|       |-- vault/
|       |   |-- parser.py
|       |   |-- chunker.py
|       |   |-- links.py
|       |   |-- indexer.py
|       |   |-- watcher.py
|       |   `-- paths.py
|       |-- search/
|       |   |-- embeddings.py
|       |   |-- lexical.py
|       |   |-- vector.py
|       |   |-- ranking.py
|       |   `-- retrieval.py
|       |-- agent/
|       |   |-- chat.py
|       |   |-- tools.py
|       |   |-- prompts.py
|       |   `-- citations.py
|       |-- memory/
|       |   |-- extractor.py
|       |   |-- classifier.py
|       |   |-- gates.py
|       |   `-- inbox.py
|       |-- changes/
|       |   |-- service.py
|       |   |-- merge.py
|       |   |-- audit.py
|       |   `-- git.py
|       `-- web/
|           |-- routes.py
|           |-- schemas.py
|           |-- templates/
|           `-- static/
|-- tests/
|   |-- unit/
|   |-- integration/
|   |-- security/
|   `-- fixtures/
`-- scripts/
		|-- inspect_vault.py
		|-- benchmark_model.py
		`-- benchmark_embeddings.py
```

The exact package layout may change during implementation, but the ownership boundaries must remain recognizable: vault parsing, retrieval, agent orchestration, memory, changes, and web presentation must not become one untestable module.

## 15. Implementation Phases

### Phase 0: Host and vault discovery

Tasks:

1. Connect to `inigo@192.168.1.117` over SSH.
2. Record Arch, CPU, RAM, disk, Docker, Compose, firewall, Caddy, Syncthing, and Git state.
3. Transfer and checksum the Obsidian archive.
4. Extract it into staging.
5. Identify the vault root.
6. Inspect note count, total size, directory structure, frontmatter, tags, aliases, links, attachments, templates, hidden directories, and duplicate names.
7. Create the persistent directory layout.
8. Configure permissions and container UID/GID mappings.
9. Configure Syncthing.
10. Initialize Git and create the initial vault commit.

Exit criteria:

- The target host is reachable.
- The archive is readable and its vault root is known.
- Available storage is sufficient for the vault, models, database, and backups.
- The intended LAN hostname and Caddy trust approach are feasible.
- The vault has a recoverable initial Git snapshot.

### Phase 1: Runtime foundation

Tasks:

1. Build or pin the application image.
2. Build or pin llama.cpp at b10828 or later.
3. Place Spark Q6 in the model directory and verify its checksum.
4. Configure internal Compose networking.
5. Configure Caddy and its internal CA.
6. Configure Syncthing volumes and permissions.
7. Add health checks and restart policies.
8. Verify a minimal llama.cpp request.
9. Confirm runtime services do not require external AI APIs.

Exit criteria:

- All services start after a clean deployment.
- Caddy reaches the application over HTTPS from another LAN device.
- The application reaches llama.cpp over the internal network.
- Model files are mounted read-only.
- No public route exposes the llama.cpp API or Syncthing administration.

### Phase 2: Indexing and database

Tasks:

1. Create SQLite migrations.
2. Implement Markdown parsing.
3. Implement frontmatter, tags, aliases, headings, and link extraction.
4. Implement deterministic heading-aware chunking.
5. Implement FTS5 indexing.
6. Install and validate sqlite-vec.
7. Install both candidate embedding runtimes.
8. Run the representative embedding benchmark.
9. Select and pin the embedding runtime.
10. Run the initial full scan.
11. Verify note counts, chunk counts, links, tags, FTS results, and vector results.
12. Enable the debounced file watcher.

Exit criteria:

- The imported vault can be scanned without modifying files.
- Exact searches return expected notes.
- Changed notes are re-indexed individually.
- Parse and embedding errors preserve the last known-good index.
- The UI can show indexing status.

### Phase 3: Retrieval and chat

Tasks:

1. Implement lexical retrieval.
2. Implement semantic retrieval.
3. Implement reciprocal-rank fusion.
4. Implement bounded graph expansion.
5. Implement token-budgeted context assembly.
6. Implement the llama.cpp client and streaming.
7. Implement the system prompt and answer validation.
8. Implement read tools.
9. Implement tool-loop limits and argument validation.
10. Implement the first browser chat and search pages.
11. Test grounded answers against known vault content.

Exit criteria:

- Exact technical questions retrieve correct notes.
- Conceptual questions retrieve semantically relevant notes.
- Answers contain only valid source citations.
- The assistant expresses uncertainty when evidence is insufficient.
- Tools cannot escape the vault or execute arbitrary operations.

### Phase 4: Memory and mutations

Tasks:

1. Implement current-session lifecycle.
2. Implement post-message extraction.
3. Implement deterministic memory gates.
4. Implement duplicate detection.
5. Implement automatic Inbox append.
6. Implement proposal storage.
7. Implement diffs and display data.
8. Implement three-way merge.
9. Implement atomic writes.
10. Implement indexing after writes.
11. Implement SQLite and JSON audit records.
12. Implement Git commit sequencing.
13. Implement rejection, stale, conflict, and revert states.
14. Add changes, audit, and status pages.

Exit criteria:

- Ephemeral and low-confidence content is not written automatically.
- Accepted Inbox entries contain provenance.
- Normal writes always require approval.
- Stale proposals cannot overwrite newer content.
- Approved writes are atomic, indexed, audited, and committed.
- Rejected writes do not change the vault.

### Phase 5: Browser workflow

Tasks:

1. Implement chat page.
2. Implement search page.
3. Implement safe note page.
4. Implement changes/proposals page.
5. Implement audit page.
6. Implement status page.
7. Add citation navigation.
8. Add conflict display.
9. Add LAN and no-auth warnings.

Exit criteria:

- A user can complete the chat-to-citation workflow in the browser.
- A user can approve and reject proposals.
- A user can inspect provenance and Git commit IDs.
- A user can understand indexing and synchronization failures.

### Phase 6: Benchmark and release

Tasks:

1. Run unit tests.
2. Run integration tests against a temporary vault.
3. Run security and path-safety tests.
4. Run a full NUC smoke test from a second LAN device.
5. Benchmark Q6.
6. Review context size and thread configuration.
7. Verify restart persistence.
8. Verify backup and restore.
9. Verify Syncthing edit propagation and incremental indexing.
10. Document the no-auth risk and Caddy certificate trust.
11. Document operational recovery.
12. Enable AI writes only after all release criteria pass.

## 16. Testing Strategy

### 16.1 Parser tests

Test:

- Frontmatter with scalars, lists, and unknown fields.
- Missing frontmatter.
- Multiple heading levels.
- Code blocks containing heading-like and link-like text.
- Wikilinks with aliases.
- Unresolved links.
- Tags in frontmatter and body.
- Empty notes.
- Unicode notes.
- Duplicate titles with distinct paths.

### 16.2 Path and security tests

Test rejection of:

- Absolute paths.
- `..` traversal.
- Encoded traversal.
- Symlinks escaping the vault.
- `.git` targets.
- `.obsidian` writes.
- Generated service directories.
- Non-Markdown writes where unsupported.
- Oversized request bodies.
- Arbitrary shell/tool names.

### 16.3 Indexing tests

Test:

- Initial full scan.
- Unchanged hash skip.
- Single-note modification.
- New note.
- Note deletion.
- Rename.
- Multiple filesystem events for one save.
- Parse failure preserving the previous index.
- Embedding failure and retry.
- Conflict-file handling.

### 16.4 Search tests

Test:

- Exact technical terms.
- Quoted phrases.
- Semantic paraphrases.
- Hybrid ranking.
- Duplicate result removal.
- Graph expansion limits.
- Context token budgets.
- Stable citation IDs.
- Citation rejection for fabricated source IDs.

### 16.5 Mutation tests

Test:

- Proposal creation.
- Approval with unchanged base.
- Rejection.
- Stale base with clean three-way merge.
- Stale base with conflict.
- Atomic-write interruption simulation.
- Index failure after write.
- Git failure after indexing.
- Recovery-required state.
- Revert with unchanged file.
- Revert with unexpected file change.
- Automatic Inbox append.
- Duplicate Inbox candidate.
- Low-confidence candidate rejection.

### 16.6 Integration tests

Use a temporary vault and test database to exercise:

1. Seed notes.
2. Index notes.
3. Search exact and semantic terms.
4. Retrieve related and backlink notes.
5. Ask a question through the chat API.
6. Validate citations.
7. Request a normal write.
8. Inspect and approve the proposal.
9. Verify the Markdown file, index, audit record, and Git commit.
10. Create a concurrent edit.
11. Verify stale proposal handling.
12. Reject or resolve the conflict.
13. Verify external change re-indexing.

### 16.7 NUC smoke test

From a separate LAN device:

1. Trust the Caddy internal CA.
2. Open the HTTPS hostname.
3. Check the no-auth warning.
4. Send an exact-term question.
5. Send a conceptual question.
6. Inspect citations.
7. Inspect a note.
8. Request a normal append and approve it.
9. Confirm the Git commit ID.
10. Add or edit a note through Obsidian on another synchronized device.
11. Confirm Syncthing propagation.
12. Confirm incremental re-indexing.
13. Restart the Compose stack.
14. Confirm the database, vault, audit history, and Git state persist.

## 17. Benchmarking

### 17.1 Spark Q6 baseline

Use representative prompts covering:

- Exact lookup.
- Multi-note synthesis.
- A question with no answer in the vault.
- Read tool use.
- Search followed by note inspection.
- A normal write proposal.
- A memory classification.

Record:

- Model startup time.
- Time to first token.
- Tokens per second.
- Full response time.
- Peak resident memory.
- CPU load.
- Tool-call success rate.
- Citation validity rate.
- Context token count.
- Failure and timeout rate.

Only after the Q6 baseline is known should Q4 or Q8 be considered. A quantization change must be justified by measured quality, speed, memory, and tool reliability.

### 17.2 Embedding comparison

Use a representative sample of actual notes and known-answer questions. Compare FastEmbed/ONNX and sentence-transformers on:

- Install size.
- Model load time.
- Per-note embedding time.
- Query embedding time.
- Peak RAM.
- CPU use.
- Retrieval quality.
- Dependency stability.
- Offline repeatability.

The selected runtime must be local, reproducible, and compatible with the offline-at-runtime requirement.

## 18. Backup and Recovery

Back up separately:

- Complete Obsidian vault.
- SQLite database.
- JSON audit records.
- Git repository.
- Compose and deployment configuration.
- Caddy internal CA data.
- Syncthing configuration as appropriate.
- Model checksums and deployment manifest.

Recovery must document:

1. Stopping services safely.
2. Restoring the vault to staging.
3. Validating the Git repository and commits.
4. Restoring SQLite and audit data when available.
5. Verifying paths and permissions.
6. Starting in read-only or maintenance mode.
7. Running consistency checks.
8. Rebuilding indexes from the vault when the database is lost.
9. Re-generating embeddings when the model changes.
10. Re-establishing Syncthing.
11. Re-trusting the Caddy internal CA.
12. Re-enabling writes only after the initial Git snapshot and index are valid.

The database and embeddings are rebuildable derived state. Loss of the database must not mean loss of the Markdown vault.

## 19. Operational Rules

- Obsidian is the source of truth.
- The application inspects before editing.
- The application prefers existing notes over duplicates.
- New durable information is appended with dates where practical.
- Existing historical facts are not silently rewritten.
- Every AI modification is reversible or has an explicit recovery explanation.
- Every AI-created claim has provenance.
- No AI operation escapes the configured vault root.
- No automatic deletion is permitted.
- No automatic move is permitted.
- No arbitrary shell is permitted.
- Index failures are visible and preserve the last valid index.
- User edits and synchronization conflicts are not silently discarded.
- Runtime external network access is not required.
- The no-authentication LAN risk remains visible to operators.

## 20. Risks and Mitigations

### Small model reliability

Risk: A 1.7B model may make unreliable tool calls or weak classifications.

Mitigations:

- Keep tool schemas small.
- Validate every tool argument in application code.
- Limit tool loops.
- Separate read and write tools.
- Require proposals for general writes.
- Use deterministic memory gates.
- Test tool reliability as a benchmark metric.

### CPU latency

Risk: Q6 inference and embedding generation may be slow on the i7-8550U.

Mitigations:

- Bound context size.
- Stream output.
- Use incremental indexing.
- Select the lighter embedding runtime from measurement.
- Keep retrieval candidate counts bounded.
- Tune threads and batch settings from benchmark results.

### No application authentication

Risk: Any reachable LAN device can use the assistant and initiate writes through the approval workflow.

Mitigations:

- LAN-only binding.
- No internet exposure.
- Caddy HTTPS.
- Prominent operator warnings.
- No destructive or arbitrary write tools.
- Explicit approval for general changes.

This risk is accepted as a deployment decision and must not be hidden.

### Synchronization conflicts

Risk: Syncthing and AI/user edits may overlap.

Mitigations:

- Preserve conflict files.
- Treat the NUC as canonical for Tiny Brain without overwriting newer edits.
- Store proposal base hashes.
- Use three-way merge.
- Require conflict review.
- Re-index after the final resolved state.

### Retrieval hallucination

Risk: The model may answer confidently without sufficient evidence.

Mitigations:

- Require retrieved context.
- Label every excerpt with stable citation IDs.
- Validate citations in application code.
- Instruct the model to state when evidence is insufficient.
- Include source excerpts in the UI.

### AI-generated garbage

Risk: Automatic memory extraction could fill the vault with duplicates or low-value content.

Mitigations:

- Conservative category and confidence gates.
- Only automatic Inbox writes in the first release.
- Deduplication against recent content.
- Session-only retention for other memory types.
- Provenance and audit records.

## 21. Explicit Exclusions

The first release excludes:

- Public internet exposure.
- Cloud inference.
- Hosted embeddings.
- Runtime external AI/API calls.
- The advertised one-million-token context mode.
- Arbitrary shell execution.
- Arbitrary filesystem access.
- Automatic note deletion.
- Automatic note moving.
- Bulk rewrites.
- Silent permanent-note edits.
- Semantic indexing of binary attachments.
- Multi-user accounts.
- Role-based access control.
- Application authentication, by explicit first-release decision.
- Automatic conflict resolution that overwrites newer edits.
- A dedicated vector database.
- Kubernetes, Postgres, Redis, Elasticsearch, Kafka, or other unnecessary infrastructure.
- A separate React/Vite frontend.

## 22. Release Checklist

The first release may be used only when every applicable item is true:

- [ ] The NUC is reachable over SSH and its OS, storage, and runtime state are documented.
- [ ] `/srv/tiny-brain` exists with correct ownership and persistent storage.
- [ ] The archive was inspected and imported without unintended changes.
- [ ] The complete vault is present on the NUC.
- [ ] Syncthing pairing and conflict behavior are documented.
- [ ] Git is initialized and the initial vault commit exists.
- [ ] Docker Compose starts all required services reproducibly.
- [ ] llama.cpp is at b10828 or later.
- [ ] Spark Q6 loads successfully in CPU mode.
- [ ] The embedding model and runtime are pinned and recorded.
- [ ] SQLite migrations complete successfully.
- [ ] FTS5 search works for exact terms.
- [ ] sqlite-vec search works for semantic queries.
- [ ] Hybrid ranking and bounded graph expansion work.
- [ ] Context assembly enforces the token budget.
- [ ] Answers contain validated citations.
- [ ] The assistant expresses uncertainty when retrieval is insufficient.
- [ ] Read tools are constrained and path-safe.
- [ ] Normal write tools create proposals rather than applying writes.
- [ ] Automatic Inbox writes are limited to high-confidence durable content.
- [ ] Duplicate Inbox content is rejected or sent to review.
- [ ] Proposal approval verifies the base hash.
- [ ] Three-way merge handles clean changes and conflicts.
- [ ] Atomic writes are used.
- [ ] Audit records are written.
- [ ] Approved changes are re-indexed.
- [ ] Approved changes create focused Git commits.
- [ ] Rejected changes do not modify the vault.
- [ ] Revert is audited and conflict-safe.
- [ ] Caddy HTTPS works from a separate LAN device.
- [ ] Caddy internal CA trust is documented.
- [ ] The no-auth LAN risk is clearly documented.
- [ ] The service is not publicly exposed.
- [ ] A note edited through Obsidian synchronizes and re-indexes incrementally.
- [ ] Restarting the stack preserves required state.
- [ ] Backup and restore have been tested.
- [ ] Q6 performance has been measured against the under-10-second target.
- [ ] Known limitations and benchmark results are recorded.

## 23. Final Definition of Done

Tiny Brain is complete for its first release when it provides a reliable local interface to the imported Obsidian vault, retrieves relevant sections using both exact and semantic search, explains answers with clickable source citations, distinguishes temporary conversation from durable knowledge, writes only through the defined safety gates, preserves provenance and audit history, handles concurrent edits without silent overwrites, re-indexes changed notes, creates Git history for approved changes, and can be operated and recovered on the NUC without external AI services.

The system should feel like an interface to the user's existing knowledge base, not like a second uncontrolled author of that knowledge base.
