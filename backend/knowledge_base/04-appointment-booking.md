# Pattern: Appointment Booking → Calendar + Confirmation Email

## Problem
A client wants to book a meeting or appointment. This triggers a back-and-forth: "Are you free Tuesday?" "No, how about Thursday?" "Works for me, what time?" Then you manually add it to your calendar, then manually send a confirmation email with the meeting link, address, or preparation instructions.

For high-booking businesses (consultants, clinics, coaches, tradespeople), this is 15–30 minutes per booking of pure admin.

## Manual time cost (benchmark)
- 15–30 minutes per booking including back-and-forth and follow-up
- 20 bookings/month = 5–10 hours/month
- At €20/hr: €100–200/month
- Also: no-show rate is higher when there's no automated reminder

## Automation approach
1. Client books via self-serve tool (Calendly, Cal.com, TidyCal) — they see your real availability
2. On booking confirmation, webhook fires
3. Automation creates/updates Google Calendar event with all details
4. Sends confirmation email to client (template with meeting details, preparation instructions, address/link)
5. Sends reminder email 24h and 1h before appointment
6. Optional: adds client to CRM, creates follow-up task after appointment

The self-serve booking tool eliminates the back-and-forth entirely. The automation handles the admin downstream.

## Recommended tools
- **Calendly** (booking tool) + **n8n** or **Make.com** (automation) — most common setup
- **Cal.com** (open-source Calendly alternative): works same way, self-hostable
- **TidyCal**: cheaper Calendly alternative, supports webhooks
- **Twenty CRM**: can be auto-updated with booking data via n8n

## n8n node outline
```
Webhook (Calendly / Cal.com booking.created event)
  → Set node (extract: attendee_name, email, start_time, end_time, meeting_type)
  → Google Calendar: Create Event (add client as attendee)
  → Gmail: Send confirmation (template with meeting details + preparation notes)
  → Wait node (trigger 24h before start_time)
  → Gmail: Send reminder
  → [Optional] Twenty CRM: Create/Update Contact + Create Activity
```

For the reminder, use n8n's Wait node set to a calculated time (start_time minus 24 hours).

## ROI benchmark
At 20 bookings/month, 20 min/booking saved:
- Time saved: 6.7 hours/month
- At €20/hr: **€134/month saved**
- Also: ~20% reduction in no-shows from automated reminders (estimated value: 4 bookings/year recovered)
- Calendly free tier covers up to 1 meeting type; Cal.com is fully free self-hosted
- Total cost: €5–15/month → payback in weeks
