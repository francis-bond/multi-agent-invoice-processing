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

import interventions
from reasons import category, headline, remediation
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
tr.authorised td{box-shadow:inset 3px 0 var(--paid)}
.card.act{border-color:var(--warn)}
tr.needs td{box-shadow:inset 3px 0 var(--warn)}
.detail td{background:var(--bg);font-size:13px;padding:14px 18px 18px}
.detail h4{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}
.detail .block{margin-bottom:14px}
.flag{padding:3px 0;font-variant-numeric:tabular-nums}
.step{padding:5px 0;max-width:84ch;line-height:1.45}
.step code{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.04em;
           color:var(--dim);margin-bottom:1px;font-family:inherit}
.flag code{background:var(--card);border:1px solid var(--line);border-radius:4px;
           padding:1px 6px;font-size:12px;margin-right:8px}
.sev-error code{color:var(--deny)}
.sev-warning code{color:var(--warn)}
.reason{color:var(--ink);max-width:76ch}
.why{max-width:38ch;color:var(--ink)}
.dim{color:var(--dim)}
.empty{padding:40px;text-align:center;color:var(--dim)}
.tools{display:flex;gap:12px;align-items:center;padding:16px 32px 0;flex-wrap:wrap}
#q{flex:1 1 260px;max-width:380px;padding:8px 12px;border:1px solid var(--line);
   border-radius:8px;background:var(--card);color:var(--ink);font-size:13.5px;
   font-family:inherit;-webkit-appearance:none}
#q:focus{outline:2px solid var(--warn);outline-offset:-1px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{padding:7px 13px;border:1px solid var(--line);border-radius:20px;background:var(--card);
      color:var(--dim);font:inherit;font-size:12.5px;cursor:pointer}
.chip:hover{color:var(--ink)}
.chip.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.card[data-f]{cursor:pointer}
.card[data-f]:hover{border-color:var(--dim)}
#count{font-size:12.5px;margin-left:auto}
"""

SCRIPT = """
document.querySelectorAll('tr.run').forEach(function(r){
  r.addEventListener('click', function(){
    var d = document.getElementById('d-' + r.dataset.run);
    if (d) d.hidden = !d.hidden;
  });
});

var box = document.getElementById('q');
var count = document.getElementById('count');
var rows = Array.prototype.slice.call(document.querySelectorAll('tr.run'));

function activeFilter(){
  var on = document.querySelector('.chip.on');
  return on ? on.dataset.f : 'all';
}

function apply(){
  var q = box.value.trim().toLowerCase();
  var f = activeFilter();
  var shown = 0;
  rows.forEach(function(r){
    var show = (f === 'all' || r.dataset.cat === f) &&
               (!q || r.dataset.q.indexOf(q) !== -1);
    r.style.display = show ? '' : 'none';
    var d = document.getElementById('d-' + r.dataset.run);
    if (d){
      // Collapse an open detail row when its parent is filtered out, or it would be left
      // floating under a row that is no longer there.
      if (!show) d.hidden = true;
      d.style.display = show ? '' : 'none';
    }
    if (show) shown++;
  });
  var none = document.getElementById('noresults');
  if (none) none.style.display = shown ? 'none' : '';
  count.textContent = shown === rows.length
    ? rows.length + ' runs'
    : shown + ' of ' + rows.length + ' runs';
}

function select(f){
  document.querySelectorAll('.chip').forEach(function(c){
    c.classList.toggle('on', c.dataset.f === f);
  });
  apply();
}

document.querySelectorAll('.chip').forEach(function(c){
  c.addEventListener('click', function(){ select(c.dataset.f); });
});
// The number on a card is a set of rows; clicking it should show that set.
document.querySelectorAll('.card[data-f]').forEach(function(c){
  c.addEventListener('click', function(){
    select(activeFilter() === c.dataset.f ? 'all' : c.dataset.f);
  });
});
box.addEventListener('input', apply);
// Escape clears the search rather than making anyone reach for the mouse.
box.addEventListener('keydown', function(e){
  if (e.key === 'Escape'){ box.value = ''; apply(); }
});
apply();
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
        # What the search box matches against. Lowercased here so the filter does not have to.
        haystack = " ".join(filter(None, [
            r["invoice_number"], r["vendor"], Path(r["source_path"]).name,
        ])).lower()

        flags = [dict(f) for f in
                 conn.execute("SELECT * FROM flags WHERE run_id=?", (rid,)).fetchall()]
        # The authorisation lives in the ledger database, not the run log: a record of who
        # approved a deviation is evidence for a payment, and pruning logs must not prune it.
        action = interventions.get(r["intervention_id"]) if r["intervention_id"] else None
        why = headline(r["outcome"], r["blocked_reason"], r["processing_error"], flags, r["decision"],
                       r["escalation_reason"], r["critique_rounds"],
                       action["actor"] if action else None)
        fhtml = "".join(
            f"<div class='flag sev-{esc(f['severity'])}'><code>{esc(f['code'])}</code>"
            f"{esc(f['detail'])}</div>" for f in flags
        ) or "<div class='dim'>No findings.</div>"

        # Where a run stopped, shown only when it did not finish. The full node path is
        # identical on every completed run, so as a column it was decoration; `logs.py` is
        # the place to inspect graph topology. What a half-finished run reached is the whole
        # story of that run, though, so keep that case.
        stopped = ""
        if r["outcome"] is None:
            reached = [row[0] for row in conn.execute(
                "SELECT node FROM steps WHERE run_id=? ORDER BY seq", (rid,))]
            last = reached[-1] if reached else "nothing"
            stopped = (f"<div class='block'><h4>Stopped after</h4>{esc(last)} "
                       f"<span class='dim'>({len(reached)} steps recorded)</span></div>")

        reason = ""
        if r["reasoning"]:
            reason = (f"<div class='block'><h4>Approval reasoning "
                      f"({esc(r['decision'])})</h4><div class='reason'>{esc(r['reasoning'])}</div></div>")
        # The gate's reason is only worth showing when it refused a payment the review agent
        # had APPROVED. On a rejected invoice it reads "not approved (decision was
        # 'reject')" - true, circular, and already stated by the outcome. It was identical on
        # all 21 rejected runs, so as a field it said nothing.
        blocked = ""
        if r["blocked_reason"] and r["decision"] == "approve":
            blocked = (f"<div class='block'><h4>Blocked by the payment gate</h4>"
                       f"<div class='reason'>The approval review said approve. The gate "
                       f"refused anyway: {esc(r['blocked_reason'])}</div></div>")
        # What to do next, in two halves. The agent's line is specific to this invoice; the
        # static list is what any invoice with these findings needs. Both, because the
        # specific one can be wrong and the static one can be insufficient.
        steps = remediation(flags)
        todo = ""
        if r["recommended_action"] or steps:
            parts = []
            if r["recommended_action"]:
                parts.append(f"<div class='reason'><strong>{esc(r['recommended_action'])}"
                             f"</strong></div>")
            for name, advice in steps:
                parts.append(f"<div class='step'><code>{esc(name)}</code>{esc(advice)}</div>")
            todo = ("<div class='block'><h4>What to do next</h4>" + "".join(parts) + "</div>")

        authorised = ""
        if action:
            waived = json.loads(action["waived"] or "[]")
            corrections = json.loads(action["corrections"] or "{}")
            bits = [f"<div class='reason'><strong>{esc(action['actor'])}</strong> &middot; "
                    f"{esc(action['resolution'])} &middot; {local(action['at'])}</div>",
                    f"<div class='reason'>&ldquo;{esc(action['justification'])}&rdquo;</div>"]
            if waived:
                bits.append("<div class='step'><code>accepted as immaterial</code>"
                            + esc(", ".join(waived)) + "</div>")
            if corrections:
                bits.append("<div class='step'><code>corrected</code>"
                            + esc(", ".join(f"{k} to {v}" for k, v in corrections.items()))
                            + "</div>")
            if r["supersedes_run"]:
                bits.append(f"<div class='step'><code>answers run</code>"
                            f"{esc(r['supersedes_run'])}</div>")
            authorised = ("<div class='block'><h4>Authorised by a person</h4>"
                          + "".join(bits) + "</div>")

        escalated = ""
        if r["escalation_reason"]:
            escalated = (f"<div class='block'><h4>Escalated</h4>"
                         f"<div class='reason'>{esc(r['escalation_reason'])}</div></div>")
        err = ""
        if r["processing_error"]:
            err = f"<div class='block'><h4>Processing error</h4>{esc(r['processing_error'])}</div>"

        rows.append(f"""
<tr class="run {"needs" if cat == "action" else ""}{" authorised" if action else ""}" data-run="{esc(rid)}"
    data-cat="{cat}" data-q="{esc(haystack)}">
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
      scrutiny: {'yes' if r['needs_scrutiny'] else 'no'} &nbsp;&middot;&nbsp;
      critic revisions: {r['critique_rounds'] or 0}</div>
  {escalated}
  <div class="block"><h4>Findings ({len(flags)})</h4>{fhtml}</div>
  {authorised}{reason}{todo}{blocked}{err}{stopped}
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
  <div class="card" data-f="paid"><div class="n">{counts['paid'][0]}</div><div class="l">Paid</div>
      <div class="amt">{counts['paid'][1]:,.2f}</div></div>
  <div class="card" data-f="denied"><div class="n">{counts['denied'][0]}</div><div class="l">Denied</div>
      <div class="amt">{counts['denied'][1]:,.2f} &middot; vendor to fix</div></div>
  <div class="card act" data-f="action"><div class="n">{counts['action'][0]}</div>
      <div class="l">Needs a person</div>
      <div class="amt">{counts['action'][1]:,.2f} &middot; internal action</div></div>
</div>
<div class="tools">
  <input id="q" type="search" placeholder="Search invoice number, vendor or file" autocomplete="off">
  <div class="chips">
    <button class="chip on" data-f="all">All</button>
    <button class="chip" data-f="paid">Paid</button>
    <button class="chip" data-f="denied">Denied</button>
    <button class="chip" data-f="action">Needs a person</button>
  </div>
  <span id="count" class="dim"></span>
</div>
<main><table>
<thead><tr><th>Invoice</th><th>Vendor</th><th class="num">Amount</th><th>Processed (local)</th>
<th>Result</th><th>Reason</th><th class="num">Flags</th><th class="num">Time</th></tr></thead>
<tbody>{body}
<tr id="noresults" style="display:none"><td colspan="8" class="empty">
  No runs match that filter.</td></tr></tbody></table></main>
<script>{SCRIPT}</script></body></html>"""

    OUT.write_text(html)
    return OUT


if __name__ == "__main__":
    p = build()
    print(f"wrote {p}")
    webbrowser.open(f"file://{p}")
