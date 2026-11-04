"""Package the independently maintained UI; contains no page markup or styles."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'ui/workshop'


def archive(*, offline=False):
    spec = importlib.util.spec_from_file_location('workshop_ui_package', ROOT / 'package.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.archive(offline=offline)
