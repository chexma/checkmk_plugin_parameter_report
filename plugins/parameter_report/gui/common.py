"""Helpers shared by the report page and the configuration page."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from cmk.gui import forms
from cmk.gui.htmllib.html import html
from cmk.gui.utils.escaping import escape
from cmk.gui.utils.html import HTML

from ..lib import gui_backend as backend
from ..lib.settings import Settings

PERMISSION = "parameter_report.use"

CSS = """
.pr_status { display:flex; flex-wrap:wrap; align-items:center; gap:6px 16px;
  max-width:900px; margin:6px 0 18px; padding:10px 14px;
  border-left:4px solid #888; background:rgba(128,128,128,.08); border-radius:2px; }
.pr_status.pr_ok { border-left-color:#13d389; }
.pr_status.pr_warn { border-left-color:#ffd703; }
.pr_status.pr_crit { border-left-color:#ff3232; }
.pr_status .pr_head { flex:1 1 auto; }
.pr_status #pr_title { font-weight:bold; }
.pr_status #pr_progress, table.data td.number { font-variant-numeric:tabular-nums; }
.pr_status #pr_progress:not(:empty) { margin-left:10px; opacity:.8; }
.pr_status .pr_meta { margin-top:3px; opacity:.7; }
.pr_status .pr_meta:empty { display:none; }
.pr_status form { margin:0; }
.pr_track { flex:1 0 100%; height:4px; background:rgba(128,128,128,.25); border-radius:2px; }
#pr_bar { height:4px; background:#13d389; border-radius:2px; transition:width .6s ease; }
table.nform td.content .pr_hint { margin-left:8px; opacity:.65; }
.pr_top { margin:8px 0 14px; }
.pr_top .pr_note { opacity:.7; }
/* lists of the configuration page (.pr_tree) and the report page (.pr_runs) */
.pr_list { --pr-ok:#13d389; --pr-warn:#ffd703; --pr-crit:#ff3232; --pr-unknown:#ffa000;
  --pr-line:rgba(128,128,128,.22); --pr-rail:rgba(128,128,128,.45);
  margin:4px 0 22px; border-top:1px solid var(--pr-line); }
.pr_list .pr_row { display:grid; align-items:center; column-gap:20px;
  padding:9px 12px; border-bottom:1px solid var(--pr-line); }
.pr_list .pr_row:not(.pr_head):hover { background:rgba(128,128,128,.06); }
.pr_list .pr_head { padding-top:6px; padding-bottom:6px; font-size:11px; font-weight:600;
  letter-spacing:.04em; text-transform:uppercase; opacity:.6; }
.pr_list .pr_head:hover { background:none; }
.pr_list .pr_cell { min-width:0; }
.pr_list .pr_name { display:flex; align-items:center; gap:8px; font-size:13px; font-weight:600; }
.pr_list .pr_name a { color:inherit; text-decoration:none; }
.pr_list .pr_name a:hover { text-decoration:underline; }
.pr_list .pr_sub { margin-top:2px; font-size:11px; opacity:.65; }
.pr_list .pr_num { font-variant-numeric:tabular-nums; }
.pr_list .pr_invalid { color:var(--pr-crit); opacity:1; }
.pr_list .pr_row.pr_sel, .pr_list .pr_row.pr_sel:hover { background:rgba(19,211,137,.07);
  box-shadow:inset 3px 0 0 var(--pr-ok); }
.pr_list .pr_tag { padding:1px 6px; border-radius:3px; font-size:10px; font-weight:600;
  letter-spacing:.04em; text-transform:uppercase; color:var(--pr-ok);
  border:1px solid currentColor; }
.pr_list .pr_tag.pr_plain { color:inherit; opacity:.8; text-transform:none; letter-spacing:0; }
.pr_list .pr_result { display:flex; align-items:center; gap:8px; min-width:0; }
.pr_list .pr_state { flex:none; min-width:38px; padding:2px 5px; border-radius:3px;
  font-size:10px; font-weight:700; text-align:center; color:#1c2228;
  background:rgba(128,128,128,.5); }
.pr_list .pr_state.pr_ok { background:var(--pr-ok); }
.pr_list .pr_state.pr_warn { background:var(--pr-warn); }
.pr_list .pr_state.pr_crit { background:var(--pr-crit); color:#fff; }
.pr_list .pr_state.pr_unknown { background:var(--pr-unknown); }
.pr_list .pr_text { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pr_list .pr_actions { display:flex; align-items:center; justify-content:flex-end; gap:6px; }
.pr_list .pr_actions form { display:inline; margin:0; }
.pr_list .pr_actions input.button, .pr_list .pr_actions a.button { margin:0; }
/* configuration page: base and profiles as an inheritance tree */
.pr_tree .pr_row {
  grid-template-columns:minmax(230px,2fr) minmax(170px,1.2fr) minmax(210px,1.6fr) 230px; }
.pr_tree .pr_base .pr_name { font-size:14px; }
/* the rail: profiles hang off the base and inherit what they do not set */
.pr_tree .pr_child { position:relative; }
.pr_tree .pr_child::before { content:""; position:absolute; left:21px; top:-1px; bottom:-1px;
  border-left:1px solid var(--pr-rail); }
.pr_tree .pr_new::before { bottom:50%; }
.pr_tree .pr_child .pr_entry, .pr_tree .pr_new form { position:relative; padding-left:30px; }
.pr_tree .pr_child .pr_entry::after, .pr_tree .pr_new form::after { content:"";
  position:absolute; left:9px; top:.8em; width:13px; border-top:1px solid var(--pr-rail); }
.pr_tree .pr_new form::after { top:50%; }
.pr_tree .pr_new { grid-template-columns:1fr; }
.pr_tree .pr_new form { display:flex; align-items:center; gap:8px; margin:0; }
/* report page: created reports, newest first */
.pr_runs .pr_row {
  grid-template-columns:minmax(260px,2.2fr) minmax(170px,1.2fr) minmax(220px,1.6fr)
    max-content; }
/* the title unfolds the details of a report */
.pr_runs a.pr_toggle { display:flex; align-items:center; gap:6px; min-width:0; cursor:pointer; }
.pr_runs a.pr_toggle::before { content:"\u25b8"; flex:none; opacity:.6; transition:transform .15s; }
.pr_runs .pr_row.pr_open a.pr_toggle::before { transform:rotate(90deg); }
.pr_runs a.pr_toggle:hover { text-decoration:none; }
.pr_runs a.pr_toggle:hover .pr_text { text-decoration:underline; }
.pr_runs a.pr_toggle:focus-visible { outline:2px solid var(--pr-ok); outline-offset:2px; }
.pr_runs .pr_details { display:none; grid-column:1 / -1; margin-top:10px; padding:10px 12px;
  border-left:2px solid var(--pr-rail); background:rgba(128,128,128,.06); }
.pr_runs .pr_row.pr_open .pr_details { display:block; }
.pr_runs .pr_details dl { display:grid; grid-template-columns:130px 1fr; gap:4px 16px;
  margin:0; font-size:11px; }
.pr_runs .pr_details dt { opacity:.65; }
.pr_runs .pr_details dd { margin:0; word-break:break-word; font-variant-numeric:tabular-nums; }
.pr_more { position:relative; }
.pr_more > summary { list-style:none; cursor:pointer; padding:3px 9px; border-radius:4px;
  border:1px solid var(--pr-rail); font-weight:600; user-select:none; }
.pr_more > summary::-webkit-details-marker { display:none; }
.pr_more > summary::after { content:" \u25be"; opacity:.7; }
.pr_more[open] > summary { border-color:var(--pr-ok); }
.pr_more > summary:focus-visible { outline:2px solid var(--pr-ok); outline-offset:2px; }
.pr_menu { position:absolute; right:0; top:calc(100% + 6px); z-index:50; width:240px;
  padding:12px; border:1px solid var(--pr-rail); border-radius:6px; text-align:left;
  background:var(--pr-surface, #1c2228); box-shadow:0 8px 24px rgba(0,0,0,.35); }
.pr_menu form { display:block !important; margin:0; }
.pr_menu input[type=text] { width:100% !important; box-sizing:border-box; }
.pr_menu .pr_menu_buttons { display:flex; gap:6px; margin-top:8px; }
.pr_menu .pr_sub { margin-top:6px; }
.pr_menu > * + * { margin-top:12px; padding-top:12px; border-top:1px solid var(--pr-line); }
.pr_menu input.pr_danger { color:var(--pr-crit); border-color:var(--pr-crit); }
@media (max-width:900px) {
  .pr_list .pr_row { grid-template-columns:1fr 1fr; row-gap:8px; }
  .pr_list .pr_head { display:none; }
  .pr_list .pr_actions { justify-content:flex-start; grid-column:1 / -1; }
}
@media (prefers-reduced-motion: reduce) {
  #pr_bar, .pr_runs a.pr_toggle::before { transition:none; }
}
"""


def plain(text: object) -> HTML:
    """Text shown as is: html.* helpers only escape permissively (links stay links)."""
    return HTML.without_escaping(escape(str(text)))


def root() -> Path:
    return Path(os.environ["OMD_ROOT"])


def field_sections(
    title: str,
    group: str,
    defaults: Settings,
    isopen: bool,
    values: Mapping[str, str] | None = None,
) -> None:
    """Fields of one group. values: texts to prefill (Configuration), else empty."""
    forms.header(title, isopen=isopen)
    for field in backend.FORM_FIELDS:
        if field.group != group:
            continue
        forms.section(field.title)
        name = field.name
        value = (values or {}).get(field.name, "")
        default = getattr(defaults, field.name)
        if field.kind == "bool":
            # a field the file does not set shows the inherited (placeholder) value
            checked = value == "yes" if name in (values or {}) else bool(default)
            html.checkbox(name, deflt=checked)
        elif field.kind == "choice":
            html.dropdown(name, [(c, c) for c in field.choices], deflt=value or str(default))
        elif field.kind == "textarea":
            placeholder = backend.display_value(defaults, field.name)
            html.text_area(name, value, rows=4, cols=50, placeholder=placeholder)
        else:
            placeholder = backend.display_value(defaults, field.name)
            size = 10 if field.kind in ("int", "float") else 50
            html.text_input(name, value, size=size, placeholder=placeholder)
        if field.help:
            html.span(field.help, class_="pr_hint")
    forms.end()


GROUPS = (
    ("Hosts to include", "selection"),
    ("Report content", "content"),
    ("Layout", "layout"),
    ("Cover page", "cover"),
    ("Advanced settings", "run"),
)


STATE_LABELS = {"ok": "OK", "warn": "WARN", "crit": "CRIT", "unknown": "UNKN", "pending": "RUN"}

# One More menu open at a time; a click elsewhere or Escape closes it. The menu takes the
# page background (the themes have no CSS variable for it).
MORE_JS = """
document.querySelectorAll(".pr_list").forEach(t =>
  t.style.setProperty("--pr-surface", getComputedStyle(document.body).backgroundColor));
document.addEventListener("click", e => {
  document.querySelectorAll("details.pr_more[open]").forEach(d => {
    if (!d.contains(e.target)) d.open = false;
  });
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape")
    document.querySelectorAll("details.pr_more[open]").forEach(d => d.open = false);
});
"""


def state_result(state: str, text: str, title: str = "") -> None:
    """State badge (Checkmk colors), followed by a one-line text if there is one."""
    html.open_div(class_="pr_result", title=title or text or STATE_LABELS[state])
    html.span(STATE_LABELS[state], class_=["pr_state", f"pr_{state}"])
    if text:
        html.span(plain(text), class_="pr_text")
    html.close_div()


def open_more_menu(title: str) -> None:
    """Start a More menu; its content follows, close_more_menu() ends it."""
    html.write_html(HTML.without_escaping('<details class="pr_more">'))
    html.write_html(HTML.without_escaping(f'<summary title="{escape(title)}">More</summary>'))
    html.open_div(class_="pr_menu")


def close_more_menu() -> None:
    html.close_div()
    html.write_html(HTML.without_escaping("</details>"))
