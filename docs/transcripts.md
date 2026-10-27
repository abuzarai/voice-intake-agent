# Transcripts

The service captures a bounded two-speaker transcript from the existing Gemini
Live conversation. It publishes best-effort browser events and provides batch
reads for a trusted application server. It does not record audio or persist
transcripts to a database.

## Capture and lifecycle

User text comes from the final user-aggregator callback after the intake-state
commit succeeds. Assistant text comes from `on_assistant_turn_stopped`, including
its `interrupted` flag. Empty turns are ignored. System instructions, thoughts,
tool results and internal proposal requests are excluded.

Each retained entry has an immutable sequence number. Sequence records append
order, not exact acoustic chronology. Observation timestamps are server times,
not word/audio offsets. Assistant text can run ahead of playback; an entry does
not prove every word was heard.

Prepare, Stop, deadline or failure seals capture before further asynchronous
work. Late callbacks cannot reopen the journal. Limits and capture errors also
seal it, without stopping voice. The first seal reason/time is retained.
`input_tail_complete` is always false.

The journal is separate from the user-only intake snapshot. It remains available
after media cleanup until the original capability expires. It is lost on process
exit. Access expiry is not a guarantee of scheduled physical RAM erasure.

## Batch reads

```http
POST /api/v1/live/sessions/{session_id}/transcript/read
Authorization: Bearer <session-capability>
Content-Type: application/json

{"after_sequence":0}
```

The request contains exactly one strict integer, `after_sequence`, from 0 through
256. A cursor greater than the last captured sequence returns 422. Body size is
limited to 256 bytes with a five-second total streaming deadline. Capabilities
are checked before and after body reading. Feature, Origin, method and error
rules follow the [API contract](contracts.md).

The endpoint does not wait for SDK cleanup or require provider configuration.
It returns 200 even for an empty/live/sealed journal. Expired or unknown access
returns 401, not an empty complete transcript. All responses use
`Cache-Control: no-store`.

```json
{
  "version": 1,
  "session_id": "example-session",
  "after_sequence": 0,
  "entries": [
    {
      "sequence": 1,
      "speaker": "assistant",
      "text": "Please describe the issue.",
      "observed_at": "2026-01-01T00:00:01Z",
      "interrupted": true
    }
  ],
  "next_sequence": 1,
  "last_captured_sequence": 1,
  "earliest_available_sequence": 1,
  "sealed": false,
  "seal_reason": null,
  "sealed_at": null,
  "truncated": false,
  "input_tail_complete": false,
  "has_more": false
}
```

| Field | Meaning |
| --- | --- |
| `version` | Contract version, currently 1. |
| `entries` | Ordered entries after the requested cursor. |
| `next_sequence` | Last returned sequence, or the input cursor for an empty batch. |
| `last_captured_sequence` | Current captured high-water mark, not a saved acknowledgment. |
| `earliest_available_sequence` | Always 1, including empty journals; no eviction occurs. |
| `has_more` | More currently captured entries remain after this batch. |
| `sealed` | No further entries will be accepted. |
| `seal_reason` / `sealed_at` | Null until the first seal. |
| `truncated` | Capture stopped because of a limit or capture error. |
| `input_tail_complete` | Always false; completeness is not established. |

Entry fields are `sequence`, `speaker` (`user` or `assistant`), `text`,
`observed_at` and boolean `interrupted`. Sequences start at 1 and are contiguous
among retained entries. Fetch the next batch using `next_sequence` when
`has_more:true`. A false value means no further entries are available *yet*;
it does not mean a live conversation has finished.

Seal reasons are `prepare`, `stopped`, `disconnected`, `timeout`, `provider_error`,
`limit` and `capture_error`. A capture seal is not always the interview end:
`limit` or `capture_error` can occur while voice continues.

## Bounds

| Dimension | Limit |
| --- | --- |
| Retained entries per session | 256 |
| Characters per entry | 4,096 Unicode characters |
| Total retained text | 262,144 UTF-8 bytes |
| Entries per batch | At most 16 |
| Encoded HTTP JSON response | At most 32,768 bytes |

The same serializer sizes and emits the HTTP response. A batch may contain fewer
than 16 entries to fit the byte ceiling. An oversized or invalid entry is omitted;
the journal seals as truncated without consuming its sequence number. It is not
silently split or reconstructed. The retained prefix remains readable.

## Live events

Turn events use the existing server-message envelope:

```json
{
  "label": "rtvi-ai",
  "type": "server-message",
  "id": "unique-envelope-id",
  "data": {
    "type": "transcript_turn",
    "version": 1,
    "sessionId": "example-session",
    "entry": {
      "sequence": 1,
      "speaker": "user",
      "text": "My question concerns a rental agreement.",
      "observed_at": "2026-01-01T00:00:01Z",
      "interrupted": false
    }
  }
}
```

The pinned browser client passes `data` to `onServerMessage`. Handle this type
separately from `interview_finished`. Validate version, session identity,
generation, types and bounds before rendering plain text. Do not enable generic
client commands or interpret transcript text as HTML.

The first seal may send a `transcript_status` message in the same envelope:

```json
{
  "type": "transcript_status",
  "version": 1,
  "sessionId": "example-session",
  "sealed": true,
  "seal_reason": "prepare",
  "sealed_at": "2026-01-01T00:04:00Z",
  "truncated": false,
  "input_tail_complete": false,
  "last_captured_sequence": 12
}
```

Entries are journaled before sending. Delivery failure does not stop voice.
Messages can be missed or delayed, including status messages during Stop.
Reconcile through batch reads; a delivered message is not proof of durable saving.

## Application integration

The application server holds the capability and binds it to an authorized user
and case. Read batches incrementally within the original TTL; saving only after
End risks losing data. Never accept browser-supplied text as authoritative
provider output.

Use `(session_id, sequence)` as entry identity. Identical replay can be
idempotent; conflicting payloads under one identity must not overwrite existing
text. A saved-through acknowledgment should advance only over contiguous,
validated, durable entries, not the largest sequence seen across gaps.

Keep capture/delivery status separate from saved status. Storage failure must
not delay Stop or trigger another voice session. A client that discards its
capability at Stop needs a bounded server-side collection strategy; a later
browser fetch is not guaranteed to recover the tail. Do not extend capability
lifetime or restore signaling access to finish transcript collection.

The host application owns durable access permissions, retention, deletion and
any corrected copies. These policies are separate from transcript capture and
case-summary confirmation. Read and preserve partialness honestly: saving every
retained entry still does not establish complete audio transcription.

Sources: [models](../app/models/live_transcript.py),
[journal](../app/services/live_transcript.py),
[routes](../app/api/live.py), [runtime](../app/services/live_runtime.py).
