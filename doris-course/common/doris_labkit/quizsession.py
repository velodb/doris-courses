"""Private, immutable Quiz presentations and submission bindings.

Only an installation/server adapter may construct an admitted presentation.
Never accept a grading snapshot or a score from an HTTP request.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re

from .quizbank import Quiz, display_order


class QuizConflict(ValueError):
    """The requested presentation or submission no longer matches its release."""


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def grading(quiz):
    return {'passing_score_percent': quiz.passing_score_percent,
            'questions': [{'id': q.question_id, 'option_ids': sorted(o.option_id for o in q.options),
                           'answer': q.answer} for q in sorted(quiz.questions, key=lambda q: q.question_id)]}


@dataclass(frozen=True)
class Presentation:
    quiz: Quiz
    quiz_id: str
    locale: str
    semantic_revision: str | None
    presentation_revision: str | None
    policy_revision: str | None
    source_digest: str

    @classmethod
    def legacy(cls, quiz, source_digest, quiz_id='legacy-local'):
        """New English self-study attempts; no invented v2 or website revision."""
        return cls(display_order(quiz), quiz_id, 'en', None, None, None, source_digest)

    @classmethod
    def admitted(cls, quiz, descriptor, *, locale, policy_revision, source_digest):
        """Bind bytes verified by content-v2 admission to its private descriptor.

        Admission verifies the public presentation digest using stable text units
        and checks revision history. This method additionally checks grading IDs.
        It deliberately does not implement a second presentation-digest algorithm.
        """
        expected = {'passing_score_percent': descriptor['passing_score_percent'], 'questions': sorted(
            [{'id': q['id'], 'option_ids': sorted(q['option_ids']), 'answer': q['answer']}
             for q in descriptor['questions']], key=lambda q: q['id'])}
        entries = [p for p in descriptor['presentations'] if p['locale'] == locale]
        if expected != grading(quiz) or len(entries) != 1:
            raise QuizConflict('Quiz presentation is not equivalent to its admitted release.')
        result = cls(display_order(quiz), descriptor['id'], locale, descriptor['semantic_revision'],
                     entries[0]['presentation_revision'], policy_revision, source_digest)
        result.check()
        return result

    def check(self):
        token = r'[a-z0-9][a-z0-9._-]{0,199}'
        if (not re.fullmatch(token, self.quiz_id) or
                not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,8}', self.locale) or
                not re.fullmatch(r'[0-9a-f]{64}', self.source_digest) or
                any(v is not None and not re.fullmatch(token, v)
                    for v in (self.semantic_revision, self.policy_revision)) or
                (self.presentation_revision is not None and not re.fullmatch(r'[0-9a-f]{64}', self.presentation_revision)) or
                (self.semantic_revision is None) != (self.presentation_revision is None) or
                (self.semantic_revision is None) != (self.policy_revision is None) or
                (self.semantic_revision is None and self.locale != 'en')):
            raise QuizConflict('Invalid Quiz release binding.')

    def binding(self):
        self.check()
        return {k: v for k, v in asdict(self).items() if k not in {'quiz', 'source_digest'}}

    def snapshot(self):
        return {'binding': self.binding(), 'source_digest': self.source_digest, 'quiz': asdict(self.quiz)}

    def equivalent(self, other):
        # Missing v2 admission is never permission to translate a legacy bank.
        return (self.semantic_revision is not None and
                self.quiz_id == other.quiz_id and self.semantic_revision == other.semantic_revision and
                self.policy_revision == other.policy_revision and grading(self.quiz) == grading(other.quiz))

    def in_order_of(self, previous):
        """Keep the session's question AND option order, even if titles differ."""
        questions = {q.question_id: q for q in self.quiz.questions}
        ordered = []
        for old in previous.quiz.questions:
            question = questions[old.question_id]
            options = {o.option_id: o for o in question.options}
            ordered.append(replace(question, options=tuple(options[o.option_id] for o in old.options)))
        return replace(self, quiz=replace(self.quiz, questions=tuple(ordered)))


def score(snapshot, answers):
    """Grade against the pinned private snapshot, using exact arithmetic."""
    questions = snapshot['quiz']['questions']
    if (not isinstance(answers, dict) or set(answers) != {q['question_id'] for q in questions} or
            any(answers[q['question_id']] not in {o['option_id'] for o in q['options']} for q in questions)):
        raise QuizConflict('Submit exactly one valid option for every question.')
    correct = sum(answers[q['question_id']] == q['answer'] for q in questions)
    total = len(questions)
    threshold = snapshot['quiz']['passing_score_percent']
    return correct, total, threshold, correct * 100 >= total * threshold
