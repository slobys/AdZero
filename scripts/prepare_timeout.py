"""Install only the pinned GNU timeout executable in native test rootfs copies."""

import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

NAME = 'coreutils-timeout_9.7-r1_x86_64.ipk'
URL = 'https://archive.openwrt.org/releases/24.10.8/packages/x86_64/packages/' + NAME
EXECUTABLE = 'usr/libexec/timeout-coreutils'
EXECUTABLE_SHA256 = '35a450ce0c155cf4be026ff84a0c9f30dc95c938d1231cff64721941ac4d36be'
ARCHIVE_LIMIT = 256 * 1024


def member_bytes(data, name, limit):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = [entry for entry in archive if entry.name.removeprefix('./') == name]
        if len(members) != 1 or not members[0].isfile() or members[0].size > limit:
            raise ValueError(f'Invalid timeout fixture member: {name}')
        return archive.extractfile(members[0]).read()


def prepare(root):
    package = Path(root) / 'artifacts/runtime/passwall-extras' / NAME
    cached = package.exists()
    if cached:
        with package.open('rb') as reader:
            raw = reader.read(ARCHIVE_LIMIT + 1)
    else:
        with urllib.request.urlopen(URL, timeout=60) as response:
            raw = response.read(ARCHIVE_LIMIT + 1)
    if len(raw) > ARCHIVE_LIMIT:
        raise ValueError('Timeout fixture archive exceeds its size limit')

    # OpenWrt republishes this IPK with new tar timestamps. Only the exact
    # executable is used. Archive modes, paths and install scripts are ignored.
    payload = member_bytes(raw, 'data.tar.gz', ARCHIVE_LIMIT)
    executable = member_bytes(payload, EXECUTABLE, 45620)
    if hashlib.sha256(executable).hexdigest() != EXECUTABLE_SHA256:
        raise ValueError('Timeout fixture executable checksum mismatch')
    if not cached:
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(raw)
    return executable


def install(root, chroot):
    executable = prepare(root)
    destination = Path(chroot) / EXECUTABLE
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(executable)
    destination.chmod(0o755)
