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
from elearning_mcp.store import write_json

ACCOUNT = {'username': 'synthetic', 'password': 'synthetic-password'}


class DshCredentialTest(unittest.TestCase):
    def test_no_legacy_secret_reads_or_secret_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / 'elearning.json', {'username': 'synthetic', 'password': 'stella-keychain:v1:unused',
                        'token': 'old-token', 'base_url': 'https://untrusted.invalid', 'courses': ['10']})
            store = DshCredentialStore(root, ACCOUNT)
            cfg = store.load_settings()
            self.assertEqual(cfg['password'], ACCOUNT['password'])
            self.assertEqual(cfg['token'], '')
            self.assertEqual(cfg['base_url'], 'https://elearning.fudan.edu.cn')
            self.assertEqual(cfg['courses'], ['10'])
            write_json(store.session_path(cfg), {'credential': 'stella-keychain:v1:unused'})
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaises(FileNotFoundError):
                store.load_session(cfg)
            store.save_session(cfg, {'cookies': [{'value': 'synthetic-cookie'}]})
            self.assertEqual(store.load_session(cfg)['cookies'][0]['value'], 'synthetic-cookie')
            copy = store.load_session(cfg)
            copy['cookies'].clear()
            self.assertTrue(store.load_session(cfg)['cookies'])
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
            with self.assertRaises(PermissionError):
                store.save_settings({'password': 'forged'})
            other = DshCredentialStore(root, {**ACCOUNT, 'username': 'another'})
            self.assertEqual(other.load_settings()['courses'], [])
            with self.assertRaises(FileNotFoundError):
                other.load_session(other.load_settings())

    def test_only_explicit_dsh_credentials_are_accepted_and_removed_from_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'STELLA_ELEARNING_CREDENTIAL': json.dumps(ACCOUNT)}):
                store = DshCredentialStore.from_environment(folder)
                self.assertNotIn('STELLA_ELEARNING_CREDENTIAL', os.environ)
                self.assertEqual(store.load_settings()['username'], 'synthetic')
            for value in ['', 'invalid', '{}', '{"username": "synthetic", "password": 42}']:
                with patch.dict(os.environ, {'STELLA_ELEARNING_CREDENTIAL': value}):
                    with self.assertRaises(ValueError):
                        DshCredentialStore.from_environment(folder)
                    self.assertNotIn('STELLA_ELEARNING_CREDENTIAL', os.environ)
            with self.assertRaises(ValueError):
                create_server(folder, legacy_dir=folder, dsh_credentials=True)
            with self.assertRaises(ValueError):
                create_server(folder, hosted_connector=True, dsh_credentials=True)


class DshWireTest(unittest.IsolatedAsyncioTestCase):
    async def test_query_transfer_tools_and_forged_admin_write_refusal(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            parameters = StdioServerParameters(command=sys.executable,
                args=[str(root / 'mcp_server.py'), '--data-dir', folder, '--dsh-credentials'],
                env={'HOME': folder, 'STELLA_ELEARNING_CREDENTIAL': json.dumps(ACCOUNT), 'PYTHONDONTWRITEBYTECODE': '1'})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=10)
                    inventory = await session.list_tools()
                    self.assertEqual({tool.name for tool in inventory.tools}, {'list_courses', 'fetch', 'list_files', 'download_file', 'get_assignment', 'submit_assignment'})
                    for tool in inventory.tools:
                        self.assertEqual(tool.annotations.readOnlyHint, tool.name not in {'download_file', 'submit_assignment'})
                    self.assertFalse(next(tool for tool in inventory.tools if tool.name == 'download_file').annotations.destructiveHint)
                    self.assertNotIn('synthetic-password', inventory.model_dump_json())
                    rejected = await session.call_tool('save_settings', {'payload': {'password': 'never-persist'}})
                    self.assertTrue(rejected.isError)
            self.assertFalse((Path(folder) / 'elearning.json').exists())

    async def test_failures_keep_categories_and_hide_secrets_on_wire(self):
        root = Path(__file__).resolve().parents[1]
        bootstrap = '''
import asyncio
from unittest.mock import patch
from elearning_mcp.errors import SyncError
from elearning_mcp.server import main
failures = iter([SyncError('synthetic-secret', code=code) for code in
            ['browser_unavailable', 'network_error', 'login_form_error', 'login_timeout']])
def courses(self):
    raise next(failures)
with patch('elearning_mcp.server.ElearningService.list_courses', new=courses):
    asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as folder:
            parameters = StdioServerParameters(command=sys.executable,
                args=['-c', bootstrap, '--data-dir', folder, '--dsh-credentials'], cwd=str(root),
                env={'HOME': folder, 'STELLA_ELEARNING_CREDENTIAL': json.dumps(ACCOUNT), 'PYTHONDONTWRITEBYTECODE': '1'})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=10)
                    for code in ['browser_unavailable', 'network_error', 'login_form_error', 'login_timeout']:
                        result = await session.call_tool('list_courses', {})
                        self.assertTrue(result.isError)
                        self.assertTrue(result.content[0].text.startswith('{'), result.content[0].text)
                        self.assertEqual(json.loads(result.content[0].text)['error_code'], code)
                        self.assertNotIn('synthetic-secret', result.model_dump_json())

    async def test_download_and_submit_over_real_mcp_transport_with_synthetic_canvas(self):
        root = Path(__file__).resolve().parents[1]
        bootstrap = '''
import asyncio
from unittest.mock import patch
from tests.test_assignment_transfers import Canvas
from elearning_mcp.server import main
canvas = Canvas()
with patch('elearning_mcp.service.ensure_login', return_value=canvas):
    asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder).resolve()
            homework = directory / 'answer.pdf'
            homework.write_bytes(b'%PDF-homework')
            destination = directory / 'lecture.pdf'
            parameters = StdioServerParameters(command=sys.executable,
                args=['-c', bootstrap, '--data-dir', str(directory / 'data'), '--dsh-credentials'], cwd=str(root),
                env={'HOME': str(directory), 'ALCOR_ELEARNING_CREDENTIAL': json.dumps(ACCOUNT), 'PYTHONDONTWRITEBYTECODE': '1'})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=10)
                    downloaded = await session.call_tool('download_file', {'course_id': '42', 'file_id': '10', 'destination': str(destination)})
                    self.assertFalse(downloaded.isError, downloaded)
                    self.assertEqual(destination.read_bytes(), b'%PDF-homework')
                    duplicate = await session.call_tool('download_file', {'course_id': '42', 'file_id': '10', 'destination': str(destination)})
                    self.assertTrue(duplicate.isError)
                    self.assertEqual(json.loads(duplicate.content[0].text)['error_code'], 'destination_exists')
                    details = await session.call_tool('get_assignment', {'course_id': '42', 'assignment_id': '20'})
                    self.assertEqual(details.structuredContent['value']['submission']['attempt'], 0)
                    submitted = await session.call_tool('submit_assignment', {'course_id': '42', 'assignment_id': '20',
                        'file_paths': [str(homework)], 'expected_attempt': 0})
                    self.assertFalse(submitted.isError, submitted)
                    self.assertEqual(submitted.structuredContent['value']['status'], 'submitted')
                    invalid = await session.call_tool('submit_assignment', {'course_id': '42', 'assignment_id': '20',
                        'file_paths': [str(homework)], 'expected_attempt': -1})
                    self.assertTrue(invalid.isError)
            self.assertFalse(any(b'synthetic-password' in path.read_bytes() for path in directory.rglob('*') if path.is_file()))
