# Architecture

The service runs a single event loop with application-scoped state. It has no
database, broker or durable recovery log. The host application owns user/case
authorization, persistence, review and submission.

## Application and API

[app.main:create_app](../app/main.py) exports the factory in
[app/application.py](../app/application.py). Each application owns one
[LiveServices](../app/composition.py) containing settings, registry and runtime.
Services cannot be reused by another application. Shutdown awaits the runtime
before clearing the registry, including when shutdown fails.

[API dependencies](../app/api/dependencies.py) retrieve the current application's
services. [Routes](../app/api/live.py) check credentials, Origin, payload bounds
and deadlines, and sanitize errors. [Settings](../app/settings.py) are explicit;
imports, application construction and health checks do not open provider sessions
or import the media adapter.

## Session registry

[LiveSessionRegistry](../app/services/live_sessions.py) owns admission, hashed
capability verifiers, absolute expiry and the single initial-offer claim. It also
retains a bounded transcript journal per session. State is synchronous,
event-loop-owned memory, with no cross-worker coordination.

Stopped sessions count against retained-session capacity until expiry. The
fixed-minute admission limit counts successful admissions only. Registry expiry
is checked on access/admission; it is not a scheduled RAM-erasure mechanism.

## Call runtime

[LiveRuntime](../app/services/live_runtime.py) reserves one active call across
all admitted sessions, including startup and cleanup. It owns peer identity,
deadlines, tasks, media gating, cancellation and teardown. A session's initial
offer is claimed before asynchronous startup; uncertain outcomes cannot be
retried as replacement calls.

A terminal session and completed cleanup are separate states. Stop, deadline,
disconnect and provider failure gate media and schedule teardown. Timeout or
caller cancellation does not cancel the cleanup owner or free the slot. Admission
remains blocked while cleanup is pending or aborted. Later successful cleanup
restores capacity without automatically starting another call.

See [configuration](configuration.md) for process limits and shutdown budgets,
and the [API contract](contracts.md) for response behavior.

## Media adapter

The lazily imported [pipeline adapter](../app/adapters/pipecat_live/pipeline.py)
creates the SmallWebRTC peer and Pipecat graph.
[Gemini compatibility](../app/adapters/pipecat_live/gemini.py) handles the pinned
SDK subclass and explicit client configuration.
[Tool bindings](../app/adapters/pipecat_live/tools.py) keep proposal and
interview-finish results on that same session.

Each SDK-bearing module activates privacy logging suppression before importing
SDKs, without replacing root log handlers. The adapter disables inherited
credentials/replay configuration, setup-file execution, retries and reconnects.
Client data-channel messages cannot mutate conversation context or intake state.
Input audio is mono PCM at 16 kHz; output is mono at 24 kHz.

On the first transport connection, the pipeline sends one initial context with
an application-controlled request for the opening greeting. This triggers the
first response on the same Gemini session without waiting for microphone input.
Duplicate connection callbacks do not resend it, and closed input prevents
startup. The control message is not committed as a user's transcript turn.

## Intake state, transcripts and proposals

[LiveIntakeState](../app/services/live_state.py) stores ordered final user turns
and revisioned lifecycle snapshots. It is separate from
[LiveTranscript](../app/services/live_transcript.py), which captures user and
assistant utterances with stable sequence numbers and interrupted metadata.
Transcript capture seals at Prepare or termination; transcript failures do not
stop an otherwise active call. Journals remain readable until session expiry,
even after media cleanup. See the [transcript contract](transcripts.md).

[AudioDraftController](../app/services/live_audio_draft.py) owns one proposal
attempt on the original provider session. Prepare gates media, validates tool
arguments and stages the result privately. Publication requires result callback
completion, same-session context forwarding and runtime ownership revalidation.
No extra inference session is created.

## Host application boundary

The authenticated host application obtains capabilities and keeps them on the
server. It associates sessions with users/cases and saves transcript batches and
the unconfirmed proposal. Proposal persistence must finish before Stop discards
it. Review, edits, exact-revision confirmation, submission and matching are
application transactions, not voice-service operations.

A transcript send is not a durable-save acknowledgment. Unknown application
write outcomes require reconciliation with the application's stored state.
Neither the model nor a new voice session can resolve them. Restarting this
service loses unsaved data and does not recover an interview automatically.
