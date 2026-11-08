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
    content, locale = None, None
    if os.environ.get('DORIS_WORKSHOP_CONTENT_ROOT'):
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
        locale = parts[1]
        shared = Path(os.environ['DORIS_WORKSHOP_SHARED_SOURCE']).resolve()
        sys.path.insert(0, str(shared / 'doris-course/common'))
        if content.course_files:
            course = next(c for c in plan['courses'] if shared / c['course_dir'] == directory)
            working = '/'.join(parts[2:])
            if working != course['course_dir'] and not working.startswith(course['course_dir'] + '/'):
                raise ValueError('content: kernel does not match the language working copy course')
            directory = (shared / content.source_course(course, locale)).resolve(strict=True)
            environment = content.environment(shared, course, locale)
            if Path(sys.prefix).resolve() != environment.resolve():
                english_bin = str(shared / course['course_dir'] / '.venv/bin')
                os.environ['VIRTUAL_ENV'] = str(environment)
                os.environ['PATH'] = str(environment / 'bin') + os.pathsep + os.pathsep.join(
                    p for p in os.environ.get('PATH', '').split(os.pathsep) if p and p != english_bin)
                interpreter = str(environment / 'bin/python')
                os.execv(interpreter, [interpreter, '-I', str(Path(__file__).resolve()), *sys.argv[1:]])
    os.chdir(directory)
    sys.path.insert(0, str(directory))
    if content:
        configure_quiz(content, locale)
    from ipykernel.kernelapp import IPKernelApp
    # ipykernel_launcher removes the cwd from sys.path; under Python -I it is
    # not added back. Launch the app directly to retain this course's import path.
    IPKernelApp.launch_instance(argv=kernel_args)


if __name__ == "__main__":
    main()
