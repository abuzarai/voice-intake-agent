"""Same-session tool binding and completion forwarding."""

import asyncio

from app.adapters.pipecat_live._activation import activate

activate()

from pipecat.frames.frames import FunctionCallResultProperties

from app.services.live_audio_draft import TOOL_NAME

CLOSING_INSTRUCTION = (
    "This completion result is a new reply. Ignore any instruction to append it "
    "to the previous reply. Say exactly one short closing sentence in the "
    "caller's language: thank them, say the interview is complete, and say the "
    "information will be reviewed. Do not ask a question, do not call a tool, "
    "and do not claim the information was saved, confirmed, filed or decided."
)


def bind_tools(
    service, controller, fail, finished, reserve_finished, completion_forwarded
):
    async def handle_tool(params):
        await controller.handle_tool(
            params, properties_factory=FunctionCallResultProperties
        )

    service.register_function(TOOL_NAME, handle_tool, cancel_on_interruption=True)

    async def handle_finished(params):
        accepted = (
            type(params.arguments) is dict
            and not params.arguments
            and reserve_finished is not None
            and reserve_finished()
        )

        async def context_updated():
            forwarded = False
            try:
                if controller.phase == "failed":
                    return  # Stop/deadline already discarded this owned call.
                if controller.result_roundtrip is None:
                    raise ValueError("missing_context_roundtrip")
                # Ack on the same session before the client can request extraction.
                await controller.result_roundtrip()
                forwarded = True
            except Exception:
                fail()  # No event, retry or inference request after failed forwarding.
            finally:
                if completion_forwarded is not None:
                    completion_forwarded(forwarded)
            if forwarded and finished is not None:
                finished()

        result = {"accepted": bool(accepted)}
        if accepted:
            result["closing_instruction"] = CLOSING_INSTRUCTION
        try:
            await params.result_callback(
                result,
                properties=FunctionCallResultProperties(
                    run_llm=False,
                    on_context_updated=context_updated if accepted else None,
                ),
            )
        except asyncio.CancelledError:
            if accepted and completion_forwarded is not None:
                completion_forwarded(False)
            fail()
            raise
        except Exception:
            if accepted and completion_forwarded is not None:
                completion_forwarded(False)
            fail()

    service.register_function(
        "interview_finished", handle_finished, cancel_on_interruption=False
    )
