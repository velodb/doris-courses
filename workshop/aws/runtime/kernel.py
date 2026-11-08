"""Start a course kernel in its own interpreter and source directory."""

import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-dir", type=Path, required=True)
    args, kernel_args = parser.parse_known_args()
    startup_directory = Path.cwd()
    directory = args.course_dir.resolve(strict=True)
    os.chdir(directory)
    from ipykernel.kernelapp import IPKernelApp

    sys.path.insert(0, str(directory))
    if os.environ.get('DORIS_WORKSHOP_CONTENT_ROOT'):
        sys.path.insert(0, str(directory.parent / 'common'))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'ui'))
        from multilingual import Content, configure_quiz
        import json
        plan = json.loads(Path(os.environ['DORIS_WORKSHOP_RUNTIME_PLAN']).read_text())
        content = Content(plan, os.environ['DORIS_WORKSHOP_CONTENT_ROOT'], os.environ['DORIS_WORKSHOP_WORKSPACE'])
        relative = startup_directory.relative_to(content.workspace.resolve())
        parts = relative.parts
        if len(parts) < 3 or parts[0] != 'work' or parts[1] not in content.links:
            raise ValueError('content: start kernels from a selected language working copy')
        configure_quiz(content, parts[1])
    # ipykernel_launcher removes the cwd from sys.path; under Python -I it is
    # not added back. Launch the app directly to retain this course's import path.
    IPKernelApp.launch_instance(argv=kernel_args)


if __name__ == "__main__":
    main()
