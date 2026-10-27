# Voice API contract

Source: [routes](../app/api/live.py), [session models](../app/models/live.py),
[proposal models](../app/models/live_audio_draft.py), [runtime](../app/services/live_runtime.py).
For environment settings and process limits, see [configuration](configuration.md).

## Access and errors

Base path: `/api/v1/live/sessions`. Admission is exactly that path, **without a
trailing slash**; automatic slash redirects are disabled. Routes use
`Cache-Control: no-store`. Keep bootstrap secrets and session capabilities on the
trusted server, out of browser examples, URLs, storage, telemetry and logs.

All Live routes require enabled Live and a configured bootstrap secret (otherwise
404). A supplied `Origin` must be a single exact configured allowlist member;
`null` and `*` are rejected (403). Missing Origin is allowed for server callers.
This is neither CORS middleware nor user authentication. User identity, roles and
case ownership belong to Express. Any query string is rejected (400), including
query capabilities. Admission requires exactly one `x-live-bootstrap-secret`
header (32–256 characters); other routes require exactly one `Authorization`
header containing `Bearer ` and a 43-character ASCII capability (scheme is
case-insensitive). Invalid credentials, unknown/expired sessions and mismatches
return 401 without distinguishing them.

JSON-reading routes require `application/json` (415); excess bytes yield 413;
malformed JSON or schema violations yield 422. Bodies are streamed with byte
limits and a fixed five-second total read deadline (not renewed per chunk).
Expiry returns sanitized bilingual 408 with `Cache-Control: no-store`; there is
no automatic retry. Authentication/gates and content type precede the read;
JSON parsing and subsequent operations are outside this deadline. Request objects forbid extra
keys. Route-handled errors use `detail: {message_en, message_ur}`; unexpected
exceptions become sanitized 500 responses, and unsupported methods return 405.
Unknown paths use framework 404 behavior. There is no generic 202, idempotency-key
protocol or retry promise in this service.

## Endpoints

All suffixes below follow `/{session_id}`. Successful responses are 200 unless noted.

| Method/path | Body | Response / additional errors |
| --- | --- | --- |
| POST base path | `{"processing_consent":true}` only; strict boolean true; 256 bytes max | 201: `session_id`, `created_at`, `expires_at`, `capability`, `token_type` (`Bearer`), `state_url`, `stop_url`; 429 rate, 503 capacity/cleanup pending/shutdown |
| GET `/state` | Not read | Snapshot after cleanup drain; 503 if cleanup remains incomplete after three seconds |
| POST `/stop` | Not read | Terminal snapshot after proposal discard and cleanup drain; 503 if cleanup remains incomplete after three seconds |
| POST `/webrtc/offer` | `sdp`, `type:"offer"` only | `sdp`, `type:"answer"`, `pc_id`; 409 busy/used/terminal, 503 config/dependency, 502 startup failure |
| PATCH `/webrtc/offer` | `pc_id`, `candidates` | `{"status":"accepted"}`; 409 unavailable/wrong peer/limit, 503 unavailable, 502 signaling failure |
| POST `/transcript/read` | `{"after_sequence":0}`; strict integer 0–256 only; 256 bytes max | Versioned bounded transcript batch; future cursor 422; no cleanup wait or provider operation; [full contract](transcripts.md) |
| GET `/audio-draft` | Not read | AudioDraftView; 409 no active owned call, 503 provider config |
| POST `/audio-draft` | `{"acknowledge_unproven_tail":true}` only; strict true; 256 bytes max | AudioDraftView after preparation; 409 unavailable/attempt used (use GET), 503 config, 502 preparation failure |

Admission itself does not require a provider key/model or start a call. Signaling
and audio-draft access require explicit key, model and free-tier attestation.
Runtime support is Python 3.12 with Pipecat 1.12.0 and google-genai 2.19.0.

Initial SDP is a strict nonempty string, at most 65,536 characters **and UTF-8
bytes**. No initial `pc_id`, `restart_pc` or `request_data` is accepted. Signaling
JSON is at most 73,728 bytes. PATCH is only for the existing owned peer, not
renegotiation: strict `pc_id` length 1–128, 1–32 candidates per request, at most
128 candidates per call. Each candidate has exactly `candidate` (strict string,
0–2048 characters; empty means end-of-candidates), `sdp_mid` (strict string,
0–32 characters), `sdp_mline_index` (strict integer 0–15). No PATCH after Prepare.

## Ephemeral transcript

The [transcript reference](transcripts.md) defines both speaker entries, live
`transcript_turn` / `transcript_status` events, cursors and response limits.
These are separate from the user-only `LiveSnapshot.turns` and proposal state.
Reads require an unexpired capability, including after Stop, and do not wait for
cleanup. A sealed or fully fetched journal does not prove a complete audio tail
or durable saving. Case storage, permissions and retention belong to the host
application.

## Cleanup waits

State and Stop wait at most three seconds for the existing cleanup owner. Pending
or aborted cleanup produces constant English/Urdu 503 detail with
`Cache-Control: no-store`; normal success retains the existing 200 snapshot.
A Stop 503 means its cleanup wait did not finish, **not** that Stop was ignored:
terminal state, proposal discard and audio gating already occurred. Authentication
and expiry checks still apply before either route waits.

The timeout does not cancel cleanup or release the occupied slot. New admissions
return 503 after authentication and body validation, before quota allocation,
while cleanup is pending/aborted or shutdown is in progress. Already-issued
sessions cannot bypass the occupied call slot. Later successful cleanup can
restore capacity, but never starts another call automatically. An aborted cleanup
owner requires authorized process replacement. Clients may discard their
capability after Stop fails; a recoverable follow-up read is not guaranteed.
Do not automatically readmit, reconnect or retry provider work. See
[shutdown limits](configuration.md).

Offer and PATCH failure paths use the same three-second shielded cleanup wait.
Pending or aborted cleanup does not replace their original sanitized signaling
failure (502) with a cleanup-pending 503. Caller cancellation propagates,
including cancellation during this wait. The slot stays occupied until cleanup
completes.

## Response schemas

Snapshot keys: `session_id`, `created_at`, `revision`, `phase` (`pending` or
`partial`), `turns` (objects with `turn_id`, `text`), `input_pending`,
`partial_reason` (null, `stopped`, `disconnected`, `timeout`, `provider_error`).
Datetime values serialize as ISO timestamps. Turns are limited to 128, each
1–4096 characters after whitespace stripping; transcript total including newline
separators is at most 65,536 characters. No transcript completeness is implied.
Repeated Stop retains the terminal snapshot until expiry; this does not create a
general HTTP exactly-once guarantee.

AudioDraftView keys: `session_id`, `phase` (`not_requested`, `requested`,
`proposed`, `failed`), `failure` (null or `prepare_failed`), `proposal` (null or
object). Proposal keys: `tool_call_id` (nonblank, 1–128 characters), `facts`,
`analysis`, `basis`, `confirmed:false`, `input_tail_complete:false`. Basis is
exactly `unconfirmed proposal from retained Live audio/conversation context`.

`facts` requires exactly the eleven canonical fields: `alias_name`,
`issue_category`, `issue_description`, `timeline`, `parties`, `location`,
`financial_impact`, `previous_action`, `urgency`, `documentation`,
`desired_outcome`. Each is null or a strict nonblank string of 1–2048 characters.

`analysis` follows [LegalAnalysis](../app/models/intake.py): required
`primary_language` (`urdu`, `english`, `mixed`), `legal_domain` (`family_law`,
`property_law`, `criminal_law`, `civil_law`, `labor_law`, `corporate_law`, `other`),
`confidence_score` (finite number 0–1), `key_entities` (string arrays `parties`,
`locations`, `dates`, `amounts`, each defaulting empty), `issue_summary`,
`adr_suitable` (boolean), `adr_reasoning`, `urgency` (`low`, `medium`, `high`),
`urgency_reasoning`; optional `case_title_en` defaults empty and `case_title_ur`
defaults null. Provider prevalidation rejects coercion and unknown keys: arguments
must contain exactly facts/analysis, serialized UTF-8 size ≤49,152 bytes,
analysis JSON ≤16,384 characters, at most 2,048 visited values, depth ≤10,
objects/arrays ≤32 entries, strings ≤4096 characters, plus the tighter fact bound.
Schema validity is not legal accuracy or user confirmation.

## Prepare and completion ordering

`propose_draft` is predeclared on the original Live call. Explicit Prepare
synchronously reserves its only attempt and gates input/future output before any
await; it does not create a text extraction call. Its deadline is the earlier of
30 seconds and the original call deadline. A staged proposal is published only
after result callback and same-session context forwarding succeed and ownership
is revalidated. Unsolicited proposal tools do not reserve an attempt.

The no-argument `interview_finished` tool is advisory, not a save/confirmation or
terminal state. After its accepted result is forwarded on the same call, the
current peer can receive:

```json
{"label":"rtvi-ai","type":"server-message","id":"<event-id>","data":{"type":"interview_finished","sessionId":"<session-id>"}}
```

Manual Prepare and agent-finish race through the same single-attempt reservation.
If completion forwarding was already reserved, Prepare waits at most five seconds
(or the remaining call deadline) for that barrier. No second attempt is created.
Stop/deadline/disconnect can invalidate either path. Gating cannot recall already
buffered playback and does not prove provider playback completion or audio-tail
flush. Read and persist the ephemeral proposal through Express **before Stop**;
Stop does not save or undo an already dispatched Express write. On unknown
outcomes reconcile application state; never automatically readmit, replay Prepare
or reconnect. See [architecture](architecture.md).

