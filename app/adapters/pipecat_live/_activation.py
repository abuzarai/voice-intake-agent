"""Privacy policy applied before any optional SDK import."""


def activate():
    import logging
    import sys

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("unsupported_live_python")

    # Pipecat logs include SDP, input text and provider exceptions. Silence only its
    # namespace BEFORE import; do not replace the legacy application's log sinks.
    from loguru import logger

    logger.disable("pipecat")
    # Transport DEBUG includes SDP and WebSocket handshake URLs. Do not alter root
    # handlers/legacy startup; silence these namespaces only on optional Live use.
    for _namespace in (
        "aiortc",
        "aioice",
        "websockets",
        "google_genai",
        "httpcore",
        "httpx",
    ):
        logging.getLogger(_namespace).setLevel(logging.CRITICAL + 1)
