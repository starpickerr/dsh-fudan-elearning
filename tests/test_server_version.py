"""The wire version identifies this service distribution, not its MCP SDK."""
import asyncio
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ServerVersionTest(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_reports_service_distribution_version(self):
        root = Path(__file__).resolve().parents[1]
        expected = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
        with tempfile.TemporaryDirectory() as data:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(root / 'mcp_server.py'), '--data-dir', data],
                env={'HOME': data, 'PYTHONDONTWRITEBYTECODE': '1'},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    result = await asyncio.wait_for(session.initialize(), timeout=10)
        self.assertEqual(result.serverInfo.name, 'elearning')
        self.assertEqual(result.serverInfo.version, expected)
