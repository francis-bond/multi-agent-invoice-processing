"""Generate a static dashboard from the run log.

    uv run python src/dashboard.py        writes dashboard.html

Static HTML rather than a server: no process to keep running, no extra dependency, and the
file can be opened from disk or attached to an email. Regenerate after a run to refresh.

Times are shown in local time. The log stores UTC, which is correct for storage and wrong
for a human scanning "when did this happen".
"""

import json
import sqlite3
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from reasons import category, headline
from runlog import DB_PATH

OUT = Path(__file__).parent.parent / "dashboard.html"

STYLE = """
:root{--bg:#f7f7f5;--card:#fff;--line:#e4e4e0;--ink:#1a1a18;--dim:#6b6b64;
      --paid:#0d7a3e;--paidbg:#e8f5ec;--deny:#b02a1f;--denybg:#fdeceb;
      --warn:#9a6400;--warnbg:#fdf3e0;}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--card:#1f1f22;--line:#33333a;
      --ink:#ececea;--dim:#9a9a94;--paidbg:#14301f;--paid:#5fc98a;
      --denybg:#3a1a17;--deny:#f08a7e;--warnbg:#36290f;--warn:#e8b955;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
header{padding:28px 32px 18px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:20px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px}
.cards{display:flex;gap:14px;padding:20px 32px 4px;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:14px 18px;min-width:150px}
.card .n{font-size:26px;font-weight:600;letter-spacing:-.02em}
.card .l{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.card .amt{color:var(--dim);font-size:13px;margin-top:2px}
main{padding:18px 32px 60px}
table{width:100%;border-collapse:collapse;background:var(--card);
      border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
   color:var(--dim);padding:11px 14px;border-bottom:1px solid var(--line);font-weight:600}
td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
tr.run{cursor:pointer}
tr.run:hover{background:var(--bg)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11.5px;font-weight:600}
.paid{background:var(--paidbg);color:var(--paid)}
.denied{background:var(--denybg);color:var(--deny)}
.action{background:var(--warnbg);color:var(--warn)}
.card.act{border-color:var(--warn)}
tr.needs td{box-shadow:inset 3px 0 var(--warn)}
.detail td{background:var(--bg);font-size:13px;padding:14px 18px 18px}
.detail h4{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}
.detail .block{margin-bottom:14px}
.flag{padding:3px 0;font-variant-numeric:tabular-nums}
.flag code{background:var(--card);border:1px solid var(--line);border-radius:4px;
           padding:1px 6px;font-size:12px;margin-right:8px}
.sev-error code{color:var(--deny)}
.sev-warning code{color:var(--warn)}
.reason{color:var(--ink);max-width:76ch}
.why{max-width:38ch;color:var(--ink)}
.dim{color:var(--dim)}
.empty{padding:40px;text-align:center;color:var(--dim)}
"""

SCRIPT = """
document.querySelectorAll('tr.run').forEach(function(r){
  r.addEventListener('click', function(){
    var d = document.getElementById('d-' + r.dataset.run);
    if (d) d.hidden = !d.hidden;
  });
});
"""


def local(ts: str | None) -> str:
    if not ts:
        return "-"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d %b %Y, %H:%M:%S")


def esc(v) -> str:
    return (str(v) if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build() -> Path:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    runs = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()

    # Bucket by what each run asks of a human, not by how the pipeline happened to end.
    counts = {"paid": [0, 0.0], "denied": [0, 0.0], "action": [0, 0.0]}
    cats = {}
    for r in runs:
        fl = [dict(f) for f in
              conn.execute("SELECT code, severity FROM flags WHERE run_id=?", (r["run_id"],))]
        cats[r["run_id"]] = c = category(r["outcome"], r["processing_error"], fl)
        counts[c][0] += 1
        counts[c][1] += r["total"] or 0

    rows = []
    for r in runs:
        rid = r["run_id"]
        outcome = r["outcome"] or "incomplete"
        cat = cats[rid]
        cls = {"paid": "paid", "denied": "denied", "action": "action"}[cat]
        label = {"paid": "Paid", "denied": "Denied", "action": "Needs a person"}[cat]
        total = f"{r['total']:,.2f}" if r["total"] is not None else "-"

        flags = [dict(f) for f in
                 conn.execute("SELECT * FROM flags WHERE run_id=?", (rid,)).fetchall()]
        why = headline(r["outcome"], r["blocked_reason"], r["processing_error"], flags, r["decision"])
        fhtml = "".join(
            f"<div class='flag sev-{esc(f['severity'])}'><code>{esc(f['code'])}</code>"
            f"{esc(f['detail'])}</div>" for f in flags
        ) or "<div class='dim'>No findings.</div>"

        nodes = " &rarr; ".join(
            esc(s["node"]) for s in
            conn.execute("SELECT node FROM steps WHERE run_id=? ORDER BY seq", (rid,))
        )

        reason = ""
        if r["reasoning"]:
            reason = (f"<div class='block'><h4>Approval reasoning "
                      f"({esc(r['decision'])})</h4><div class='reason'>{esc(r['reasoning'])}</div></div>")
        blocked = ""
        if r["blocked_reason"]:
            blocked = f"<div class='block'><h4>Not paid because</h4>{esc(r['blocked_reason'])}</div>"
        err = ""
        if r["processing_error"]:
            err = f"<div class='block'><h4>Processing error</h4>{esc(r['processing_error'])}</div>"

        rows.append(f"""
<tr class="run {"needs" if cat == "action" else ""}" data-run="{esc(rid)}">
  <td><strong>{esc(r['invoice_number'] or '-')}</strong></td>
  <td>{esc(r['vendor'] or '-')}</td>
  <td class="num">{total}</td>
  <td>{local(r['started_at'])}</td>
  <td><span class="pill {cls}">{label}</span></td>
  <td class="why">{esc(why)}</td>
  <td class="num">{r['flag_count'] or 0}</td>
  <td class="num dim">{r['duration_ms'] or 0} ms</td>
</tr>
<tr class="detail" id="d-{esc(rid)}" hidden><td colspan="8">
  <div class="block"><h4>Source</h4>{esc(r['source_path'])} &nbsp;&middot;&nbsp;
      run <code>{esc(rid)}</code> &nbsp;&middot;&nbsp;
      scrutiny: {'yes' if r['needs_scrutiny'] else 'no'}</div>
  <div class="block"><h4>Findings ({len(flags)})</h4>{fhtml}</div>
  {reason}{blocked}{err}
  <div class="block"><h4>Nodes</h4><span class="dim">{nodes}</span></div>
</td></tr>""")

    body = "".join(rows) or "<tr><td colspan='8' class='empty'>No runs recorded yet.</td></tr>"

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invoice Processing</title><style>{STYLE}</style></head><body>
<header>
  <h1>Invoice Processing</h1>
  <div class="sub">{len(runs)} runs &middot; generated {datetime.now().astimezone().strftime('%d %b %Y, %H:%M')} local
    &middot; click a row for detail</div>
</header>
<div class="cards">
  <div class="card"><div class="n">{counts['paid'][0]}</div><div class="l">Paid</div>
      <div class="amt">{counts['paid'][1]:,.2f}</div></div>
  <div class="card"><div class="n">{counts['denied'][0]}</div><div class="l">Denied</div>
      <div class="amt">{counts['denied'][1]:,.2f} &middot; vendor to fix</div></div>
  <div class="card act"><div class="n">{counts['action'][0]}</div>
      <div class="l">Needs a person</div>
      <div class="amt">{counts['action'][1]:,.2f} &middot; internal action</div></div>
</div>
<main><table>
<thead><tr><th>Invoice</th><th>Vendor</th><th class="num">Amount</th><th>Processed (local)</th>
<th>Result</th><th>Reason</th><th class="num">Flags</th><th class="num">Time</th></tr></thead>
<tbody>{body}</tbody></table></main>
<script>{SCRIPT}</script></body></html>"""

    OUT.write_text(html)
    return OUT


if __name__ == "__main__":
    p = build()
    print(f"wrote {p}")
    webbrowser.open(f"file://{p}")
