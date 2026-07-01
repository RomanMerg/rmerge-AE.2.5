# Pattern: Email Parsing → Extract Data, Log to Database

## Problem
Emails arrive containing structured data: order confirmations, booking requests, supplier invoices, support tickets, form submissions forwarded by other systems. Someone manually opens each email, reads it, copies the relevant fields into a spreadsheet, database, or other system.

This is error-prone (copy-paste mistakes), slow (2–5 min per email), and creates a bottleneck when volume spikes.

## Manual time cost (benchmark)
- 2–5 minutes per email to open, read, extract, paste
- 50 emails/week = 2–4 hours/week = 8–16 hours/month
- At €20/hr: €160–320/month in labour cost
- Higher-volume inboxes (200+ emails/week) can justify a €500/month automation budget

## Automation approach
1. Email trigger monitors an inbox (Gmail, IMAP, Outlook)
2. Filter: only process emails matching subject/sender pattern
3. Extract fields using one of:
   - **Regex/code**: for highly structured, consistent formats (order numbers, amounts)
   - **AI extraction**: for freeform text where field positions vary (use GPT-4o-mini, cost ~€0.001/email)
4. Write extracted data to target system: Postgres, Google Sheets, Airtable, or CRM
5. Mark email as processed (label, archive, or forward)

For order confirmations or supplier invoices with consistent formats, regex is fast and free. For freeform customer emails, a short AI prompt extracting 3–5 fields costs fractions of a cent.

## Recommended tools
- **n8n**: Email Trigger node + Code node (regex or call OpenRouter API) + database node
- **Make.com**: Email module + Text Parser or OpenAI module
- **Zapier + Formatter**: works for simple extractions; struggles with AI extraction without Zapier's premium AI tier

## n8n node outline
```
Email Trigger (IMAP / Gmail)
  → IF node (filter: subject contains "Order" or sender = orders@supplier.com)
  → Code node (extract: order_id, amount, customer_name, date — regex or AI call)
  → Postgres: Insert Row (or Google Sheets: Append Row)
  → Gmail: Add Label "processed"
```

For AI extraction, the Code node calls OpenRouter with a prompt like:
"Extract order_id, total_amount, customer_email from this email. Return JSON."

## ROI benchmark
At 50 emails/week, 3 min/email manual:
- Time saved: 2.5 hours/week = 10 hours/month
- At €20/hr: **€200/month saved**
- Automation cost: €10–30/month (n8n VPS + minimal AI API costs)
- Payback: first month

Break-even volume: ~10 emails/week at €20/hr. Below that, manual is fine.
