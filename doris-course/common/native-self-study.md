# Native Quiz self-study

`CourseQuiz.from_yaml(path)` can run a native language Quiz directly in Python or
Jupyter. It resolves the actual Quiz path and finds the nearest ancestor
`course.yaml`. A course with `language: kk-Cyrl` uses its language root's `ui.yaml`
message mapping and accepts the Kazakh Quiz title. The normal source layout is
`doris-course/kk-Cyrl/<course>/course.yaml`, with messages at
`doris-course/kk-Cyrl/ui.yaml`.

No Workshop runtime adapter is needed. The existing Notebook code remains:

```python
quiz = CourseQuiz.from_yaml(COURSE_ROOT / "level1/module01-introduction/quiz01_doris_fundamentals.yaml")
quiz.show()
```

Explicit `messages` take priority over an explicit `locale`. An explicit locale
overrides automatic UI selection. For example, `locale="en"` selects English
controls for a native Kazakh bank; the questions remain Kazakh and the result
remains unbound self-study. `quiz.source_locale` retains the actual course source
language. An English source keeps its English title validation.
Callers with a custom source layout can supply a validated `Messages` instance.
Only `en` and `kk-Cyrl` are supported. Unknown locale strings are rejected before
reading a Quiz. Dictionaries use the fixed local `ui.yaml` filename, require the
matching language root, and reject symlinks, oversized files, missing keys and
invalid placeholders. There is no search for another dictionary or language.

A native Kazakh Quiz without an admitted presentation is self-study:
`quiz.locale` defaults to `kk-Cyrl`, and `quiz.presentation` is `None`. The widget supports
answers, feedback, results, review and retry. It creates no English legacy
presentation, semantic revision, policy binding, trusted receipt or completion
credit. Its result does not call the progress store, including when Workshop
environment variables happen to be present. An unbound self-study Quiz cannot
switch into an admitted release.

English `from_yaml` calls preserve their existing title checks, shuffled order,
legacy identity and file digest. Trusted installation adapters continue to pass
an admitted presentation and matching message context explicitly. Their bound
grading and progress paths are unchanged.

The offline tests load and answer all 33 native Quiz banks in real widget
instances, then repeat all 33 in an isolated ordinary IPython kernel. They do not
call `runtime.configure_quiz`, start a Jupyter server, execute Lab cells, or touch
an existing learner kernel.

From the repository root, run them with an existing course environment:

```bash
PYTHONPATH=doris-course/common doris-course/01-real-time-analytics/.venv/bin/python \
  -m unittest discover -s doris-course/common/tests -p test_native_quiz.py -v
```
