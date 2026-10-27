"""Bounded session contracts; no provider, confirmation or durable case state."""

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_TURNS = 128
MAX_TURN_CHARS = 4096
MAX_TRANSCRIPT_CHARS = 65536
TurnText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TURN_CHARS),
]


class LivePhase(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"


class LegalIntakeField(str, Enum):
    ALIAS_NAME = "alias_name"
    ISSUE_CATEGORY = "issue_category"
    ISSUE_DESCRIPTION = "issue_description"
    TIMELINE = "timeline"
    PARTIES = "parties"
    LOCATION = "location"
    FINANCIAL_IMPACT = "financial_impact"
    PREVIOUS_ACTION = "previous_action"
    URGENCY = "urgency"
    DOCUMENTATION = "documentation"
    DESIRED_OUTCOME = "desired_outcome"


class PartialReason(str, Enum):
    STOPPED = "stopped"
    DISCONNECTED = "disconnected"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"


class LiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommittedTurn(LiveModel):
    turn_id: int = Field(ge=1, le=MAX_TURNS)
    text: TurnText


class LiveSnapshot(LiveModel):
    session_id: str
    created_at: datetime
    revision: int = 0
    phase: LivePhase = LivePhase.PENDING
    turns: tuple[CommittedTurn, ...] = ()
    input_pending: bool = False
    partial_reason: PartialReason | None = None

    @property
    def last_turn_id(self) -> int:
        return self.turns[-1].turn_id if self.turns else 0
