"""
وكيل الربط: نفق SSH عكسي من جهاز قاعدة العميل إلى sshd داخل حاوية التطبيق.

جهاز العميل يفتح اتصالاً صادراً (ssh -R) فيظهر أوراكل العميل على 127.0.0.1:ORACLE_LINK_LOCAL_PORT
داخل الحاوية — بلا Port Forward في راوتر العميل وبلا كشف أوراكل للإنترنت.

الشاشة تولّد زوج مفاتيح جديداً في كل مرة: العام يُكتب مقيّداً في authorized_keys
(لا يسمح إلا بعكس منفذ واحد)، والخاص يُسلَّم في الحزمة فقط ولا يُحفظ على الخادم.

مفتاح الخادم وسطر authorized_keys محفوظان في القاعدة (OracleLinkKeys) ويُستعادان للقرص
عند كل إقلاع — فيبقى الربط بعد إعادة النشر ولا يتغيّر إلا بتوليد ملف جديد.
"""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import zipfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

AUTHORIZED_KEYS = 'authorized_keys'
HOST_KEY = 'ssh_host_ed25519_key'
HOST_KEY_PUB = 'ssh_host_ed25519_key.pub'
PACKAGE_ROOT = 'OracleLink'
KEY_COMMENT = 'oracle-link'


class OracleLinkError(Exception):
    """تعذّر توليد/إلغاء ملف الربط."""


def _link_dir() -> Path:
    return Path(settings.ORACLE_LINK_DIR)


def _local_port() -> int:
    return int(settings.ORACLE_LINK_LOCAL_PORT)


def _authorized_line(public_key: str) -> str:
    # restrict يمنع كل شيء، ثم port-forwarding مع permitlisten يسمح بعكس هذا المنفذ وحده
    return (
        f'restrict,port-forwarding,permitlisten="127.0.0.1:{_local_port()}" '
        f'{public_key.strip()} {KEY_COMMENT}\n'
    )


def _generate_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode('ascii')
    public = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode('ascii')
    return private, public


def _server_host_key() -> str:
    path = _link_dir() / HOST_KEY_PUB
    try:
        parts = path.read_text(encoding='ascii').split()
    except OSError as exc:
        raise OracleLinkError(
            'خادم الربط غير جاهز (مفتاح الخادم غير موجود). أعد نشر التطبيق ثم حاول مرة أخرى.'
        ) from exc
    if len(parts) < 2:
        raise OracleLinkError('مفتاح خادم الربط تالف. أعد نشر التطبيق.')
    return f'{parts[0]} {parts[1]}'


def _write_file(name: str, content: str, mode: int) -> None:
    directory = _link_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    tmp = directory / (name + '.tmp')
    tmp.write_text(content, encoding='ascii')
    os.chmod(tmp, mode)
    os.replace(tmp, target)


def _write_authorized_keys(content: str) -> None:
    _write_file(AUTHORIZED_KEYS, content, 0o644)


def _keys_row():
    from .models import OracleLinkKeys

    row, _ = OracleLinkKeys.objects.get_or_create(pk=OracleLinkKeys.SINGLETON_PK)
    return row


def _save_authorized_keys(content: str) -> None:
    """يحفظ مفتاح جهاز العميل في القاعدة ثم على القرص (sshd يقرأ الملف مع كل اتصال)."""
    row = _keys_row()
    row.authorized_keys = content
    row.save(update_fields=['authorized_keys', 'updated_at'])
    _write_authorized_keys(content)


def restore_link_files() -> str:
    """يكتب مفاتيح الربط المحفوظة إلى مجلد sshd قبل تشغيله — يُستدعى عند الإقلاع.

    أول مرة: يعتمد مفتاح الخادم الموجود على القرص إن وُجد (حتى لا تبطل حزمة مثبّتة)،
    وإلا يولّد مفتاحاً جديداً، ثم يحفظه في القاعدة.
    """
    row = _keys_row()
    private_path = _link_dir() / HOST_KEY
    public_path = _link_dir() / HOST_KEY_PUB
    if row.host_private_key and row.host_public_key:
        source = 'database'
    elif private_path.exists() and public_path.exists():
        row.host_private_key = private_path.read_text(encoding='ascii')
        row.host_public_key = public_path.read_text(encoding='ascii').strip()
        source = 'disk'
    else:
        private, public = _generate_keypair()
        row.host_private_key = private
        row.host_public_key = f'{public} oracle-link-server'
        source = 'generated'
    if source != 'database':
        row.save(update_fields=['host_private_key', 'host_public_key', 'updated_at'])
    _write_file(HOST_KEY, row.host_private_key, 0o600)
    _write_file(HOST_KEY_PUB, row.host_public_key.strip() + '\n', 0o644)
    _write_authorized_keys(row.authorized_keys)
    logger.info('Oracle link keys restored (host key from %s)', source)
    return source


def _template_files() -> list[Path]:
    root = Path(settings.ORACLE_LINK_TEMPLATE_DIR)
    if not root.is_dir():
        raise OracleLinkError(f'مجلد قالب الربط غير موجود: {root.name}')
    return sorted(p for p in root.iterdir() if p.is_file())


def generate_link_package(public_host: str) -> bytes:
    """يولّد مفتاحاً جديداً (يُلغي السابق) ويعيد حزمة zip لجهاز العميل."""
    host = str(public_host or '').strip()
    if not host:
        raise OracleLinkError('تعذّر تحديد عنوان الخادم. اضبط ORACLE_LINK_PUBLIC_HOST.')
    ssh_port = int(settings.ORACLE_LINK_SSH_PORT)
    host_key = _server_host_key()
    templates = _template_files()

    private, public = _generate_keypair()
    _save_authorized_keys(_authorized_line(public))

    config = {
        'server': host,
        'ssh_port': ssh_port,
        'user': 'oralink',
        'remote_port': _local_port(),
        'oracle_host': 'localhost',
        'oracle_port': 1521,
    }
    known_host = host if ssh_port == 22 else f'[{host}]:{ssh_port}'

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in templates:
            zf.write(path, f'{PACKAGE_ROOT}/{path.name}')
        zf.writestr(f'{PACKAGE_ROOT}/id_ed25519', private)
        zf.writestr(f'{PACKAGE_ROOT}/known_hosts', f'{known_host} {host_key}\n')
        zf.writestr(
            f'{PACKAGE_ROOT}/config.json',
            json.dumps(config, ensure_ascii=False, indent=2),
        )
    logger.info('Oracle link package generated for %s:%s', host, ssh_port)
    return buffer.getvalue()


def revoke_link() -> None:
    """يلغي المفتاح المفعّل — النفق الحالي يُرفض عند إعادة الاتصال."""
    _save_authorized_keys('')
    logger.info('Oracle link revoked')


def link_status() -> dict:
    path = _link_dir() / AUTHORIZED_KEYS
    try:
        has_key = bool(path.read_text(encoding='ascii').strip())
    except OSError:
        has_key = False
    connected = False
    if has_key:
        try:
            with socket.create_connection(('127.0.0.1', _local_port()), timeout=1):
                connected = True
        except OSError:
            connected = False
    return {
        'has_key': has_key,
        'connected': connected,
        'server_ready': (_link_dir() / HOST_KEY_PUB).exists(),
        'local_host': '127.0.0.1',
        'local_port': _local_port(),
        'ssh_port': int(settings.ORACLE_LINK_SSH_PORT),
    }
