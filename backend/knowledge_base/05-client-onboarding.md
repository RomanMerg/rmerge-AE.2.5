# Pattern: New Client Onboarding (Welcome Email, Folder Creation, CRM Entry)

## Problem
A new client signs a contract or pays a deposit. Now you need to: send a welcome email with next steps and their portal login, create a project folder in Google Drive (with the right subfolder structure), add them to your CRM, assign them to a project management tool, and possibly send a questionnaire.

This takes 45–90 minutes per client and happens when you're most excited (just closed a deal) but also most busy. Steps get skipped.

## Manual time cost (benchmark)
- 45–90 minutes per new client across all onboarding steps
- 4 new clients/month = 3–6 hours/month
- At €30/hr (higher rate because this is high-trust, client-facing work): €90–180/month
- Risk: missed steps damage the first impression with a new client

## Automation approach
1. Trigger: contract signed (PandaDoc/DocuSign webhook), payment received (Stripe webhook), or manual trigger via CRM deal stage change
2. All downstream steps run in parallel:
   - Create Google Drive folder from template structure
   - Set folder sharing permissions for client email
   - Send welcome email (template with project timeline, portal link, first steps)
   - Create CRM contact + project record
   - Create project in project management tool (Notion, Linear, Asana)
   - Send onboarding questionnaire (Typeform)
3. Internal notification to you with summary of what was set up

The most important insight: these steps are independent and can all run simultaneously. What takes 90 minutes manually takes 30 seconds automated.

## Recommended tools
- **n8n**: best for multi-system integrations; handles Google Drive, Gmail, CRM, Typeform natively
- **Make.com**: visual builder, good for teams who want visibility into the workflow
- **Zapier**: works but multi-step + parallel branches require their premium tier
- **Trigger via**: PandaDoc, DocuSign, Stripe, or a simple form (Tally/Typeform) that you fill yourself

## n8n node outline
```
Webhook (contract signed or Stripe payment.succeeded)
  → Set node (extract: client_name, client_email, project_name, start_date)
  → [Parallel branch 1] Google Drive: Copy Template Folder → Rename → Share with client_email
  → [Parallel branch 2] Gmail: Send welcome email (template)
  → [Parallel branch 3] Twenty CRM: Create Person + Create Opportunity (status: active)
  → [Parallel branch 4] Notion/Linear: Create Project with client details
  → [Parallel branch 5] Typeform: Create response collector or send invite link
  → Wait for all branches (Merge node)
  → Gmail: Send internal summary to yourself
```

## ROI benchmark
At 4 new clients/month, 75 min/client:
- Time saved: 5 hours/month
- At €30/hr: **€150/month saved**
- One-time setup: 3–5 hours
- Running cost: €5–10/month
- Payback: first month
- Hidden benefit: consistent, professional onboarding increases perceived value — hard to price but real
