"""Synthetic bilingual widget adapter on disposable real Jupyter processes.

This is not a production installer or a Kazakh translation. Reader lessons use
their existing English source; only the selected Quiz has fixture presentations.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

from progress_fixture import ProgressFixture, ROOT
sys.path.insert(0, str(ROOT / 'doris-course/common'))
from doris_labkit.quizsession import digest
import yaml


class QuizProgressFixture(ProgressFixture):
    def __init__(self, directory):
        super().__init__(directory)
        source = self.directory / 'quiz-source'
        plan = copy.deepcopy(self.plan)
        course = next(c for c in plan['courses'] if c['slug'] == 'real-time-analytics')
        module = course['modules'][0]
        original = yaml.safe_load((ROOT / course['course_dir'] / module['quiz']).read_text())
        descriptor = {'id': 'fixture.rta.module-1', 'semantic_revision': 'fixture.meaning.1',
            'passing_score_percent': original.get('passing_score_percent', 80),
            'questions': [{'id': q['id'], 'option_ids': [o['id'] for o in q['options']], 'answer': q['answer']}
                          for q in original['questions']], 'presentations': []}
        module.update(quiz_binding=descriptor, policy_revision='fixture.policy.1', quiz_presentations=[])
        for locale in ('en', 'kk-Cyrl'):
            bank = copy.deepcopy(original)
            if locale != 'en':
                bank['title'] = 'Ә synthetic Quiz'
                bank['description'] = 'Ә ' + bank['description']
                for question in bank['questions']:
                    for field in ('prompt', 'objective', 'explanation'):
                        question[field] = 'Ә ' + question[field]
                    for option in question['options']:
                        option['text'] = 'Ә ' + option['text']
            relative = str(Path(course['course_dir']) / module['quiz']) if locale == 'en' else 'kk-Cyrl/quiz.yaml'
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False))
            descriptor['presentations'].append({'locale': locale, 'presentation_revision': digest({'fixture': bank, 'locale': locale})})
            module['quiz_presentations'].append({'locale': locale, 'path': relative,
                'source_digest': hashlib.sha256(path.read_bytes()).hexdigest()})
        plan_path = self.directory / 'quiz-plan.json'
        plan_path.write_text(json.dumps(plan))
        code = f'''import os, sys, json
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'doris-course/common')!r})
from doris_labkit.quiz import CourseQuiz
from doris_labkit.quizbank import load_quiz
from doris_labkit.quizsession import Presentation
from doris_labkit.messages import Messages
import ipywidgets as widgets
from IPython.display import display
os.environ['DORIS_WORKSHOP_PROGRESS_SOURCE'] = {str(source)!r}
os.environ['DORIS_WORKSHOP_PROGRESS_PLAN'] = {str(plan_path)!r}
source = Path({str(source)!r})
module = next(c for c in json.loads(Path({str(plan_path)!r}).read_text())['courses'] if c['slug'] == 'real-time-analytics')['modules'][0]
entries = {{e['locale']: e for e in module['quiz_presentations']}}
views = {{tag: Presentation.admitted(load_quiz(source / e['path'], localized=tag != 'en'),
    module['quiz_binding'], locale=tag, policy_revision=module['policy_revision'], source_digest=e['source_digest'])
    for tag,e in entries.items()}}
english = Messages()
copy = {{'en': english, 'kk-Cyrl': Messages('kk-Cyrl', messages={{k: 'Ә ' + v for k,v in english.source.items()}})}}
quiz = CourseQuiz.from_yaml(source / entries['en']['path'], presentation=views['en'])
selector = widgets.ToggleButtons(options=[('English', 'en'), ('Synthetic Cyrillic', 'kk-Cyrl')])
def change_language(event):
    tag = event['new']
    quiz.switch_presentation(views[tag], messages=copy[tag], source_path=source / entries[tag]['path'])
selector.observe(change_language, names='value')
display(selector)
_ = quiz.show()
'''
        target = self.workspace / course['course_dir'] / str(Path(module['quiz']).with_suffix('.ipynb'))
        notebook = json.loads(target.read_text())
        notebook['cells'][0]['source'] = code
        target.write_text(json.dumps(notebook))
