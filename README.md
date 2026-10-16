# neo4j-memory (Hermes memory provider) v0.6.0

Direct Bolt connection to Neo4j AuraDB through the official `neo4j-agent-memory` SDK.
No MCP, no `npx`, no runtime edits of `plugin.yaml`.

## Install / update
1. Copy this folder to `$HERMES_HOME/plugins/neo4j-memory/` of **every profile** that uses it
   (or `python install.py --hermes-home <path>`), then restart each gateway / CLI session.
2. First install: `hermes memory setup` -> `neo4j-memory`
   (password -> `$HERMES_HOME/.env`, URI/user/database -> `$HERMES_HOME/neo4j-memory.json`).
3. `hermes memory status`, `hermes neo4j-memory status`.

## Upgrading from 0.1.x / 0.2.x (embedding model changed!)
The default model is now multilingual (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`,
384 dims like the old `bge-small-en`, ~470 MB download on first start). Vectors of different models are not
comparable even with equal dimensions, so existing entities must be re-embedded once:
1. Update the plugin in **all** profiles first (an old profile would keep writing old-model vectors unmarked).
2. `hermes neo4j-memory stats` -> shows `MISMATCH` for a pre-0.3 graph.
3. Pause other Hermes sessions, run `hermes neo4j-memory reembed`.
4. `hermes neo4j-memory stats` -> `OK`.
Until then entity writes are refused with `embedding_model_mismatch` (reads and preferences keep working).
Staying on the old model: set `NEO4J_MEMORY_EMBEDDING=BAAI/bge-small-en-v1.5`.
Other models: also set `NEO4J_MEMORY_EMBEDDING_DIMENSIONS`; a different dimension needs the SDK's index migration.
Preferences from <= 0.1.x use other `user_identifier`s and are not read (re-create them via `neo4j_memory_remember`).

## Tools
| Tool | Purpose |
|---|---|
| `neo4j_memory_search` | entities by meaning **and** keywords (hybrid, optional `type` and `since`/`until` filter on the learned date) + this profile's preferences; says so when nothing matches |
| `neo4j_memory_remember` | entities (POLE+O), relationships, preferences; existing ones reused, not overwritten |
| `neo4j_memory_update` | change an entity description / replace a preference (`supersede_preference`) |
| `neo4j_memory_forget` | delete an entity (with relationships) or a preference; ambiguous names delete nothing |
| `neo4j_memory_query` | read-only Cypher; without arguments returns schema + examples. Cron sessions, or `NEO4J_MEMORY_QUERY_TOOL=1` |
| `neo4j_memory_maintain` | only where the runtime loads provider tools in cron sessions (or `NEO4J_MEMORY_MAINTENANCE_TOOLS=1`): `report` / `merge` / `reject` / `digest` |

Update/forget/merge results are verified by re-reading the graph (`failed` instead of a false success).
The SDK has no `update_entity`/`delete_entity`/`delete_preference`, so these use Cypher via `client.graph`
(the SDK says this accessor is being retired in favour of NAMS' `query.cypher`).

## Running without Hermes' built-in memory (0.5.0)
The built-in `MEMORY.md`/`USER.md` give Hermes (a) an always-present snapshot in the system prompt, (b) a
security scan, and (c) a write path for its background self-improvement review. Switching it off removes all
three, so this plugin now provides them.

**How to switch it off correctly** (`$HERMES_HOME/config.yaml`):
```yaml
memory:
  provider: neo4j-memory
  memory_enabled: false
  user_profile_enabled: false
```
Do **not** list `memory` under `agent.disabled_toolsets` (that is what `hermes tools disable memory` does):
per the Hermes docs this also hides the tools of external providers. `hermes neo4j-memory status` reads the
file, tells you whether the built-in stores are off and warns about that toolset.

| Built-in feature | Replacement here |
|---|---|
| Frozen snapshot in the system prompt | **Core digest**: `hermes neo4j-memory digest --write` (also the last step of the dreaming job) writes `$HERMES_HOME/neo4j-memory-digest.md` from the graph: the profile's preferences (`user:profile` scope), minus entries that only repeat `USER.md` (token overlap) and categories in `NEO4J_MEMORY_DIGEST_EXCLUDE`; optionally the N best-connected entities (`NEO4J_MEMORY_DIGEST_TOPICS`, default 0, because hubs are often import artefacts). Size cap `NEO4J_MEMORY_DIGEST_CHARS` (1500). It is read from the local file at session start (no network, stable prompt prefix). `NEO4J_MEMORY_DIGEST=1` always uses it, `0` never; unset = when MEMORY.md is off. `NEO4J_MEMORY_DIGEST_CATEGORIES=communication,style` limits it to behaviour rules (facts belong in query-driven recall, not in every prompt). It changes between sessions, like the built-in snapshot |
| Security scan | **Memory defense** on every write (`remember`, `update`, mirrored writes, optional raw turns) and again on recall/digest: secrets are redacted (private keys, AWS/GitHub/Slack/OpenAI-style keys, JWT, bearer tokens, connection strings, `password=`/`token=` assignments, IBAN with checksum, credit cards with Luhn), invisible Unicode is stripped, prompt-injection/exfiltration phrases (EN/DE) are blocked. `NEO4J_MEMORY_DEFENSE=redact` (default) / `block` / `off`; `NEO4J_MEMORY_DEFENSE_PII=1` also redacts e-mail addresses and phone numbers. Pattern lists are heuristics, not a guarantee |
| Prompt wording | In standalone mode the system prompt tells the agent to save proactively (user profile, people, projects, decisions, corrections) and to skip trivia |
| Background review writes | See below |

**Digest and profiles.** The digest file lives in each profile's home and is built from that profile's preferences. If a
dedicated curator profile runs the nightly job, tell it which profiles to refresh (profile homes must already exist; unknown
names are skipped, never created): put `NEO4J_MEMORY_DIGEST_PROFILES=default,clippy` into the curator profile's `.env`, or run
`hermes neo4j-memory digest --write --profiles default,clippy`. Use the same `NEO4J_MEMORY_USER` everywhere, otherwise the CLI/cron
identity (`default:<profile>`) differs from live sessions and sees different preferences (`--user NAME` selects one for a single call).

**Hermes' background review** (the self-improvement pass after a turn) wrote to the built-in memory. It may use
extra tools you whitelist (`auxiliary.background_review.extra_tools`, see the Hermes memory docs):
```yaml
auxiliary:
  background_review:
    extra_tools:
      - neo4j_memory_remember
      - neo4j_memory_search
```
Keep the list narrow (no `forget`/`update`). Whether Hermes' review prompt actually calls them is unverified:
check afterwards with `hermes neo4j-memory query "MATCH (e:Entity) WHERE e.hermes_origin = 'review' RETURN e.name LIMIT 10"`.
Writes made from the review fork are tagged `hermes_origin = review`. Without it, the nightly job is the only
automatic capture; run it more often with `hermes neo4j-memory install-cron --every-hours 6 --update --apply`.

## What 0.4.0 took from neo4j-graphrag-python
Patterns re-implemented on top of `neo4j-agent-memory` (no extra dependency; `neo4j-graphrag` would duplicate the SDK).

| GraphRAG idea | Here | Why |
|---|---|---|
| `HybridRetriever` (vector + full-text) | Full-text index `hermes_entity_fulltext` on name/description/aliases, created on first connect; results fused by reciprocal-rank fusion | The SDK embeds only an entity's *name*, so descriptions were not searchable. `NEO4J_MEMORY_HYBRID=0` turns it off; if the index cannot be created it falls back to vector-only |
| `VectorCypherRetriever` (match, then traverse) | Recall = hybrid hits + one-hop relations (existing) | already similar; kept SDK calls instead of raw Cypher |
| Schema-guided extraction (node/relationship types, patterns, `additional_*`) | Relationship vocabulary with aliases and patterns (`EMPLOYED_BY` -> `WORKS_AT`); lists in tool description and prompt; optional strict mode via `$HERMES_HOME/neo4j-memory-schema.json` | stops vocabulary drift (`WORKS_AT` vs `WORKED_AT` vs `EMPLOYED_BY`) |
| Graph pruner ("incorrect direction corrected") | A reversed relationship is flipped when the pattern only exists the other way round; every adjustment is reported in `adjusted` | |
| `FuzzyMatchResolver` | New duplicate rule `fuzzy` (stdlib `difflib`, same type, similar normalized names, threshold 0.88; names that differ in their numbers, e.g. `CP710 ...` vs `CP720 ...` or `Batch 2` vs `Batch 3`, are never proposed) and umlaut folding (`Müller` == `Mueller` is now an exact match) | cheaper than embeddings, German aware. Resolvers there *replace* nodes; ours keeps the source marked `merged_into` |
| `Text2CypherRetriever` | `neo4j_memory_query`: the agent writes read-only Cypher; no argument returns labels, relationship patterns, properties and examples. Writes/`CALL` refused, 50 rows / 6000 chars, vectors stripped, 20 s timeout | structured questions (counts, multi-hop) that similarity search cannot answer. Off in normal sessions by default to save tokens |
| Lexical graph (chunk provenance) | Entities created by the plugin carry `hermes_origin` (`tool`/`dream`), `hermes_session`, `hermes_learned_at` | audit trail, visible via the query tool or Neo4j Browser |
| `RetryRateLimitHandler` (exponential backoff) | Connect retries with 1 s / 2 s back-off; one retry of a tool call after `ServiceUnavailable`/`SessionExpired`/`TransientError`; the dead connection is replaced immediately | AuraDB wakes up from a pause with a failed first call |
| Pre-filters (`filters`) | `type` filter on search | |
| `response_fallback` (no context -> no answer) | `note: "Nothing stored matches ... Do not guess"` | prevents invented facts |

Not adopted: `SimpleKGPipeline` (LLM extraction inside the plugin; the nightly agent job does that and keeps curation under your control), spaCy resolver (heavy extra dependency for little gain over our embedding rule), external vector stores, `ToolsRetriever` (the Hermes agent already chooses between the tools).

Upgrading from 0.3.x: nothing to migrate. On first connect the plugin creates the full-text index (it fills in the background; `hermes neo4j-memory stats` shows its state) and starts tagging new entities with provenance. Check the SDK's full-text support on your instance with `stats`.

## Recall (`prefetch`): query driven, after Hindsight
No fixed slice of the memory goes into every prompt (apart from the optional digest, see below). Before each turn the plugin
searches with the user's message and injects only what is relevant, with a small budget:

| Step | What happens | Knob |
|---|---|---|
| Candidates | vector search **and** full-text search over entities, fused by reciprocal-rank fusion; merged-away duplicates removed | `NEO4J_MEMORY_HYBRID`, `NEO4J_MEMORY_MIN_SIMILARITY` |
| Relevance floor | keyword-only hits scoring below 40 % of the best keyword hit are dropped (the vector channel has its own similarity threshold) | `NEO4J_MEMORY_MIN_TEXT_SCORE` |
| Optional reranker | a cross-encoder scores (question, entity) pairs; candidates below the floor are dropped, so an unrelated question injects **nothing** (Hindsight's `recall_min_scores`) | `NEO4J_MEMORY_RERANKER` (model name, default off), `NEO4J_MEMORY_RERANK_MIN` (0.25) |
| Budget | `low` / `mid` / `high`: 3/5/8 entities, 2/3/5 relations each, 3/5/8 preferences, 700/1200/2400 characters | `NEO4J_MEMORY_RECALL_BUDGET`, `NEO4J_MEMORY_MAX_CHARS` |
| Timing | default: recall for the current message (6 s timeout, skipped while connecting and for 60 s after 3 failures). `NEO4J_MEMORY_RECALL_SYNC=0`: never wait, use the recall prepared after the previous turn (Hindsight's default) | `NEO4J_MEMORY_RECALL_SYNC` |

Preferences are ranked by relevance (German/English stems, umlaut-folded); categories in `NEO4J_MEMORY_ALWAYS_CATEGORIES`
(communication, style, language, ...) are always included (max 2); mirrored built-in notes (`hermes_*`) are not injected again.
One-hop relations of the top entities are listed (`→ WORKS_AT Acme`, `← Bob MANAGES`); expired relations are hidden. The explicit
`neo4j_memory_search` tool applies no floors (the agent asked for it) and says "do not guess" when nothing matches.

**Tuning with real data:** `hermes neo4j-memory recall "<question>"` prints the settings, every candidate with its scores
(vector similarity, keyword score, reranker score), whether it was kept and why not, and the exact text that would be injected.
The default floors are conservative guesses, not measured values: run it with 10-20 real questions (related and unrelated) and adjust.
A reranker is untested against a real model here; a multilingual cross-encoder is needed for German (for example
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, unverified suggestion). Score scales differ per model, so calibrate `NEO4J_MEMORY_RERANK_MIN`.

## Dreaming loop (nightly consolidation)
Two layers, both opt-in to the cron scheduler Hermes already has:
* **Deterministic (no LLM)** - `hermes neo4j-memory dream` lists duplicate candidates
  (`exact_norm`: same normalized name+type; `likely`: name is a token subset, e.g. "Maya" < "Maya Chen";
  `possible`: embedding similarity >= 0.88; `sdk_flagged`: pending SDK `SAME_AS`), orphans and mirrored notes.
  `--apply` merges only `exact_norm` pairs. Merges use the SDK's `merge_duplicate_entities`
  (the source node is kept and marked `merged_into`, never deleted; its name becomes an alias).
* **Agent judgement** - `hermes neo4j-memory install-cron [--hour 3 --minute 30 --profile NAME] [--apply]`
  creates the Hermes cron job `neo4j-memory-dream` (dry run by default, `--deliver local`). Per Hermes' docs
  provider plugins are initialized in cron runs too (`agent_context="cron"`, only automatic writes are skipped),
  so the job **prefers the `neo4j_memory_*` tools** and falls back to terminal equivalents if a tool is missing:

  | CLI fallback | Does |
  |---|---|
  | `hermes neo4j-memory dream --json` | report: duplicates (with ids), orphans, mirrored notes, `warning` |
  | `hermes neo4j-memory merge <keep_id> <drop_id>` | merge (same type; source is marked `merged_into`, not deleted) |
  | `hermes neo4j-memory reject <id_a> <id_b>` | "different things", not proposed again |
  | `hermes neo4j-memory search "<query>"` | entities + preferences |
  | `hermes neo4j-memory tool neo4j_memory_remember\|update\|forget --args-file file.json` | stores / corrects (JSON also via stdin or `--args`) |

  The agent reviews the last 24 h of sessions with Hermes' session search, stores durable facts, promotes
  mirrored built-in notes to entities and fixes contradictions; it must answer with a bare `[SILENT]` when
  nothing changed. Existing job: `hermes neo4j-memory install-cron --update --apply` replaces its prompt.
  Merges are global; run the job in the profiles whose sessions you want consolidated.
* **If a cron run reports a missing tool** (seen once with `neo4j_memory_maintain`): create a throwaway job
  `hermes cron create "every 1d" "List the exact names of every tool you can call whose name contains neo4j_memory. Reply with only that list." --name tool-check --deliver local`
  and `hermes cron run tool-check`. Empty list = the provider is not loaded in cron runs (Hermes version/job
  settings; check `hermes --version`, community docs say cron gets memory natively from 0.21). Four tools but no
  `neo4j_memory_maintain` = old plugin code still loaded in the running gateway (restart it) or the maintenance
  gate not active. Five = fine. `hermes cron remove tool-check` afterwards.
* **One identity everywhere.** Preferences (incl. mirrored built-in notes) are stored per `<user>:<profile>`.
  Live sessions get the user id from Hermes (e.g. a chat id on a gateway); the CLI/cron has none. Set the same
  `NEO4J_MEMORY_USER=<name>` in each profile's `.env` so all of them share one identity. `dream` prints the identity
  it used and warns when mirrored notes exist under other identities (`--user` selects one for a single call).

## Hermes' own self-improvement
`NEO4J_MEMORY_MIRROR_BUILTIN` (now **default on**, `0` disables): every add/replace/remove Hermes makes in
`MEMORY.md`/`USER.md` (including its periodic memory nudges) is mirrored as a preference in category
`hermes_memory`/`hermes_user`. `replace` supersedes the old entry, `remove` deletes it.
Raw chat turns are still NOT stored (`NEO4J_MEMORY_STORE_MESSAGES=1` to enable).

## Scoping
Preferences and optional raw messages use `<user>:<profile>`; the profile comes from `NEO4J_MEMORY_PROFILE`,
else from `.../profiles/<name>`, else `default`. Entities are shared across profiles.

## Not included
Automatic extraction inside the plugin (the nightly agent job does it), relationship delete/update tools,
export/backup, entity scoping per profile.

## Conformance with Hermes' "Memory Provider Plugins" guide
Checked against the developer guide (`tests/suites/recall.py` asserts what can be asserted against stubs).

| Requirement | Status |
|---|---|
| `name`, `is_available()` without network, `initialize(session_id, **kwargs)` tolerant of missing kwargs (`hermes_home`, `agent_context`, `user_id`, `agent_identity`), `get_tool_schemas`, `handle_tool_call`, `get_config_schema`, `save_config(values, hermes_home)`, `system_prompt_block`, `prefetch`, `queue_prefetch`, `sync_turn`, `on_memory_write(action, target, content, metadata)`, `shutdown`, `register(ctx)` | implemented, signatures tested |
| `plugin.yaml` with `python_dependencies` and the hooks actually implemented | yes; the SDK is pinned to `>=0.7.0,<0.8` (the range the code was written against; `client.graph` is announced to be replaced, so unreviewed minor releases are not pulled in) |
| Threads only via `spawn_context_thread`, never a bare `threading.Thread` | yes (tested); credentials are resolved once in `initialize()` so the event-loop thread does not depend on a per-turn secret scope |
| Profile isolation: paths from the `hermes_home` kwarg | sidecar, digest, vocabulary and `config.yaml` are read from the provider's own home; the sidecar comes from the kwarg (tested) |
| Non-primary contexts (`cron`, `subagent`, `flush`): no automatic writes | `sync_turn` and the built-in mirror are skipped; explicit tool calls still work |
| Mirroring built-in writes: skip destructive actions when `previous_content` is missing | `remove` is skipped without it, `replace` only adds (tested) |
| Prefetch stays far below Hermes' ceiling | 700-2400 characters, cached for 120 s |
| Secrets via `.env`, non-secret settings via `save_config`; never write `config.yaml` | yes |
| `cli.py` with `register_cli` | yes |
| Optional `on_session_end`, `on_pre_compress` | not implemented on purpose: the plugin has no LLM; extraction is done by the dreaming job |
| Optional `post_setup`, `get_status_config()`, dashboard `config_schema.py` | not implemented: the guide does not specify their formats; `hermes neo4j-memory status` covers the checks. The dashboard panel therefore has no schema file |
| `ctx.register_skill`, `utils.atomic_json_write` / `read_json_or_empty`, checkpoint API v2 | not used (own atomic write, no skills, no transcript archive) |
| Contract tests against the real `MemoryManager` | not run: no Hermes installation in the build environment, tests use stubs |

## Development
Work happens in the GitHub repo (`GroveGuard/neo4j-memory-Plugin-for-Hermes`). The tests run against stubs of Hermes,
`neo4j-agent-memory` and the Neo4j driver (they check the plugin's logic, **not** the real SDK or a real database):
```
python tests/run_all.py
```
