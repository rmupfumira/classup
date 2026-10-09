# Email deliverability — Gmail spam fix

**Why Gmail was flagging ClassUp mail as spam, and what landed in code vs
what still needs DNS.**

## What changed in code (2026-10-09)

Fixed in `app/services/email_service.py`:

1. **Plaintext body** — every send now ships `multipart/alternative`
   with a proper `text/plain` part rendered from the HTML (not just
   HTML). Gmail penalises HTML-only mail heavily.
2. **List-Unsubscribe + List-Unsubscribe-Post headers** — Gmail's
   bulk-sender policy expects both an `https://` one-click URL and a
   `mailto:` fallback. Shipping these alone takes you out of the
   "promotional" bucket.
3. **Reply-To defaulting to the tenant's email** — if the caller
   doesn't pass a `reply_to` and `context['tenant_email']` is set,
   it becomes the Reply-To. Parents hitting Reply land at the
   school, not `test@classup.co.za`.
4. **Message-ID + Date headers** — mail without these looks like
   a bot handoff.
5. **Public `/unsubscribe` endpoint** — handles both GET (human
   browser click) and POST (Gmail one-click). Flips
   `email_opted_in=False` + `whatsapp_opted_in=False` for every
   row with that email. Exempt from Auth + Subscription middleware.
6. **Low-reputation local-part warning** — if the configured
   `from_email` is `test@`, `demo@`, `temp@` or `admin@`, the
   service logs a warning on startup so operators notice.

## What still needs doing in DNS (classup.co.za)

Add these three records to the DNS zone before expecting real
inbox placement:

### 1. SPF (TXT @)

```
v=spf1 include:_spf.google.com include:mail.resend.com ~all
```

Adjust the `include:` targets to match the SMTP relay you're
using. If you're on your own SMTP server, publish its outbound IP:
`v=spf1 ip4:<server-ip> ~all`.

### 2. DKIM

**Resend**: the dashboard shows you three CNAME records
(`resend._domainkey`, `resend2._domainkey`, …). Copy them in.

**Google Workspace / SMTP self-hosted**: generate a 2048-bit key,
publish the public key as a TXT at `default._domainkey.classup.co.za`.

### 3. DMARC (TXT _dmarc)

```
v=DMARC1; p=quarantine; rua=mailto:dmarc@classup.co.za; adkim=s; aspf=s
```

Start with `p=quarantine` and leave it there for a week while you
read the aggregate reports, then move to `p=reject`.

## Operational to-do

1. **Change `from_email` away from `test@classup.co.za`** — go to
   `/admin/email-settings` and set it to `notifications@classup.co.za`
   or `school@classup.co.za`. Keep the mailbox active so bounces
   can reach it.
2. **Create an `unsubscribe@classup.co.za` catch-all** — Gmail's
   `List-Unsubscribe` mailto fallback posts here. A rule that forwards
   it to the ops inbox is fine; it just can't bounce.
3. **Verify SPF/DKIM alignment** — once DNS propagates, send a test
   to `test@mail-tester.com` or `check-auth@verifier.port25.com` and
   confirm you get a 10/10 score. Anything less is still a spam risk.
4. **Enable `app_base_url` in config** — the one-click unsubscribe
   URL needs an absolute base. Make sure `APP_BASE_URL` is set in
   `.env`/Railway to the real production domain.
5. **Warm up the sending IP** — if `classup.co.za` is new to the
   SMTP relay, ramp up volume over 2–3 weeks (50 → 200 → 500/day)
   so Gmail builds a reputation.

## How to verify

- **Live header check**: send yourself a parent invite from staging,
  open the message in Gmail, click the three-dot menu → "Show
  original". Confirm the headers show `SPF=PASS`, `DKIM=PASS`,
  `DMARC=PASS`, and `List-Unsubscribe` appears.
- **Automated test suite**: `pytest tests/test_services/test_email_deliverability.py`
  covers the plaintext + headers + Reply-To paths.
- **Mail-tester**: send a copy to the throwaway address they give
  you at [mail-tester.com](https://www.mail-tester.com); aim for ≥9/10.
