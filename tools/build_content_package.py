#!/usr/bin/env python3
"""Register stable English units or build an admitted v2 content artifact."""
import argparse
import json
from pathlib import Path
import sys

import content_contract_v2 as c
import content_producer as p


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-ref', required=True, help='exact, committed 40-character Git SHA')
    commands = parser.add_subparsers(dest='command', required=True)
    register = commands.add_parser('register', help='explicitly assign/reconcile stable source units')
    register.add_argument('--previous', type=Path)
    register.add_argument('--bindings', type=Path, help='JSON object: existing unit ID -> candidate handle')
    register.add_argument('--retire', action='append', default=[])
    register.add_argument('--accept-new', action='store_true')
    register.add_argument('--output', type=Path, required=True)
    commands.add_parser('inspect', help='print independently discovered candidate handles and texts')
    check = commands.add_parser('check-map', help='verify full extraction against the committed source map')
    build = commands.add_parser('build', help='build a new private/public artifact directory')
    build.add_argument('--profile', action='append', required=True)
    build.add_argument('--locale', action='append', default=None)
    build.add_argument('--state', choices=['release', 'preview'], default='release')
    build.add_argument('--default-locale', default='en')
    build.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = p.GitSource(args.repo, args.source_ref)
        if args.command == 'register':
            previous = c.read_json(args.previous.read_bytes()) if args.previous else None
            bindings = c.read_json(args.bindings.read_bytes()) if args.bindings else None
            result = p.reconcile(source, previous, bindings, allow_new=args.accept_new, retire=args.retire)
            if args.output.exists():
                c.fail('registration output exists; write a new file and review its diff')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(p.json_bytes(result))
            print(json.dumps({'units': len(result['units']), 'output': str(args.output)}))
        elif args.command == 'inspect':
            print(json.dumps(p.candidates(source), ensure_ascii=False, indent=2))
        elif args.command == 'check-map':
            units = p.complete_map(source, p.load(source.read(p.MAP), p.MAP))
            print(json.dumps({'units': len(units), 'source_ref': source.ref}))
        else:
            result = p.build(source, args.output, args.profile, args.locale or ['en'],
                             state=args.state, default=args.default_locale)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, IndexError, OSError, UnicodeError, RecursionError, p.yaml.YAMLError) as error:
        print(f'content producer: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
