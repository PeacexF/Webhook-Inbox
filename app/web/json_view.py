import json
from typing import Any

from markupsafe import Markup, escape

AUTO_OPEN_DEPTH = 2


def _scalar(value: Any) -> str:
    if value is None:
        return '<span class="j-null">null</span>'
    if isinstance(value, bool):
        return f'<span class="j-bool">{str(value).lower()}</span>'
    if isinstance(value, int | float):
        return f'<span class="j-num">{escape(str(value))}</span>'
    return f'<span class="j-str">"{escape(str(value))}"</span>'


def _render(value: Any, depth: int) -> str:
    if isinstance(value, dict):
        if not value:
            return '<span class="j-punct">{}</span>'
        rows = "".join(
            f'<div class="j-row"><span class="j-key">{escape(str(key))}</span>'
            f'<span class="j-punct">:</span> {_render(item, depth + 1)}</div>'
            for key, item in value.items()
        )
        return _wrap(f"{{{len(value)}}}", rows, depth)

    if isinstance(value, list):
        if not value:
            return '<span class="j-punct">[]</span>'
        rows = "".join(
            f'<div class="j-row"><span class="j-index">{index}</span>'
            f'<span class="j-punct">:</span> {_render(item, depth + 1)}</div>'
            for index, item in enumerate(value)
        )
        return _wrap(f"[{len(value)}]", rows, depth)

    return _scalar(value)


def _wrap(summary: str, rows: str, depth: int) -> str:
    open_attr = " open" if depth < AUTO_OPEN_DEPTH else ""
    return (
        f"<details class='j-node'{open_attr}>"
        f'<summary class="j-summary">{escape(summary)}</summary>'
        f'<div class="j-children">{rows}</div>'
        f"</details>"
    )


def render_json(value: Any) -> Markup:
    return Markup(f'<div class="json-view">{_render(value, 0)}</div>')


def pretty_json(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except TypeError, ValueError:
        return str(value)
