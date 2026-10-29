"""Start a course kernel in its own interpreter and source directory."""

import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-dir", type=Path, required=True)
    args, kernel_args = parser.parse_known_args()
    directory = args.course_dir.resolve(strict=True)
    os.chdir(directory)
    from ipykernel.kernelapp import IPKernelApp

    sys.path.insert(0, str(directory))
    # ipykernel_launcher removes the cwd from sys.path; under Python -I it is
    # not added back. Launch the app directly to retain this course's import path.
    IPKernelApp.launch_instance(argv=kernel_args)


if __name__ == "__main__":
    main()
