"""Unconfirmed retained-audio proposals, independent of committed transcript drafts."""

from typing import Annotated, Literal

from pydantic import StrictBool, StringConstraints, model_validator

from app.models.intake import LegalAnalysis
from app.models.live import LegalIntakeField, LiveModel

BASIS = "unconfirmed proposal from retained Live audio/conversation context"
FACT_FIELDS = tuple(field.value for field in LegalIntakeField)
MAX_FACT_CHARS = 2048
FactText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=MAX_FACT_CHARS)
]


class AudioDraftRequest(LiveModel):
    acknowledge_unproven_tail: StrictBool

    @model_validator(mode="after")
    def acknowledgment_required(self):
        if self.acknowledge_unproven_tail is not True:
            raise ValueError("explicit acknowledgment required")
        return self


class AudioContextProposal(LiveModel):
    tool_call_id: Annotated[
        str, StringConstraints(strict=True, min_length=1, max_length=128)
    ]
    facts: dict[LegalIntakeField, FactText | None]
    analysis: LegalAnalysis
    basis: Literal[
        "unconfirmed proposal from retained Live audio/conversation context"
    ] = BASIS
    confirmed: Literal[False] = False
    input_tail_complete: Literal[False] = False

    @model_validator(mode="after")
    def exactly_eleven_fields(self):
        if set(self.facts) != set(LegalIntakeField):
            raise ValueError("all eleven facts required")
        if not self.tool_call_id.strip() or any(
            v is not None and not v.strip() for v in self.facts.values()
        ):
            raise ValueError("nonempty values required")
        return self


class AudioDraftView(LiveModel):
    session_id: str
    phase: Literal["not_requested", "requested", "proposed", "failed"]
    failure: Literal["prepare_failed"] | None = None
    proposal: AudioContextProposal | None = None
