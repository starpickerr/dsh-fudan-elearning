"""路径、配置读写、文本整理和运行锁等通用工具。"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import re
import urllib.parse
from html.parser import HTMLParser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

from .errors import SyncError

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / ".private"
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "config" / "config.yaml"
LOG_FILE = ROOT / "logs" / "elearning.log"
SHANGHAI = dt.timezone(dt.timedelta(hours=8))


def load_config(path=CONFIG_FILE):
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise SyncError("配置文件 YAML 格式不正确，请检查缩进和引号。") from None
    if not isinstance(config, dict):
        raise SyncError("配置文件必须是 YAML 键值表。")
    base = config.get("base_url")
    if not isinstance(base, str):
        raise SyncError("请在配置中填写 base_url。")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SyncError("base_url 必须是不含账号密码的 HTTPS 地址。")
    for key in ("courses", "extensions", "include_patterns", "ignore_patterns", "ignore_file_ids"):
        if key in config and not isinstance(config[key], list):
            raise SyncError("配置项 {} 必须是列表。".format(key))
    if config.get("only_file_ids") is not None and not isinstance(config["only_file_ids"], list):
        raise SyncError("only_file_ids 必须是列表或 null。")
    if "output_dir" in config and (not isinstance(config["output_dir"], str) or not config["output_dir"].strip()):
        raise SyncError("output_dir 必须是非空路径。")
    for key in ("request_interval", "announcement_days"):
        if key in config and (not isinstance(config[key], (int, float)) or config[key] < 0):
            raise SyncError("配置项 {} 必须是非负数。".format(key))
    return config


def save_config(path, config):
    atomic_write(path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))


def read_secrets(required=False):
    if not ENV_FILE.is_file():
        if required:
            raise SyncError("缺少 .env，请参考 .env.example 创建并填写 ELEARNING_USERNAME 和 ELEARNING_PASSWORD。")
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        raise SyncError("缺少 .env 读取组件，请运行 python -m pip install -r requirements.txt。") from None
    os.chmod(ENV_FILE, 0o600)
    return dotenv_values(ENV_FILE, encoding="utf-8", interpolate=False)


def setup_logging(path=None):
    path = Path(path) if path is not None else LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("elearning")
    # Avoid duplicate handlers if the entry point is called repeatedly.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def atomic_write(path, data, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        if private:
            os.chmod(tmp, 0o600)
        stream.write(data)
    os.replace(tmp, path)


def save_json(path, data, private=False):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2), private)


def safe_name(value):
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", str(value))
    value = value.strip(" .") or "未命名"
    if len(value.encode("utf-8")) > 170:
        suffix = Path(value).suffix
        suffix = suffix if len(suffix.encode("utf-8")) < 16 else ""
        stem = value[:-len(suffix)] if suffix else value
        value = stem.encode("utf-8")[:170 - len(suffix.encode("utf-8"))].decode("utf-8", errors="ignore") + suffix
    return value


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_date(value):
    if not value:
        return None
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.replace(tzinfo=dt.timezone.utc) if result.tzinfo is None else result
    except (ValueError, AttributeError):
        return None


def display_date(value):
    parsed = parse_date(value)
    return parsed.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M") if parsed else "未设置"


def md(value):
    return re.sub(r"([\\`*_[\]<>|])", r"\\\1", str(value)).replace("\n", " ")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.skip = [], [], 0
        self.file_references = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")
        if tag == "a":
            attributes = dict(attrs)
            href = attributes.get("href", "")
            if href:
                self.links.append(href)
                self.file_references.append(href)
            if attributes.get("data-api-endpoint"):
                self.file_references.append(attributes["data-api-endpoint"])
        if tag == "img":
            attributes = dict(attrs)
            for key in ("src", "data-api-endpoint"):
                if attributes.get(key):
                    self.file_references.append(attributes[key])
            if attributes.get("src"):
                self.links.append(attributes["src"])
            if attributes.get("alt"):
                self.parts.append(attributes["alt"])

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if not self.skip and tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extract(html):
    parser = TextExtractor()
    parser.feed(html or "")
    text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    return text, list(dict.fromkeys(parser.links))


def canvas_attachment_ids(html, base_url, page_url):
    parser = TextExtractor()
    parser.feed(html or "")
    origin = urllib.parse.urlparse(base_url).netloc
    ids = []
    for reference in parser.file_references:
        url = urllib.parse.urlparse(urllib.parse.urljoin(page_url, reference))
        if url.scheme != "https" or url.netloc != origin:
            continue
        match = re.search(r"/files/(\d+)(?:/|$)", url.path)
        fid = match[1] if match else None
        if fid is None and url.path.endswith("/files"):
            values = urllib.parse.parse_qs(url.query).get("preview", [])
            fid = values[0] if values and values[0].isdigit() else None
        if fid and fid not in ids:
            ids.append(fid)
    return ids


def next_link(header):
    for url, rel in re.findall(r'<([^>]+)>\s*;\s*rel="([^"]+)"', header or ""):
        if "next" in rel.split():
            return url
    return None


def choices(value, count):
    selected = set()
    for part in re.split(r"[,，\s]+", value.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise SyncError("请输入编号，例如 1,3-5。")
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end > count or start > end:
            raise SyncError("编号超出范围。")
        selected.update(range(start - 1, end))
    if not selected:
        raise SyncError("至少选择一项。")
    return sorted(selected)


def prompt_choices(prompt, count):
    while True:
        try:
            return choices(input(prompt), count)
        except SyncError as error:
            print(error)


@contextlib.contextmanager
def run_lock():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    with (PRIVATE / "run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError("另一个同步正在运行，请稍后再试。") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
