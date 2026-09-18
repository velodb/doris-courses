"""Reuse course 01's display components rather than duplicate its CSS."""

import json

from IPython import get_ipython
from IPython.display import HTML, display

from ._shared import load_component

_shared = load_component("doris_client")
install_styles = _shared.install_styles
card = _shared.card
show_sql = _shared.show_sql
show_frame = _shared.show_frame
show_log = _shared.show_log
workflow_html = _shared.DorisLab._workflow_html


def in_notebook():
    return get_ipython() is not None


class WorkflowProgress:
    """Update one shared workflow panel; keep complete logs below the panel."""

    def __init__(self, title, steps):
        self.title = title
        self.steps = steps
        self.current = 1
        self.logs = []
        self.handle = None
        self.notebook = in_notebook()

    def _render(self, state, detail=""):
        panel = workflow_html(self.title, self.steps, self.current, state, detail)
        headline = {
            "success": ("Completed", "已完成"),
            "failure": ("Failed", "失败"),
            "running": (f"Step {self.current} of {len(self.steps)}",
                        f"第 {self.current} / {len(self.steps)} 步"),
        }[state]
        panel = HTML(panel.data.replace(f"<span>{headline[0]}</span>",
                                       f"<span>{headline[1]}</span>"))
        if self.handle is None:
            self.handle = display(panel, display_id=True)
        else:
            self.handle.update(panel)

    def advance(self, number):
        self.current = number
        self.log(f"[{number}/{len(self.steps)}] {self.steps[number]}")
        if self.notebook:
            self._render("running")
        else:
            print(self.logs[-1], flush=True)

    def log(self, text):
        self.logs.append(text)

    def finish(self):
        if self.notebook:
            self._render("success")
            show_log("查看完整启动日志", "\n".join(self.logs))
        else:
            print(f"已完成：{self.title}", flush=True)

    def fail(self, detail):
        self.log(detail)
        if self.notebook:
            self._render("failure", detail)
            show_log("查看失败原因与完整日志", "\n".join(self.logs), opened=True)
        else:
            print("\n".join(self.logs), flush=True)


def show_response(response, title="Stream Load response"):
    content = json.dumps(response, ensure_ascii=False, indent=2)
    if in_notebook():
        show_log(title, content, opened=True)
    else:
        print(content)
