# Analytics & UTM Tracking Setup
**Agent:** raj  
**Client:** clickpointconsulting.com.au  
**Campaign:** AI Applied. Australia Ready.  
**Generated:** 22 Apr 2026 18:06  

---

# Analytics & Tracking Framework
## ClickPoint Consulting — "AI Applied. Australia Ready." Campaign

**Prepared by:** Raj Nair, SEO & Analytics Specialist
**Date:** Campaign Pre-Launch
**Document Status:** Ready for implementation

---

## Executive Summary

Before any ad spend goes live, we need verified tracking across three systems: GA4 for behaviour and conversion tracking, Meta Pixel for Facebook/Instagram attribution and retargeting, and LinkedIn Insight Tag for paid campaign measurement. This document provides the complete technical setup, UTM architecture, and reporting framework.

**Critical path item:** Sarah's non-negotiable is tracking verification before ad launch. I'll need developer access or GTM access to the client's site to implement. Flag this to the client immediately.

---

## 1. GA4 Setup & Configuration

### 1.1 Required Events

GA4 uses an event-based model. Below are all events we need configured, organised by type.

#### Automatically Collected Events (Verify These Are Firing)
| Event Name | What It Tracks | Verification Method |
|------------|----------------|---------------------|
| `page_view` | All page loads | GA4 Realtime > Events |
| `session_start` | New sessions | GA4 Realtime > Events |
| `first_visit` | New users | GA4 Realtime > Events |
| `scroll` | 90% page scroll | Enable in Enhanced Measurement |

#### Enhanced Measurement Events (Enable in GA4 Admin)
Navigate to: **Admin > Data Streams > [Web Stream] > Enhanced Measurement**

Enable all of the following:
- ✅ Page views
- ✅ Scrolls
- ✅ Outbound clicks
- ✅ Site search (if search exists)
- ✅ File downloads
- ✅ Video engagement (if applicable)

#### Custom Events to Configure (Via GTM)

**Event 1: Discovery Call Button Click**
```
Event name: discovery_call_click
Trigger: Click on any element containing "book" or "discovery" in button text/class, OR clicks to Calendly/booking URL
Parameters:
  - button_location: {{Click Element}} parent section ID
  - page_path: {{Page Path}}
```

**Event 2: Discovery Call Form Submission (Thank You Page)**
```
Event name: discovery_call_booked
Trigger: Page view where URL contains "/thank-you" or "/booking-confirmed" (confirm actual URL with client)
Parameters:
  - page_path: {{Page Path}}
  - page_referrer: {{Referrer}}
```

**Event 3: Lead Magnet CTA Click**
```
Event name: lead_magnet_click
Trigger: Click on download button/link for AI Readiness Checklist
Parameters:
  - asset_name: "ai_readiness_checklist"
  - button_location: {{Click Element}} parent section
  - page_path: {{Page Path}}
```

**Event 4: Lead Magnet Download Complete**
```
Event name: lead_magnet_download
Trigger: Page view of download thank-you page OR form submission confirmation
Parameters:
  - asset_name: "ai_readiness_checklist"
  - method: "form_submission" or "direct_download"
```

**Event 5: Contact Form Submission**
```
Event name: contact_form_submit
Trigger: Form submission on contact page
Parameters:
  - form_name: {{Form ID}}
  - page_path: {{Page Path}}
```

**Event 6: Key Page Engagement**
```
Event name: high_value_page_view
Trigger: Page view on /services, /about, /case-studies (or equivalent)
Parameters:
  - page_category: "services" | "about" | "proof"
  - page_path: {{Page Path}}
```

**Event 7: External Link Clicks (LinkedIn, etc.)**
```
Event name: social_profile_click
Trigger: Click on outbound links to LinkedIn, social profiles
Parameters:
  - destination: {{Click URL}}
  - link_location: {{Click Element}} parent section
```

### 1.2 Conversion Events (Mark in GA4)

Navigate to: **Admin > Conversions > New conversion event**

Mark these events as conversions:

| Event Name | Conversion Value | Priority |
|------------|-----------------|----------|
| `discovery_call_booked` | $500 (estimated lead value) | PRIMARY |
| `lead_magnet_download` | $50 (micro-conversion value) | SECONDARY |
| `contact_form_submit` | $100 | SECONDARY |
| `discovery_call_click` | — (no value, intent signal) | MONITOR |

**Why assign values:** This allows GA4 to calculate ROAS and helps the algorithm understand relative importance. Values are directional—adjust based on actual close rate data from client.

### 1.3 GA4 Audiences (For Retargeting & Analysis)

Navigate to: **Admin > Audiences > New audience**

#### Audience 1: High-Intent Visitors
```
Name: High_Intent_Discovery
Conditions: 
  - Event: discovery_call_click (at least 1 time)
  - OR Page view: /services, /pricing, /contact
  - Exclude: discovery_call_booked
Membership duration: 30 days
```

#### Audience 2: Lead Magnet Engagers
```
Name: Lead_Magnet_Engaged
Conditions:
  - Event: lead_magnet_download
Membership duration: 90 days
```

#### Audience 3: Nurture Pool (Visited but No Action)
```
Name: Nurture_Pool
Conditions:
  - Session count >= 2
  - Exclude: discovery_call_booked
  - Exclude: lead_magnet_download
Membership duration: 60 days
```

#### Audience 4: LinkedIn Traffic
```
Name: LinkedIn_Visitors
Conditions:
  - Session source contains "linkedin"
Membership duration: 90 days
```

#### Audience 5: Paid Traffic Non-Converters
```
Name: Paid_Retarget_Pool
Conditions:
  - Session medium = "cpc" or "paid" or "sponsored"
  - Exclude: discovery_call_booked
Membership duration: 30 days
```

**Note for Derek:** These GA4 audiences can be exported to Google Ads if we add that channel later. For Meta retargeting, we'll build parallel audiences in Meta Business Suite using Pixel data.

### 1.4 Channel Grouping & Dimensions

GA4's default channel grouping should handle most traffic correctly, but verify these source/medium combinations map properly:

| Traffic Source | Expected Channel Group | UTM Parameters |
|---------------|----------------------|----------------|
| LinkedIn organic | Organic Social | source=linkedin, medium=organic |
| LinkedIn paid | Paid Social | source=linkedin, medium=cpc |
| Instagram organic | Organic Social | source=instagram, medium=organic |
| Facebook organic | Organic Social | source=facebook, medium=organic |
| Meta paid (FB/IG) | Paid Social | source=meta, medium=cpc |
| Direct/no UTM | Direct | (none) |

**Key dimensions to use in reports:**
- Session source/medium
- Session campaign
- Landing page
- Device category
- Country (filter to Australia)

**Key metrics per channel:**
- Sessions
- Engaged sessions
- Engagement rate
- Conversions (discovery_call_booked)
- Conversion rate
- Average engagement time

---

## 2. Additional Tracking Pixels

### 2.1 Meta Pixel Setup

**Pixel ID:** Client to provide (or create in Meta Business Suite > Events Manager)

**Standard Events to Configure:**

```javascript
// Page View (fires on all pages)
fbq('track', 'PageView');

// Lead Magnet Download
fbq('track', 'Lead', {
  content_name: 'AI Readiness Checklist',
  content_category: 'Lead Magnet'
});

// Discovery Call Booked
fbq('track', 'Schedule', {
  content_name: 'Discovery Call',
  value: 500.00,
  currency: 'AUD'
});

// Contact Form Submit
fbq('track', 'Contact