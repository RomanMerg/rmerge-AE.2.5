# Pattern: Invoice/Bill Generation from Spreadsheet Rows

## Problem
At the end of the week or month, you open a spreadsheet tracking completed jobs, hours, or deliverables. For each row, you manually create an invoice in your billing tool (or worse, a Word template), fill in the client name, line items, and total, then email it as a PDF.

This is 10–20 minutes per invoice, done under time pressure at billing time, and easy to get wrong.

## Manual time cost (benchmark)
- 10–20 minutes per invoice (open tracker, create invoice, fill fields, PDF, email)
- 20 invoices/month = 3–7 hours/month
- At €25/hr: €75–175/month in labour cost, every month, forever

## Automation approach
1. Trigger: scheduled (first of month), or row-change in spreadsheet (status = "ready to invoice")
2. Read new/approved rows from Google Sheets or Airtable
3. Group by client (one invoice per client, multiple line items)
4. Generate invoice: either via API (Stripe, QuickBooks, Xero, FreeAgent) or generate HTML/PDF directly
5. Email PDF to client with standard message
6. Mark rows as "invoiced" in the tracker

The most reliable approach is integrating with your existing invoicing tool's API. If you don't use one, n8n can generate and email a clean HTML invoice converted to PDF.

## Recommended tools
- **n8n**: Cron → Google Sheets → HTTP Request (billing API) → Gmail
- **Make.com**: Scheduler → Sheets → Stripe/Xero module → Email
- **Zapier**: works well if your billing tool has a Zap (Stripe, FreshBooks, Wave)
- **Stripe**: if you already use Stripe, their invoicing API is excellent and free per invoice

## n8n node outline
```
Cron (1st of month, 9am)
  → Google Sheets: Get Rows (filter: status = "approved", invoiced = false)
  → Split In Batches (group by client_email)
  → Aggregate node (sum line items per client)
  → HTTP Request: POST to billing API (create invoice)
       OR Code node: generate invoice HTML
       → HTML to PDF conversion (external service or Puppeteer)
  → Gmail: Send invoice PDF to client
  → Google Sheets: Update Row (invoiced = true, invoice_id = ...)
```

## ROI benchmark
At 20 invoices/month, 15 min/invoice:
- Time saved: 5 hours/month
- At €25/hr: **€125/month saved**
- Setup cost: 2–4 hours one-time (€50–100 at consultant rates, or DIY)
- Automation running cost: €5–10/month
- Payback: first billing cycle

Unusually high ROI because invoicing is high-stakes (late invoices = late payments) and monthly recurring.
