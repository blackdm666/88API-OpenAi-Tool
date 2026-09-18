"""Current-user Windows DPAPI storage. No plaintext credential files."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

_lock = threading.RLock()


def documents_directory():
    if os.name != 'nt':
        raise RuntimeError('账号资料库需要 Windows 用户加密支持')
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) != 0:
        raise RuntimeError('无法读取 Windows 文档目录')
    return Path(buffer.value)


def vault_path():
    return documents_directory() / 'OpenAI-Token-Manager' / 'credentials' / 'accounts.dpapi'


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, *, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('账号资料库需要 Windows DPAPI，不能降级为明文')
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    dll = ctypes.WinDLL('crypt32', use_last_error=True)
    name = 'CryptUnprotectData' if decrypt else 'CryptProtectData'
    fn = getattr(dll, name)
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('账号资料加解密失败，请使用保存资料的同一 Windows 用户')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class CredentialVault:
    def __init__(self, path=None):
        self.path = Path(path) if path else vault_path()

    def load(self):
        with _lock:
            if not self.path.exists():
                return {}
            try:
                data = json.loads(crypt(self.path.read_bytes(), decrypt=True))
                if data.get('version') != 1 or not isinstance(data.get('accounts'), dict):
                    raise ValueError()
                return data['accounts']
            except Exception:
                raise RuntimeError('无法读取加密账号资料库；请检查 Windows 用户或重新导入资料') from None

    def save_accounts(self, accounts):
        with _lock:
            data = self.load()
            incoming = {}
            for account in accounts:
                key = account.email.strip().lower()
                value = {'email': account.email.strip(), 'password': account.password,
                         'totp_secret': account.totp_secret}
                if key in incoming and incoming[key] != value:
                    raise ValueError('同一邮箱有多份不同的授权资料，请核对后保存')
                incoming[key] = value
            data.update(incoming)
            encrypted = crypt(json.dumps({'version': 1, 'accounts': data}, ensure_ascii=False).encode())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix='.encrypted-')
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return len(data)

    def lookup(self, email):
        return self.load().get(str(email).strip().lower())


def credential_revision(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() if data else ''
