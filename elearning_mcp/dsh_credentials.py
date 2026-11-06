"""DSH owns credentials; this process keeps only a transient login session."""
import json
import os
from copy import deepcopy
from threading import RLock

from .store import DEFAULTS, ElearningStore


class DshCredentialStore(ElearningStore):
    def __init__(self, root, account):
        super().__init__(root)
        if (not isinstance(account, dict) or set(account) != {'username', 'password'}
                or not isinstance(account['username'], str) or not 0 < len(account['username'].strip()) <= 160
                or not isinstance(account['password'], str) or not 0 < len(account['password']) <= 4096):
            raise ValueError('DSH eLearning 凭据无效，请在 DSH 中重新输入。')
        self.account = {**account, 'username': account['username'].strip()}
        self.session = None
        self.lock = RLock()

    @classmethod
    def from_environment(cls, root):
        # Remove before launching a browser; the password is never inherited by its children.
        raw = os.environ.pop('STELLA_ELEARNING_CREDENTIAL', '')
        try:
            account = json.loads(raw)
        except (ValueError, TypeError):
            raise ValueError('请通过 DSH 原生凭据连接启动 eLearning。') from None
        return cls(root, account)

    def load_settings(self):
        old = super().load_settings()
        # Never use the old password, token or session file, including Keychain references.
        return {**DEFAULTS, 'enabled': True, **self.account,
                'courses': old.get('courses', []) if old.get('username') == self.account['username'] else [],
                'request_interval': old.get('request_interval', 0.4)}

    def has_credentials(self, cfg):
        return bool(self.account['password'])

    def load_session(self, config):
        with self.lock:
            if self.session is None:
                raise FileNotFoundError('DSH 连接尚未登录')
            return deepcopy(self.session)

    def save_session(self, config, data):
        with self.lock:
            self.session = deepcopy(data)

    def save_settings(self, payload):
        raise PermissionError('请在 DSH 中管理 eLearning 凭据。')
