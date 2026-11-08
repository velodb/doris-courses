"""Reusable single-choice quiz UI for course knowledge checks.

The questions come from the module's quiz YAML file; ``doris_labkit.quizbank``
describes its structure and the fixed shuffled order of the options.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from functools import wraps
import hashlib
import threading
import uuid
from .messages import Messages

try:
    import yaml
    import ipywidgets as widgets
    from IPython.display import display

    from .quizbank import Quiz, QuizOption, QuizQuestion, display_order, load_quiz
    from .quizsession import Presentation, QuizConflict, digest
except ImportError as exc:  # pragma: no cover - learner environment diagnostic
    raise RuntimeError(Messages()('quiz.dependencies')) from exc

__all__ = ["CourseQuiz", "QuizOption", "QuizQuestion"]


_QUIZ_STYLES = """
<style>
.doris-quiz-shell {
  border: 1px solid #dbe4e8;
  border-top: 4px solid #0f766e;
  border-radius: 6px;
  background: #ffffff;
  padding: 22px 24px;
  color: #17212b;
  max-width: 920px;
  box-sizing: border-box;
}
.doris-quiz-kicker {
  color: #0f766e;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: .7px;
  text-transform: uppercase;
  margin-bottom: 8px;
}
.doris-quiz-question {
  color: #000000;
  font-size: 19px;
  line-height: 1.5;
  font-weight: 700;
  margin: 8px 0 14px;
  white-space: pre-wrap;
}
.doris-quiz-objective {
  display: inline-block;
  border-radius: 4px;
  background: #f0fdfa;
  color: #115e59;
  font-size: 14px;
  padding: 4px 8px;
}
.doris-quiz-progress-track {
  height: 6px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
  margin: 12px 0 18px;
}
.doris-quiz-progress-value {
  height: 100%;
  background: #0f766e;
}
.doris-quiz-feedback {
  border-left: 4px solid #0f766e;
  border-radius: 4px;
  background: #f0fdfa;
  color: #115e59;
  font-size: 16px;
  padding: 12px 14px;
  margin: 14px 0 4px;
  line-height: 1.55;
}
.doris-quiz-feedback.incorrect {
  border-left-color: #d97706;
  background: #fffbeb;
}
.doris-quiz-feedback.warning {
  border-left-color: #b45309;
  background: #fff7ed;
}
.doris-quiz-result {
  font-size: 24px;
  font-weight: 750;
  margin: 4px 0 8px;
}
.doris-quiz-review {
  border-top: 1px solid #dbe4e8;
  font-size: 16px;
  margin-top: 18px;
  padding-top: 16px;
  line-height: 1.55;
}
.doris-quiz-result-description {
  font-size: 16px;
}
.doris-quiz-review-item {
  border-bottom: 1px solid #e2e8f0;
  margin: 0 0 28px;
  padding-bottom: 24px;
}
.doris-quiz-review-item:last-child {
  border-bottom: 0;
  margin-bottom: 0;
  padding-bottom: 0;
}
.doris-quiz-review-title {
  display: block;
  font-size: 18px;
  margin-bottom: 6px;
}
.doris-quiz-review-answer-label,
.doris-quiz-review-description-label {
  color: #475569;
  font-size: 16px;
  margin-top: 10px;
}
.doris-quiz-review-answer,
.doris-quiz-review-description {
  box-sizing: border-box;
  display: block;
  width: 100%;
  max-width: 100%;
  border: 1px solid #dbe4e8;
  border-radius: 4px;
  background: #f8fafc;
  color: #17212b;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 15px;
  line-height: 1.5;
  margin: 4px 0 10px;
  padding: 10px 12px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.doris-quiz-shell .widget-radio-box,
.doris-quiz-shell .jupyter-widget-radio-box {
  overflow: visible;
}
.doris-quiz-shell .widget-radio-box label,
.doris-quiz-shell .jupyter-widget-radio-box label {
  display: block !important;
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: auto !important;
  min-height: 0 !important;
  padding: 0 0 0 30px;
  white-space: normal !important;
  font-size: 16px;
  line-height: 1.45;
  margin: 0 0 12px !important;
}
.doris-quiz-shell .widget-radio-box label:last-child,
.doris-quiz-shell .jupyter-widget-radio-box label:last-child {
  margin-bottom: 2px !important;
}
.doris-quiz-shell .widget-radio-box label input[type="radio"],
.doris-quiz-shell .jupyter-widget-radio-box label input[type="radio"] {
  position: absolute;
  top: calc(0.725em - 8px);
  font-size: inherit;
  left: 0;
  float: none !important;
  width: 16px;
  height: 16px !important;
  margin: 0 !important;
}
.doris-quiz-root .jupyter-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.doris-quiz-root .jupyter-button i,
.doris-quiz-root .jupyter-button .fa {
  margin: 0;
  line-height: 1;
}
.doris-quiz-controls { gap: 8px; flex-wrap: wrap; }
.doris-quiz-controls .jupyter-button { width: 170px; flex: 0 0 170px; }
</style>
"""


def synchronized(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return run


_SOURCE_LOCALES = {'en', 'kk-Cyrl'}


def _checked_locale(value):
    if not isinstance(value, str) or value not in _SOURCE_LOCALES:
        raise ValueError('Unsupported Quiz locale.')
    return value


def _native_yaml(path):
    # These are fixed adjacent source files, never a locale-supplied path or a
    # search for dictionaries elsewhere in the user's filesystem.
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Native Quiz metadata must be a regular local YAML file.')
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def _course_context(path):
    """Use the actual Quiz location, including when a caller supplied a symlink."""
    for parent in path.parents:
        manifest = parent / 'course.yaml'
        if manifest.is_symlink() or manifest.is_file():
            document = _native_yaml(manifest)
            if not isinstance(document, dict):
                raise ValueError('Native Quiz course metadata must be a mapping.')
            return _checked_locale(document.get('language', 'en')), parent
    return 'en', None


def _source_messages(course_root, locale):
    if locale == 'en':
        return Messages()
    if course_root is None or course_root.parent.name != locale:
        raise ValueError('Native Quiz messages require the matching language source root.')
    return Messages(locale, messages=_native_yaml(course_root.parent / 'ui.yaml'))


class CourseQuiz:
    """Render a one-question-at-a-time, self-assessed course quiz."""

    def __init__(
        self,
        *,
        title: str,
        description: str,
        questions: tuple[QuizQuestion, ...],
        passing_score_percent: int = 80,
        source_path: str | Path | None = None,
        messages: Messages | None = None,
        locale: str = "en",
        presentation: Presentation | None = None,
        _self_study: bool = False,
    ) -> None:
        if not questions:
            raise ValueError("A quiz must contain at least one question.")
        self._lock = threading.RLock()
        _checked_locale(locale)
        self.t = messages or Messages(locale)
        _checked_locale(self.t.locale)
        self.locale = self.t.locale
        self.source_locale = self.locale
        bank = Quiz(title, description, questions, passing_score_percent)
        if _self_study and presentation is not None:
            raise QuizConflict('Unbound self-study cannot carry an admitted release.')
        if presentation is None:
            if self.locale == 'en' and not _self_study:
                presentation = Presentation.legacy(bank, digest({'quiz': repr(bank)}))
                bank = presentation.quiz
            else:
                # Native non-English self-study has no admitted or legacy
                # English identity. Its result is never sent to a credit store.
                bank = display_order(bank)
            questions = bank.questions
        self.presentation = presentation
        if self.presentation is not None:
            self.presentation.check()
            if self.presentation.quiz != bank or self.presentation.locale != self.locale:
                raise QuizConflict('Quiz content and UI must use the bound presentation locale.')
        self.title = title
        self.description = description
        self.questions = questions
        self.passing_score_percent = passing_score_percent
        self.source_path = Path(source_path).resolve() if source_path is not None else None
        self._current = 0
        self._answers: dict[str, str] = {}
        self._drafts: dict[str, str] = {}
        self._generation = 0
        self._results_visible = False
        self._attempt_id = uuid.uuid4().hex
        self._submission = None
        self._save_complete = False
        self._receipt = None
        self._root = widgets.VBox()
        self._root.add_class("doris-quiz-root")

    @classmethod
    def from_yaml(cls, path: str | Path, *, messages: Messages | None = None, locale: str | None = None,
                  presentation: Presentation | None = None) -> "CourseQuiz":
        """Load a quiz file and show its options in the quiz's fixed shuffled order.

        Explicit messages and locale precede native course language detection.
        Native non-English sources without admission run as unbound self-study.

        Raises ``doris_labkit.quizbank.QuizFormatError`` (a ValueError) listing
        every problem when the file does not follow the quiz structure.
        """
        if locale is not None:
            _checked_locale(locale)
        if messages is not None:
            _checked_locale(messages.locale)
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError('Quiz source must be a regular local file.')
        source_locale, course_root = _course_context(path) if presentation is None else ('en', None)
        # Source language decides whether a bank is native self-study. Explicit
        # UI preferences cannot turn that source into a legacy English release.
        # Admitted callers continue supplying their own release and context.
        selected_locale = messages.locale if messages is not None else (locale or source_locale)
        if messages is None and selected_locale != 'en':
            if course_root is None:
                _, course_root = _course_context(path)
            messages = _source_messages(course_root, selected_locale)
        if presentation is None:
            raw_quiz = load_quiz(path, localized=source_locale != 'en' or selected_locale != 'en')
            quiz = display_order(raw_quiz)
            if source_locale == 'en' and selected_locale == 'en':
                presentation = Presentation.legacy(raw_quiz, hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            quiz = presentation.quiz
            raw = Path(path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != presentation.source_digest:
                raise QuizConflict('Installed Quiz bytes changed; reload before submitting.')
            loaded = Presentation.admitted(load_quiz(path, localized=True), {
                'id': presentation.quiz_id, 'semantic_revision': presentation.semantic_revision,
                'passing_score_percent': quiz.passing_score_percent,
                'questions': [{'id': q.question_id, 'option_ids': [o.option_id for o in q.options], 'answer': q.answer}
                              for q in quiz.questions],
                'presentations': [{'locale': presentation.locale, 'presentation_revision': presentation.presentation_revision}]},
                locale=presentation.locale, policy_revision=presentation.policy_revision, source_digest=presentation.source_digest)
            if loaded.in_order_of(presentation).snapshot() != presentation.snapshot():
                raise QuizConflict('Quiz content does not match its admitted presentation.')
        result = cls(
            title=quiz.title,
            description=quiz.description,
            questions=quiz.questions,
            passing_score_percent=quiz.passing_score_percent,
            source_path=path,
            messages=messages, locale=selected_locale, presentation=presentation,
            _self_study=presentation is None and source_locale != 'en',
        )
        result.source_locale = source_locale if presentation is None else presentation.locale
        return result

    @synchronized
    def show(self) -> widgets.VBox:
        """Display the quiz and reset any answers from an earlier attempt."""
        self._restart()
        display(widgets.HTML(_QUIZ_STYLES))
        display(self._root)
        self._render_question()
        return self._root

    @synchronized
    def _restart(self):
        self._current = 0
        self._answers = {}
        self._drafts = {}
        self._attempt_id = uuid.uuid4().hex
        self._submission = None
        self._receipt = None
        self._save_complete = False
        self._results_visible = False

    @synchronized
    def switch_presentation(self, presentation: Presentation, *, messages: Messages,
                            source_path: str | Path | None = None):
        """Replace equivalent display text without starting/submitting an attempt."""
        presentation.check()
        if self.presentation is None or not self.presentation.equivalent(presentation) or messages.locale != presentation.locale:
            raise QuizConflict('Quiz presentation is not equivalent to its admitted release.')
        presentation = presentation.in_order_of(self.presentation)
        self.presentation = presentation
        self.t, self.locale = messages, messages.locale
        self.title, self.description = presentation.quiz.title, presentation.quiz.description
        self.questions = presentation.quiz.questions
        if source_path is not None:
            self.source_path = Path(source_path).resolve()
        if self._results_visible:
            self._render_results(save=False)
        else:
            self._render_question()
        return self._root

    def _live(self, callback):
        generation = self._generation
        def run(event):
            with self._lock:
                if generation == self._generation:
                    callback(event)
        return run

    @synchronized
    def _render_question(self) -> None:
        self._generation += 1
        self._results_visible = False
        question = self.questions[self._current]
        number = self._current + 1
        total = len(self.questions)
        progress = number / total * 100

        header = widgets.HTML(
            value=(
                f'<div class="doris-quiz-kicker">{self.t.html("quiz.text-15", number=number, total=total)}</div>'
                f'<span class="doris-quiz-objective">{escape(question.objective)}</span>'
                f'<div class="doris-quiz-question">{number}. {escape(question.prompt)}</div>'
                '<div class="doris-quiz-progress-track">'
                f'<div class="doris-quiz-progress-value" style="width:{progress:.0f}%"></div>'
                "</div>"
            )
        )
        saved_answer = self._answers.get(question.question_id)
        saved_selection = saved_answer or self._drafts.get(question.question_id)
        choices = widgets.RadioButtons(
            options=[(option.text, option.option_id) for option in question.options],
            value=saved_selection,
            description="",
            layout=widgets.Layout(width="100%"),
            disabled=self._submission is not None,
        )
        feedback = widgets.HTML()
        previous_button = widgets.Button(
            description=self.t("quiz.text-1"),
            icon="arrow-left",
            layout=widgets.Layout(
                visibility="hidden" if self._current == 0 else "visible"
            ),
        )
        submit = widgets.Button(
            description=self.t("quiz.text-2"),
            button_style="success",
            icon="check",
        )
        next_label = (
            self.t("quiz.text-3") if number == total else self.t("quiz.text-4")
        )
        next_button = widgets.Button(
            description=next_label,
            icon="arrow-right",
            layout=widgets.Layout(visibility="hidden"),
        )

        def show_feedback(selected: str) -> None:
            correct = selected == question.answer
            if correct:
                feedback.value = (
                    f'<div class="doris-quiz-feedback"><strong>{self.t.html("quiz.text-5")}</strong> '
                    f"{escape(question.explanation)}</div>"
                )
            else:
                correct_text = question.option_text(question.answer)
                feedback.value = (
                    '<div class="doris-quiz-feedback incorrect">'
                    f'<strong>{self.t.html("quiz.text-6")}</strong> {self.t.html("quiz.text-20", correct_text=correct_text)} '
                    f"{escape(question.explanation)}</div>"
                )

        if saved_answer is not None:
            submit.disabled = True
            next_button.layout.visibility = "visible"
            show_feedback(saved_answer)
        if self._submission is not None:
            submit.disabled = True

        def submit_answer(_: widgets.Button) -> None:
            if self._submission is not None:
                return
            selected = choices.value
            if selected is None:
                feedback.value = (
                    '<div class="doris-quiz-feedback warning">'
                    f'{self.t.html("quiz.text-7")}</div>'
                )
                return

            self._answers[question.question_id] = str(selected)
            self._drafts.pop(question.question_id, None)
            submit.disabled = True
            next_button.layout.visibility = "visible"
            show_feedback(str(selected))

        def change_selection(change: dict) -> None:
            if self._submission is not None:
                return
            self._answers.pop(question.question_id, None)
            if change["new"] is not None:
                self._drafts[question.question_id] = str(change["new"])
            submit.disabled = False
            next_button.layout.visibility = "hidden"
            feedback.value = ""

        choices.observe(self._live(change_selection), names="value")

        def go_back(_: widgets.Button) -> None:
            if question.question_id not in self._answers and choices.value is not None:
                self._drafts[question.question_id] = str(choices.value)
            self._current -= 1
            self._render_question()

        def advance(_: widgets.Button) -> None:
            if self._current + 1 == len(self.questions):
                self._render_results()
            else:
                self._current += 1
                self._render_question()

        submit.on_click(self._live(submit_answer))
        next_button.on_click(self._live(advance))
        previous_button.on_click(self._live(go_back))
        for button in (previous_button, submit, next_button):
            button.layout.width = "170px"
            button.layout.flex = "0 0 170px"
        controls = widgets.HBox(
            [previous_button, submit, next_button],
            layout=widgets.Layout(flex_flow="row wrap"),
        )
        controls.add_class("doris-quiz-controls")
        content = widgets.VBox([header, choices, feedback, controls])
        content.add_class("doris-quiz-shell")
        content.layout = widgets.Layout(max_width="920px")
        self._root.children = (content,)

    @synchronized
    def _render_results(self, *, save=True) -> None:
        self._generation += 1
        self._results_visible = True
        total = len(self.questions)
        complete = all(self._answers.get(q.question_id) in {o.option_id for o in q.options} for q in self.questions)
        correct_count = sum(
            self._answers.get(question.question_id) == question.answer
            for question in self.questions
        )
        missed = [
            (number, question)
            for number, question in enumerate(self.questions, start=1)
            if self._answers.get(question.question_id) != question.answer
        ]
        percent = round(correct_count / total * 100)
        passed = correct_count * 100 >= self.passing_score_percent * total
        saved_text = ""
        save_failed = False
        if complete and self._submission is None:
            self._submission = (self.source_path, self.presentation, self._attempt_id, dict(self._answers))
        if complete and self.presentation is None:
            # An unbound native Quiz is a local knowledge check even if a caller
            # inherited Workshop environment variables. Do not register, save,
            # import, or manufacture a trusted presentation for that result.
            self._save_complete = True
        elif complete and self.source_path is not None:
            from .progress import save_submission_from_environment
            try:
                if save and not self._save_complete:
                    self._receipt = save_submission_from_environment(*self._submission)
                    self._save_complete = True
                record = self._receipt
                if not self._save_complete:
                    save_failed = True
                    saved_text = self.t('quiz.text-21')
                if record is not None and record.get('optional'):
                    saved_text = self.t("quiz.text-26")
                elif record is not None:
                    saved_text = (self.t("quiz.text-23")
                                  if record["quiz_passed"] else self.t("quiz.text-24"))
                    if record["quiz_passed"] and not passed:
                        saved_text = self.t("quiz.text-22")
            except Exception:
                save_failed = True
                saved_text = self.t("quiz.text-21")
        elif not complete:
            saved_text = self.t("quiz.text-25")

        if missed:
            review_items = []
            for number, question in missed:
                selected = self._answers.get(question.question_id)
                review_items.append(
                    '<div class="doris-quiz-review-item">'
                    f"<strong>{number}. {escape(question.prompt)}</strong><br>"
                    f'<div class="doris-quiz-review-answer-label">{self.t.html("quiz.text-8")}</div>'
                    '<div class="doris-quiz-review-answer">'
                    f"{escape(question.option_text(selected) if selected is not None else self.t('quiz.no-answer'))}</div>"
                    f'<div class="doris-quiz-review-answer-label">{self.t.html("quiz.text-9")}</div>'
                    '<div class="doris-quiz-review-answer">'
                    f"{escape(question.option_text(question.answer))}</div>"
                    f'<div class="doris-quiz-review-description-label">{self.t.html("quiz.text-10")}</div>'
                    '<div class="doris-quiz-review-description">'
                    f"{escape(question.explanation)}</div></div>"
                )
            review = (
                '<div class="doris-quiz-review">'
                f'<strong class="doris-quiz-review-title">{self.t.html("quiz.text-11")}</strong>'
                + "".join(review_items)
                + "</div>"
            )
        else:
            review = (
                f'<div class="doris-quiz-review">{self.t.html("quiz.text-27")}</div>'
            )

        result = widgets.HTML(
            value=(
                f'<div class="doris-quiz-shell" lang="{escape(self.locale)}">'
                '<div class="doris-quiz-kicker">' + (self.t.html("quiz.text-12") if complete else self.t.html("quiz.text-13")) + '</div>'
                f'<div class="doris-quiz-result">{self.t.html("quiz.text-16", correct_count=correct_count, total=total, percent=percent)}</div>'
                f'<p>{self.t.html("quiz.text-17", passing_score_percent=self.passing_score_percent)} ' + (self.t.html("quiz.text-18") if complete and passed else self.t.html("quiz.text-19")) + '</p>'
                f'<p role="status">{escape(saved_text)}</p>'
                '<div class="doris-quiz-result-description">'
                f"{escape(self.description)}</div>{review}</div>"
            )
        )
        retry = widgets.Button(description=self.t("quiz.text-14"), icon="refresh")
        previous_button = widgets.Button(
            description=self.t("quiz.text-1"),
            icon="arrow-left",
        )

        def restart(_: widgets.Button) -> None:
            self._restart()
            self._render_question()

        def review_last_question(_: widgets.Button) -> None:
            self._current = len(self.questions) - 1
            self._render_question()

        retry.on_click(self._live(restart))
        previous_button.on_click(self._live(review_last_question))
        controls = widgets.HBox([previous_button, retry])
        if save_failed:
            save_again = widgets.Button(description=self.t("quiz.retry-save"), icon="save")
            save_again.on_click(self._live(lambda _: self._render_results()))
            controls.children = (*controls.children, save_again)
        controls.add_class("doris-quiz-controls")
        content = widgets.VBox([result, controls])
        content.layout = widgets.Layout(max_width="920px")
        self._root.children = (content,)
