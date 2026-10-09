# Maintaining and adapting this system

Written for the next person deploying this somewhere else. Most changes a client asks for are
one of four kinds, and knowing which you have tells you where to go.

| What you are changing | Where | Needs a code change? |
|---|---|---|
| What we buy, what it costs, who we buy from | `src/inventory.py` | No — reference data |
| Thresholds, tolerances, limits, currency | `src/policy.py` or `.env` | No — configuration |
| A new rule about what is acceptable | `src/validate.py` + wiring | Yes, and the tests tell you what you missed |
| A new file format, field, or model | see below | Yes |

A useful first question: **is this a fact, a policy, or a rule?** Facts about the world
(we stock this, we buy from them, it costs that) are reference data. Decisions a client would
want to set differently (how much needs a second look) are policy. Rules about what makes an
invoice unacceptable are code, because they have to be auditable and cannot be waived by a
language model.

---

## What we buy, and who we buy from

Both live in `src/inventory.py`, seeded into `inventory.db`.

```python
# Item, stock on hand, agreed unit price
SEED = [
    ("WidgetA", 15, 250.00),
    ...
]

# The approved supplier list
VENDORS = [
    "Widgets Inc.",
    ...
]
```

Edit the lists and re-seed:

```bash
uv run python src/inventory.py
```

That rewrites the rows it knows about and leaves anything else alone.

**In a real deployment neither list is hard-coded.** They stand in for an ERP's item master
and vendor master, and the integration point is `resolve()` and `vendor_is_approved()` in that
module — replace the SQLite query with a call to the client's system and nothing upstream
changes. The matching behaviour (case and punctuation folded, a trailing qualifier stripped
with a warning) belongs here too, so it stays consistent whatever the data source is.

**Why the price matters as much as the stock level.** Without an agreed price nothing in the
system notices a unit price at all: arithmetic only checks that the figures agree with each
other, and the approval agent has no reference to compare against. A WidgetA billed at 2,500.00
instead of 250.00 is internally consistent, within stock, from a known supplier, and would
simply be paid. If a client cannot supply prices, say plainly that overcharge detection is off.

**Adding a supplier is the normal resolution** to `unknown_vendor` on a legitimate new vendor.
That is a `records_wrong` intervention: update the list, re-seed, and re-run the invoice.

---

## Thresholds, tolerances and limits

All in `src/policy.py`, each overridable by an environment variable so a client can change one
without touching code. They are in one file on purpose: an auditor asking "what are the rules?"
should not have to read the implementation to find them.

| Setting | Default | What it decides |
|---|---|---|
| `SCRUTINY_THRESHOLD` | 10000 | Above this, an invoice takes the careful path however clean it looks |
| `HOME_CURRENCY` | USD | What we can pay. Anything else goes to a person |
| `PRICE_TOLERANCE` | 0.10 | How far above the agreed price a line may be billed before it is flagged |
| `MAX_EXTRACTION_ATTEMPTS` | 2 | Attempts at reading one document |
| `CRITIQUE_MAX_ROUNDS` | 2 | Times the critic may send the approval agent back |
| `CRITIC_MAX_TOOL_STEPS` | 4 | Lookups the critic may make before it must write its critique |

**Raising `SCRUTINY_THRESHOLD`** sends fewer invoices to the critic, which is cheaper and
faster. Note that any flag at all also triggers scrutiny regardless of amount, so this only
affects clean invoices.

**Lowering `PRICE_TOLERANCE`** catches smaller overcharges and produces more flags on
legitimate premiums. A client with negotiated rush rates may want it higher, or may want the
rush rate in the catalogue as its own item instead.

**Raising the loop limits is usually the wrong instinct.** The only thing that differs between
attempts is the feedback, so if an informed retry has not settled it, another carries the same
complaint to the same model at the same temperature. If a client wants five rounds, the
question to ask is what would be different on round five. A person reading the document is the
better answer, which is what escalation is for.

`LLM_MAX_ATTEMPTS` is deliberately **not** here: it is about the network being unreliable, not
about how an invoice should be handled, and it is the same for every client.

---

## Adding a rule about what is acceptable

Say a client wants invoices refused when the due date has already passed.

1. **Write the check** in `src/validate.py` as a function returning `Flag`s, and add it to
   `validate()`. Return findings rather than raising — an invoice with five problems should
   report five, not stop at the first.

2. **Classify where its facts come from** in `EVIDENCE_SOURCE`, same file. `"document"` if it
   is read off the invoice, `"system"` if it comes from our own records. This decides whether
   the critic may argue with it: it can see the document and cannot see our databases, so a
   system-derived finding is not its to overturn. An unclassified code defaults to `"system"`,
   which fails safe.

3. **Say what it means to a human** in `src/reasons.py`: a short `LABELS` entry, a `REMEDIATION`
   entry saying what to actually do about it, and a position in `PRIORITY` — ordered by
   consequence, so a duplicate payment leads and a missing due date does not. Add it to
   `ACTION_REQUIRED` if it means *we* have work to do rather than the vendor.

4. **If it is system-derived**, give the critic a lookup that answers it: a `@tool` in
   `src/lookups.py` and an entry in `ANSWERED_BY`. Without one the critic can never object to
   it, which is a silent dead end rather than a decision.

5. **Decide whether the gate blocks it.** The gate refuses anything our own records contradict,
   so a `"system"` error blocks payment automatically and an approval cannot waive it.
   Document-derived findings stay the agent's judgment.

**You do not have to remember steps 2 to 4.** Five tests scan the source for every finding
code the code emits and fail if one has no label, no remediation advice, no priority, no
evidence source, or — for a system-derived finding — no lookup that answers it. Add a check and
wire up none of it, and the suite names all five omissions.

That is worth knowing because the mistake is easy and the symptom is not obvious: a finding
with no label shows an auditor a snake_case code, and one with no evidence source silently
defaults to unarguable. Verified by adding an unwired finding and watching all five fail.

---

## Other changes

**A new file format.** Add a reader to `READERS` in `src/documents.py`. Import its library
inside the function, not at module top, so nobody installs a dependency for a format they never
receive. Make the failure message name the package. If the format has no text layer, say it
needs OCR rather than reporting an empty invoice — "blank" and "scanned" need different
responses.

**A new field on the invoice.** Add it to `src/models.py`, describe it in the extraction prompt
in `src/extract.py`, then re-record the fixtures:

```bash
uv run python scripts/record_fixtures.py
```

Fields are required rather than optional on purpose. Making one optional tells the model it is
skippable, and four fields were dropped from a messy invoice the one time this was tried.
Anything carrying a figure should carry a citation, which the verification in
`src/citations.py` then checks.

**A different model or provider.** `src/llm.py`, and nowhere else. That is the point of going
through LangChain's chat interface rather than a vendor SDK. Set `XAI_MODEL` for a different
model from the same provider.

**A new lookup for the critic.** A `@tool` in `src/lookups.py`, added to `TOOLS`. Keep it
read-only: these give the agent evidence, not authority. Nothing the critic reaches should
write anything.

---

## After any change

```bash
uv run python -m pytest tests/ -q          # fast, no API key, no cost
uv run python main.py --all                 # the whole sample set, end to end
uv run python src/dashboard.py              # look at what it decided
```

The test suite is the quick check and runs offline. The batch is the real one, because the
tests stub every model call — they prove the control flow is right, not that the prompts still
work. Changing a prompt is exactly the case where the tests will pass and the behaviour will
not, so run the batch after touching one.

---

## Where not to put things

**Not in a prompt: anything that must always happen.** A prompt is a request. `currency` was
asked for as `null` in capitals with examples and came back as `""` anyway; a schema permitted
the empty string because it constrains the type and not the value. Only a code check made it
hold. If a client says "it must never do X", X belongs in `validate.py` or the gate.

**Not in the agent: a decision you need to defend.** Routing, thresholds, grounding and the
pre-payment gate are deterministic so an auditor gets a rule and a line number. The agents
decide only things that do not reduce to a rule.

**Not a flag: something that should not change the decision.** A warning was once raised on
both copies of an invoice submitted in two formats, and the approval agent treated it as a
reason to refuse whichever copy happened to be processed first. If it should not affect the
outcome, it does not belong in `flags`.

**Not a bypass: human intervention.** Resolving an escalation produces a new run through every
control. If someone asks for a "just pay it" button, the answer is that a person supplies a
better input and not the verdict — and that paying a figure no document states leaves the
vendor's receivable unreconciled anyway.
