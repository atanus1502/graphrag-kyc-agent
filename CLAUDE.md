# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A GraphRAG (Graph Retrieval-Augmented Generation) "Know-Your-Customer" (KYC) agent demo. An LLM agent, built with the OpenAI Agent SDK, answers questions about customers/accounts/transactions stored as a graph in Neo4j — combining hand-written Cypher tools, a fine-tuned local text-to-Cypher model (via Ollama), and (in one variant) the Neo4j MCP server for open-ended querying.

## Setup & commands

- Requires Python 3.13+ and the `uv` package manager.
- Install deps: `uv venv && source .venv/bin/activate && uv sync`
- Start local Neo4j: `docker compose up -d` (neo4j 2025.05.0, APOC plugin, auth `neo4j/password`, bolt on 7687, browser on 7474). Alternative: a Neo4j AuraDB Free instance — see README for how to derive `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_DATABASE` from the AuraDB instance id.
- Config is via a `.env` file (not checked in): `OPENAI_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`. All Neo4j vars fall back to local-docker defaults (`bolt://localhost:7687`, `neo4j`/`password`/`neo4j`) if unset.
- Seed the graph (run once against an empty database): `python generate_kyc_dataset.py`
- Run the full agent (MCP server + text2cypher): `python kyc_agent.py`
- Run the alternate agent (hand-written Cypher tools only): `python kyc_cypher_tools.py`
- Both agents are interactive REPLs: type a question at the `Enter your KYC query (or 'quit' to exit):` prompt; `quit` exits.
- `kyc_agent.py`'s `generate_cypher` tool requires a local Ollama server (`ollama serve`) with the model `ed-neo4j/t2c-gemma3-4b-it-q8_0-35k` pulled (`ollama pull ed-neo4j/t2c-gemma3-4b-it-q8_0-35k`).
- No test suite, linter, or CI config exists in this repo.

## Architecture

### Two parallel agent entrypoints

There are **two independent, non-shared implementations** of the same KYC agent concept — don't assume changes to one apply to the other:

- **`kyc_agent.py`** — the "full" agent. Registers 4 custom `@function_tool`s (`get_customer_and_accounts`, `find_customer_rings`, `create_memory`, `generate_cypher`) plus the external **Neo4j MCP server** (`mcp-neo4j-cypher`, launched via `uvx`, connected/cleaned up around the `main()` coroutine) for arbitrary schema inspection and read/write Cypher execution. `generate_cypher` calls a local fine-tuned Ollama model to translate NL → Cypher, which the agent then runs via the MCP server's execute-query tool. Tool I/O for `get_customer_and_accounts` is validated through Pydantic models in `schemas.py`.
- **`kyc_cypher_tools.py`** — a simpler, self-contained agent. Registers 5 hand-written `@function_tool`s that each encode one specific fixed Cypher pattern (`get_customer_info`, `find_customers_in_rings`, `is_customer_in_suspicious_ring`, `is_customer_bridge`, `is_customer_linked_to_hot_property`) and returns plain dicts — no MCP server, no text2cypher. Note its agent `instructions` string is copy-pasted from `kyc_agent.py` and still references the MCP server / `generate_cypher` tool even though this file doesn't wire either up — treat that instruction text as stale if editing this file.

Both files independently duplicate the same Neo4j driver bootstrap (env var reads, `get_neo4j_driver()`, logging setup) — keep any changes to connection handling in sync across both if they matter.

### Graph data model

Produced by `generate_kyc_dataset.py`, consumed by both agents' Cypher queries:

- **Nodes**: `Customer` (`id`, `name`, `on_watchlist`, `is_pep`), `Account`, `Company` (`industry`), `Address` (`city`), `Device` (`os`), `IP_Address`, `Payment_Method` (`pm_type`, `card_number`), `Transaction` (`amount`, `timestamp`).
- **Relationships**: `Customer -[:OWNS]-> Account`, `Customer -[:EMPLOYED_BY]-> Company`, `Customer -[:LIVES_AT]-> Address`, `Customer -[:USES_DEVICE]-> Device -[:ASSOCIATED_WITH]-> IP_Address`, `Customer -[:HAS_METHOD]-> Payment_Method`, `Account -[:FROM]-> Transaction -[:TO]-> Account`, and `Memory` nodes (created at runtime by `create_memory`) linked via `FOR_CUSTOMER` / `FOR_ACCOUNT` / `FOR_TRANSACTION`.
- All node ids have a uniqueness constraint created on first run of `generate_kyc_dataset.py`.
- The dataset generator seeds `random`/`numpy` with 42 for reproducibility, bulk-loads via `UNWIND ... CALL (...) { ... } IN TRANSACTIONS OF $batch_size ROWS`, then injects five specific anomaly patterns that the agent tools are designed to detect: **super-hubs** (customers with 50 extra accounts), **circular rings** (3-customer transaction cycles, matched by the `FROM|TO*6` path pattern used in `find_customer_rings`/`find_customers_in_rings`), **bridges** (customers employed by >2 companies), **isolates** (device/IP pairs with no owning customer), and a **dense cluster** (many customers sharing one address + one payment method, flagged `on_watchlist = true`).

### Tool design pattern

Every domain tool follows the same shape: open a Neo4j session (`driver.session()`), run one parameterized Cypher query, shape the result into a dict/Pydantic model, and `logger.info(...)` the call with its key args before returning. When adding a new tool, match this pattern rather than introducing a different query/session style.
