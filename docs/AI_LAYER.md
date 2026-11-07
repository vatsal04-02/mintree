# AI Layer

Status: built, **switched off by default**, proven with a scripted provider. **Not yet evaluated against a live model** in this repository (no API key was available); the evaluation harness exists for exactly that step (see §8).

## 1. What it is, and what it refuses to be

| Feature | What it does | Who can use it |
|---|---|---|
| **Assistant** (Ctrl+J) | Answers questions about the company's own records by calling read-only tools; cites the records it used; flags any figure it cannot trace. | management, purchase, stores, accounts, dispatch, production, quality |
| **Document reader** | Reads a supplier invoice or delivery note (PDF, image, text) into fields, matches supplier / PO / lines, re-checks every sum, and prepares a *draft*. | purchase, stores, accounts |
| **Suggestions** | Deterministic rules propose a purchase request for items below reorder level and a follow-up for overdue invoices. | purchase, stores, dispatch |
| **Daily briefing** | What needs attention today, built from the same read-only tools. No model, no tokens. | anyone with the assistant |

It never creates, approves, releases, posts, pays or deletes anything on its own, never does arithmetic that ends up in the books, and never sees more than the signed-in user could open themselves.

## 2. Guarantees and where each is enforced

| Guarantee | Mechanism (code, not prompt) | Proven by |
|---|---|---|
| Off unless an administrator turns it on | `MINTREE_AI_ENABLED=false` default; every AI endpoint that spends tokens or changes state returns `ai_disabled` | `test_switched_off_by_default…`, `test_switched_off_and_budget`, `test_permissions_and_switch` |
| Runs as the signed-in user | The tools offered are filtered by the user's permissions; each tool re-checks its permission on every call; unknown or forbidden tools return a refusal and are logged | `test_only_permitted_tools_are_offered_and_a_forced_call_is_denied`, `test_the_user_cannot_make_the_assistant_act_for_someone_else` |
| Tools cannot write | Each call runs in a SAVEPOINT with `transaction_read_only = on`; PostgreSQL itself refuses writes | `test_tools_run_read_only_even_if_a_tool_tries_to_write` |
| Only `ai_*` rows are created by the layer | Static guard over the package source; business changes happen only through imported services after a human click | `test_no_endpoint_or_tool_in_the_ai_package_can_write_business_tables` |
| Numbers come from the database | Figures in an answer are compared with the tool results; those absent from them are returned as `unverified` and shown as a warning. Totals/matches/duplicates in extraction are computed by code in `Decimal` | `test_figures_not_in_any_tool_result_are_flagged`, `test_arithmetic_is_redone_by_code_and_blocks` |
| Citations are real | Built by the server from the records the tools returned, never from model text | `test_answer_comes_from_tools_with_citations_and_is_audited` |
| Hostile data is data | Tool output is wrapped in `<tool_data>`, text values clipped to 160 chars, arguments strict (`extra="forbid"`, length-bounded, parameterised SQL, LIKE wildcards escaped); documents are sent inside `<document>` with the untrusted-data rule; injection phrases in a document raise a blocking flag | `test_hostile_text_in_master_data…`, `test_sql_and_wildcard_arguments_are_inert`, `test_instructions_hidden_in_a_document…` |
| Secrets stay out | No tool selects bank, password, token or key columns (scanned in source); the API key is read from the environment, never stored, never in a provider error | `test_secrets_never_reach_the_model_or_storage`, `test_no_tool_query_can_select_bank_or_secret_columns`, `test_anthropic_provider_errors_never_leak_the_key` |
| Cost is bounded | Per-user daily token budget (business-date), message length limit, tool-round limit, document size limit | `test_budget_and_message_limits`, `test_tool_loop_is_bounded` |
| Everything is traceable | `ai_tool_calls` (tool, arguments, status, result hash), `ai.ask` / `ai.extract` / `ai.proposal_*` audit rows, usage per user per day | tests above; integrity checks `ai_accepted_proposals_…`, `ai_reviewed_extractions_…` |
| Humans decide | Proposals and extractions are drafts. Accepting runs the normal service as the accepting user (their permissions, audit, idempotency); posting an extracted invoice is the ordinary `POST /accounts/supplier-invoices` with an `Idempotency-Key` | `test_clean_invoice_is_matched…`, `test_reorder_proposal_is_generated_once…`, `test_posting_requires_the_normal_permission…` |
| Private conversations | A conversation id belonging to someone else is `404` | `test_conversations_are_private` |

## 3. Architecture

```
browser ──► /api/ai/* ──► assistant.py ──► provider (anthropic | ollama | fake)
                │              │  tool loop (≤ N rounds)
                │              └──► tools.py  (11 read-only tools, permission re-check, READ ONLY savepoint)
                ├──► extraction.py (model reads → code reconciles → draft; the person posts via the normal endpoint)
                ├──► proposals.py  (SQL rules → ai_proposals; accept = normal service as the user)
                └──► briefing.py   (tools only, cached per user per business day)
```

* `providers.py` — one interface (`complete(system, messages, tools, model, max_tokens, force_tool)`). Anthropic uses the Messages API over `httpx` (no SDK dependency; JSON parsed with `Decimal` so money is not floated). Ollama is the local option. `fake` is a scripted/keyword provider for tests and demos; the UI labels it **Demo provider**.
* Extraction uses a forced tool call (`record_extraction`) so the model can only return the schema; fields that do not parse become `null`; confidence outside 0–1 is discarded.
* The reconciliation rules (supplier by GSTIN then name ≥ 0.85, PO by exact number, lines by item code/name similarity, qty vs received-not-invoiced, price vs PO, subtotal/total within ₹0.05, duplicate supplier invoice, injection phrases, low confidence) live in `extraction.reconcile` and are listed in `flags` with `ERROR` (blocks posting in the UI) or `WARN`.

## 4. Permissions

| Permission | Granted to |
|---|---|
| `ai.assistant:use` | management, purchase_manager, stores_manager, accounts_manager, dispatch_manager, production_manager, quality_manager (+ system administrator) |
| `ai.extraction:use` | purchase_manager, stores_manager, accounts_manager |
| `ai.proposal:review` | purchase_manager, stores_manager, dispatch_manager |

A proposal is visible and actionable only to a reviewer who also holds the permission the underlying action needs (`procurement.pr:create`, `sales.followup:create`). Extraction additionally needs `procurement.po:read` to match; posting the invoice needs `accounts.supplier_invoice:post`, enforced by the ordinary endpoint (a purchase manager can read a draft but not post it).

## 5. Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `MINTREE_AI_ENABLED` | `false` | Global kill switch |
| `MINTREE_AI_PROVIDER` | `anthropic` | `anthropic`, `ollama`, or `fake` |
| `MINTREE_AI_API_KEY` | empty | Provider key. **Environment only.** Never commit it |
| `MINTREE_AI_MODEL` / `MINTREE_AI_EXTRACT_MODEL` | `claude-sonnet-5-5` / `claude-haiku-5-5` | Assistant / extraction models |
| `MINTREE_AI_BASE_URL` | provider default | Proxy or Ollama URL |
| `MINTREE_AI_DAILY_TOKEN_BUDGET_PER_USER` | 200000 | Per user per business day |
| `MINTREE_AI_MAX_TOOL_ROUNDS`, `MINTREE_AI_MAX_OUTPUT_TOKENS`, `MINTREE_AI_MAX_MESSAGE_CHARS`, `MINTREE_AI_MAX_DOCUMENT_MB`, `MINTREE_AI_TIMEOUT_S` | 6, 1500, 4000, 10, 60 | Limits |

## 6. What leaves the premises (with the cloud provider)

Sent: the user's question and the last few turns; the **results of the tools the user is permitted to use** (document numbers, names of customers/suppliers/items, quantities, amounts, dates); for extraction, the uploaded document itself. Not sent: bank details, passwords, tokens, API keys, any table no tool reads. Use `ollama` to keep everything local (PDF/image reading then needs a vision-capable local model and is not wired; the reader accepts text only for non-Anthropic providers). Treat the provider's data-retention terms as a deployment decision for Mintree.

## 7. Data model (migration 0025)

`ai_conversations`, `ai_messages` (citations, unverified figures, tokens), `ai_tool_calls`, `ai_usage` (user, business day), `ai_proposals` (partial unique index on `(kind, dedupe_key) WHERE status='PROPOSED'`), `ai_extractions` (fields, matched, flags, draft, the person's corrections), `ai_briefings`. No raw uploaded file is stored by the layer; only its SHA-256 and the extracted fields.

## 8. Evaluating a real model before enabling it

```bash
MINTREE_ENVIRONMENT=demo MINTREE_DATABASE_URL=… MINTREE_AI_ENABLED=true \
MINTREE_AI_PROVIDER=anthropic MINTREE_AI_API_KEY=… python -m scripts.ai_eval --strict --docs path/to/sample-documents
```

Runs nine assistant cases as real demo users (tool choice, no unverified figures, denied roles get nothing, a hostile prompt changes nothing) and, with `--docs`, compares extraction against `<name>.expected.json` files placed beside real PDFs/images. With the scripted provider the cases pass by construction — they prove the plumbing, not model quality. **Before a rollout, run it with the real model on real (redacted) Mintree documents and read every failure.**

## 9. Honest limits

* Not run against a live model here; answer quality, tool-choice accuracy and PDF/image extraction accuracy are **unmeasured**.
* English only. No voice, no e-mail ingestion, no autonomous scheduling, no writes by the model.
* The figure check catches numbers that appear nowhere in the tool results; it cannot catch a *wrong sentence built from right numbers* (e.g. attributing a figure to the wrong customer). Citations let the reader check.
* The assistant sees whole-company data to the extent the user's role allows; there is no row-level (e.g. per-customer) scoping in the product yet, so none is applied here.
* Prompt-injection defences are layered but not provable; the hard guarantees are the code ones in §2 (no write path, permissions, read-only transactions, human posting).
* Token cost and latency under load are not benchmarked. Budget figures are caps, not forecasts.
* Delivery-note extraction yields a draft to read from; it does not pre-fill the goods-receipt form.
