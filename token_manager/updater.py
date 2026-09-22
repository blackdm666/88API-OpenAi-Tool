"""Safe cloud update checks and Windows executable replacement helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any
from urllib.parse import urlparse

import requests


class UpdateError(RuntimeError):
    """Raised when an update manifest or package cannot be trusted/applied."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    size: int | None = None
    notes: str = ""
    mandatory: bool = False
    release_url: str = ""


def version_key(value: str) -> tuple[int, int, int, int, str]:
    """Compare the app's 2.2.0-88api.56 style versions safely."""
    text = str(value or "").strip()
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:[-+](?:88api[.-]?)?(\d+))?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise UpdateError(f"更新版本号格式无效：{text or '空'}")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4) or 0),
        text.casefold(),
    )


def _https_url(value: Any, field: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise UpdateError(f"{field} 必须使用 HTTPS 地址")
    return url


def _notes_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def parse_manifest(payload: dict[str, Any]) -> UpdateInfo:
    if not isinstance(payload, dict):
        raise UpdateError("更新清单不是 JSON 对象")
    version = str(payload.get("version") or "").strip()
    version_key(version)
    download_url = _https_url(
        payload.get("download_url") or payload.get("url"),
        "下载地址",
    )
    sha256 = str(payload.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("更新清单缺少有效的 SHA-256")
    raw_size = payload.get("size")
    size: int | None
    if raw_size in (None, ""):
        size = None
    else:
        try:
            size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise UpdateError("更新清单中的文件大小无效") from exc
        if size <= 0:
            raise UpdateError("更新清单中的文件大小必须大于0")
    return UpdateInfo(
        version=version,
        download_url=download_url,
        sha256=sha256,
        size=size,
        notes=_notes_text(payload.get("notes") or payload.get("changes")),
        mandatory=bool(payload.get("mandatory", False)),
        release_url=str(payload.get("release_url") or "").strip(),
    )


def fetch_manifest(manifest_url: str, *, timeout: float = 15.0) -> UpdateInfo:
    url = _https_url(manifest_url, "更新清单地址")
    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"读取更新清单失败：{exc}") from exc
    return parse_manifest(payload)


def check_for_update(
    manifest_url: str,
    *,
    current_version: str,
    timeout: float = 15.0,
) -> UpdateInfo | None:
    info = fetch_manifest(manifest_url, timeout=timeout)
    return info if version_key(info.version) > version_key(current_version) else None


def _safe_package_name(version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", version).strip("._")
    return f"88API-号池自动维护工具-v{safe}.exe"


def download_update_package(
    info: UpdateInfo,
    *,
    destination_dir: str | Path | None = None,
    progress_cb: Callable[[int, int | None], None] | None = None,
    timeout: tuple[float, float] = (15.0, 120.0),
) -> Path:
    destination = Path(destination_dir or tempfile.gettempdir()).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    final_path = destination / _safe_package_name(info.version)
    partial_path = final_path.with_suffix(final_path.suffix + ".part")
    digest = hashlib.sha256()
    total = info.size
    received = 0
    try:
        with requests.get(info.download_url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            header_size = response.headers.get("Content-Length")
            if total is None and header_size:
                try:
                    total = int(header_size)
                except ValueError:
                    total = None
            with partial_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if callable(progress_cb):
                        progress_cb(received, total)
        if total is not None and received != total:
            raise UpdateError(f"更新包大小不一致：期望 {total} 字节，实际 {received} 字节")
        actual = digest.hexdigest().lower()
        if actual != info.sha256:
            raise UpdateError("更新包 SHA-256 校验失败，已拒绝安装")
        os.replace(partial_path, final_path)
        return final_path
    except UpdateError:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise UpdateError(f"下载更新包失败：{exc}") from exc


def current_executable() -> Path:
    if not getattr(sys, "frozen", False):
        raise UpdateError("开发模式不能自动替换程序，请使用打包后的 EXE 测试更新")
    executable = Path(sys.executable).resolve()
    if executable.suffix.lower() != ".exe":
        raise UpdateError("当前运行文件不是 Windows EXE")
    return executable


def _wait_for_process(pid: int, timeout: float = 60.0) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
        if handle:
            try:
                result = ctypes.windll.kernel32.WaitForSingleObject(
                    handle, int(max(0.0, timeout) * 1000)
                )
                if result == 0x00000102:
                    raise UpdateError("旧版本程序未能在规定时间内退出")
                return
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    raise UpdateError("旧版本程序未能在规定时间内退出")


def launch_update(package_path: str | Path, *, parent_pid: int | None = None) -> None:
    """Start an external updater so Windows never has to replace a locked EXE."""
    target = current_executable()
    package = Path(package_path).expanduser().resolve()
    if not package.is_file():
        raise UpdateError(f"更新包不存在：{package}")
    if package == target:
        raise UpdateError("更新包不能与当前运行文件相同")
    if os.name != "nt":
        raise UpdateError("云更新目前只支持 Windows 打包版")
    script_fd, script_name = tempfile.mkstemp(prefix="88api-updater-", suffix=".ps1")
    os.close(script_fd)
    script_path = Path(script_name)
    script_path.write_text(
        textwrap.dedent(
            r"""
            param(
                [Parameter(Mandatory=$true)][string]$PackagePath,
                [Parameter(Mandatory=$true)][string]$TargetPath,
                [Parameter(Mandatory=$true)][int]$ParentPid,
                [Parameter(Mandatory=$true)][string]$ScriptPath
            )
            $ErrorActionPreference = 'Stop'
            $deadline = (Get-Date).AddSeconds(90)
            while ((Get-Date) -lt $deadline) {
                if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
                Start-Sleep -Milliseconds 250
            }
            if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
                throw '旧版本程序未能在规定时间内退出'
            }
            $package = [IO.Path]::GetFullPath($PackagePath)
            $target = [IO.Path]::GetFullPath($TargetPath)
            $final = [IO.Path]::Combine(
                [IO.Path]::GetDirectoryName($target),
                [IO.Path]::GetFileName($package)
            )
            $backup = "$target.previous"
            $movedTarget = $false
            try {
                if (Test-Path -LiteralPath $backup) {
                    Remove-Item -LiteralPath $backup -Force
                }
                Move-Item -LiteralPath $target -Destination $backup -Force
                $movedTarget = $true
                Move-Item -LiteralPath $package -Destination $final -Force
                Start-Process -FilePath $final -WorkingDirectory ([IO.Path]::GetDirectoryName($final))
                Remove-Item -LiteralPath $backup -Force
            } catch {
                if ($movedTarget -and (Test-Path -LiteralPath $backup) -and
                    -not (Test-Path -LiteralPath $target)) {
                    Move-Item -LiteralPath $backup -Destination $target -Force
                }
                throw
            } finally {
                Remove-Item -LiteralPath $ScriptPath -Force -ErrorAction SilentlyContinue
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(script_path),
        "-PackagePath",
        str(package),
        "-TargetPath",
        str(target),
        "-ParentPid",
        str(int(parent_pid or os.getpid())),
        "-ScriptPath",
        str(script_path),
    ]
    creation_flags = 0
    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    try:
        subprocess.Popen(
            command,
            cwd=str(target.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
    except Exception:
        script_path.unlink(missing_ok=True)
        raise


def apply_update(
    package_path: str | Path,
    target_path: str | Path,
    *,
    parent_pid: int = 0,
) -> int:
    """Replace the old EXE after it exits and relaunch the new one."""
    package = Path(package_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    if not package.is_file() or package.suffix.lower() != ".exe":
        raise UpdateError("更新包不是有效 EXE")
    if target.suffix.lower() != ".exe" or not target.parent.is_dir():
        raise UpdateError("更新目标不是有效 Windows EXE 路径")
    _wait_for_process(int(parent_pid or 0))
    backup = target.with_name(f"{target.stem}.previous{target.suffix}")
    try:
        if backup.exists():
            backup.unlink()
        os.replace(target, backup)
        try:
            os.replace(package, target)
        except Exception:
            os.replace(backup, target)
            raise
        subprocess.Popen(
            [str(target)],
            cwd=str(target.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return 0
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"替换程序失败：{exc}") from exc
