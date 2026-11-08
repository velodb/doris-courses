#!/usr/bin/env python3
"""Package directly maintained language course files from an exact Git commit."""
import argparse
import json
from pathlib import Path
import sys

from content_producer import GitSource
from course_files_producer import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-ref', required=True)
    parser.add_argument('--profile', action='append', required=True)
    parser.add_argument('--locale', action='append', default=None)
    parser.add_argument('--default-locale', default='en')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(GitSource(args.repo, args.source_ref), args.output, args.profile,
                               args.locale or ['en', 'kk-Cyrl'], args.default_locale), indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('course files: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
