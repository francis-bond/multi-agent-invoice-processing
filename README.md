# Multi-Agent Invoice Processing

Automates invoice processing end to end: ingest from mixed formats, extract structured data,
validate against inventory, route for approval, and pay or reject with reasoning.

## The problem

Acme Corp processes vendor invoices by hand: staff read them, check items against a legacy inventory
database, chase VP approval over email, and trigger payment. It runs at a 30% error rate with five-day
delays.

Invoices arrive in whatever format the vendor sends — PDF, CSV, JSON, XML, plain text — with typos,
missing fields, impossible quantities, and the occasional item that does not exist.

> **Status: in progress.** Currently extraction only. This README will be rewritten when the
> system is complete.

## Running it

```bash
cp .env.example .env     # add your xAI key
uv sync
uv run python src/extract.py data/invoices/invoice_1001.txt
```

## Design

Decisions and reasoning: [`LEARNING_NOTES.md`](LEARNING_NOTES.md).

The short version: **every control decision is code, every judgment call is an agent, nothing in
between.** Approval thresholds, routing, and the pre-payment check are deterministic and auditable.
Extraction, fuzzy matching, and approval reasoning are agents.

## Test data

`data/invoices/` holds the 20 provided sample invoices. They are committed so the repo runs
standalone. They deliberately include broken cases: quantities exceeding stock, unknown items,
negative quantities, duplicated invoice numbers, and at least one date written with a capital `O`
in place of a zero.
