"""Test adapters over the independent MCP store and service.

Only the retired test calling conventions are translated; all validation,
fetching, cancellation, authorization and downloads execute Server business code.
"""
import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

NAME = 'stella_elearning_domain_tests'
root = Path(__file__).resolve().parents[2] / 'elearning_mcp'
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(NAME, root / '__init__.py', submodule_search_locations=[str(root)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = package
    spec.loader.exec_module(package)
store_module = importlib.import_module(NAME + '.store')
service_module = importlib.import_module(NAME + '.service')
client = importlib.import_module(NAME + '.client')
source = importlib.import_module(NAME + '.source')
file_sync = importlib.import_module(NAME + '.file_sync')
errors = importlib.import_module(NAME + '.errors')
LoginRequired, SyncCancelled, SyncError = errors.LoginRequired, errors.SyncCancelled, errors.SyncError
ElearningStore, ElearningService = store_module.ElearningStore, service_module.ElearningService


class Settings:
    DEFAULTS = store_module.DEFAULTS
    def settings_path(self):
        raise RuntimeError('test must bind an isolated settings path')
    def store(self):
        return ElearningStore(self.settings_path().parent)
    def __getattr__(self, name):
        return getattr(self.store(), name)
    def check_connection(self, payload):
        return ElearningService(self.store().root).test_connection(payload)
    def elearning_sources(self):
        cfg = self.store().load_settings()
        return [ElearningSource(cfg, briefing_only=True)] if cfg['enabled'] else []


settings = Settings()


def Canvas(config, *args, **kwargs):
    return client.Canvas(config, settings.store(), *args, **kwargs)


def ElearningSource(config, **kwargs):
    return source.ElearningSource(config, settings.store(), **kwargs)


def sync_files(client, config, rows, **kwargs):
    return file_sync.sync_files(client, config, rows, settings.store(), **kwargs)


class ServiceCall:
    def __init__(self, client_factory=None, clock=None):
        self.client_factory, self.clock, self.cancel_event = client_factory, clock, None
    def execute(self, params):
        svc = ElearningService(settings.store().root, client_factory=self.client_factory,
                               clock=self.clock, cancel_event=self.cancel_event)
        method = 'list_courses' if params['action'] == 'courses' else params['action']
        import json
        try:
            value=getattr(svc,method)(**{k:v for k,v in params.items() if k!='action'})
            value={'courses':value} if method=='list_courses' else value
            return SimpleNamespace(status='success',result=json.dumps(value,ensure_ascii=False))
        except (ValueError,LoginRequired,SyncCancelled,SyncError) as exc:
            return SimpleNamespace(status='error',result=str(exc))
