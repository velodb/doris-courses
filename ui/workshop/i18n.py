"""Locale context shared by renderers and installed-content consumer adapters."""
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
try:
    from workshop_messages import Messages
except ModuleNotFoundError:
    spec = importlib.util.spec_from_file_location('workshop_messages', ROOT.parents[1] / 'doris-course/common/doris_labkit/messages.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Messages = module.Messages


def registry():
    path = ROOT / 'locale-registry.json'
    if not path.exists():
        path = ROOT.parents[1] / 'contracts/v2/locales.json'
    return json.loads(path.read_text())['locales']


class LocaleUnavailable(ValueError):
    pass


def with_locale(url, locale):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'locale']
    query.append(('locale', locale))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class InstalledUI:
    """Only an installation adapter supplies admitted dictionaries and content.

    The default v1 installation is English. Supplying a registry alone cannot
    enable a language. ML-05 supplies the admitted package-to-runtime adapter.
    """
    def __init__(self, dictionaries=None, *, default='en', content=None, labels=None, anchors=None):
        english = Messages()
        dictionaries = {'en': english.source} if dictionaries is None else dictionaries
        self.messages = {tag: Messages(tag, messages=value) for tag, value in dictionaries.items()}
        self.registry = {row['tag']: row for row in registry() if row['tag'] in self.messages}
        if set(self.registry) != set(self.messages) or default not in self.messages or 'en' not in self.messages:
            raise ValueError('Installed locale set is invalid')
        self.default, self.content, self.labels, self.anchors = default, content, labels, anchors

    def display(self, kind, identity, values, locale):
        if locale == 'en':
            return values
        if self.labels is None:
            raise LocaleUnavailable('Localized display metadata is not installed')
        result = self.labels(kind, identity, dict(values), locale)
        if set(result) != set(values):
            raise ValueError('Display adapter changed metadata fields')
        return result

    def match(self, value):
        for tag, row in self.registry.items():
            if value.lower() in {x.lower() for x in [tag, *row['aliases']]}:
                return tag
        return None

    def browser(self, header):
        choices = []
        for index, item in enumerate(header.split(',')):
            bits = item.strip().split(';')
            try:
                quality = float(bits[1].strip().removeprefix('q=')) if len(bits) > 1 else 1
            except ValueError:
                continue
            if 0 < quality <= 1:
                choices.append((-quality, index, bits[0].strip()))
        for _, _, value in sorted(choices):
            while value:
                match = self.match(value)
                if match:
                    return match
                value = value.rsplit('-', 1)[0] if '-' in value else ''
        return self.default

    def context(self, *, explicit=None, preference=None, browser='', activity='', learner='', scope=''):
        if explicit is not None:
            tag = self.match(explicit)
            if not tag:
                raise LocaleUnavailable('Locale is not installed')
        else:
            tag = self.match(preference or '') or self.browser(browser)
        # Identity comes from the authenticated assignment / instance adapter,
        # never a URL or a learner-supplied preference key.
        key = hashlib.sha256(json.dumps([scope, activity, learner], ensure_ascii=False).encode()).hexdigest()
        return LocaleContext(self, tag, key, explicit is not None)


@dataclass(frozen=True)
class LocaleContext:
    installed: InstalledUI
    locale: str
    preference_key: str
    explicit: bool

    @property
    def messages(self):
        return self.installed.messages[self.locale]

    @property
    def direction(self):
        return self.installed.registry[self.locale]['dir']

    def url(self, url):
        return with_locale(url, self.locale)

    def client(self):
        return {'version': 1, 'locale': self.locale, 'default': self.installed.default,
                'explicit': self.explicit, 'key': 'doris.workshop.locale.v1.' + self.preference_key,
                'locales': list(self.installed.registry.values()), 'storageError': self.messages('locale.storage')}

    def heading_ids(self, path):
        if self.installed.anchors is None:
            if self.locale != 'en':
                raise LocaleUnavailable('Stable section anchors are not installed')
            return None  # Historical English renderer keeps its existing slugs.
        values = self.installed.anchors(path, self.locale)
        if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,199}', value) for value in values) or len(values) != len(set(values)):
            raise ValueError('Invalid stable section anchors')
        return values

    def resolve_content(self, path, kind):
        """Return (path, actual_locale) for lesson/image/notebook/quiz consumers.

        An English navigation mirror returns actual_locale=en. The caller must
        route to it explicitly. No target-language path may display that mirror.
        """
        if self.installed.content:
            return self.installed.content(path, kind, self.locale)
        if self.locale != 'en':
            raise LocaleUnavailable('Localized content is not installed')
        return path, 'en'
