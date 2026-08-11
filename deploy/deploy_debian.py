from __future__ import annotations

import base64
import getpass
import hashlib
import os
import posixpath
import shlex
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import paramiko


PROJECT_DIR = Path(__file__).resolve().parents[1]
HOST = os.environ.get("IMG_COMPARE_DEPLOY_HOST", "192.168.100.71")
PORT = int(os.environ.get("IMG_COMPARE_DEPLOY_PORT", "32200"))
USER = os.environ.get("IMG_COMPARE_DEPLOY_USER", "qiujiawei")
REMOTE_DIR = os.environ.get(
    "IMG_COMPARE_DEPLOY_DIR", f"/home/{USER}/img_compare_2"
).rstrip("/")
EXPECTED_HOST_KEY_MD5 = os.environ.get(
    "IMG_COMPARE_DEPLOY_HOST_KEY_MD5",
    "d2:cb:29:a2:02:f4:c7:b4:d2:1b:51:eb:70:df:18:c6",
).lower()

LOCAL_ONLY_FILES = {
    "deploy.bat",
    "requirements-deploy.txt",
    "deploy/deploy_debian.py",
}
PROTECTED_REMOTE_PREFIXES = (
    "data/",
    ".venv/",
    ".run/",
    "logs/",
    ".deploy-backups/",
)
PROTECTED_REMOTE_FILES = {"config.env", "config.local.bat"}


class PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        fingerprint = key.get_fingerprint().hex(":").lower()
        if fingerprint != EXPECTED_HOST_KEY_MD5:
            raise paramiko.SSHException(
                "服务器主机密钥不匹配："
                f"期望 {EXPECTED_HOST_KEY_MD5}，实际 {fingerprint}。"
            )
        client._host_keys.add(hostname, key.get_name(), key)


def validate_remote_dir() -> None:
    expected_parent = PurePosixPath("/home") / USER
    remote_path = PurePosixPath(REMOTE_DIR)
    if remote_path.parent != expected_parent or remote_path.name != "img_compare_2":
        raise RuntimeError(
            "为避免覆盖错误目录，远端路径必须是 "
            f"/home/{USER}/img_compare_2，当前为 {REMOTE_DIR}。"
        )


def deployable_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        relative_name = raw_name.decode("utf-8")
        relative_path = Path(relative_name)
        portable_name = relative_path.as_posix()
        if portable_name in LOCAL_ONLY_FILES:
            continue
        if portable_name in PROTECTED_REMOTE_FILES:
            continue
        if portable_name.startswith(PROTECTED_REMOTE_PREFIXES):
            continue
        if ".." in relative_path.parts or relative_path.is_absolute():
            raise RuntimeError(f"拒绝部署不安全的相对路径：{portable_name}")
        if (PROJECT_DIR / relative_path).is_file():
            paths.append(relative_path)
    if not paths:
        raise RuntimeError("没有找到可部署文件。")
    return sorted(paths, key=lambda path: path.as_posix())


def connect() -> paramiko.SSHClient:
    password = os.environ.get("IMG_COMPARE_DEPLOY_PASSWORD")
    if password is None:
        password = getpass.getpass(f"{USER}@{HOST} 的 SSH 密码（不会保存）：")

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(PinnedHostKeyPolicy())
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    server_key = client.get_transport().get_remote_server_key()
    fingerprint = server_key.get_fingerprint().hex(":").lower()
    if fingerprint != EXPECTED_HOST_KEY_MD5:
        client.close()
        raise paramiko.SSHException("服务器主机密钥校验失败。")
    print(f"已连接 {HOST}:{PORT}，主机密钥已校验。")
    return client


def remote_command(script: str) -> str:
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return f"printf '%s' {payload} | base64 -d | bash"


def run_capture(
    client: paramiko.SSHClient,
    script: str,
    *,
    timeout: int = 30,
) -> tuple[int, str, str]:
    _stdin, stdout, stderr = client.exec_command(
        remote_command(script), timeout=timeout
    )
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, output, error


def run_streaming(
    client: paramiko.SSHClient,
    script: str,
    *,
    timeout: int = 300,
) -> tuple[int, str]:
    channel = client.get_transport().open_session()
    channel.get_pty(term="dumb", width=160, height=40)
    channel.exec_command(remote_command(script))
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while True:
        received = False
        while channel.recv_ready():
            chunk = channel.recv(32768)
            chunks.append(chunk)
            sys.stdout.write(chunk.decode("utf-8", "replace"))
            sys.stdout.flush()
            received = True
        if channel.exit_status_ready() and not channel.recv_ready():
            break
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError(f"远端命令超过 {timeout} 秒仍未完成。")
        if not received:
            time.sleep(0.05)
    code = channel.recv_exit_status()
    return code, b"".join(chunks).decode("utf-8", "replace")


def remote_exists(sftp: paramiko.SFTPClient, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def ensure_remote_dir(sftp: paramiko.SFTPClient, path: str) -> None:
    current = "/"
    for part in PurePosixPath(path).parts[1:]:
        current = posixpath.join(current, part)
        if not remote_exists(sftp, current):
            sftp.mkdir(current, mode=0o755)


def remote_file_hash(sftp: paramiko.SFTPClient, path: str) -> str:
    digest = hashlib.sha256()
    with sftp.file(path, "rb") as remote_file:
        while chunk := remote_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def upload_file(
    sftp: paramiko.SFTPClient,
    relative_path: Path,
    stamp: str,
) -> None:
    local_path = PROJECT_DIR / relative_path
    portable_name = relative_path.as_posix()
    remote_path = posixpath.join(REMOTE_DIR, *PurePosixPath(portable_name).parts)
    temporary_path = f"{remote_path}.codex-{stamp}"
    ensure_remote_dir(sftp, posixpath.dirname(remote_path))

    if remote_exists(sftp, remote_path):
        mode = stat.S_IMODE(sftp.stat(remote_path).st_mode)
    else:
        mode = 0o755 if relative_path.suffix == ".sh" else 0o644

    if relative_path.suffix == ".sh":
        content = local_path.read_bytes().replace(b"\r\n", b"\n")
        with sftp.file(temporary_path, "wb") as remote_file:
            remote_file.write(content)
    else:
        sftp.put(str(local_path), temporary_path)
    sftp.chmod(temporary_path, mode)
    sftp.posix_rename(temporary_path, remote_path)


def backup_script(existing_files: list[Path], backup_dir: str) -> str:
    commands = ["set -Eeuo pipefail", "umask 077"]
    for relative_path in existing_files:
        portable_name = relative_path.as_posix()
        source = posixpath.join(REMOTE_DIR, *PurePosixPath(portable_name).parts)
        destination = posixpath.join(
            backup_dir, *PurePosixPath(portable_name).parts
        )
        commands.append(f"mkdir -p {shlex.quote(posixpath.dirname(destination))}")
        commands.append(
            f"cp --preserve=mode,timestamps -- {shlex.quote(source)} "
            f"{shlex.quote(destination)}"
        )
    return "\n".join(commands)


def rollback_script(
    existing_files: list[Path],
    new_files: list[Path],
    backup_dir: str,
) -> str:
    commands = ["set -u", f"cd {shlex.quote(REMOTE_DIR)}"]
    for relative_path in existing_files:
        portable_name = relative_path.as_posix()
        source = posixpath.join(
            backup_dir, *PurePosixPath(portable_name).parts
        )
        destination = posixpath.join(
            REMOTE_DIR, *PurePosixPath(portable_name).parts
        )
        commands.append(
            f"cp --preserve=mode,timestamps -- {shlex.quote(source)} "
            f"{shlex.quote(destination)}"
        )
    for relative_path in new_files:
        destination = posixpath.join(
            REMOTE_DIR, *PurePosixPath(relative_path.as_posix()).parts
        )
        commands.append(f"rm -f -- {shlex.quote(destination)}")
    commands.append("bash deploy/debian/manage.sh restart || true")
    return "\n".join(commands)


def main() -> int:
    validate_remote_dir()
    files = deployable_files()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = f"{REMOTE_DIR}/.deploy-backups/{stamp}"
    client: paramiko.SSHClient | None = None
    existing_files: list[Path] = []
    new_files: list[Path] = []
    backup_ready = False

    try:
        client = connect()
        sftp = client.open_sftp()
        for required in (REMOTE_DIR, f"{REMOTE_DIR}/config.env"):
            if not remote_exists(sftp, required):
                raise RuntimeError(f"远端部署不完整，缺少：{required}")
        config_hashes_before = {
            name: remote_file_hash(sftp, f"{REMOTE_DIR}/{name}")
            for name in ("config.env", "config.local.bat")
            if remote_exists(sftp, f"{REMOTE_DIR}/{name}")
        }
        for relative_path in files:
            remote_path = posixpath.join(
                REMOTE_DIR, *PurePosixPath(relative_path.as_posix()).parts
            )
            if remote_exists(sftp, remote_path):
                existing_files.append(relative_path)
            else:
                new_files.append(relative_path)

        code, output, error = run_capture(
            client,
            backup_script(existing_files, backup_dir),
            timeout=120,
        )
        if code != 0:
            raise RuntimeError(f"远端备份失败：{error or output}")
        backup_ready = True
        print(f"已备份上一版代码：{backup_dir}")

        print(f"开始上传 {len(files)} 个文件……")
        for index, relative_path in enumerate(files, start=1):
            upload_file(sftp, relative_path, stamp)
            print(f"  [{index}/{len(files)}] {relative_path.as_posix()}")
        sftp.close()

        deploy_script = f"""set -Eeuo pipefail
cd {shlex.quote(REMOTE_DIR)}
bash -n start.sh deploy/debian/manage.sh deploy/debian/install.sh
bash deploy/debian/install.sh
set -a
source config.env
set +a
curl --silent --show-error --fail "http://127.0.0.1:${{PORT:-8000}}/api/health" >/dev/null
curl --silent --show-error --fail "http://127.0.0.1:${{PORT:-8000}}/" >/dev/null
curl --silent --show-error --fail "http://127.0.0.1:${{PORT:-8000}}/gallery/new" >/dev/null
curl --silent --show-error --fail "http://127.0.0.1:${{PORT:-8000}}/api/feishu/status" >/dev/null
printf '远端验收通过：端口 %s，首页、图集、健康检查和飞书状态接口正常。\\n' "${{PORT:-8000}}"
"""
        code, _output = run_streaming(client, deploy_script, timeout=300)
        if code != 0:
            raise RuntimeError(f"远端安装或验收失败，退出码 {code}。")

        sftp = client.open_sftp()
        config_hashes_after = {
            name: remote_file_hash(sftp, f"{REMOTE_DIR}/{name}")
            for name in config_hashes_before
        }
        sftp.close()
        if config_hashes_after != config_hashes_before:
            raise RuntimeError("部署前后服务器配置文件哈希不一致。")

        print("服务器配置、图片数据目录和日志目录均未参与代码覆盖。")
        print(f"部署成功，回滚备份保留在：{backup_dir}")
        return 0
    except Exception as error:
        print(f"部署失败：{error}", file=sys.stderr)
        if client is not None and backup_ready:
            print("正在恢复上一版代码……", file=sys.stderr)
            try:
                run_streaming(
                    client,
                    rollback_script(existing_files, new_files, backup_dir),
                    timeout=180,
                )
                print("上一版代码已恢复。", file=sys.stderr)
            except Exception as rollback_error:
                print(f"自动恢复失败：{rollback_error}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
