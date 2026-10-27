"""Bounded synchronous journal. No persistence, provider work or cleanup waits."""

from datetime import datetime, timezone
from typing import Callable

from app.models.live_transcript import (
    MAX_BATCH_ENTRIES,
    MAX_ENTRIES,
    MAX_ENTRY_CHARS,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_BYTES,
    SealReason,
    TranscriptBatch,
    TranscriptEntry,
    TranscriptRead,
)


def encode_batch(batch: TranscriptBatch) -> bytes:
    """The same exact serializer is used for sizing and HTTP delivery."""
    return batch.model_dump_json().encode("utf-8")


class LiveTranscript:
    def __init__(
        self,
        session_id: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if not 1 <= len(session_id) <= 128 or not session_id.isascii():
            raise ValueError("invalid transcript identity")
        self.session_id = session_id
        self._clock = clock
        self._entries: list[TranscriptEntry] = []
        self._text_bytes = 0
        self.sealed = False
        self.seal_reason: SealReason | None = None
        self.sealed_at: datetime | None = None
        self.truncated = False

    @property
    def last_sequence(self):
        return len(self._entries)

    def seal(self, reason: SealReason) -> bool:
        if self.sealed:
            return False
        self.sealed_at = self._clock()
        self.seal_reason = reason
        self.truncated = reason in ("limit", "capture_error")
        self.sealed = True
        return True

    def append(self, speaker, text, interrupted=False) -> TranscriptEntry | None:
        if self.sealed:
            return None
        try:
            if speaker not in ("user", "assistant") or type(interrupted) is not bool:
                raise ValueError()
            if not isinstance(text, str):
                raise ValueError()
            if len(text) > MAX_ENTRY_CHARS:
                self.seal("limit")
                return None
            size = len(text.encode("utf-8"))
            if not text.strip():
                return None
            if (
                self.last_sequence >= MAX_ENTRIES
                or self._text_bytes + size > MAX_TEXT_BYTES
            ):
                self.seal("limit")
                return None
            entry = TranscriptEntry(
                sequence=self.last_sequence + 1,
                speaker=speaker,
                text=text,
                observed_at=self._clock(),
                interrupted=interrupted,
            )
            self._entries.append(entry)
            self._text_bytes += size
            return entry
        except Exception:
            self.seal("capture_error")
            return None

    def read(self, after_sequence: int) -> TranscriptBatch:
        TranscriptRead(after_sequence=after_sequence)
        if after_sequence > self.last_sequence:
            raise ValueError("invalid transcript cursor")
        entries: list[TranscriptEntry] = []

        def batch():
            next_sequence = entries[-1].sequence if entries else after_sequence
            return TranscriptBatch(
                session_id=self.session_id,
                after_sequence=after_sequence,
                entries=tuple(entries),
                next_sequence=next_sequence,
                last_captured_sequence=self.last_sequence,
                sealed=self.sealed,
                seal_reason=self.seal_reason,
                sealed_at=self.sealed_at,
                truncated=self.truncated,
                has_more=next_sequence < self.last_sequence,
            )

        result = batch()
        for index in range(
            after_sequence, min(self.last_sequence, after_sequence + MAX_BATCH_ENTRIES)
        ):
            entries.append(self._entries[index])
            candidate = batch()
            if len(encode_batch(candidate)) > MAX_RESPONSE_BYTES:
                break
            result = candidate
        return result
