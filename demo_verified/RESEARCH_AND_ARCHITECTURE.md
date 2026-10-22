# Lane Phase 0 — How current systems handle booking-agent failures

Research date: 22 October 2026. This is a product-engineering review, **not** evidence of demand, a feature-parity certification, or a claim that Lane outperforms the referenced systems.

## What the published systems actually demonstrate

| Source | Documented approach | Design adopted for Lane | What cannot be claimed |
|---|---|---|---|
| [Cekura booking flow guide (Aug 14, 2026)](https://www.cekura.ai/discover/booking-reservation-flow-testing-voice-agents) | Checks availability, confirmed slot, write-back and escalation as separate steps; uses mock PMS/calendar responses in testing; tests stale reads, lost reschedule state and failed writes. | Make booking extraction a separate stage from fresh calendar observation; use explicit false success, fallback and error fixtures. | Cekura's published examples do not establish Lane's differentiation or real-booking reliability. |
| [Hamming AI](https://hamming.ai/) | Tests outcomes and tool-call traces, and turns production failures into reproducible regression scenarios. | Evaluate claims against event/tool evidence, keep a versioned test suite, and record case-level errors. | Transcript-only QA would not be distinctive; Hamming already tests booking outcomes. |
| [Rasa CALM correction flow](https://github.com/RasaHQ/rasa-calm-demo/blob/main/data/flows/patterns.yml) | Represents corrections as flow/slot changes and explicitly confirms a correction. | Propose chronological structured events; replay changes, linking the final agent confirmation to a caller-accepted slot. | Our offline event reducer is not equivalent to Rasa's full live dialogue manager. |
| [Retell call lifecycle](https://www.retellai.com/learn/how-ai-voice-agents-work-on-a-call), [webhook discussion](https://community.retellai.com/t/webhook-race-condition-call-ended-fires-immediately-before-call-analyzed-causing-zapier-to-miss-analyzed-event/800) | `call_ended` precedes post-call analysis; transcripts/tool traces and analysis can arrive as separate events, with retried delivery. | Ingest raw evidence promptly; deduplicate by call ID; don't equate post-call extraction or agent claims with completed CRM actions. | This patch is not a replacement for durable webhook ingestion or a Retell call simulator. |
| [HighLevel appointment by ID](https://marketplace.gohighlevel.com/docs/ghl/calendars/get-appointment/index.html), [contact's appointments](https://marketplace.gohighlevel.com/docs/ghl/contacts/get-appointments-for-contact/index.html) | Read-only endpoints expose the appointment and associated calendar events. | Continue read-only independent verification of contact, calendar, start and status, with unknown when data is incomplete. | Simulated records do not prove real HighLevel writes or an integration install. |
| [Temporal retries](https://docs.temporal.io/develop/python/best-practices/error-handling), [LangGraph persistence](https://langchain-ai.github.io/langgraph/concepts/durable_execution/) | Workflows persist history/checkpoints; retryable activities need idempotency and bounded retry policies. | Keep extraction, observations and rechecks separate; use scheduled jobs and immutable observations; fail closed on missing evidence. | Lane's Postgres jobs table is *not* a full Temporal/LangGraph replacement. |
| [Python `zoneinfo`](https://docs.python.org/3/library/zoneinfo.html), [PEP 495](https://peps.python.org/pep-0495/) | IANA rules and `fold` distinguish valid, repeated and nonexistent local times. | Compute offsets from business timezone; reject DST gaps and repeated hours without clarification. | An offset alone does not establish which slot the caller accepted. |
| [Microsoft STATE-Bench](https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/) | Stateful multi-turn simulations use consistent user profiles and a changing tool-backed environment. | Add multi-turn, stateful fixtures and evaluate outcomes separately from dialogue fluency. | Synthetic datasets are not a substitute for real, consented customer evaluations. |

## Comprehensive failure matrix

| Problem | Proposed control | Status in this patch |
|---|---|---|
| Mid-call date/time corrections | Model proposes request/revision events with exact quotes and stable IDs; Python resolves final accepted slot | Implemented, experimental `groq_events` |
| Conditional fallback | Agent confirmation explicitly links to one of the prior candidate requests; unclear link means review | Implemented |
| Availability inquiry ≠ consent | Query stays separate from booking request; agent cannot create caller consent | Implemented, review flag added to pipeline |
| Unilateral booking claim | Transcript-only finding for agent claim without caller consent; no automatic appointment verification | Implemented: `unaccepted_booking_claim`, **flag only** |
| Agent offers a time, then caller accepts | Separate offer → accept → confirmed lifecycle | Implemented |
| Caller rejects/cancels after confirmation | Later rejection invalidates a preceding alleged confirmed booking | Implemented for a single commitment; cancellation reconciliation deferred |
| Spoken times / tomorrow / year rollover | Parse evidence deterministically, normalize business-local civil time | Implemented for supported English forms only |
| “Next Friday” semantic ambiguity | Require explicit calendar date in agent confirmation | Implemented; conservatively abstains otherwise |
| DST fold or spring-forward gap | IANA timezone roundtrip and abstain on invalid local times | Already implemented; reused |
| Multiple people / candidate bookings | Confirmation must reference a specific prior request; several completed distinct bookings → review | Implemented conservatively; multiple-appointment verification deferred |
| Model hallucinated quote/date/time | Check quote is a substring of the attributed turn, and slot is grounded in date/time evidence | Implemented |
| Agent confirms wrong slot | Reject link when agent explicitly repeats a different time/date | Implemented |
| LLM output invalid, rate-limited or unauthenticated | Strict JSON schema, Pydantic validation; API failure is **not** scored as abstention | Implemented |
| Tool says success but calendar is missing | Existing read-only HighLevel verifier reads the record independently at scheduled intervals | Existing Phase 0 code; **not** live-tested here |
| Delayed writes / API outage | 2/15/60-minute reads, `pending` and `unknown` outcomes; database jobs/retries | Existing Phase 0 code |
| Duplicate appointments, reschedule/cancellation write-back | Compare multiple appointment histories and operation IDs, avoid applying accidental writes | **Not implemented** (Phase 1 / v1.5) |
| Caller identity collision or multiple linked contacts | Verify contact ID and sub-account; ambiguous IDs mean unknown | Existing conservative Phase 0 check; advanced resolution deferred |
| Webhook duplicates/out-of-order payloads | Signature verification, idempotent call ingestion, per-call version binding | Existing Phase 0 primitives; production stress test remains |
| Untrusted transcript prompt injection / PII | Treat as data, quote grounding, redact before storage, key isolation | Partial. Do not ingest unsanitized real calls into a public/shared demo. |
| Noise, ASR errors, multilingual speakers | Evaluate original audio/transcript alignment, uncertain identity/time → review | **Not implemented**; requires consented audio + platform simulation |

## Why the implementation changes

The original extractor returned one free-form `confirmed/final_start`. That forces the model to combine several tasks at once: understand each turn, identify accepted requests, resolve fallback, choose timezone, and declare confirmation. The added `groq_events` backend uses a narrow typed event schema (`request`, `revision`, `offer`, `accept`, `availability_query`, `confirmation`, `reject`, `cancel`, `not_booked`). The model **proposes** quotes, request links and local wall times; Python independently checks role attribution, chronology, quote grounding, consent, slot agreement, IANA timezone and the final state.

This is a design to **test**, not a guaranteed improvement. Event extraction may itself miss a turn or link an offer incorrectly; forcing strict validation can increase abstentions. The existing `groq` and `groq_guarded` backends are retained for a fair comparison using unchanged input and gold labels. Raw/guarded reuse the same raw Groq response, while `groq_events` uses a separate, richer schema and a separate model call. Report them separately.

## Metrics and next gate

* Exact booking correctness with abstentions counted wrong (existing, conservative metric).
* Coverage, false booking assertions, missed bookings, wrong instants and review/abstention rate.
* False-positive operational findings after a human disposition, with denominator.
* Calendar verification coverage and resolution time **only after** actual HighLevel read-access testing.

The 8-case challenge was used to develop these safeguards and is **not an untouched holdout**. The bundled additional 22-case synthetic challenge is exploratory, not an independent real-world benchmark. Before a production claim: recruit an agency, obtain appropriate data permission, assemble a held-out human-annotated sample, run repeat trials per scenario, and compare against Retell's native QA/Hamming/Cekura and simple scripts.

## Security / scope limitations

Phase 0 remains read-only. Never auto-create or cancel a customer's appointment based on an LLM extraction. The event path currently parses role-labelled plain text: it does not preserve immutable, provider-authenticated turn IDs or detect transcript spoofing reliably. Production requires structured Retell turn segments, safe redaction and retention policies, tenant isolation, token scoping, explicit monitoring permissions and operational reviews. These were not delivered by this patch.
