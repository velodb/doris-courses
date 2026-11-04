"""Install two public UI assets by pinned hash; not used by request rendering."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
NAMES = {'assets/manrope.ttf', 'assets/jupyter.svg'}


def install():
    manifest = json.loads((ROOT / 'assets/runtime-downloads.json').read_text())
    if set(manifest) != NAMES:
        raise ValueError('Unexpected UI download manifest')
    for name, row in manifest.items():
        target = ROOT / name
        if target.is_symlink():
            raise ValueError('Unsafe UI asset target')
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == row['sha256']:
            continue
        url = urlsplit(row['url'])
        if url.scheme != 'https' or url.hostname not in {'raw.githubusercontent.com', 'jupyter.org'} or url.username or url.password:
            raise ValueError('Unexpected UI asset origin')
        with urlopen(row['url'], timeout=60) as response:
            if urlsplit(response.url).hostname != url.hostname:
                raise ValueError('Unexpected UI asset redirect')
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('UI asset checksum differs')
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        try:
            temporary.chmod(0o644)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    install()
