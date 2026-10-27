"""Fresh-process optional adapter activation and import-order contracts."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["gemini", "tools", "pipeline"])
@pytest.mark.sdk
def test_direct_sdk_module_sanitizes_before_first_sdk_import(module):
    script = r"""
import importlib
import logging
import sys
from loguru import logger
root = logging.getLogger()
handlers = tuple(root.handlers)
level = root.level
names = ("aiortc", "aioice", "websockets", "google_genai", "httpcore", "httpx")
seen = []
class Check:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ("pipecat", "aiortc", "google"):
            assert all(logging.getLogger(n).level == logging.CRITICAL + 1 for n in names)
            assert ("pipecat.", False) in logger._core.activation_list
            seen.append(fullname)
        return None
sys.meta_path.insert(0, Check())
importlib.import_module("app.adapters.pipecat_live." + sys.argv[1])
assert seen
assert tuple(root.handlers) == handlers and root.level == level
"""
    subprocess.run([sys.executable, "-c", script, module], check=True, timeout=30)


@pytest.mark.integration
def test_packages_and_normal_entry_imports_remain_inert():
    script = r"""
import builtins
import importlib
import logging
import socket
import sys
root = logging.getLogger()
handlers, level = tuple(root.handlers), root.level
def forbidden(*a, **kw):
    raise AssertionError("external access forbidden")
socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = forbidden
original = builtins.open
def safe_open(file, *a, **kw):
    assert not str(file).endswith(".env")
    return original(file, *a, **kw)
builtins.open = safe_open
class Deny:
    def find_spec(self, fullname, path=None, target=None):
        assert fullname.split(".")[0] not in ("pipecat", "aiortc", "google", "loguru")
        return None
sys.meta_path.insert(0, Deny())
for module in ("app.adapters", "app.adapters.pipecat_live", "app.composition", "app.services.live_runtime"):
    importlib.import_module(module)
assert tuple(root.handlers) == handlers and root.level == level
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)
