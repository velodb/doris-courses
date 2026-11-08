"""Use the same selected-course contract on the operator and validation host."""
import runpy
from pathlib import PurePosixPath

import workshop_config as wc
import workshop_runtime as runtime

_contract = runpy.run_path(str(runtime.RUNTIME / 'validate_course.py'))
selection_plan = _contract['selection_plan']
valid_selection_report = _contract['valid_selection_report']


def selection(config):
    courses = runtime.course_selection(config)
    if len(courses) != 1:
        raise wc.ConfigError('validation: certify one course selection at a time')
    course = courses[0]
    try:
        steps = selection_plan(course)
    except ValueError as error:
        raise wc.ConfigError(str(error)) from error
    return course, steps


def verify_report(config, report):
    course, steps = selection(config)
    source = {step['notebook']: wc.git(wc.REPO, 'show',
              f"{config['course_ref']}:{course['course_dir']}/{step['notebook']}").encode()
              for step in steps}
    if config.get('schema_version', 1) == 2:
        if report.get('schema_version') != 3 or report.get('content') != config['content']:
            return False
        if config['content']['abi'] == 'doris-workshop-content-3.0':
            source = {locale: {step['notebook']: wc.git(wc.REPO, 'show',
                f"{config['course_ref']}:{course['course_dir'] if locale == 'en' else 'doris-course/' + locale + '/' + PurePosixPath(course['course_dir']).name}/{step['notebook']}").encode()
                for step in steps} for locale in config['content']['available_locales']}
    return valid_selection_report(report, steps, config['course_profiles'], source)
