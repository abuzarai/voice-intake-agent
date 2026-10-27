"""Immutable ephemeral transcript wire contracts, separate from intake turns."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

MAX_ENTRIES = 256
MAX_ENTRY_CHARS = 4096
MAX_TEXT_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 32 * 1024
MAX_BATCH_ENTRIES = 16
SealReason = Literal[
    "prepare",
    "stopped",
    "disconnected",
    "timeout",
    "provider_error",
    "limit",
    "capture_error",
]


class TranscriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TranscriptRead(TranscriptModel):
    after_sequence: Annotated[StrictInt, Field(ge=0, le=MAX_ENTRIES)]


class TranscriptEntry(TranscriptModel):
    sequence: Annotated[StrictInt, Field(ge=1, le=MAX_ENTRIES)]
    speaker: Literal["user", "assistant"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_ENTRY_CHARS)]
    observed_at: datetime
    interrupted: bool


class TranscriptBatch(TranscriptModel):
    version: Literal[1] = 1
    session_id: Annotated[str, Field(min_length=1, max_length=128)]
    after_sequence: int
    entries: tuple[TranscriptEntry, ...]
    next_sequence: int
    last_captured_sequence: int
    earliest_available_sequence: Literal[1] = 1
    sealed: bool
    seal_reason: SealReason | None
    sealed_at: datetime | None
    truncated: bool
    input_tail_complete: Literal[False] = False
    has_more: bool
