"""Synthetic UI dictionaries only. This is not a Kazakh translation or admission."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ui/workshop'))
from i18n import InstalledUI, Messages


def installed(**kwargs):
    english = Messages().source
    target = {key: 'ӘҒҚҢӨҰҮҺІ · Сынақ мәтіні · ' + value for key, value in english.items()}
    return InstalledUI({'en': english, 'kk-Cyrl': target}, **kwargs)
