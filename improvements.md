# Optimization & Architecture Improvements (Intel i7-8550U)

## 1. Where Your Current Pipeline Hurts the i7-8550U

Looking at your subsystems (Section 5) and workflows (Section 6), a single user message currently triggers up to four or five separate LLM invocations:

* **`search_planner`**: Evaluates the message to generate a 1–6 word query (*LLM Call #1*).
* **`chat` (Tool Loop)**: Receives retrieved context + 6 tool definitions. If the 2B model calls a tool, it re-evaluates the entire growing prompt (*LLM Call #2 + $N$ tool loops*).
* **`chat` (Final Answer + Thinking)**: You mention separating internal thinking tokens from final answer tokens. On a CPU, every "thinking" token costs ~80–150ms of user wait time before visible output starts.
* **`memory.extractor`**: Prompts the model to classify statements into 9 memory types with confidence scores (*LLM Call #3*).
* **`vault_review`**: Analyzes the conversation for durable knowledge and generates unified diffs (*LLM Call #4*).

> [!WARNING]
> On an i7-8550U, if each call processes 1,500–3,000 prompt tokens, a single turn can easily rack up **15–45 seconds of CPU contention**.

---

## 2. High-Impact Architectural Improvements

Here is how we can re-architect the pipeline so your 2B model punches like a 7B+ model while cutting latency by 50–70%.

### A. Replace LLM Pre-Steps with Deterministic Python & Heuristics

A 2B model shouldn't be wasted on tasks that classical NLP or lightweight classifiers can do in 2 milliseconds.

| Current Module | Current Behavior (LLM-Heavy) | Proposed Upgrade (CPU-Friendly) |
| :--- | :--- | :--- |
| **`search_planner`** | Calls LLM to write a 1–6 word search query or output `NONE`. | **Deterministic Keyword Extraction + Intent Regex**: Strip stopwords (via spaCy or a lightweight stopword list + POS tagger) and pass the remaining nouns/entities directly to FTS5 and Vector search. Use simple regex/length heuristics to skip search on greetings ("hi", "thanks"). Saves 1–3 seconds before search even begins. |
| **`agent.tools` (6 Tools)** | Passes 6 tool JSON schemas into the system prompt; lets the 2B model loop. | **Pre-Routed Context Injection (Zero-Shot RAG)**: 2B models struggle with choosing between 6 tools (`find_backlinks` vs `find_related_notes`) and eat up massive prompt tokens reading tool schemas. Instead, let your Python retrieval module automatically fetch top hits + 1-hop backlinks + tag matches in parallel (SQLite takes <5ms for this), rank them via RRF, and feed a single clean context block to the LLM with zero tools for standard Q&A. |
| **`memory.extractor`** | Calls LLM to classify claims into 9 categories with float confidence scores. | **Two-Stage Gate + GBNF Grammar**: Only trigger the extractor if the user message contains first-person declarative markers or explicit cues ("remember", "I decided", "my preference", "todo"). When triggered, force llama.cpp to use a GBNF grammar (or JSON schema constraint) so the 2B model outputs a tiny, 15-token JSON object (`{"type": "decision", "conf": 0.9, "fact": "..."}`) without rambling. |

---

### B. Offload Post-Chat Work to an Asynchronous Background Queue

Right now, if `memory.extractor` or `vault_review` runs during or immediately after the chat turn, `llama.cpp` is locked up and cannot respond if the user sends a follow-up question.

* **The Fix**: Only run the core chat completion interactively.
* Push memory extraction and `vault_review` jobs into an internal SQLite queue (similar to your existing `index_jobs` table).
* Have a background worker process that queue only when `llama.cpp` has been idle for >10 seconds.

---

### C. Fix Unified Diff Generation for 2B Models (`vault_review`)

In Section 5.3, you note that `vault_review` produces unified diffs and proposals. 2B models are notoriously bad at generating accurate unified diffs (they miscount line numbers, hallucinate context lines, or subtly rewrite adjacent paragraphs).

* **The Fix**: Never ask `gemma-4-E2B` to output a full note or a unified diff.
* Instead, since your chunker already splits notes into deterministic heading sections with unique IDs:
  1. Ask the LLM to output only the target `section_id`, an operation (`APPEND`, `REPLACE_SECTION`, or `CREATE_NOTE`), and the new Markdown paragraph.
  2. Have your Python `changes` module splice that paragraph into the original note text in memory and use Python's standard `difflib.unified_diff` to generate the exact diff for the database and UI.

---

## 3. Making Retrieval Smarter (So the LLM Doesn't Have To Be)

When using a 2B parameter model, retrieval quality dictates answer quality. If you feed a 2B model 5 noisy chunks (2,000 tokens), it gets confused ("lost in the middle") and takes forever to prefill on an i7-8550U. If you feed it the 2 exact chunks (400 tokens) that answer the question, it responds quickly and accurately.

### Add a Lightweight Cross-Encoder Reranker
Right now, your ranking module uses Reciprocal Rank Fusion (RRF) on BM25 and Cosine Similarity (threshold 0.75). Adding a tiny ONNX/FastEmbed cross-encoder reranker (like `ms-marco-MiniLM-L-6-v2`, which is only ~80MB and runs in ~40ms on CPU) after RRF lets you score the top 15 candidates and pass only the top 3 sections to the LLM.

### Prepend Breadcrumb Context to Chunks
Your chunker already preserves the hierarchical heading path. Make sure that when a section is embedded and when it is handed to the LLM, it includes a compact header:

```markdown
[Doc: filename.md > Section: Heading 1 > Subheading]
```

This prevents the 2B model from confusing which note a section belongs to and makes its `[S1]` citation validation (Section 5.3) nearly 100% reliable.

### Lower or Dynamicize the 0.75 Cosine Threshold
Depending on your embedding model (e.g., `bge-small-en-v1.5` vs `all-MiniLM-L6-v2`), a hard cosine similarity cutoff of 0.75 can actually be quite strict and might discard relevant conceptual matches. Letting RRF take top-$K$ (e.g., top 10 vector + top 10 FTS5) and letting a cross-encoder threshold the final score is much more resilient.

---

## 4. llama.cpp & Hardware Tuning for i7-8550U

The Intel i7-8550U has 4 physical cores and 8 threads, thermal-throttles quickly under sustained AVX2 loads, and shares memory bandwidth across cores.

* **Thread Count (`-t 4`, NOT `-t 8`)**:
  Hyperthreading (`-t 8`) almost always hurts `llama.cpp` performance on 8th-gen Intel CPUs because threads fight over L3 cache and memory bandwidth. Explicitly set `--threads 4` (physical cores only) for generation, and test `--threads-batch 4` or `6` for prompt processing.
* **Prompt Caching (`--cache-reuse` / Slot Persistence)**:
  Ensure your `llama.cpp` server starts with `-np 1` (single parallel slot, since it's a single-user local system) and keep your System Prompt + Retrieved Context prefix strictly deterministic. If the system prompt never changes at the top of the message array, `llama.cpp`'s KV cache will reuse it in 0ms on every turn.
* **Disable or Toggle "Thinking" Mode by Default**:
  If `gemma-4-E2B` is generating a reasoning trace (`<think>...</think>`) before every answer, it is burning 100–300 output tokens before answering. Add a UI toggle ("Deep Think" vs "Fast Chat") that controls whether thinking is enabled, or restrict thinking only to complex `vault_review` operations.
* **Flash Attention & KV Cache Quantization**:
  Pass `-fa` (Flash Attention) and `-ctk q8_0 -ctv q8_0` to `llama.cpp` to reduce memory bandwidth pressure on long contexts.
* **OpenVINO / SYCL on the Intel UHD 620 iGPU**:
  Your i7-8550U has an integrated Intel UHD 620 GPU. While it shares system RAM, compiling/running `llama.cpp` with the SYCL or Vulkan backend (passing `/dev/dri` into the Docker container) can accelerate prompt evaluation (prefill) by 1.5x–2x compared to pure AVX2 CPU, while preventing CPU thermal throttling.

---

## 5. Section 4 Implementation Status & Benchmark Results

### Implementation Status

| Tweak | Status | Implementation Details |
| :--- | :--- | :--- |
| **Physical Thread Count (`-t 4`, `-tb 4`)** | **Completed** | Explicitly configured `--threads 4` and `--threads-batch 4` in `compose.yaml`, `.env.example`, and deployment configuration. Replaced `-t 6` which caused severe L3 cache thrashing and thermal throttling. |
| **Prompt Caching & Single Slot (`-np 1`)** | **Completed** | Added `-np 1` to `llama` command in `compose.yaml`. Eliminates slot rotation (`id 0`..`id 3`) that previously evicted KV cache between chat turns. System prompt prefix remains strictly deterministic. |
| **Default Thinking Mode Disabled ("Fast Chat")** | **Completed** | Defaulted `thinking: bool = False` in `src/tiny_brain/web/schemas.py`. Replaced the auto-checked toggle in `src/tiny_brain/web/templates/index.html` with an unchecked toggle that dynamically switches between "Fast Chat" (default) and "Deep Think". Added automated test in `tests/test_web.py`. |
| **Flash Attention & KV Quantization (`-fa on`, `-ctk q8_0 -ctv q8_0`)** | **Completed** | Enabled `-fa on` and quantized KV cache to 8-bit (`-ctk q8_0 -ctv q8_0`) in `compose.yaml`. Bounded context tokens in `.env` from 131,072 down to 8,192, reducing KV RAM footprint by ~32x. |
| **iGPU Acceleration & `/dev/dri` Passthrough** | **Evaluated & Configured** | Added `devices: [/dev/dri:/dev/dri]` and configurable `-ngl ${LLAMA_N_GPU_LAYERS:-0}`. Tested `ghcr.io/ggml-org/llama.cpp:server-vulkan` with GPU offload. Benchmarks revealed that on the Intel KBL GT2 UHD 620 (24 EUs), pure AVX2 on 4 physical CPU cores yields ~12.5 t/s vs 4.1 t/s on the iGPU due to shared DDR4 memory bus saturation. AVX2 with `-t 4` remains the optimal default on this CPU. |

### Benchmark Results (Before vs After)

Benchmarks were run against the live application using `scripts/benchmark_app.py` under identical test prompts:

| Metric | Baseline (Before) | Optimized (After) | Improvement |
| :--- | :--- | :--- | :--- |
| **Model Init / Startup Latency** | 17.9 s | **4.6 s** | **3.9x faster initialization** |
| **KV Cache RAM Allocation** | 131,072 tokens (FP16) | **8,192 tokens (Q8_0)** | **~32x reduction in memory pressure** |
| **Token Generation Speed (`tg`)** | 10.16 – 10.74 t/s | **11.89 – 12.47 t/s** | **+16% to +22% higher throughput** |
| **Note Creation Total Duration** | 91.80 s | **81.49 s** | **10.3 s faster turn completion** |
| **Note Creation Rate (Application)** | 2.23 chunks/sec | **2.43 chunks/sec** | **+9.0% draft generation rate** |
| **Thinking Mode Overhead** | 423–878 reasoning tokens burned | **0 reasoning tokens burned (Fast Chat)** | Avoids 40–85 s user wait time per turn |
| **KV Slot Management** | Multiple rotating slots (`id 0`..`id 3`) | **Single slot (`-np 1`)** | Deterministic KV cache retention |