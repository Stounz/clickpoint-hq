#!/usr/bin/env python3
"""
ClickPoint Marketing — Campaign Runner
Runs the full 3-month clickpointconsulting.com.au ad campaign
through the agent team sequentially, saving each output to file.

Usage:
  python3 campaign_runner.py

Requires server.py to be running on localhost:3001.
"""

import json
import urllib.request
import urllib.error
import os
import sys
import time
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
SERVER = 'http://localhost:3001'
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'campaign_output')
CLIENT = 'clickpointconsulting.com.au'
CAMPAIGN_NAME = 'AI Applied. Australia Ready.'

# ── Helpers ───────────────────────────────────────────────────────────────────
def banner(text, char='═', width=70):
    print(f'\n  {char * width}')
    print(f'  {text}')
    print(f'  {char * width}')

def step(emoji, label, agent):
    print(f'\n  {emoji}  [{agent.upper()}] {label}')
    print(f'      {"─" * 60}')

def call_agent(agent_id, prompt, context='', max_tokens=2500):
    payload = json.dumps({
        'agentId': agent_id,
        'messages': [{'role': 'user', 'content': prompt}],
        'context': context,
    }).encode()
    req = urllib.request.Request(
        f'{SERVER}/api/agent',
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
        return data['content']

def save(filename, title, agent, content):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    header = (
        f'# {title}\n'
        f'**Agent:** {agent}  \n'
        f'**Client:** {CLIENT}  \n'
        f'**Campaign:** {CAMPAIGN_NAME}  \n'
        f'**Generated:** {datetime.now().strftime("%d %b %Y %H:%M")}  \n\n'
        f'---\n\n'
    )
    with open(path, 'w') as f:
        f.write(header + content)
    print(f'      ✅ Saved → campaign_output/{filename}')
    return content

def check_server():
    try:
        with urllib.request.urlopen(f'{SERVER}/health', timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get('status') == 'ok'
    except Exception:
        return False

# ── Campaign workflow steps ───────────────────────────────────────────────────
STEPS = [
    {
        'file':    '01_strategy_brief.md',
        'title':   'Campaign Strategy Brief',
        'agent':   'sarah',
        'emoji':   '📋',
        'label':   'Campaign Strategy & Team Delegation',
        'context': '',
        'prompt':  f"""New client campaign brief for {CLIENT} — an Australian AI consulting firm.

Campaign: "{CAMPAIGN_NAME}"
Duration: 3 months
Deliverables:
- 3 organic social posts per week (36 total) across LinkedIn and Instagram/Facebook
- 2 paid ads: 1x LinkedIn Lead Gen form ad, 1x Meta awareness video/static ad

Target audience: Australian SME owners, operations managers, CEOs (10–500 staff) in professional services, logistics, finance, retail — exploring AI but unsure where to start.

Tone: Authoritative but approachable, results-driven, Australian market context
CTA options: Book a discovery call | Download guide | clickpointconsulting.com.au

Please provide:
1. Your strategic direction for this campaign (2–3 paragraphs)
2. Clear delegation brief for each team member (Cleo, Zara, Derek, Raj, Jess)
3. Key messaging pillars the whole team must stay consistent on
4. Your 3 non-negotiables for this campaign's success
""",
    },
    {
        'file':    '02_month1_social_posts.md',
        'title':   'Month 1 Social Posts — Education & Authority',
        'agent':   'cleo',
        'emoji':   '📱',
        'label':   'Month 1 Posts: Education & AI Reality Check',
        'context_key': '01',
        'prompt':  f"""Write all 12 social posts for Month 1 of the {CLIENT} campaign.

Month 1 Theme: "The AI Reality Check" — Education & Authority building

Platform split: LinkedIn (primary, more detailed) | Instagram/Facebook (shorter, punchier)
Posting cadence: 3 posts per week across 4 weeks

WEEK 1 POSTS:
Post 1 — LinkedIn: What is an AI Agent? (plain English explanation for Aussie business owners)
Post 2 — Instagram: Before/After graphic — hiring agency vs. AI automation (reference the WebinarKit ad style: before = expensive/slow, after = fast/automated)
Post 3 — LinkedIn: Carousel — "Google's Complete AI Stack explained for Australian businesses"

WEEK 2 POSTS:
Post 4 — LinkedIn: Story post — "We wasted $40k on software before we knew this..." (relatable failure story with lesson)
Post 5 — Instagram: Carousel — "5 AI tools every Australian SME should know in 2026"
Post 6 — LinkedIn: Honest take — "What AI CAN'T do (yet)" builds trust via transparency

WEEK 3 POSTS:
Post 7 — LinkedIn: AI Agent workflow visual explained (based on the hub-and-spoke agent diagram: Agent Communication, Task Allocation, Workflow Management, Integration with Tools/APIs)
Post 8 — LinkedIn: Poll — "Where is your business with AI right now?"
Post 9 — Instagram: "3 signs you're ready for AI automation"

WEEK 4 POSTS:
Post 10 — LinkedIn: Case study format — professional services firm saved X hours/week
Post 11 — Instagram: Reel script — "The AI consulting myth: you don't need a massive budget"
Post 12 — LinkedIn: Lead magnet post — "Australia's AI Readiness Checklist" (download CTA)

For each post write:
- Full caption/copy (ready to publish)
- Hashtags (Instagram posts only)
- Visual direction note (1 sentence)
- CTA
""",
    },
    {
        'file':    '03_month2_social_posts.md',
        'title':   'Month 2 Social Posts — Proof & Social Trust',
        'agent':   'cleo',
        'emoji':   '📱',
        'label':   'Month 2 Posts: AI In Action',
        'context_key': '01',
        'prompt':  f"""Write all 12 social posts for Month 2 of the {CLIENT} campaign.

Month 2 Theme: "AI In Action" — Proof & Social Trust

WEEK 5 POSTS:
Post 13 — LinkedIn: Behind the scenes — what a ClickPoint discovery call looks like (30 min, no sales pressure)
Post 14 — Instagram: Short Reel script — "30 seconds to explain AI agents"
Post 15 — LinkedIn: Carousel — "AI for Australian professional services firms" (lawyers, accountants, consultants)

WEEK 6 POSTS:
Post 16 — LinkedIn: Testimonial/result spotlight (quote from ops manager about hours recovered)
Post 17 — Instagram: Graphic — "The real cost of NOT automating in 2026" (time + money calculations)
Post 18 — LinkedIn: List post — "5 questions to ask before hiring an AI consultant" (builds trust, filters bad actors)

WEEK 7 POSTS:
Post 19 — LinkedIn: Step-by-step — "We built an AI agent for email triage. Here's the full build." (specific 5-step breakdown)
Post 20 — Instagram: Carousel — "AI vs Hiring: the honest side-by-side"
Post 21 — LinkedIn: Transparency post — "The AI tools we actually use at ClickPoint" (eats own cooking)

WEEK 8 POSTS:
Post 22 — LinkedIn: Industry spotlight — AI in logistics (dispatchers, ETAs, invoicing)
Post 23 — Instagram: Story/repost — share a client win moment
Post 24 — LinkedIn: Data post — "State of AI in Australian Business 2026" with stats

For each post write:
- Full caption/copy (ready to publish)
- Hashtags (Instagram posts only)
- Visual direction note (1 sentence)
- CTA
""",
    },
    {
        'file':    '04_month3_social_posts.md',
        'title':   'Month 3 Social Posts — Conversion & Urgency',
        'agent':   'cleo',
        'emoji':   '📱',
        'label':   'Month 3 Posts: Your Move — Conversion Push',
        'context_key': '01',
        'prompt':  f"""Write all 12 social posts for Month 3 of the {CLIENT} campaign.

Month 3 Theme: "Your Move" — Conversion & Urgency

WEEK 9 POSTS:
Post 25 — LinkedIn: Self-assessment post — "Mid-year AI audit: Is your business keeping up?" (scored checklist)
Post 26 — Instagram: Reel — dramatic before/after AI transformation (Monday morning contrast)
Post 27 — LinkedIn: Direct offer — "Book a free AI strategy session" (10 spots, limited)

WEEK 10 POSTS:
Post 28 — LinkedIn: Carousel — "The 3-step ClickPoint process: Discover → Build → Embed"
Post 29 — Instagram: Objection handle — "We'll do it ourselves" response
Post 30 — LinkedIn: FAQ long-form — "What does an AI consultant actually do?" (full explanation)

WEEK 11 POSTS:
Post 31 — LinkedIn: ROI breakdown — specific numbers (implementation cost vs. annual saving, payback weeks)
Post 32 — Instagram: Reel/carousel — "6 weeks from zero to fully automated" client story
Post 33 — LinkedIn: Thought leadership — "The Australian businesses winning with AI right now"

WEEK 12 POSTS:
Post 34 — LinkedIn: Urgency — "8 discovery call spots remaining this quarter"
Post 35 — LinkedIn: Authority — "What we learned from 50+ AI implementations" (lessons list)
Post 36 — Instagram + LinkedIn: Brand story — thank you + what's coming next from ClickPoint

For each post write:
- Full caption/copy (ready to publish)
- Hashtags (Instagram posts only)
- Visual direction note (1 sentence)
- CTA
""",
    },
    {
        'file':    '05_design_briefs.md',
        'title':   'Creative Design Briefs — All Posts & Ads',
        'agent':   'zara',
        'emoji':   '🎨',
        'label':   'Visual Creative Direction & Design Briefs',
        'context_key': '01',
        'prompt':  f"""Create complete design briefs for the {CLIENT} social media campaign.

Brand context: clickpointconsulting.com.au — Australian AI consulting firm
Tone: Authoritative, modern, approachable. Not cold tech. Warm expertise.

I need design briefs for the following asset types:

1. LINKEDIN POST TEMPLATE (text-heavy posts)
- Background treatment
- Typography system (font, weights, sizes)
- Colour palette with hex codes
- Logo placement
- URL treatment

2. CAROUSEL SLIDE TEMPLATE (LinkedIn & Instagram)
- Cover slide layout
- Body slide layout
- Data/stat slide layout
- Final CTA slide layout
- Colour system across slides

3. BEFORE/AFTER GRAPHIC (Instagram — inspired by WebinarKit ad style)
- Split layout direction
- Left side (before) colour and typography
- Right side (after) colour and typography
- Headline treatment
- Content hierarchy

4. REEL COVER / THUMBNAIL TEMPLATE (Instagram)
- Text overlay style
- Background treatment
- Hook text positioning

5. PAID AD CREATIVE — LinkedIn Lead Gen (1200x627px)
- Full layout specification
- Headline positioning
- Visual element (workflow diagram style)
- CTA button style
- Colour system

6. PAID AD CREATIVE — Meta Awareness (1080x1920px vertical + 1080x1080px square)
- Full layout for both sizes
- Text hierarchy
- Before/after data visual
- Brand signature

For each, specify:
- Exact hex colour codes
- Font names and weights
- Layout description (element positions)
- Any reference style (e.g. "Databricks dark ad aesthetic", "clean Notion-style")
- File format and size recommendations
""",
    },
    {
        'file':    '06_paid_ad_linkedin.md',
        'title':   'Paid Ad — LinkedIn Lead Gen Campaign',
        'agent':   'derek',
        'emoji':   '💼',
        'label':   'LinkedIn Lead Gen Ad — Full Campaign Setup',
        'context_key': '01',
        'prompt':  f"""Set up the complete LinkedIn Lead Gen paid ad campaign for {CLIENT}.

Client: clickpointconsulting.com.au — Australian AI consulting firm
Goal: Book discovery calls with qualified Australian SME owners and decision-makers
Budget: Flexible — provide recommendation for a monthly budget to generate 15–20 leads/month
Timeline: Running across the 3-month campaign

Please provide the FULL campaign specification:

1. CAMPAIGN STRUCTURE
- Campaign objective setting in LinkedIn Campaign Manager
- Campaign group name and structure
- Ad set names and logic

2. AUDIENCE TARGETING (be specific with LinkedIn options)
- Job titles to target
- Seniority levels
- Company sizes
- Industries
- Geographic targeting (Australia — any specific states to prioritise?)
- Skills or interests if applicable
- Audience exclusions

3. LEAD GEN FORM
- Form name
- Fields to collect (and which are required)
- Offer/headline on the form
- Thank you message

4. AD CREATIVE SPECS
- Ad format recommendation
- 3 headline variants (A/B test)
- 3 intro text variants
- CTA button options
- Image/creative direction

5. BIDDING STRATEGY
- Recommended bid type
- Starting bid range (AUD)
- Budget pacing recommendation

6. CONVERSION TRACKING
- What events to track in LinkedIn Insight Tag
- How to connect to the booking system

7. OPTIMISATION SCHEDULE
- Week 1–2: setup and baseline
- Week 3–4: first optimisation pass
- Month 2 onwards: scaling approach

Provide specific numbers, settings, and copy — not just frameworks.
""",
    },
    {
        'file':    '07_paid_ad_meta.md',
        'title':   'Paid Ad — Meta Awareness Campaign',
        'agent':   'cleo',
        'emoji':   '📣',
        'label':   'Meta Awareness Ad — Full Campaign Setup',
        'context_key': '01',
        'prompt':  f"""Set up the complete Meta (Facebook + Instagram) awareness paid ad campaign for {CLIENT}.

Client: clickpointconsulting.com.au — Australian AI consulting firm
Goal: Brand awareness and reach among Australian business owners exploring AI
Inspired by: Databricks "State of AI Agents" ad style — bold, data-driven, high-contrast
Budget: Provide recommendation alongside the lead gen campaign
Timeline: 3-month campaign

Please provide the FULL Meta campaign specification:

1. CAMPAIGN STRUCTURE (in Meta Ads Manager)
- Campaign objective
- Campaign name
- Ad set structure

2. AUDIENCE TARGETING
- Core audience (interests, demographics)
- Geographic: Australia — which regions to prioritise?
- Age range
- Lookalike or Advantage+ audience recommendation
- Exclusions

3. AD CREATIVE SPEC
- Recommended format (video/static/carousel)
- Primary text (3 variants for A/B)
- Headline (3 variants)
- Description
- CTA button

4. VIDEO REEL SCRIPT (30-second, if recommending video)
- Full script with visual direction notes per segment
- Hook (first 3 seconds — critical)
- Body
- CTA close

5. STATIC AD OPTION (fallback if video not ready)
- Layout direction
- Copy for static version
- Size recommendations (feed + stories/reels)

6. BIDDING & BUDGET
- Campaign budget optimisation vs ad set budget
- Recommended daily/monthly spend (AUD)
- Bid strategy

7. PIXEL & TRACKING SETUP
- Events to track
- UTM parameters to use (provide exact UTM strings)
- How to retarget website visitors in Month 2

8. CREATIVE TESTING PLAN
- What to test in Month 1
- How to rotate creative in Month 2–3 to avoid fatigue

Provide specific, actionable setup instructions — not just strategy.
""",
    },
    {
        'file':    '08_analytics_and_tracking.md',
        'title':   'Analytics & UTM Tracking Setup',
        'agent':   'raj',
        'emoji':   '📊',
        'label':   'GA4 + UTM Tracking for Full Campaign',
        'context_key': '01',
        'prompt':  f"""Set up the complete analytics and tracking framework for the {CLIENT} 3-month campaign.

Client website: clickpointconsulting.com.au
Campaign: "{CAMPAIGN_NAME}"
Channels: LinkedIn (organic + paid), Instagram (organic), Facebook (organic + paid)
Primary goal: Discovery call bookings
Secondary goal: Lead magnet downloads (AI Readiness Checklist)

Please provide:

1. GA4 SETUP
- Events to configure (list each event name, trigger, and parameters)
- Conversion events to mark in GA4 (which events = conversions)
- Recommended GA4 audiences to create for retargeting
- Key dimensions and metrics to track per channel

2. UTM PARAMETER FRAMEWORK
Provide a complete UTM naming convention and example UTMs for every channel:
- LinkedIn organic posts (each monthly theme)
- LinkedIn paid ad
- Instagram organic posts
- Facebook/Meta paid ad
- Lead magnet download link
- Discovery call booking link

3. LINK SETUP
- URL shortener recommendation for social posts
- How to manage UTM links at scale (spreadsheet structure or tool)

4. REPORTING DASHBOARD
- Recommended GA4 report structure
- Weekly vs monthly metrics to review
- KPIs and targets for each:
  * Discovery calls booked (target: 50 over 90 days)
  * Lead magnet downloads (target: 200+)
  * LinkedIn follower growth (target: +30%)
  * Organic social reach per platform

5. WEEKLY REPORTING CADENCE
- What to check daily (2-min scan)
- What to review weekly (team standup)
- Monthly performance report structure

6. RED FLAGS TO WATCH
- What metrics signal a post/ad is underperforming
- When to pause and pivot vs. give it more time
- Benchmark CPL for LinkedIn in AU market

Provide specific event names, exact UTM strings, and GA4 configuration steps — not just methodology.
""",
    },
    {
        'file':    '09_lead_magnet_checklist.md',
        'title':   'Lead Magnet — AI Readiness Checklist Copy',
        'agent':   'jess',
        'emoji':   '📥',
        'label':   'AI Readiness Checklist — Full Copy',
        'context_key': '01',
        'prompt':  f"""Write the full copy for the {CLIENT} lead magnet: "Australia's AI Readiness Checklist".

This is a downloadable PDF checklist used as the primary lead magnet across the campaign.
It should be valuable enough to trade an email address for.
The checklist gates access via a landing page form → email delivery.

Please write:

1. LANDING PAGE COPY (the page where people download it)
- Hero headline (3 variants)
- Subheadline
- What you'll get (bullet list, 5–7 items)
- Social proof line (placeholder format for future testimonial)
- Form CTA button text (3 variants)
- Privacy micro-copy below form

2. EMAIL DELIVERY (the email sent after download)
- Subject line (3 variants)
- Preview text
- Email body (welcome + link to download)
- PS line with soft CTA to book a discovery call

3. THE CHECKLIST ITSELF — full copy, 10 sections:
Format each as: [Category] Question — Why it matters (1 line explanation)

Categories to cover:
- Workflow documentation
- Data quality & accessibility
- Automation opportunity identification
- Team readiness & change management
- Integration infrastructure
- Success metrics definition
- Internal ownership & accountability
- Australian Privacy Act compliance considerations
- Pilot scope & phased approach
- ROI justification framework

Include:
- A scoring system (how to interpret your score)
- A "What to do next" section based on score range
- ClickPoint CTA at the end

Tone: Expert but practical. Not salesy. Genuinely useful.
""",
    },
]

# ── Main runner ───────────────────────────────────────────────────────────────
def main():
    banner(f'ClickPoint Campaign Runner — {CLIENT}', '═')
    print(f'  Campaign: {CAMPAIGN_NAME}')
    print(f'  Output:   {OUTPUT_DIR}')
    print(f'  Steps:    {len(STEPS)} agent tasks')
    print(f'  Time:     {datetime.now().strftime("%d %b %Y %H:%M")}')

    # Check server
    print(f'\n  Checking agent server at {SERVER}...')
    if not check_server():
        print(f'\n  ❌ Server not reachable at {SERVER}')
        print('  Start it with: python3 server.py\n')
        sys.exit(1)
    print('  ✅ Server online — all agents ready\n')

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Run each step
    outputs = {}  # store content by file prefix key
    for i, s in enumerate(STEPS, 1):
        step(s['emoji'], s['label'], s['agent'])
        print(f'      Calling {s["agent"]}...', end='', flush=True)

        # Build context from a prior step if specified
        context = ''
        if 'context_key' in s:
            prior_key = s['context_key']
            if prior_key in outputs:
                context = f"Campaign strategy brief from CMO Sarah Lin:\n\n{outputs[prior_key]}"

        try:
            t0 = time.time()
            content = call_agent(s['agent'], s['prompt'], context=context, max_tokens=3000)
            elapsed = time.time() - t0
            print(f' done ({elapsed:.0f}s)')
        except urllib.error.HTTPError as e:
            print(f'\n      ❌ HTTP {e.code}: {e.read().decode()[:200]}')
            print('      Skipping this step and continuing...')
            continue
        except Exception as e:
            print(f'\n      ❌ Error: {e}')
            print('      Skipping this step and continuing...')
            continue

        save(s['file'], s['title'], s['agent'], content)

        # Store output using numeric prefix as key
        prefix = s['file'].split('_')[0]
        outputs[prefix] = content

        # Small pause between API calls to be kind to rate limits
        if i < len(STEPS):
            time.sleep(2)

    # ── Generate summary index ──────────────────────────────────────────────
    banner('Generating Campaign Summary Index', '─')
    summary_lines = [
        f'# {CLIENT} — Campaign Output Index\n',
        f'**Campaign:** {CAMPAIGN_NAME}  \n',
        f'**Generated:** {datetime.now().strftime("%d %b %Y %H:%M")}  \n',
        f'**Total steps:** {len(STEPS)}  \n\n---\n\n',
        '## Files\n\n',
    ]
    for s in STEPS:
        path = os.path.join(OUTPUT_DIR, s['file'])
        exists = '✅' if os.path.exists(path) else '❌'
        summary_lines.append(f'- {exists} [{s["title"]}]({s["file"]}) — _{s["agent"]}_\n')

    summary_lines += [
        '\n---\n\n## Campaign at a Glance\n\n',
        '| | |\n|---|---|\n',
        f'| Client | {CLIENT} |\n',
        f'| Campaign | {CAMPAIGN_NAME} |\n',
        '| Duration | 3 months |\n',
        '| Social posts | 36 (3/week) |\n',
        '| Paid ads | 2 (LinkedIn + Meta) |\n',
        '| Platforms | LinkedIn, Instagram, Facebook |\n',
        '| Primary CTA | Book discovery call |\n',
        '| Lead magnet | AI Readiness Checklist |\n',
        '\n---\n\n## Agent Team\n\n',
        '| Agent | Role | Output |\n|---|---|---|\n',
        '| Sarah Lin | CMO | Strategy brief & delegation |\n',
        '| Cleo Chan | Social Media | 36 posts + Meta ad |\n',
        '| Zara Osei | Creative | Design briefs for all assets |\n',
        '| Derek Wu | Paid Search | LinkedIn lead gen ad |\n',
        '| Raj Nair | Analytics | GA4 + UTM tracking |\n',
        '| Jess Park | Content | Lead magnet checklist |\n',
    ]

    summary_path = os.path.join(OUTPUT_DIR, '00_campaign_index.md')
    with open(summary_path, 'w') as f:
        f.writelines(summary_lines)

    banner('Campaign Complete!', '═')
    print(f'  All outputs saved to: {OUTPUT_DIR}')
    print(f'  Open 00_campaign_index.md to navigate all files.\n')


if __name__ == '__main__':
    main()
