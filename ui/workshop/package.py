"""Deterministic UI-only archive for the existing runtime and portal installers."""
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent
FILES = ('i18n.py', 'src/locale.js', 'doris_workshop_ui.py', 'service_guidance.py', 'install_assets.py', 'src/styles.css', 'src/copy.json', 'src/services.json',
    'assets/doris-trimmed.svg', 'assets/runtime-downloads.json', 'assets/manrope-OFL.txt',
    *(f'src/templates/{name}.html' for name in ('page', 'activities', 'workspace', 'course', 'lesson', 'terminal', 'state', 'progress')))


def archive(*, offline=False):
    names = (*FILES, 'assets/manrope.ttf', 'assets/jupyter.svg') if offline else FILES
    shared = {'workshop_messages.py': ROOT.parents[1] / 'doris-course/common/doris_labkit/messages.py',
              'ui.en.json': ROOT.parents[1] / 'locales/ui.en.json',
              'ui.contract.json': ROOT.parents[1] / 'locales/ui.contract.json',
              'locale-registry.json': ROOT.parents[1] / 'contracts/v2/locales.json'}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name in sorted((*names, *shared)):
            source = shared.get(name, ROOT / name)
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(ROOT.parents[1].resolve()):
                raise ValueError('UI package is missing a regular source file')
            data = source.read_bytes()
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            tar.addfile(member, io.BytesIO(data))
    return output.getvalue()
