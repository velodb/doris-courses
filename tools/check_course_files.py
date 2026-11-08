#!/usr/bin/env python3
"""Validate current language course files without committing or publishing a package."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import content_files_contract as contract
import course_files_producer as producer
from content_producer import write_tree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.repo.resolve()

    class WorkingFiles:
        # Internal validation only: this object is never a release artifact.
        ref = '0' * 40

        def paths(self):
            paths = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-co', '--exclude-standard',
                '--', 'doris-course', 'locales', 'tools', 'contracts', 'catalog.yaml'], text=True).splitlines()
            return sorted(set(p for p in paths if (root / p).is_file()))

        def read(self, path):
            return contract.safe_path(root, path).read_bytes()

    try:
        files, package = producer.build_tree(WorkingFiles(), [
            'real-time-analytics-complete', 'data-warehousing-complete', 'observability-complete'], ['en', 'kk-Cyrl'])
        with tempfile.TemporaryDirectory(prefix='course-source-check-') as directory:
            tree = Path(directory) / 'tree'
            write_tree(files, tree)
            result = contract.validate_package(tree)
        print(json.dumps({'passed': True, 'working_tree_only': True, 'published': False,
                          'locales': result['available_locales'], 'profiles': result['selected_profiles']}, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('course files: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
