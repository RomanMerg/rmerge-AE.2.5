# Pattern: Social Media Post Scheduling

## Problem
Consistent social media presence requires posting at regular intervals. Most small businesses either post inconsistently (when they remember) or spend 2–4 hours/week manually composing and publishing to each platform. Platforms have different formats, character limits, and optimal posting times.

The manual approach also means everything stops when you're on holiday, sick, or just busy with client work.

## Manual time cost (benchmark)
- Writing + formatting + posting to 2–3 platforms: 30–60 min per post
- 3 posts/week across 2 platforms = 3–6 hours/week = 12–24 hours/month
- At €20/hr: €240–480/month in time cost
- Opportunity cost: consistent posting drives organic reach; inconsistency tanks algorithm performance

## Automation approach
1. Maintain a content calendar in Google Sheets or Notion (title, text, image URL, scheduled date, platforms)
2. Scheduled trigger checks for posts due today
3. For each post: retrieve text and image, format for each platform
4. Publish via platform APIs (Twitter/X, LinkedIn, Facebook, Instagram via Meta API)
5. Mark as posted in the calendar
6. Optional: pull performance metrics back into the sheet 48h later

This separates content creation (your creative work, done in batches) from publishing (a mechanical task the automation handles).

## Recommended tools
- **n8n**: best for direct API access to all major platforms without per-post fees
- **Make.com**: good alternative, has native social media modules
- **Buffer/Hootsuite**: dedicated scheduling tools (simpler setup but ongoing per-post costs)
- **Zapier**: works but per-task pricing makes high-volume posting expensive
- Note: Instagram and TikTok have restrictive API terms for automation — verify before building

## n8n node outline
```
Cron (daily, 8am)
  → Google Sheets: Get Rows (filter: publish_date = today, status = "approved")
  → Loop over each post:
      → [Branch: Twitter/X] HTTP Request: POST to Twitter API v2
      → [Branch: LinkedIn] HTTP Request: POST to LinkedIn API
      → [Branch: Facebook] Facebook Graph API node
      → Google Sheets: Update Row (status = "posted", posted_at = timestamp)
  → [Optional, 48h later] Cron → fetch engagement metrics → update sheet
```

Image handling: store images in Google Drive or an S3 bucket; include the public URL in the sheet. n8n fetches the image as binary data and includes it in the API request.

## ROI benchmark
At 3 posts/week × 2 platforms, 45 min/post manual:
- Time saved: 2.25 hours/week = 9 hours/month
- At €20/hr: **€180/month saved**
- n8n self-hosted cost: €5–10/month shared with other workflows
- Payback: less than 2 weeks
- Additional benefit: batch content creation is cognitively cheaper than daily context-switching
