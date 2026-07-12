# DeltaIQ Landing Page — Draft Copy

*Draft v1 — for review before build. TCPA opt-in section flagged for Sam Davidson review before going live.*

---

## 1. Hero Section

**Headline (options — pick one or mix):**
- "Systematic 0DTE signals for SPY/SPX traders."
- "A disciplined, rules-based read on 0DTE SPY/SPX conditions — delivered straight to your phone."
- "Signals, not predictions. A structured framework for reading 0DTE market conditions."

**Subheadline:**
DeltaIQ is a systematic scoring engine that analyzes SPY/SPX market conditions in real time and delivers observational alerts via SMS — no app to download, no noise to sift through.

**Primary CTA button:** `Subscribe Now` → Stripe Payment Link
**Secondary CTA (text link below button):** `See how it works ↓`

*Note: keep hero language strictly observational/descriptive of the tool's mechanics — avoid performance claims or implied outcomes here. Save any track-record language for a separate section Sam reviews specifically.*

---

## 2. What Is DeltaIQ (Plain-English Overview)

DeltaIQ is an educational tool built for traders who follow 0DTE SPY and SPX options. It applies a consistent, rules-based method to monitor price action throughout the trading day and sends a plain-language SMS when conditions relevant to a defined price threshold are observed.

*(Note: intentionally high-level — no mention of T1/T2, regime classification, or internal scoring mechanics. The goal is to convey "there is a rigorous, consistent method" without describing how it works.)*

**What it does:**
- Tracks SPY/SPX price action relative to a defined dollar-based threshold throughout the trading day
- Applies a consistent, rules-based method to flag when conditions relevant to that threshold occur
- Sends a plain-language SMS alert — no app required

**What it doesn't do:**
- DeltaIQ does not provide personalized investment advice
- Alerts are observational and educational — they describe current conditions, not predictions or recommendations
- You are responsible for your own trading decisions

---

## 3. How It Works

**Step 1: Subscribe**
Sign up and complete SMS opt-in (see below).

**Step 2: Learn the framework**
DeltaIQ alerts are built around a simple, educational concept: price movement relative to a defined dollar-based threshold in SPY. When conditions relevant to that threshold are observed, you receive a concise SMS.

**Step 3: Interpret and decide**
Each alert is observational and educational — it describes current conditions relative to the threshold, not a prediction or recommendation. What you do with that information is entirely up to you.

*(Optional visual: simple 3-icon row — phone/signal icon, alert bubble, checkmark)*

*(⚠️ Flag for Sam: this section describes alerts as tied to a defined price threshold "historically associated with" notable price movement — deliberately avoiding predictive language like "identifies when the market will move." Wording here should get the same review priority as the opt-in language, since this is the section most likely to be read as predictive if not carefully worded.)*

---

## 4. Subscribe CTA (repeat, mid/lower page)

**Headline:** Ready to get started?
**Body:** One plan, delivered by text. No app, no login, no clutter.
**Button:** `Subscribe Now` → Stripe Payment Link

*(Pricing display: pull from Stripe Payment Link page itself, or list $49/mo here if you want price visible before click — your call.)*

---

## 5. SMS Opt-In & TCPA Consent Section — ⚠️ FLAG FOR SAM'S REVIEW

*This section needs to be publicly visible and describe your actual consent flow in the exact language you intend to use, since carriers reviewing A2P 10DLC registration typically check for this. Draft below — treat as a starting point, not final compliance language.*

**Section heading:** Text Message Alerts — Terms & Consent

**Draft body copy:**

> By subscribing to DeltaIQ, you consent to receive SMS alerts from DeltaIQ related to market condition signals. Message frequency varies based on market activity. Message and data rates may apply.
>
> - Reply **STOP** at any time to unsubscribe from all messages.
> - Reply **HELP** for assistance, or contact us at [support email].
> - Consent to receive SMS alerts is not a condition of purchase where required by law.
> - See our [Privacy Policy] and [Terms of Service] for more information.

**Opt-in checkbox language (for use at signup/checkout):**

> ☐ I agree to receive SMS alerts from DeltaIQ at the phone number provided. Message and data rates may apply. Reply STOP to opt out at any time. [Link: Terms] [Link: Privacy Policy]

*Note: exact carrier/TCPA requirements can be specific about placement, checkbox default state (must be unchecked by default), and required disclosures — this is the section where Sam's sign-off matters most before anything goes live.*

---

## 6a. Important Disclosures Page (separate page: `disclosures.html`) — ⚠️ FLAG FOR SAM'S REVIEW

*A dedicated page, linked from the site footer, the opt-in section, and usable as a reference link in daily recap posts. More explicit and complete than the footer disclaimer alone.*

**Page heading:** Important Disclosures

**Draft body copy:**

> **DeltaIQ is an educational and observational tool. It is not:**
> - Investment advice, financial advice, or a recommendation to buy or sell any security
> - A registered investment advisor, broker-dealer, or financial planner
> - A guarantee or prediction of future market movement or trading outcomes
>
> **What DeltaIQ is:**
> - A rules-based, educational alert service that flags observed price conditions in SPY/SPX relative to a defined dollar threshold
> - A tool intended to support your own independent research and decision-making — not replace it
>
> **Risk acknowledgment:**
> Options trading, particularly 0DTE (zero days to expiration) strategies, carries substantial risk of loss and is not suitable for all investors. You should only trade with capital you can afford to lose, and consult a licensed financial professional regarding your individual circumstances before making trading decisions.
>
> **No performance guarantee:**
> Any historical or illustrative information referenced by DeltaIQ, ClearStrike Analytics LLC, or its affiliates is for educational purposes only and is not indicative of future results.

*Note: this page carries real compliance weight for your publisher-exclusion positioning — send to Sam alongside Section 5 and the "How It Works" language above before anything goes live.*

---

## 6. Disclaimer / Compliance Footer

**Draft language (consistent with observational/non-advisory framing used elsewhere):**

> DeltaIQ provides observational market condition alerts for informational purposes only. Nothing provided by DeltaIQ, ClearStrike Analytics LLC, or its affiliates constitutes financial, investment, or trading advice. DeltaIQ is not a registered investment advisor or broker-dealer. Options trading involves substantial risk and is not suitable for all investors. Past performance, where referenced, is not indicative of future results. You are solely responsible for your own trading decisions.

*Note: this footer should appear on every page (landing page + any future recap/dashboard pages), not just this one — worth treating as a reusable site-wide component.*

---

## Site structure (updated)

Confirmed as separate pages, linked from a site-wide footer:
- `index.html` — main landing page (Sections 1–4 above)
- `terms.html` — Terms of Service (content pending from Sam)
- `privacy.html` — Privacy Policy (content pending from Sam)
- `disclosures.html` — Important Disclosures (draft in Section 6a above)

## Open items / decisions needed from you:
1. Final headline choice (Section 1)
2. Support email / contact address to list in opt-in section
3. Whether pricing is shown on-page or only after clicking through to Stripe
4. Send Section 5, 3 (How It Works), and 6a (Disclosures) to Sam before anything goes live — these three carry the most compliance weight
5. Once Sam sends Terms/Privacy content, drop it into `terms.html` / `privacy.html`
