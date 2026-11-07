"""Data-only, typed workshop messages. No online translation or locale fallback."""
from html import escape
import json
import math
from pathlib import Path
from string import Formatter


def default_data(name):
    """Use the frozen source tree, installed UI archive, or packaged wheel data."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        for path in (parent / name, parent / 'locales' / name):
            if path.is_file():
                return json.loads(path.read_text())
    from importlib.resources import files
    return json.loads(files('doris_workshop_locales').joinpath(name).read_text())


def fields(value):
    names = []
    for _, name, spec, conversion in Formatter().parse(value):
        if name is not None:
            if not name.isidentifier() or spec or conversion:
                raise ValueError('Messages require simple named placeholders')
            names.append(name)
    return set(names)


class Messages:
    def __init__(self, locale='en', *, messages=None, source=None, contract=None):
        self.locale = locale
        self.source = default_data('ui.en.json')['messages'] if source is None else source
        self.contract = default_data('ui.contract.json') if contract is None else contract
        if messages is None and locale != 'en':
            raise ValueError('Locale has no installed messages')
        if (not isinstance(self.source, dict) or not self.source or not isinstance(self.contract, dict) or
                set(self.contract) != {'version', 'parameters', 'plurals'} or type(self.contract['version']) is not int or self.contract['version'] != 1 or
                not isinstance(self.contract['parameters'], dict) or not isinstance(self.contract['plurals'], dict) or
                any(not isinstance(spec, dict) or any(kind not in {'text', 'integer', 'number'} for kind in spec.values())
                    for spec in self.contract['parameters'].values())):
            raise ValueError('Invalid message parameter contract')
        for forms in self.contract['plurals'].values():
            if not isinstance(forms, dict) or set(forms) != {'one', 'other'} or any(not isinstance(key, str) or key not in self.source or self.contract['parameters'].get(key, {}).get('count') != 'integer' for key in forms.values()):
                raise ValueError('Invalid message count forms')
        self.messages = dict(self.source if messages is None else messages)
        if set(self.source) != set(self.messages) or set(self.source) != set(self.contract['parameters']):
            raise ValueError('Incomplete message dictionary or parameter contract')
        for key, value in self.messages.items():
            if not isinstance(value, str) or not value or fields(value) != set(self.contract['parameters'][key]):
                raise ValueError('Message placeholders differ: ' + key)
            if fields(self.source[key]) != fields(value):
                raise ValueError('Message source placeholders differ: ' + key)

    def __call__(self, key, **parameters):
        expected = self.contract['parameters'][key]
        if set(parameters) != set(expected):
            raise ValueError('Message parameters differ: ' + key)
        for name, kind in expected.items():
            value = parameters[name]
            valid = ((kind == 'text' and isinstance(value, str)) or
                     (kind == 'integer' and type(value) is int and value >= 0) or
                     (kind == 'number' and type(value) in (int, float) and math.isfinite(value)))
            if not valid:
                raise ValueError('Invalid message parameter: ' + key + '.' + name)
        return self.messages[key].format(**parameters)

    def html(self, key, **parameters):
        return escape(self(key, **parameters), quote=True)

    def plural(self, key, count, **parameters):
        # The registered en / kk-Cyrl UI contract has one/other integer forms.
        return self(self.contract['plurals'][key]['one' if count == 1 else 'other'], count=count, **parameters)

    def error(self, error, *, fallback='import.invalid'):
        # Only exact, known static diagnostics can become learner copy. Unknown
        # exception text may contain paths, tokens or database details.
        key = next((key for key, value in self.source.items() if key.startswith('progress.error.') and value == str(error)), fallback)
        return self(key)

    def subset(self, prefix):
        return {key: value for key, value in self.messages.items() if key.startswith(prefix)}
