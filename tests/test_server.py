"""Tests for FastMCP server module importability."""

from __future__ import annotations

import inspect


def test_server_imports() -> None:
    from pikvm_mcp import server

    assert server.mcp is not None


def test_hid_send_key_defaults_to_one_shot_release() -> None:
    from pikvm_mcp import server

    signature = inspect.signature(server.pikvm_hid_send_key)
    assert signature.parameters["finish"].default is True
