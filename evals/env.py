"""One reader for the eval environment, shared by the bot and the runner.

A blank value counts as absent. CI turns a missing secret into an empty
variable, and treating that as present makes the runner start a bot that
cannot authenticate.
"""

import os
from pathlib import Path


def read_env_file(path):
    """The settings in a named env file, without touching the environment."""
    values = {}
    if not path:
        return values
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def setting(name, fallback=None, env_file=None):
    """The first non-blank value for ``name`` or ``fallback``, or ``None``."""
    names = [key for key in (name, fallback) if key]
    for source in (os.environ, read_env_file(env_file)):
        for key in names:
            value = source.get(key)
            if value and value.strip():
                return value.strip()
    return None


def credentials(env_file=None):
    """The provider key and model, or ``None`` when either is blank."""
    key = setting("LIVE_EVAL_GEMINI_KEY", "LIVE_API_KEY", env_file)
    model = setting("LIVE_EVAL_MODEL", "LIVE_MODEL", env_file)
    if not key or not model:
        return None
    return key, model
