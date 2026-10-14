# Local AI Second Brain
## Spark-X2.5 + Obsidian + Tiny NUC

## 1. Project goal

Build a private, local AI assistant that runs on my NUC and acts as an interface to my Obsidian vault.

The system should be able to:

- chat naturally
- search my Obsidian vault
- understand relationships between notes
- retrieve relevant context before answering
- cite the notes it used
- remember useful information from conversations
- create new notes
- append information to existing notes
- propose edits to existing notes
- update tags and metadata
- find related notes
- maintain links between ideas
- distinguish temporary conversation from durable knowledge
- automatically re-index changed notes
- keep an audit trail of every AI modification
- remain entirely local unless I explicitly add an external service

The primary model will be:

> **Spark-X2.5-1.7B-GGUF**

The initial goal is not to build "ChatGPT running locally."

The goal is:

> **Build an AI interface to my own knowledge base.**

---

# 2. Hardware target

## NUC hardware

```text
CPU:
Intel Core i7-8550U
4 cores / 8 threads

RAM:
16 GB

GPU:
Integrated Intel graphics

Primary constraint:
CPU-only inference or limited integrated-GPU acceleration
```

This hardware is small, but the selected model is also very small.

The Spark-X2.5 GGUF repository currently lists:

```text
Q4_K_M  ≈ 1.11 GB
Q8_0    ≈ 1.82 GB
```

and describes Spark-X2.5 as a compact model intended for conversation, reasoning, coding, tool use, and agentic workflows.

That makes this model a particularly reasonable choice for a small always-on server.

---

# 3. Model choice

## Primary model

```text
Spark-X2.5-1.7B
```

Start with:

```text
Spark-X2.5-1.7B-Q8_0.gguf
```

and benchmark it against:

```text
Spark-X2.5-1.7B-Q6_K.gguf
Spark-X2.5-1.7B-Q4_K_M.gguf
```

The official repository currently lists Q4_K_M at about 1.11 GB and Q8_0 at about 1.82 GB.

Because the NUC has 16 GB of RAM, the extra model memory between Q4 and Q8 is relatively small compared with the total system memory.

The important thing is not simply model size.

The important question is:

> Which quantization gives the best combination of response quality, tool reliability, and CPU latency on this particular NUC?

Therefore, benchmark all three before committing.

## Initial recommendation

Start with:

```text
Q8_0
```

Then test:

```text
Q6_K
```

If Q8 feels noticeably slower without providing a meaningful improvement in actual use, use Q6.

Use Q4 if responsiveness matters more than quality.

---

# 4. Important context-window point

Spark-X2.5 advertises a native context capability of up to 1M tokens.

That does **not** mean the NUC should actually run the model with a 1M-token context.

A huge context requires substantial memory for the KV cache and increases the amount of computation required to process the prompt.

The NUC has 16 GB RAM and is CPU-bound.

Therefore:

```text
Do not:
"Load the entire Obsidian vault into context."

Instead:
"Retrieve the useful pieces of the vault and give those to Spark."
```

The enormous context capability is interesting for the architecture, but normal operation should still use retrieval.

A sensible initial context target would be something like:

```text
8K–32K tokens
```

and then increase it only if actual testing shows that the NUC handles it comfortably.

---

# 5. Core architecture

The overall system should look like this:

```text
                         ┌─────────────────────┐
                         │      User           │
                         │  Browser / UI       │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │    Chat API         │
                         │    FastAPI           │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      Spark          │
                         │   X2.5 1.7B         │
                         └──────────┬──────────┘
                                    │
                         tool calls │
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                      │                      │
             ▼                      ▼                      ▼
      ┌─────────────┐        ┌─────────────┐       ┌─────────────┐
      │   Search    │        │   Memory    │       │    Notes    │
      │   tools     │        │   tools     │       │   tools     │
      └──────┬──────┘        └──────┬──────┘       └──────┬──────┘
             │                      │                     │
             └──────────────────────┼─────────────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      SQLite         │
                         │                     │
                         │ FTS5                │
                         │ metadata            │
                         │ relationships       │
                         │ edit history        │
                         │ embeddings/index    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Obsidian Vault    │
                         │      Markdown       │
                         └─────────────────────┘
```

The Markdown vault remains the **source of truth**.

The database is an index and a memory layer.

---

# 6. Why Obsidian should remain the source of truth

The AI should never become the owner of my knowledge.

Instead:

```text
Obsidian:
canonical knowledge

AI database:
search index
embeddings
metadata
relationships
history

Spark:
reasoning/interface layer
```

This means I can shut down the AI completely and still have a normal Obsidian vault.

The vault should remain readable and editable by normal Obsidian without the AI infrastructure.

---

# 7. Software stack

## Model runtime

Use:

```text
llama.cpp
```

The Spark-X2.5 repository explicitly documents local `llama.cpp` deployment and an OpenAI-compatible server. The repository says native `spark2_5` support requires `llama.cpp` `b10828` or later.

Example:

```bash
llama serve -m /path/to/Spark-X2.5-1.7B-GGUF
```

This provides the model as a local API.

The most useful consequence is:

```text
My application
      │
      │ OpenAI-compatible HTTP API
      ▼
llama.cpp
      │
      ▼
Spark-X2.5
```

The application does not need to care about the underlying inference implementation.

That makes it easy to replace the model later.

---

# 8. Application layer

Use a small Python service.

Suggested stack:

```text
Python
FastAPI
Pydantic
SQLite
sqlite3 / SQLAlchemy
watchdog
httpx
```

The main server would expose endpoints such as:

```text
POST /chat
POST /search
GET  /notes/{id}
POST /notes
PATCH /notes/{id}
POST /notes/{id}/suggest-edit
GET  /changes
POST /changes/{id}/approve
POST /changes/{id}/reject
```

---

# 9. Why I would use SQLite

For this particular machine, I would initially avoid building a large infrastructure stack.

You do not need:

```text
Kubernetes
Elasticsearch
Postgres
Redis
Kafka
multiple microservices
```

for the first version.

SQLite is extremely attractive here because the whole project can remain almost absurdly small.

Use SQLite for:

- note metadata
- file hashes
- chunk metadata
- full-text search
- conversation history
- AI memory
- edit history
- pending changes
- relationships

For vector search, either use a SQLite-compatible vector extension or add a dedicated vector database later if needed.

The first version should minimize moving parts.

---

# 10. Vault indexing

The AI needs an index of the Obsidian vault.

A file watcher should monitor the vault:

```text
Obsidian Vault
      │
      │ filesystem event
      ▼
Python watcher
      │
      ▼
Determine changed note
      │
      ├── hash file
      ├── parse Markdown
      ├── parse frontmatter
      ├── extract links
      ├── extract tags
      ├── chunk text
      ├── generate embeddings
      └── update index
```

The system should not re-index the entire vault every time something changes.

Instead:

```text
note changed
    ↓
hash changed?
    ↓
yes
    ↓
re-index only that note
```

---

# 11. Markdown parsing

Every note should be understood as structured data.

For example:

```markdown
---
title: NUC AI Server
tags:
  - homelab
  - ai
status: active
---

# NUC AI Server

The NUC will primarily be used as a local AI server.

## Architecture

...

## Decisions

...

## Experiments

...
```

The indexer should extract:

```text
title
frontmatter
headings
body
tags
aliases
wikilinks
links
created date
modified date
file path
```

The AI should know about these separately from the raw text.

---

# 12. Chunking strategy

Do not split notes into arbitrary fixed-size pieces only.

Obsidian notes are naturally structured.

Prefer:

```text
Note
 ├── Heading
 │    ├── subsection
 │    └── subsection
 ├── Heading
 └── Heading
```

A chunk should ideally correspond to a meaningful section.

For example:

```text
NUC AI Server
    ├── Overview
    ├── Hardware
    ├── Model selection
    ├── Software architecture
    ├── Experiments
    └── Decisions
```

This improves retrieval considerably.

---

# 13. Hybrid search

Do not rely exclusively on vector search.

Use two search methods.

## Full-text search

Useful for exact terms:

```text
"Spark-X2.5"
"i7-8550U"
"Proxmox"
"Docker"
```

SQLite FTS5 is ideal for this.

## Semantic search

Useful when the user asks something conceptually:

> What did I decide about keeping the homelab simple?

even if the notes don't literally contain those words.

The embedding search might retrieve:

```text
NUC Architecture
Homelab Philosophy
Docker Setup
Server Experiments
```

## Combine the two

The retrieval pipeline becomes:

```text
User question
      │
      ├─────────────► lexical search
      │
      └─────────────► semantic search
                          │
                          ▼
                    merge results
                          │
                          ▼
                     rank results
                          │
                          ▼
                     top context
                          │
                          ▼
                        Spark
```

This should work much better than using embeddings alone.

---

# 14. Embedding model

Spark should not also be responsible for embeddings.

Use a separate small embedding model.

Good candidates to benchmark include:

```text
multilingual-e5-small
bge-small
other compact sentence-transformer models
```

The important criteria are:

```text
small RAM footprint
fast CPU inference
good semantic retrieval
language support appropriate for my notes
```

The embedding model does not need to be anywhere near as large as the chat model.

A small embedding model running periodically when notes change is perfectly reasonable for this NUC.

---

# 15. Retrieval flow

When I ask:

> What did I decide about using Kubernetes on the NUC?

the application should do approximately this:

```text
1. Receive question

2. Generate query embedding

3. Search semantic index

4. Perform lexical search

5. Merge candidates

6. Rank candidates

7. Select relevant chunks

8. Load surrounding context where necessary

9. Give selected context to Spark

10. Ask Spark to answer only from retrieved information

11. Return answer

12. Include citations to source notes
```

---

# 16. Source citations

This is one of the most important features.

The assistant should not simply answer:

> You chose Docker because Kubernetes was overkill.

It should answer:

> You decided to use Docker rather than Kubernetes because you considered Kubernetes unnecessarily complex for the size of the NUC setup.

Then:

```text
Sources:
- [[NUC Architecture]]
- [[Homelab Decisions]]
- [[Docker Setup]]
```

Ideally the UI should make these clickable.

The AI therefore becomes a **search interface to my vault**, rather than an oracle.

---

# 17. The write capability

This is where the project gets interesting.

The AI should receive explicit tools.

For example:

```text
search_notes()
read_note()
find_related_notes()
find_backlinks()

create_note()
append_note()
propose_edit()
update_frontmatter()

create_link()
add_tag()
```

The model should never directly receive unrestricted filesystem access.

Instead:

```text
Spark
  │
  │ tool call
  ▼
Application
  │
  ├── validate parameters
  ├── validate path
  ├── validate operation
  ├── check permissions
  └── perform operation
```

This is especially important because a 1.7B model is much more likely to make mistakes than a large frontier model.

The application should enforce correctness.

---

# 18. Separate "read" tools from "write" tools

## Read tools

These can be effectively automatic:

```text
search_notes(query)
read_note(path)
find_backlinks(path)
find_related_notes(path)
find_notes_by_tag(tag)
find_recent_notes()
```

## Write tools

These should be controlled:

```text
create_note(...)
append_note(...)
propose_edit(...)
update_frontmatter(...)
move_note(...)
delete_note(...)
```

I would initially allow:

```text
create
append
propose edit
```

but disable:

```text
delete
move
bulk rewrite
```

until the system has proven itself.

---

# 19. Do not immediately let the AI rewrite everything

The tempting implementation is:

```text
AI
 ↓
write_file()
```

I would specifically avoid this.

Instead create an intermediate mutation system.

Example:

```text
AI wants to edit note
        │
        ▼
create proposed change
        │
        ▼
validate
        │
        ▼
show user
        │
        ├── Approve
        │      ↓
        │   apply change
        │
        └── Reject
               ↓
             discard
```

A proposed edit might look like:

```text
┌────────────────────────────────────────┐
│ AI proposed change                     │
├────────────────────────────────────────┤
│ File:                                  │
│ Homelab/NUC Architecture.md            │
│                                        │
│ Operation: append                      │
│                                        │
│ Reason:                                │
│ User explicitly decided to avoid       │
│ Kubernetes for this machine.           │
│                                        │
│ [Approve]             [Reject]          │
└────────────────────────────────────────┘
```

This should be the default during development.

---

# 20. AI Inbox

Rather than immediately rewriting permanent notes, create an AI inbox.

Example:

```text
AI/
    Inbox.md
```

or:

```text
_AI/
    Inbox.md
    Memory.md
    Decisions.md
    Changes/
```

During normal conversation, the AI can detect durable information.

For example:

> I've decided that the NUC should remain Debian-based and I don't want to introduce Kubernetes.

The assistant can classify this as:

```yaml
type: decision
confidence: high
topic: homelab
subject: NUC
decision: remain Debian-based
rejected: Kubernetes
```

and append:

```markdown
## 2026-10-07

### NUC architecture decision

The NUC should remain Debian-based.

Kubernetes should not be introduced for this machine because the additional
operational complexity is unnecessary for the intended workload.

Related:
- [[NUC AI Server]]
- [[Homelab Architecture]]
```

This can later become part of the permanent knowledge base.

---

# 21. The AI should distinguish different kinds of memory

One major design goal should be preventing the vault from filling up with useless AI-generated junk.

Not everything said in conversation deserves to become permanent knowledge.

Create categories such as:

```text
ephemeral
working memory
user fact
decision
project information
idea
task
reference
permanent knowledge
```

For example:

> "I wonder whether Qwen would be faster."

Probably:

```text
ephemeral
```

Whereas:

> "I've decided to use Spark-X2.5 Q8 for the NUC."

is:

```text
decision
```

And:

> "The NUC has 16 GB RAM."

is:

```text
project information
```

And:

> "I should benchmark Q4, Q6 and Q8."

is:

```text
task
```

The AI's job is not to remember everything.

Its job is to determine:

> **What is worth remembering?**

---

# 22. Conversation memory vs vault memory

These should be separate.

## Conversation memory

Temporary information:

```text
"What were we just discussing?"
```

Can live in:

```text
conversation history
```

## Vault memory

Durable information:

```text
"I decided..."
"I learned..."
"The project now uses..."
"My architecture is..."
```

Belongs in:

```text
Obsidian
```

## AI internal state

Technical information:

```text
last retrieval
tool results
pending edits
agent state
```

Belongs in:

```text
application database
```

This separation will prevent a lot of architectural problems later.

---

# 23. Automatic note updating

The long-term workflow should feel like this:

```text
                     Conversation
                          │
                          ▼
                     Spark-X2.5
                          │
                          ▼
                Does this contain
                 durable knowledge?
                       /     \
                     no       yes
                     │         │
                     ▼         ▼
                  continue   classify
                               │
                    ┌──────────┼─────────┐
                    │          │         │
                    ▼          ▼         ▼
                 decision    idea      fact
                    │          │         │
                    └──────────┼─────────┘
                               │
                               ▼
                       Find best note
                               │
                               ▼
                       Propose mutation
                               │
                               ▼
                            approve
                               │
                               ▼
                         update note
                               │
                               ▼
                         re-embed note
```

Eventually, some low-risk operations could become automatic.

---

# 24. A useful rule system

Instead of relying entirely on the model's judgment, define deterministic rules.

For example:

```text
Rule 1:
Never delete notes automatically.

Rule 2:
Never modify a note unrelated to the current conversation.

Rule 3:
Never silently change existing factual content.

Rule 4:
Appending a new dated observation is safer than rewriting history.

Rule 5:
Changes to permanent notes require approval.

Rule 6:
AI-generated notes must contain provenance.

Rule 7:
Every AI change must be reversible.

Rule 8:
Every AI-created claim should retain a source conversation
or source note where appropriate.
```

This gives the system structure even when the model makes mistakes.

---

# 25. Provenance

Every AI-generated piece of knowledge should have provenance.

For example:

```markdown
## NUC architecture decision

The NUC will use Spark-X2.5 as its primary local model.

<!-- AI metadata:
source: conversation:2026-10-07T17:30
created_by: spark-agent
confidence: high
-->
```

I would actually keep machine metadata outside the visible Markdown whenever possible, but the underlying concept is important.

The system should be able to answer:

> Why is this information in my vault?

and return:

```text
Created from:
Conversation on 2026-10-07
```

or:

```text
Derived from:
[[NUC AI Architecture]]
[[Model Benchmarks]]
```

---

# 26. Edit history

Every change made by the AI should be logged.

Example:

```text
.ai/
    database.sqlite
    changes/
        2026/
            10/
                07/
                    change-0001.json
                    change-0002.json
```

A change record could contain:

```json
{
  "id": "change-0001",
  "timestamp": "2026-10-07T17:35:00",
  "file": "Homelab/NUC Architecture.md",
  "operation": "append",
  "reason": "User stated an explicit architectural decision",
  "before": "...",
  "after": "...",
  "approved": true
}
```

Then the application can support:

```text
undo last AI change
```

or:

```text
show all AI changes from today
```

---

# 27. Git integration

This is an excellent additional safety mechanism.

Put the Obsidian vault under Git.

Then AI changes become:

```text
AI modification
      ↓
Git diff
      ↓
commit
```

You can then inspect:

```bash
git diff
```

and revert mistakes.

Eventually the AI service could create commits such as:

```text
AI: update NUC architecture decision
```

or:

```text
AI: add notes from conversation
```

This gives you a complete historical record.

---

# 28. Note organization

The AI should not invent a completely new organizational philosophy.

It should learn the structure already present in the vault.

For example, if the vault looks like:

```text
Projects/
Areas/
Resources/
Notes/
People/
Ideas/
```

the AI should work within that structure.

It shouldn't suddenly decide:

```text
Knowledge/
Knowledge2/
AI Knowledge/
AI Knowledge Final/
AI Knowledge Final 2/
```

The existing vault conventions should be treated as constraints.

---

# 29. Wikilinks

Obsidian's links are extremely valuable.

When the AI creates a note, it should actively look for existing notes that are related.

For example:

```markdown
# Spark-X2.5 NUC

...

Related:
- [[NUC AI Server]]
- [[Local LLM Experiments]]
- [[Homelab Architecture]]
```

The system should use the existing vault graph.

This gives the assistant two different ways to understand your knowledge:

```text
semantic relationships
        +
explicit Obsidian relationships
```

That combination is much stronger than embeddings alone.

---

# 30. Graph-aware retrieval

Suppose I ask:

> What experiments have I done with local AI?

Semantic search may return:

```text
Local LLM Experiment
NUC AI
Model Tests
Voice Assistant
```

Then the application can traverse Obsidian relationships:

```text
Local LLM Experiment
       │
       ├── [[NUC AI]]
       ├── [[Spark Models]]
       └── [[Voice Assistant]]
```

The retrieval process can therefore expand from:

```text
semantic match
```

into:

```text
semantic match
    +
backlinks
    +
outgoing links
    +
same project
    +
same tags
```

This is where the "second brain" concept gets significantly more interesting.

---

# 31. Tool definitions

I would give Spark a small, highly constrained toolset.

## Search

```text
search_notes(
    query: string,
    limit: integer
)
```

## Read

```text
read_note(
    path: string
)
```

## Related notes

```text
find_related_notes(
    path: string,
    limit: integer
)
```

## Backlinks

```text
find_backlinks(
    path: string
)
```

## Create

```text
create_note(
    path: string,
    content: string
)
```

## Append

```text
append_to_note(
    path: string,
    content: string
)
```

## Propose edit

```text
propose_edit(
    path: string,
    description: string,
    new_content: string
)
```

## Metadata

```text
update_frontmatter(
    path: string,
    changes: object
)
```

That is enough for a surprisingly powerful system.

---

# 32. Don't give Spark arbitrary shell access

Avoid:

```text
run_any_shell_command()
```

at least initially.

A small model plus unrestricted shell access is a poor combination.

Instead expose very specific operations:

```text
search_notes()
read_note()
create_note()
append_note()
```

For system administration later, use narrowly defined tools such as:

```text
get_cpu_usage()
get_memory_usage()
docker_status()
docker_logs(container)
restart_container(container)
```

rather than:

```text
execute(command)
```

---

# 33. The system prompt

Spark should get a strong system prompt describing its role.

Conceptually:

```text
You are the local AI assistant for the user's Obsidian knowledge base.

You have access to tools for searching and modifying the knowledge base.

Rules:

1. Prefer retrieved information over assumptions.
2. Cite notes used to answer questions.
3. Never invent facts about the vault.
4. Do not modify notes merely because they are relevant.
5. Treat user statements explicitly phrased as decisions as durable information.
6. Distinguish ideas from decisions.
7. Prefer adding new information over rewriting historical information.
8. Before modifying an existing note, inspect it first.
9. Never delete information automatically.
10. Never create duplicate notes when an existing note is appropriate.
11. Use existing Obsidian naming and linking conventions.
12. When uncertain where information belongs, propose a change instead of applying it.
```

The application should enforce the dangerous parts independently of the prompt.

---

# 34. The AI should inspect before editing

Never do:

```text
User:
"Update my NUC note."

AI:
write immediately
```

Instead:

```text
search_notes("NUC")
       ↓
find candidate note
       ↓
read_note()
       ↓
understand structure
       ↓
determine insertion point
       ↓
generate proposed change
       ↓
validate
       ↓
apply
```

This dramatically reduces accidental duplication and destructive edits.

---

# 35. Conversation example

Imagine I say:

> I'm going to use Spark-X2.5 Q8 as the first model on the NUC. I want it to handle chat and Obsidian retrieval, while embeddings should be handled separately.

The system should understand that this contains several durable facts:

```text
project:
NUC AI

decision:
Spark-X2.5 Q8 is the initial model

architecture:
chat/reasoning → Spark

architecture:
embeddings → separate embedding model
```

It then searches:

```text
[[NUC AI Server]]
[[Local AI Architecture]]
[[Obsidian AI]]
```

and discovers that:

```text
NUC AI Server.md
```

already contains the architecture section.

The assistant then proposes:

```diff
## Model

- TBD
+ Initial model: Spark-X2.5-1.7B Q8_0.

## Architecture

- TBD
+ Spark handles conversational reasoning and tool use.
+ Embeddings are handled by a separate embedding model.
```

I approve.

The file changes.

The indexer notices:

```text
NUC AI Server.md changed
```

and re-indexes only that file.

The updated embeddings become searchable immediately.

---

# 36. A more advanced conversation example

Later I say:

> I tested Q8 and Q6. Q6 is nearly as good but noticeably faster, so I'm switching to Q6.

The AI should not simply create another random note.

It should find the existing decision.

It could change:

```markdown
Initial model:
Spark-X2.5-1.7B Q8_0
```

into a history such as:

```markdown
## Model decisions

### 2026-10-07

Started with:

`Spark-X2.5-1.7B Q8_0`

### 2026-10-08

Switched to:

`Spark-X2.5-1.7B Q6_K`

Reason:

Q6 provided a better speed/quality trade-off on the NUC.
```

This is better than silently overwriting the old decision.

The vault becomes a record of how the project evolved.

---

# 37. Long-term memory extraction

You can eventually make the AI inspect every conversation for durable information.

For example:

```text
Conversation
     │
     ▼
Spark
     │
     ▼
Memory extraction pass
     │
     ├── facts
     ├── decisions
     ├── ideas
     ├── tasks
     ├── preferences
     └── nothing durable
```

Only the extracted durable information goes into the memory pipeline.

This can happen:

```text
after each conversation
```

or:

```text
when the conversation ends
```

The second approach may be more reliable because the AI sees the full context.

---

# 38. Avoiding "AI garbage"

A major failure mode of AI note systems is:

```text
10 conversations
↓
47 AI notes
↓
duplicate information
↓
AI starts retrieving its own generated garbage
↓
knowledge base quality decreases
```

Prevent this aggressively.

Rules:

```text
Prefer updating existing notes.

Prefer appending to an appropriate existing note.

Create a new note only when there is a genuine new entity/concept.

Never create a note solely because something was mentioned once.

Do not rewrite an entire note when a small append