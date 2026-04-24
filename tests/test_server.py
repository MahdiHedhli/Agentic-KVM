"""Tests for FastMCP server module importability."""

from __future__ import annotations


def test_server_imports() -> None:
    from pikvm_mcp import server

    assert server.mcp is not None
