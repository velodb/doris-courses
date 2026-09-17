"""Reuse course 01's display components rather than duplicate its CSS."""

import json

from IPython import get_ipython

from ._shared import load_component

_shared = load_component("doris_client")
install_styles = _shared.install_styles
card = _shared.card
show_sql = _shared.show_sql
show_frame = _shared.show_frame
show_log = _shared.show_log


def in_notebook():
    return get_ipython() is not None


def show_response(response, title="Stream Load response"):
    content = json.dumps(response, ensure_ascii=False, indent=2)
    if in_notebook():
        show_log(title, content, opened=True)
    else:
        print(content)
