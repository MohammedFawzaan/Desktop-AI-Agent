from __future__ import annotations

from friday.tools import ToolRegistry
from friday.tools.system import register_system_tools
from tests.fakes import allow_all, call


async def test_get_system_context_discovers_real_locations() -> None:
    registry = ToolRegistry(allow_all)
    register_system_tools(registry)
    out = (await registry.execute(call("get_system_context"))).output
    for expected in ("OS:", "User:", "RAM:", "Drives:", "Desktop:", "Documents:", "Downloads:"):
        assert expected in out
