import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from elearning_mcp.dsh_credentials import DshCredentialStore
from elearning_mcp.server import create_server

NEW = 'ALCOR_ELEARNING_CREDENTIAL'
OLD = 'STELLA_ELEARNING_CREDENTIAL'
ACCOUNT = {'username': 'synthetic', 'password': 'synthetic-password'}
RAW = json.dumps(ACCOUNT)


class CredentialEnvironmentTest(unittest.TestCase):
    def test_inputs_are_consumed_on_success_and_failure(self):
        cases = [
            ({NEW: RAW}, True), ({OLD: RAW}, True),
            ({NEW: RAW, OLD: RAW}, True),
            ({NEW: RAW, OLD: 'other-secret'}, False),
            ({NEW: '', OLD: RAW}, False), ({NEW: RAW, OLD: ''}, False),
            ({NEW: ''}, False), ({OLD: ''}, False), ({}, False),
            ({NEW: 'bad-secret'}, False), ({OLD: '{}'}, False),
            ({NEW: json.dumps([])}, False),
        ]
        with tempfile.TemporaryDirectory() as folder:
            for values, valid in cases:
                with self.subTest(keys=tuple(values), valid=valid), patch.dict(os.environ, values, clear=True):
                    try:
                        result = DshCredentialStore.from_environment(folder)
                        self.assertTrue(valid)
                        self.assertIsNotNone(result)
                    except ValueError as exc:
                        self.assertFalse(valid)
                        for secret in (RAW, 'other-secret', 'bad-secret'):
                            self.assertNotIn(secret, str(exc))
                    finally:
                        self.assertNotIn(NEW, os.environ)
                        self.assertNotIn(OLD, os.environ)


    def test_startup_errors_also_clear_both_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            for options in ({'legacy_dir': folder}, {'hosted_connector': True}):
                with self.subTest(options=options), patch.dict(os.environ, {NEW: RAW, OLD: RAW}, clear=True):
                    with self.assertRaises(ValueError):
                        create_server(folder, dsh_credentials=True, **options)
                    self.assertNotIn(NEW, os.environ)
                    self.assertNotIn(OLD, os.environ)
            blocked = Path(folder) / 'file'
            blocked.write_text('synthetic')
            with patch.dict(os.environ, {NEW: RAW, OLD: RAW}, clear=True):
                with self.assertRaises(OSError):
                    create_server(blocked / 'data', dsh_credentials=True)
                self.assertNotIn(NEW, os.environ)
                self.assertNotIn(OLD, os.environ)


class CredentialStdioTest(unittest.IsolatedAsyncioTestCase):
    async def test_script_and_module_entry_points_accept_new_name(self):
        root = Path(__file__).resolve().parents[1]
        for entry in ([str(root / 'mcp_server.py')], ['-m', 'elearning_mcp.server']):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as folder:
                params = StdioServerParameters(command=sys.executable,
                    args=entry + ['--data-dir', folder, '--dsh-credentials'], cwd=str(root),
                    env={'HOME': folder, NEW: RAW, 'PYTHONDONTWRITEBYTECODE': '1'})
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), timeout=10)
                        inventory = await session.list_tools()
                        self.assertIn('list_courses', {tool.name for tool in inventory.tools})
                        self.assertNotIn('synthetic-password', inventory.model_dump_json())


if __name__ == '__main__':
    unittest.main()
