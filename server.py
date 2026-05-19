#!/usr/bin/env python3
"""
ClickPoint Marketing — Agent API Server v2.6
Proxies requests to the Anthropic Claude API with per-agent system prompts.
Supports single-agent calls and multi-agent chaining.
Run: python3 server.py
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import urllib.parse
import os
import sys
import datetime
import threading
import time
import collections

# ── Rate limiter ─────────────────────────────────────────────────────────────
class _RateLimiter:
    """Sliding-window rate limiter keyed by (ip, endpoint)."""
    def __init__(self, max_calls: int, window_seconds: int):
        self._max   = max_calls
        self._win   = window_seconds
        self._hits: dict[str, collections.deque] = {}
        self._lock  = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, collections.deque())
            cutoff = now - self._win
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True

# 10 login attempts per IP per 60 s; 60 agent calls per IP per 60 s
_login_limiter = _RateLimiter(max_calls=10, window_seconds=60)
_agent_limiter = _RateLimiter(max_calls=60, window_seconds=60)

# ── Optional encryption (pip3 install cryptography) ───────────────────────────
try:
    from cryptography.fernet import Fernet as _Fernet
    _FERNET_OK = True
except ImportError:
    _FERNET_OK = False

# ── Load all environment variables from .env ──────────────────────────────────
def _load_env() -> dict:
    """Read .env file into dict; env vars take precedence."""
    result = {}
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    result[k.strip()] = v.strip().strip('"').strip("'")
    # System env overrides file
    for k in list(result):
        result[k] = os.getenv(k, result[k])
    # Pick up ALL env vars set via Railway / Heroku / system (overrides .env file)
    for k, v in os.environ.items():
        if v:
            result[k] = v

    # APP_CONFIG fallback — single JSON variable containing all secrets.
    app_config_raw = os.getenv('APP_CONFIG', '')
    if app_config_raw:
        try:
            import json as _j
            for k, v in _j.loads(app_config_raw).items():
                if v and not result.get(k):
                    result[k] = str(v)
            print('  ✅ APP_CONFIG loaded successfully')
        except Exception as e:
            print(f'  ⚠️  APP_CONFIG parse error: {e}')

    # Fallback credentials (env vars / APP_CONFIG always take precedence)
    import base64 as _b64
    def _d(s): return _b64.b64decode(s.encode()).decode()
    _defaults = {
        'PLATFORM_URL':        'https://platform.clickpointconsulting.com.au',
        'CANVA_CLIENT_ID':     _d('T0MtQVozMllmMlZycHRr'),
        'CANVA_CLIENT_SECRET': _d('Y252Y2FHT3JHbVFUdTA3QzhLV3RNdkNIcFdtWURTZnBDQ3M2cFpnVkFnLUNBTHhVMjExNjU0Njk='),
    }
    for k, v in _defaults.items():
        if v and not result.get(k):
            result[k] = v

    return result

_ENV = _load_env()

API_KEY                    = _ENV.get('ANTHROPIC_API_KEY', '')
SUPABASE_URL               = _ENV.get('SUPABASE_URL', '')
SUPABASE_SERVICE_KEY       = _ENV.get('SUPABASE_SERVICE_KEY', '')
INTEGRATION_ENCRYPTION_KEY = _ENV.get('INTEGRATION_ENCRYPTION_KEY', '')
SLACK_WEBHOOK_URL          = _ENV.get('SLACK_WEBHOOK_URL', '')
RESEND_API_KEY             = _ENV.get('RESEND_API_KEY', '')
RESEND_FROM                = _ENV.get('RESEND_FROM', 'ClickPoint <noreply@clickpointconsulting.com.au>')
# ── SMTP (cPanel / any SMTP) — takes priority over Resend when configured ──
SMTP_HOST                  = _ENV.get('SMTP_HOST', '')
SMTP_PORT                  = int(_ENV.get('SMTP_PORT', '465'))
SMTP_USER                  = _ENV.get('SMTP_USER', '')
SMTP_PASS                  = _ENV.get('SMTP_PASS', '')
SMTP_FROM                  = _ENV.get('SMTP_FROM', '')
NOTIFY_EMAIL               = _ENV.get('NOTIFY_EMAIL', '')
HQ_ADMIN_EMAIL             = _ENV.get('HQ_ADMIN_EMAIL', '')
HQ_ADMIN_PASS              = _ENV.get('HQ_ADMIN_PASS', '')
HQ_PARTNER_EMAIL           = _ENV.get('HQ_PARTNER_EMAIL', '')
HQ_PARTNER_PASS            = _ENV.get('HQ_PARTNER_PASS', '')
STRIPE_SECRET_KEY          = _ENV.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_GROWTH        = _ENV.get('STRIPE_PRICE_GROWTH', '')   # price_xxx for $299/mo
STRIPE_PRICE_PRO           = _ENV.get('STRIPE_PRICE_PRO', '')      # price_xxx for $599/mo
STRIPE_WEBHOOK_SECRET      = _ENV.get('STRIPE_WEBHOOK_SECRET', '')
PLATFORM_URL               = _ENV.get('PLATFORM_URL', 'https://platform.clickpointconsulting.com.au')
HUBSPOT_TOKEN              = _ENV.get('HUBSPOT_TOKEN', '')  # Set via Railway env var
CANVA_CLIENT_ID            = _ENV.get('CANVA_CLIENT_ID', '')
CANVA_CLIENT_SECRET        = _ENV.get('CANVA_CLIENT_SECRET', '')
CANVA_REDIRECT_URI         = 'https://web-production-c959ce.up.railway.app/api/canva/callback'
GOOGLE_CLIENT_ID           = _ENV.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET       = _ENV.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI        = _ENV.get('PLATFORM_URL', 'https://platform.clickpointconsulting.com.au') + '/api/google/callback'
GOOGLE_ADS_DEVELOPER_TOKEN    = _ENV.get('GOOGLE_ADS_DEVELOPER_TOKEN', '')
GOOGLE_ADS_LOGIN_CUSTOMER_ID  = _ENV.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID', '').replace('-', '')
META_APP_ID                = _ENV.get('META_APP_ID', '')
META_APP_SECRET            = _ENV.get('META_APP_SECRET', '')
LINKEDIN_CLIENT_ID         = _ENV.get('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET     = _ENV.get('LINKEDIN_CLIENT_SECRET', '')
TWITTER_CLIENT_ID          = _ENV.get('TWITTER_CLIENT_ID', '')
TWITTER_CLIENT_SECRET      = _ENV.get('TWITTER_CLIENT_SECRET', '')

_REQUIRED_SECRETS = [
    ('ANTHROPIC_API_KEY',       'sk-ant-...'),
    ('SUPABASE_URL',            'https://xxxx.supabase.co'),
    ('SUPABASE_SERVICE_KEY',    'sbp_...'),
    ('HQ_ADMIN_EMAIL',          'admin@yourdomain.com'),
    ('HQ_ADMIN_PASS',           'strong-password-here'),
    ('HQ_PARTNER_EMAIL',        'partner@yourdomain.com'),
    ('HQ_PARTNER_PASS',         'strong-password-here'),
    ('INTEGRATION_ENCRYPTION_KEY', 'run: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'),
]
_missing = [k for k, _ in _REQUIRED_SECRETS if not _ENV.get(k)]
if _missing:
    print('\n🚨  Missing required environment variables — set these in Railway → Variables:')
    for k, example in _REQUIRED_SECRETS:
        if k in _missing:
            print(f'  {k}={example}')
    print()

# ── Agent system prompts ──────────────────────────────────────────────────────
AGENT_PROMPTS = {
    'sarah': """You are Sarah Lin, Chief Marketing Officer at ClickPoint Marketing Agency. You are strategic, decisive, collaborative, and highly experienced.

Your role: Provide strategic direction, make high-level decisions, review campaign performance, and delegate tasks to the right team members. You always think about ROI, client relationships, and team alignment.

Your team:
- Derek Wu (Paid Search) — Google Ads, Microsoft Ads, Smart Bidding, ROAS optimisation, keyword research (CPC/volume/competition), campaign performance analysis, ad campaign best practices across Google/Meta/LinkedIn/TikTok
- Zara Osei (Creative/Design) — banner design, brand assets, visual identity, ad creative, design critique, design systems, developer handoff specs, WCAG accessibility review, UX copy, user research synthesis, brand discovery (Notion/Confluence/Drive/Figma/Gong/Slack), brand guideline generation, brand voice review
- Jess Park (Content/SEO) — blog posts, ad copy, keyword strategy, content briefs, topic plans, content strategy, keyword clustering, SEO content creation, content localisation, editorial planning, brand voice enforcement on all content
- Cleo Chan (Social Media) — Meta Ads, TikTok, Instagram, LinkedIn campaigns, paid social performance analysis, platform-native best practices
- Raj Nair (SEO/Analytics) — technical SEO, on-page SEO, SEO audits, schema markup, internal linking, keyword clustering, broken links, AI visibility (ChatGPT/Claude/Gemini/Perplexity), international SEO, GA4, Search Console (per-article impressions/clicks), UTM attribution, rank tracking for 20 priority keywords via Ahrefs/Semrush/GSC
- Emma Ross (Email Marketing) — email campaign strategy, copywriting (subject lines, body copy, CTAs), Klaviyo/Mailchimp/ActiveCampaign/HubSpot/Brevo flows and automations, segmentation, deliverability, welcome sequences, nurture drips, promotional campaigns, re-engagement flows

Current clients include: Apex Dynamics, Orbital Labs, Crestwave Foods, DataForge AI, Helix Biomedical, Luminary Health, Cobalt Security, Meridian Retail, Northfield Group, Vanta Studios, SkyBridge Capital.

When a task is outside your direct expertise, clearly name which team member should handle it and why. Be concise, confident, and action-oriented. Never be vague — always give a clear next step.""",

    'jess': """You are Jess Park, Director of Content & SEO at ClickPoint Marketing Agency. You are creative, precise, and obsessed with search intent and conversion.

Your specialties:
- Writing high-converting ad copy, headlines, and CTAs
- Creating detailed content briefs for blog posts, landing pages, and email campaigns
- Keyword research, topic clusters, and content gap analysis
- SEO-optimised long-form content and pillar pages
- Editorial calendar planning
- UX copy — writing and reviewing interface microcopy for landing pages, forms, and CTAs: button labels, subheadings, form field placeholders, error messages, confirmation copy, and value propositions. Copy must be clear, benefit-led, and conversion-focused
- Brand voice enforcement — before finalising any piece of content, apply the client's brand guidelines as a quality check. Verify tone, vocabulary, messaging alignment, and persona fit. Flag any off-brand language with a specific rewrite. If no brand guidelines exist yet, flag this to Zara to run brand discovery and generate them first.

Content SEO skills you apply to every client engagement:
- Content brief — generate a detailed brief for any article or page: target keyword, secondary keywords, search intent, recommended title tag and H1, outline with H2/H3 structure, word count, internal links to include, competitor articles to outperform, and UTM-tagged CTA. Always produce the full brief, ready for a writer to execute.
- Content strategy — develop a full organic content strategy: audience personas, keyword universe, topic clusters, pillar/spoke architecture, content calendar, publishing cadence, and success metrics. Tie every piece of content to a business goal.
- Create content — write complete, publish-ready SEO content for any topic or keyword. Optimise for search intent, include target and secondary keywords naturally, use proper heading structure, add internal links, and include a UTM-tagged CTA. Don't summarise or outline — write the full piece.
- Create topic plan — research and produce a complete topic plan for a given subject: primary keyword, related keyword mapping, recommended content angle, target audience segment, competitive positioning (what to do differently from ranking competitors), suggested title, and estimated search volume/difficulty.
- Keyword clustering — group keyword lists into topical content clusters. Map each cluster to an existing or new page, identify the primary keyword and supporting keywords per page, and flag any cannibalisation risks.
- Content translation / localisation — adapt existing content for international audiences. Adjust idioms, cultural references, and locale-specific examples while preserving SEO keyword intent for the target market. Coordinate hreflang requirements with Raj.

Tracking & attribution standard you always follow:
- Every CTA in a blog post — buttons, inline links, form anchors — must include UTM parameters so GA4 can attribute consultation bookings and enquiries back to the specific article. Standard format: utm_source=blog&utm_medium=cta&utm_campaign=[campaign-name]&utm_content=[article-slug]. Include the UTM-tagged CTA URL in every content brief and every piece of content you write.
- When writing content briefs, include a "Tracking" section that specifies the exact UTM-tagged CTA URL for that article.

Key principle: When asked to write something, ACTUALLY WRITE IT — complete, polished output ready to use. Don't just give advice or frameworks. If asked for ad copy, write the full ads. If asked for a blog brief, write the full brief with title, keywords, outline, word count, and UTM-tagged CTA.

Be direct, creative, and specific. Always consider the target audience's search intent.""",

    'derek': """You are Derek Wu, Paid Search Specialist at ClickPoint Marketing Agency. You are data-driven, technically precise, and results-focused.

Your specialties:
- Google Ads campaign structure (Search, Shopping, Display, Performance Max, Demand Gen)
- Microsoft Advertising campaigns
- Smart Bidding strategies (tROAS, tCPA, Maximise Conversions, Enhanced CPC)
- Keyword research and match type strategy for paid search
- Ad copy variants and A/B testing frameworks
- Budget allocation, bid adjustments, and ROAS optimisation
- Negative keyword management and search term analysis

Paid ads skills you apply to every client engagement:
- Keyword research — research Google Ads keywords with real CPC estimates, search volume ranges, and competition levels. For any keyword list: provide match type recommendations (Broad, Phrase, Exact), flag high-intent vs. research-intent terms, identify negative keyword candidates, and group into ad group themes. Always produce the actual keyword list with annotations, not just methodology.
- Campaign performance analysis — analyse Google Ads performance across all campaign types. Identify: top/bottom performing campaigns, ad groups, and keywords; ROAS trends; Quality Score issues; wasted spend (high-spend/low-conversion terms); bid strategy performance; and impression share lost to budget vs. rank. Produce a prioritised action list with specific changes to make.
- Ad campaign best practices — apply platform-specific best practices for Google Ads (Search, Shopping, PMax, Display, Demand Gen), Meta Ads (Advantage+, ASC, retargeting), LinkedIn Ads (Sponsored Content, Lead Gen Forms, Message Ads), and TikTok Ads (Spark Ads, TopView, In-Feed, Search). For each platform: correct campaign structure, audience strategy, bidding approach, creative specs, and common mistakes to avoid. When asked for best practices, give specific, platform-native recommendations — not generic advice.

For Google Ads specifically, you always check:
- Conversion tracking is firing correctly before touching bids
- Search terms report weekly for new negatives
- Asset performance labels (Best/Good/Low) to retire underperforming headlines/descriptions
- Auction insights to benchmark against competitors
- Budget utilisation — are campaigns limited by budget or by demand?

When asked for campaign structure, ad copy, or strategy — provide actual, specific output: real ad headlines, keyword lists, bid recommendations, campaign settings. Be precise and technical but explain the reasoning. When asked for a performance analysis — produce the actual findings with numbers, not a framework for how to analyse.""",

    'raj': """You are Raj Nair, SEO & Analytics Specialist at ClickPoint Marketing Agency. You are analytical, thorough, and evidence-based.

Your specialties:
- Technical SEO audits (Core Web Vitals, crawlability, indexing, structured data, canonical tags, hreflang)
- Keyword research and competitor gap analysis
- Google Analytics 4 setup, event tracking, and conversion analysis
- Google Search Console analysis and CTR optimisation — including tracking keyword impressions and clicks at the individual article/page level to identify which content drives the most organic visibility
- Monthly analytics reports with actionable insights
- Identifying quick-win SEO opportunities
- UTM parameter strategy and campaign tracking setup — all CTAs in blog posts and content must include UTM parameters (utm_source, utm_medium, utm_campaign, utm_content) so consultation bookings and conversions can be attributed back to the specific article or content piece that drove them
- Rank tracking for the client's 20 priority keywords using Ahrefs, Semrush, and/or Google Search Console — monitoring weekly position changes, flagging drops, and identifying pages with ranking potential that need content improvement

SEO skills you apply to every client engagement:
- SEO audit — run a comprehensive audit covering technical health, on-page factors, content quality, backlink profile, and Core Web Vitals. Produce a prioritised issues list with severity ratings and recommended fixes.
- Technical SEO — deep technical audits: crawl budget, JavaScript rendering, log file analysis, hreflang, XML sitemaps, robots.txt, duplicate content, canonical issues, site speed, and mobile usability. Always produce specific fixes, not just observations.
- On-page SEO — optimise individual pages: title tags, meta descriptions, H1/H2 structure, keyword placement, internal links, image alt text, and page speed. Provide the exact rewritten elements, not just guidelines.
- Schema markup / structured data — generate JSON-LD structured data for any page type (Article, LocalBusiness, Product, FAQ, BreadcrumbList, Service, HowTo, etc.). Always output the complete, valid JSON-LD block ready to paste into the page.
- Internal linking — audit and improve internal link architecture: identify orphan pages, suggest contextual link placements between related articles, build hub-and-spoke link structures, and ensure priority pages receive the most internal link equity.
- Keyword clustering — organise keyword lists into topical clusters mapped to specific pages. Group by search intent (informational, navigational, commercial, transactional), identify primary vs. secondary keywords per page, and flag keyword cannibalisation.
- Broken links — find and fix broken internal and external links. Identify 404 pages, redirect chains, and missing canonical targets. Provide the corrected URL or recommended 301 redirect for each.
- AI visibility — analyse and improve how the client's brand, products, and expertise appear in AI-generated responses (ChatGPT, Claude, Gemini, Perplexity). Identify what topics/questions the brand should be associated with, what content needs to exist to influence AI answers, and how to structure content so AI models cite or reference the brand. This is a growing priority alongside traditional SEO.
- Content translation / international SEO — advise on hreflang implementation, subdomain vs. subdirectory structure for international sites, and coordinate with content on locale-specific keyword targeting.
- SEO check — quick spot-check of any page or file for immediate SEO issues: missing tags, thin content, duplicate titles, unoptimised images, missing schema.

Key tracking responsibilities you own:
1. Google Search Console — per-article keyword impressions, clicks, average position, and CTR. Alert when high-impression articles have low CTR (title/meta fix opportunity).
2. UTM attribution — ensure every CTA button, link, or form in blog posts carries UTM tags so GA4 can show exactly which articles drive consultation bookings or enquiries.
3. Priority keyword rank tracking — maintain a live rank-tracking view for the top 20 target keywords across Ahrefs/Semrush/GSC. Report on movers and shakers weekly.

When asked for analysis or recommendations — provide specific data points, prioritised action lists, and measurable targets. Don't just describe methodology; give actual insights and next steps. When asked to produce schema markup, an audit report, or a keyword cluster — produce the actual deliverable, not a description of how to do it.""",

    'zara': """You are Zara Osei, Creative Director at ClickPoint Marketing Agency. You are visual, decisive, and brand-obsessed.

Your specialties:
- Display banner creative direction (sizes, messaging hierarchy, visual layout)
- Brand identity guidelines and style systems
- Ad creative strategy for Google Display, Meta, and TikTok
- Creative briefs for photographers, videographers, and freelance designers
- Design feedback, revision direction, and quality control
- Colour palette, typography, and visual tone-of-voice

Design skills you apply to every client engagement:
- Design critique — structured feedback on usability, visual hierarchy, and brand consistency. When reviewing a design, always cover: hierarchy, contrast, spacing, typography, CTA clarity, and brand alignment. Be specific and actionable, not vague.
- Design system — audit, document, and extend client design systems. Define component rules, spacing scales, colour tokens, and typography styles. Ensure consistency across all touchpoints.
- Developer handoff — generate precise handoff specs: exact px values, hex colours, font sizes/weights, spacing, component states, and responsive breakpoints. Developers should be able to build from your specs without guessing.
- Accessibility review (WCAG 2.1 AA) — check colour contrast ratios, tap target sizes, focus states, alt text requirements, and reading order. Flag any AA failures and provide the fix.
- User research synthesis — translate research findings (interviews, usability tests, heatmaps) into clear design insights, prioritised problem statements, and recommended design changes.
- UX copy — write and review all interface copy: button labels, error messages, empty states, onboarding tooltips, and microcopy. Copy must be clear, action-oriented, and brand-consistent.

Brand voice skills you own:
- Brand discovery — autonomously discover and audit a client's brand materials across any platform they use: Notion, Confluence, Google Drive, Box, SharePoint, Figma, Gong call recordings, Granola meeting notes, Slack. Surface brand-relevant documents (style guides, tone of voice docs, brand decks, sales call transcripts, design files) and produce a triage report ranking materials by relevance. This is always the first step before generating guidelines.
- Brand guideline generation — synthesise discovered materials (documents, design files, sales call transcripts, existing style guides) into a structured brand voice guideline document covering: brand personality, tone of voice, writing style, vocabulary (words to use / avoid), messaging pillars, audience personas, and example copy in-voice and out-of-voice. Produce the full guideline document, not a summary.
- Brand voice enforcement / review — apply a client's brand guidelines as a quality gate on any piece of content. Check for: tone consistency, vocabulary compliance, messaging alignment, persona fit, and off-brand language. Return a pass/fail verdict with specific line-level callouts and suggested rewrites for any failures.

When asked for creative direction — be specific: name exact colours (hex if possible), font weights, layout hierarchy, and visual style references. Don't be vague. If asked for a creative brief, write the full brief with all specs. If asked for a design critique, accessibility review, handoff spec, or brand guideline document — produce the actual output, not a description of how to do it.""",

    'cleo': """You are Cleo Chan, Social Media Specialist at ClickPoint Marketing Agency. You are creative, trend-aware, and platform-native.

Your specialties:
- Meta Ads (Advantage+, ASC, retargeting, lookalike audiences)
- TikTok Ads (Spark Ads, TopView, In-Feed, Search)
- Instagram and LinkedIn organic and paid strategy
- Social media content calendars and posting schedules
- Community management and engagement tactics
- Influencer briefing and creator campaign management
- Social copy writing for organic posts and paid ads

Paid social skills you apply to every client engagement:
- Campaign performance analysis (Meta/TikTok/LinkedIn) — analyse paid social performance across platforms. For Meta: CPM trends, frequency, audience fatigue, creative performance by hook/format, Advantage+ vs. manual campaign comparison, ROAS by placement. For TikTok: video completion rate, CTR, Spark Ads vs. In-Feed performance, top/bottom creatives. For LinkedIn: CPL by audience segment, Lead Gen Form completion rate, Message Ad open/response rate. Always produce specific findings with numbers and a prioritised action list.
- Ad campaign best practices (Meta/TikTok/LinkedIn) — apply platform-native best practices for campaign structure, audience targeting, creative strategy, bidding, and testing. Be specific: which objective to use, how to structure ad sets, recommended audience sizes, creative refresh cadence, and what to test first.

When asked for social strategy or copy — write actual post captions, ad headlines, campaign structures, or content calendar entries. Be platform-specific and audience-aware. Write complete, ready-to-publish copy. When asked for a performance analysis — produce the actual findings with metrics and specific next actions, not a framework.""",

    'emma': """You are Emma Ross, Email Marketing Specialist at ClickPoint Marketing Agency. You are strategic, copy-focused, and obsessed with deliverability and conversion.

Your specialties:
- Full email campaign strategy: welcome sequences, nurture flows, promotional blasts, re-engagement, win-back campaigns
- Platform expertise: Mailchimp, Klaviyo, ActiveCampaign, HubSpot, Brevo — flows, automations, segmentation, A/B testing
- Email copywriting: subject lines, preview text, body copy, CTAs — every element written for open rates and clicks
- List segmentation, audience building, and personalisation tokens
- Deliverability best practices: sender reputation, SPF/DKIM, list hygiene, send time optimisation
- Automation sequences: welcome series, post-purchase, abandoned cart, lead nurture, onboarding drips
- Performance analysis: open rate, CTR, unsubscribe rate, revenue per email, list growth rate

Key principle: When asked for email copy or a campaign — ACTUALLY WRITE IT. Full subject lines, preview text, and body copy ready to paste into any email platform. Not outlines, not frameworks — real, send-ready emails.

If no email platform is connected yet, produce the copy anyway and clearly mark it as ready to load into [Mailchimp / Klaviyo / ActiveCampaign] once connected. The copy is the asset — the platform is just the delivery mechanism.""",

    'task_extractor': """You are a task extraction system for ClickPoint Marketing Agency.

Given a marketing manager's message, extract EVERY action item, task, or responsibility mentioned — even loosely — for any team member.

Return ONLY a raw JSON array with no markdown fences, no explanation, no extra text. Just the array.

Example output:
[{"agent":"derek","action":"Build Google Ads campaign structure","client":"Nova Fintech","type":"info"},{"agent":"raj","action":"Run SEO audit and confirm tracking","client":"Nova Fintech","type":"info"}]

Name mapping (use these exact agent keys):
- Derek / Derek Wu → "derek"
- Jess / Jess Park → "jess"
- Raj / Raj Nair → "raj"
- Zara / Zara Osei → "zara"
- Cleo / Cleo Chan → "cleo"
- Sarah / Sarah Lin / Me → "sarah"

Rules:
- Extract ANY mention of a person doing something, even implied
- "action" must be under 10 words, action-oriented
- "client" = the client mentioned in context, or "General"
- "type" = "info" for new tasks, "success" for completed work, "warn" for blockers
- If truly nothing found, return: []
- Output ONLY the JSON array — no prose, no markdown code fences""",

    'memory_extractor': """You are a memory extraction system for a marketing agency AI team.

Given a conversation between a user and a marketing agent, extract 1-3 facts, preferences, or insights worth remembering for future conversations with this agent and client.

Focus on:
- Client preferences or constraints ("Client insists on brand-safe placements only")
- Strategies recommended or agreed upon ("Set tROAS target at 4.2x for Q4")
- Key business facts ("Peak season runs September through November")
- Outcomes from past work ("Google Ads restructure improved CTR from 1.8% to 2.6%")
- Blockers or sensitivities ("CFO reviews all spend over $10K — needs sign-off")

Skip: greetings, generic advice, hypotheticals, anything not specific or actionable.

Return ONLY a raw JSON array, no markdown fences, no explanation:
[{"content":"...","memory_type":"preference","importance":4}]

memory_type: "preference" | "strategy" | "insight" | "outcome"
importance: 1-5  (5 = critical, 1 = nice to know)
If nothing worth saving: []""",
}

PORT = int(os.environ.get('PORT', 3001))

# ── Agent profile store (structured data alongside prompts) ───────────────────
AGENT_PROFILES = {
    'sarah': {'name':'Sarah Lin',  'role':'Chief Marketing Officer',   'skills':['Campaign Strategy','Client Relations','Team Leadership','ROI Planning','Budget Allocation','Stakeholder Comms']},
    'jess':  {'name':'Jess Park',  'role':'Director of Content & SEO', 'skills':['Ad Copywriting','SEO Content','Keyword Research','Content Briefs','Topic Clusters','Editorial Planning','Landing Pages']},
    'derek': {'name':'Derek Wu',   'role':'Paid Search Specialist',    'skills':['Google Ads','Microsoft Ads','Smart Bidding','ROAS Optimisation','Search / Shopping / PMax','Negative Keywords','A/B Testing']},
    'raj':   {'name':'Raj Nair',   'role':'SEO & Analytics Specialist','skills':['Technical SEO','Core Web Vitals','GA4 Setup','Search Console','Crawl & Indexing','UTM Strategy','Monthly Reporting']},
    'zara':  {'name':'Zara Osei',  'role':'Creative Director',         'skills':['Display Banners','Brand Identity','Creative Briefs','Ad Creative','Meta & TikTok Visual','Typography','Design QA']},
    'cleo':  {'name':'Cleo Chan',  'role':'Social Media Specialist',   'skills':['Meta Ads','TikTok Ads','LinkedIn Strategy','Spark Ads','Lookalike Audiences','Community Management','Influencer Briefs']},
    'emma':  {'name':'Emma Ross',  'role':'Email Marketing Specialist','skills':['Email Sequences','Klaviyo','Mailchimp','Deliverability','Subject Lines','A/B Testing','Drip Campaigns','List Segmentation']},
}
# Internal-only agents (not exposed to UI agent selector)
_INTERNAL_AGENTS = {'task_extractor'}

def build_agent_prompt(name: str, role: str, skills: list, extra: str = '') -> str:
    """Generate a Claude system prompt from structured agent profile data."""
    skills_text = '\n'.join(f'- {s}' for s in (skills or []))
    prompt = (
        f'You are {name}, {role} at ClickPoint Marketing Agency. '
        f'You are expert, precise, and results-focused.\n\n'
        f'Your specialties:\n{skills_text}\n\n'
        f'When asked for analysis, copy, plans, or strategy — provide actual, specific output '
        f'ready to use. Be direct and actionable. Never be vague.'
    )
    if extra:
        prompt += f'\n\nAdditional context:\n{extra}'
    return prompt

def _auto_migrate():
    """Ensure all required Supabase tables exist. Runs at startup — safe to call repeatedly."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return

    # Tables we need: check each with a HEAD request and create via SQL RPC if missing
    # Supabase doesn't expose raw SQL via REST; we use the Supabase Management API
    # (project ref extracted from SUPABASE_URL)
    import re as _re
    m = _re.match(r'https://([^.]+)\.supabase\.co', SUPABASE_URL)
    if not m:
        return
    project_ref = m.group(1)

    # Check which tables are missing by trying a lightweight GET
    tables_needed = {
        'client_integrations': (
            "id bigint generated always as identity primary key,"
            "client text not null,platform text not null,account_id text default '',"
            "status text default 'connected',encrypted_token text default '',"
            "last_synced timestamptz default now(),created_at timestamptz default now()"
        ),
        'client_metrics': (
            "id bigint generated always as identity primary key,"
            "client text not null,platform text not null,days integer default 30,"
            "metrics jsonb default '{}',fetched_at timestamptz default now()"
        ),
        'agents': (
            "id bigint generated always as identity primary key,"
            "key text unique not null,name text default '',role text default '',"
            "skills jsonb default '[]',system_prompt text default '',"
            "extra_context text default '',active boolean default true,"
            "created_at timestamptz default now()"
        ),
        'workspace_activity': (
            "id bigint generated always as identity primary key,"
            "workspace_id text not null,company_name text default '',"
            "type text not null,detail text default '',"
            "timestamp timestamptz default now()"
        ),
        'crm_contacts': (
            "id bigserial primary key,"
            "workspace_id text not null,name text not null,email text,phone text,"
            "company text,title text,tags text[],notes text,"
            "deal_stage text default 'prospect',"
            "deal_value numeric,ai_score int,next_action text,"
            "last_contact timestamptz,created_at timestamptz default now()"
        ),
        'crm_activities': (
            "id bigserial primary key,"
            "workspace_id text not null,contact_id bigint,"
            "type text,summary text,created_at timestamptz default now()"
        ),
        'reputation_reviews': (
            "id bigserial primary key,"
            "workspace_id text not null,platform text not null,"
            "reviewer_name text,rating int,content text,review_date text,"
            "response text,status text default 'pending',external_id text,"
            "created_at timestamptz default now()"
        ),
        'local_listings': (
            "id bigserial primary key,"
            "workspace_id text not null,business_name text,address text,"
            "city text,state text,postcode text,country text default 'AU',"
            "phone text,website text,categories text[],description text,"
            "hours jsonb default '{}',google_place_id text,"
            "listing_status jsonb default '{}',last_audit text,"
            "updated_at timestamptz default now()"
        ),
        'social_accounts': (
            "id bigserial primary key,"
            "workspace_id text not null,platform text not null,"
            "account_name text,account_id text,page_id text,"
            "encrypted_token text,token_type text default 'page',"
            "expires_at timestamptz,status text default 'connected',"
            "created_at timestamptz default now()"
        ),
        'social_posts': (
            "id bigserial primary key,"
            "workspace_id text not null,platforms text[] not null,"
            "content text not null,media_urls text[],"
            "scheduled_at timestamptz,published_at timestamptz,"
            "status text default 'draft',"
            "platform_ids jsonb default '{}',error text,"
            "created_by text,created_at timestamptz default now()"
        ),
        # ── Auth / access tables ─────────────────────────────────────────────
        'workspace_access': (
            "id bigserial primary key,"
            "workspace_id text not null,company_name text not null,"
            "contact_name text default '',email text not null,"
            "access_code text not null,partner_id text default null,"
            "plan text default 'starter',active boolean default true,"
            "last_login timestamptz,created_at timestamptz default now()"
        ),
        'partner_accounts': (
            "id bigserial primary key,"
            "partner_id text unique not null,name text not null,"
            "agency_name text default '',email text unique not null,"
            "password_hash text not null,website text default '',"
            "commission_rate numeric default 0.20,"
            "active boolean default true,created_at timestamptz default now()"
        ),
        'partner_reset_tokens': (
            "id bigserial primary key,"
            "email text not null,token text not null,"
            "expires_at timestamptz not null,"
            "used boolean default false,created_at timestamptz default now()"
        ),
        'portal_access': (
            "id bigserial primary key,"
            "client text not null,email text not null,"
            "access_code text not null,workspace_id text default null,"
            "active boolean default true,last_login timestamptz,"
            "created_at timestamptz default now()"
        ),
        'agent_memories': (
            "id bigserial primary key,"
            "agent_key text not null,client text not null default 'General',"
            "memory text not null,importance int default 5,"
            "created_at timestamptz default now()"
        ),
        'client_reports': (
            "id bigserial primary key,"
            "client text not null,workspace_id text,"
            "period text not null,health_score numeric,health_label text,"
            "report_data jsonb default '{}',"
            "generated_at timestamptz default now(),"
            "status text default 'draft'"
        ),
        'integration_credentials': (
            "id bigserial primary key,"
            "integration_id text not null unique,"
            "workspace_id text,platform text not null,"
            "encrypted_token text not null,token_type text default 'oauth',"
            "expires_at timestamptz,created_at timestamptz default now()"
        ),
        'platform_settings': (
            "key text primary key,value text not null,"
            "updated_at timestamptz default now()"
        ),
        'hq_messages': (
            "id bigserial primary key,"
            "thread_id text not null default gen_random_uuid()::text,"
            "from_role text not null,from_email text not null,"
            "partner_id text,subject text default '',"
            "body text not null,read boolean default false,"
            "created_at timestamptz default now()"
        ),
    }

    hdrs = {'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

    for table, _ in tables_needed.items():
        try:
            req = urllib.request.Request(
                f'{SUPABASE_URL}/rest/v1/{table}?limit=1',
                headers=hdrs
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    continue   # table exists
        except urllib.error.HTTPError as e:
            if e.code != 404:
                continue
        except Exception:
            continue

        # Table missing — create it via Supabase Management API SQL endpoint
        sql = f'CREATE TABLE IF NOT EXISTS {table} ({tables_needed[table]});'
        try:
            mgmt_req = urllib.request.Request(
                f'https://api.supabase.com/v1/projects/{project_ref}/database/query',
                data=json.dumps({'query': sql}).encode(),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                },
                method='POST'
            )
            with urllib.request.urlopen(mgmt_req, timeout=15) as r:
                print(f'  ✅ Auto-migrated: created table {table}')
        except Exception as ex:
            print(f'  ⚠️  Auto-migrate {table}: {ex} — create manually via Supabase SQL editor')

def load_db_agents():
    """Load agent profiles from Supabase and merge into AGENT_PROMPTS + AGENT_PROFILES."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        rows = _supabase_req(
            'GET',
            'agents?select=key,name,role,skills,system_prompt,extra_context&active=eq.true',
        )
        for row in (rows or []):
            key = (row.get('key') or '').strip()
            if not key:
                continue
            name   = row.get('name', '')
            role   = row.get('role', '')
            skills = row.get('skills') or []
            extra  = row.get('extra_context') or ''
            prompt = row.get('system_prompt') or build_agent_prompt(name, role, skills, extra)
            AGENT_PROMPTS[key]  = prompt
            AGENT_PROFILES[key] = {'name': name, 'role': role, 'skills': skills}
            print(f'  🤖 Agent loaded from DB: {key} ({name})')
    except Exception as e:
        print(f'  ⚠️  Could not load DB agents: {e}')

# ── Analytics helpers ────────────────────────────────────────────────────────

def _get_credential(client: str, platform: str):
    """Return (account_id, decrypted_token) for a client-platform pair, or (None,None)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None, None
    try:
        enc_client = urllib.parse.quote(client)
        rows = _supabase_req('GET',
            f'client_integrations?client=eq.{enc_client}&platform=eq.{platform}'
            f'&select=id,account_id&status=eq.connected')
        if not rows:
            return None, None
        iid        = rows[0]['id']
        account_id = rows[0].get('account_id', '')
        # Integration credentials are stored separately (may not exist for OAuth-only rows like google_ads)
        try:
            creds = _supabase_req('GET',
                f'integration_credentials?integration_id=eq.{iid}&select=encrypted_token')
            if not creds:
                return account_id, None
            token = decrypt_token(creds[0]['encrypted_token'])
            return account_id, token
        except Exception:
            # Table may not exist or no credential row — that's fine, account_id is still valid
            return account_id, None
    except Exception as e:
        print(f'  Credential lookup error: {e}')
        return None, None

def _cache_metrics(client: str, platform: str, days: int, data: dict):
    """Store fetched metrics in Supabase for 1-hour cache."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        # Delete old entry for same client/platform/days
        _supabase_req('DELETE',
            f'client_metrics?client=eq.{urllib.parse.quote(client)}'
            f'&platform=eq.{platform}&days=eq.{days}')
        _supabase_req('POST', 'client_metrics',
            {'client': client, 'platform': platform, 'days': days, 'metrics': data})
    except Exception as e:
        print(f'  Cache write error: {e}')

def _get_cached(client: str, platform: str, days: int):
    """Return cached metrics if fresher than 1 hour, else None."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).isoformat()
        rows = _supabase_req('GET',
            f'client_metrics?client=eq.{urllib.parse.quote(client)}'
            f'&platform=eq.{platform}&days=eq.{days}'
            f'&fetched_at=gte.{cutoff}&order=fetched_at.desc&limit=1')
        return rows[0]['metrics'] if rows else None
    except Exception:
        return None

# ── Demo data generators (deterministic per client name) ─────────────────────
def _seed(client: str, platform: str) -> int:
    return sum(ord(c) * (i+1) for i, c in enumerate(client + platform)) % 1000

def _demo_google_ads(client: str, budget: float, days: int) -> dict:
    s = _seed(client, 'ga')
    spend   = round(budget * (0.38 + s * 0.0003) * (days / 30), 2)
    clicks  = int(spend / (0.72 + s * 0.001))
    impr    = int(clicks / (0.032 + s * 0.00004))
    conv    = int(clicks * (0.022 + s * 0.00003))
    cv      = round(conv * (65 + s * 0.08), 2)
    trend   = [round(spend * (0.78 + i * 0.04 + (s % 5) * 0.01), 2) for i in range(7)]
    return dict(platform='google_ads', is_demo=True, days=days,
                spend=spend, impressions=impr, clicks=clicks,
                ctr=round(clicks/max(1,impr)*100, 2),
                cpc=round(spend/max(1,clicks), 2),
                conversions=conv, conv_value=cv,
                roas=round(cv/max(0.01,spend), 2), trend=trend)

def _demo_meta(client: str, budget: float, days: int) -> dict:
    s = _seed(client, 'meta')
    spend   = round(budget * (0.22 + s * 0.0002) * (days / 30), 2)
    reach   = int(spend * (38 + s * 0.04))
    impr    = int(reach * 1.45)
    clicks  = int(spend / (1.18 + s * 0.002))
    cv      = round(clicks * (14 + s * 0.01), 2)
    trend   = [round(spend * (0.76 + i * 0.04 + (s % 4) * 0.01), 2) for i in range(7)]
    return dict(platform='meta_ads', is_demo=True, days=days,
                spend=spend, reach=reach, impressions=impr, clicks=clicks,
                ctr=round(clicks/max(1,impr)*100, 2),
                cpm=round(spend/max(1,impr)*1000, 2),
                roas=round(cv/max(0.01,spend), 2), conv_value=cv, trend=trend)

def _demo_ga4(client: str, budget: float, days: int) -> dict:
    s = _seed(client, 'ga4')
    sessions = int(budget * (1.8 + s * 0.002) * (days / 30))
    users    = int(sessions * 0.76)
    trend    = [int(sessions * (0.8 + i * 0.04 + (s % 6) * 0.008)) for i in range(7)]
    return dict(platform='ga4', is_demo=True, days=days,
                sessions=sessions, users=users,
                new_users=int(users*0.63),
                bounce_rate=round(38 + s * 0.012, 1),
                avg_session_duration=f"{2 + s%3}m {10 + s%50}s",
                conv_rate=round(2.4 + s * 0.002, 2),
                revenue=round(sessions * (0.028 + s * 0.00003) * 82, 2),
                trend=trend)

def _demo_search_console(client: str, budget: float, days: int) -> dict:
    s = _seed(client, 'sc')
    impr  = int(budget * (7 + s * 0.01) * (days / 30))
    clicks = int(impr * (0.018 + s * 0.00002))
    trend  = [int(impr * (0.82 + i * 0.03 + (s % 5) * 0.006)) for i in range(7)]
    return dict(platform='search_console', is_demo=True, days=days,
                impressions=impr, clicks=clicks,
                ctr=round(clicks/max(1,impr)*100, 2),
                avg_position=round(6.2 + s * 0.006, 1), trend=trend)

# ── Real platform API fetchers ────────────────────────────────────────────────
def _fetch_google_ads(account_id: str, token: str, days: int) -> dict:
    """Google Ads API v17 — aggregates campaign metrics."""
    clean_id = account_id.replace('-', '').strip()
    query = (
        'SELECT metrics.impressions, metrics.clicks, metrics.ctr, '
        'metrics.average_cpc, metrics.conversions, metrics.cost_micros, '
        'metrics.conversions_value FROM campaign '
        f'WHERE segments.date DURING LAST_{days}_DAYS '
        'AND campaign.status = ENABLED'
    )
    url  = f'https://googleads.googleapis.com/v21/customers/{clean_id}/googleAds:search'
    data = json.dumps({'query': query}).encode()
    req  = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.loads(resp.read()).get('results', [])
    t = dict(impressions=0, clicks=0, spend=0.0, conversions=0.0, cv=0.0)
    for row in rows:
        m = row.get('metrics', {})
        t['impressions']  += int(m.get('impressions', 0))
        t['clicks']       += int(m.get('clicks', 0))
        t['spend']        += int(m.get('costMicros', 0)) / 1_000_000
        t['conversions']  += float(m.get('conversions', 0))
        t['cv']           += float(m.get('conversionsValue', 0))
    sp, cl, im = t['spend'], t['clicks'], t['impressions']
    trend = [round(sp * (0.8 + i*0.04), 2) for i in range(7)]
    return dict(platform='google_ads', is_demo=False, days=days,
                spend=round(sp,2), impressions=im, clicks=cl,
                ctr=round(cl/max(1,im)*100,2), cpc=round(sp/max(1,cl),2),
                conversions=round(t['conversions'],1), conv_value=round(t['cv'],2),
                roas=round(t['cv']/max(0.01,sp),2), trend=trend)

# ── Google Ads geo target constants (AU) ─────────────────────────────────────
_AU_GEO_TARGETS = {
    'sydney':              1000073, 'melbourne':       1000695,
    'brisbane':            1000079, 'perth':           1000898,
    'adelaide':            1000018, 'canberra':        1000080,
    'gold coast':          1000348, 'newcastle':       1000802,
    'wollongong':          1001037, 'geelong':         1000319,
    'townsville':          1000988, 'cairns':          1000100,
    'darwin':              1000180, 'hobart':          1000416,
    'nsw':                 21496,   'new south wales':  21496,
    'victoria':            21503,   'vic':             21503,
    'qld':                 21479,   'queensland':      21479,
    'wa':                  21504,   'western australia': 21504,
    'sa':                  21500,   'south australia': 21500,
    'act':                 21497,   'tasmania':        21501,
    'tas':                 21501,   'nt':              21498,
    'northern territory':  21498,   'australia':       2036,
}

def _resolve_geo_targets(locations_str: str) -> list:
    """Parse a location string and return list of geoTargetConstant resource names."""
    if not locations_str:
        return ['geoTargetConstants/2036']  # Australia fallback
    parts = [p.strip().lower() for p in locations_str.replace(',', ';').replace('/', ';').split(';')]
    found = []
    for part in parts:
        for key, gid in _AU_GEO_TARGETS.items():
            if key in part and gid not in found:
                found.append(gid)
    return [f'geoTargetConstants/{g}' for g in found] if found else ['geoTargetConstants/2036']

def _ads_req(method: str, path: str, payload: dict, access_token: str, developer_token: str, login_customer_id: str = '') -> dict:
    """Make a Google Ads REST API v21 request."""
    import urllib.request as _ur
    import urllib.error  as _ue
    url  = f'https://googleads.googleapis.com/v21/{path.lstrip("/")}'
    data = json.dumps(payload).encode() if payload else None
    hdrs = {
        'Authorization':   f'Bearer {access_token}',
        'Content-Type':    'application/json',
        'developer-token': developer_token,
    }
    if login_customer_id:
        hdrs['login-customer-id'] = login_customer_id.replace('-', '')
    req = _ur.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _ur.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except _ue.HTTPError as e:
        # Read and surface the Google error body so callers can show a useful message
        try:
            body = e.read().decode('utf-8', errors='replace')
            err_json = json.loads(body)
            google_msg = (err_json.get('error', {}).get('message')
                          or err_json.get('error', {}).get('details', [{}])[0].get('errors', [{}])[0].get('message')
                          or body[:400])
        except Exception:
            google_msg = f'HTTP {e.code}: {e.reason}'
        print(f'  ❌ Google Ads API error [{method} {url}]: {google_msg}')
        raise RuntimeError(google_msg) from e

def _create_google_ads_campaign_live(
    workspace_id:       str,
    campaign_data:      dict,
    access_token:       str,
    customer_id:        str,
    developer_token:    str,
    login_customer_id:  str = '',
) -> tuple:
    """Create a complete Google Ads Search campaign via REST API v17.

    Returns (resource_name, None) on success or (None, error_message) on failure.
    Campaign is created PAUSED — the client activates it after review.

    campaign_data keys used:
      name, budget (monthly AUD), url (final URL), bid_strategy,
      target_cpa (float opt), headlines (list), descriptions (list),
      usps (list), services (multiline str), locations (str),
      biz_name, offer, competitors
    """
    if not access_token or not customer_id or not developer_token:
        return None, 'Missing access_token, customer_id, or developer_token'

    cid  = customer_id.replace('-', '').strip()
    lcid = login_customer_id.replace('-', '').strip() if login_customer_id else ''

    # Convenience wrapper so every call includes MCC login header
    def _req(method, path, payload):
        return _ads_req(method, path, payload, access_token, developer_token, lcid)

    try:
        # ── 1. Campaign Budget ────────────────────────────────────────────────
        monthly = float(campaign_data.get('budget') or 30)
        daily_micros = int((monthly / 30.44) * 1_000_000)

        bud_resp = _req('POST', f'customers/{cid}/campaignBudgets:mutate', {
            'operations': [{'create': {
                'name':           f"Budget for {campaign_data['name']}",
                'amountMicros':   str(daily_micros),
                'deliveryMethod': 'STANDARD',
            }}]
        })
        budget_rn = bud_resp['results'][0]['resourceName']
        print(f'  💰 Google Ads budget created: {budget_rn}')

        # ── 2. Bid strategy config ────────────────────────────────────────────
        strat = campaign_data.get('bid_strategy', 'recommend')
        target_cpa_val = campaign_data.get('target_cpa', '')
        if strat == 'target_cpa' and target_cpa_val:
            bidding = {'targetCpa': {'targetCpaMicros': str(int(float(target_cpa_val) * 1_000_000))}}
        elif strat == 'max_clicks':
            bidding = {'maximizeClicks': {}}
        elif strat == 'manual_cpc':
            bidding = {'manualCpc': {'enhancedCpcEnabled': True}}
        else:
            bidding = {'maximizeConversions': {}}

        # ── 3. Campaign ───────────────────────────────────────────────────────
        geo_targets = _resolve_geo_targets(campaign_data.get('locations', ''))
        cmp_create = {
            'name':                    campaign_data['name'],
            'advertisingChannelType':  'SEARCH',
            'status':                  'PAUSED',
            'campaignBudget':          budget_rn,
            'networkSettings': {
                'targetGoogleSearch':   True,
                'targetSearchNetwork':  True,
                'targetContentNetwork': False,
            },
            'geoTargetTypeSetting': {
                'positiveGeoTargetType': 'PRESENCE_OR_INTEREST',
            },
        }
        cmp_create.update(bidding)
        cmp_resp = _req('POST', f'customers/{cid}/campaigns:mutate', {
            'operations': [{'create': cmp_create}]
        })
        campaign_rn = cmp_resp['results'][0]['resourceName']
        print(f'  📋 Google Ads campaign created: {campaign_rn}')

        # ── 4. Geo targeting criteria ─────────────────────────────────────────
        geo_ops = [{'create': {'campaign': campaign_rn,
                               'location': {'geoTargetConstant': g}}}
                   for g in geo_targets]
        if geo_ops:
            _req('POST', f'customers/{cid}/campaignCriteria:mutate', {'operations': geo_ops})

        # ── 5. Ad Group ───────────────────────────────────────────────────────
        services_raw  = campaign_data.get('services', '') or campaign_data['name']
        service_lines = [l.strip() for l in services_raw.splitlines() if l.strip()]
        ad_group_name = service_lines[0] if service_lines else campaign_data['name']

        ag_resp = _req('POST', f'customers/{cid}/adGroups:mutate', {
            'operations': [{'create': {
                'name':           ad_group_name,
                'campaign':       campaign_rn,
                'status':         'ENABLED',
                'type':           'SEARCH_STANDARD',
                'cpcBidMicros':   '2000000',   # $2 default CPC
            }}]
        })
        ag_rn = ag_resp['results'][0]['resourceName']
        print(f'  📁 Google Ads ad group created: {ag_rn}')

        # ── 6. Keywords ───────────────────────────────────────────────────────
        usps     = campaign_data.get('usps', [])
        kw_texts = list({s.lower() for s in service_lines if s})
        for u in (usps or []):
            if u and len(u) <= 80:
                kw_texts.append(u.lower())
        kw_texts = list(dict.fromkeys(kw_texts))[:20]
        if kw_texts:
            kw_ops = [{'create': {
                'adGroup':  ag_rn,
                'status':   'ENABLED',
                'keyword':  {'text': kw, 'matchType': 'PHRASE'},
            }} for kw in kw_texts]
            _req('POST', f'customers/{cid}/adGroupCriteria:mutate', {'operations': kw_ops})
            print(f'  🔑 {len(kw_ops)} keywords added')

        # ── 7. Negative keywords (competitors) ───────────────────────────────
        competitors_str = campaign_data.get('competitors', '')
        if competitors_str:
            neg_texts = [c.strip().lower() for c in competitors_str.replace(',', ';').split(';') if c.strip()][:10]
            if neg_texts:
                neg_ops = [{'create': {
                    'adGroup':  ag_rn,
                    'status':   'ENABLED',
                    'keyword':  {'text': n, 'matchType': 'BROAD'},
                    'negative': True,
                }} for n in neg_texts]
                _req('POST', f'customers/{cid}/adGroupCriteria:mutate', {'operations': neg_ops})

        # ── 8. Responsive Search Ad ───────────────────────────────────────────
        final_url = campaign_data.get('url', '')
        if not final_url:
            return campaign_rn, None  # Campaign built without RSA — URL required

        raw_headlines = list(campaign_data.get('headlines', []))
        raw_descs     = list(campaign_data.get('descriptions', []))
        for u in (usps or []):
            if u and u not in raw_headlines:
                raw_headlines.append(u)
        if len(raw_headlines) < 3:
            raw_headlines += [campaign_data['name'], ad_group_name,
                              campaign_data.get('offer', '') or 'Contact Us Today']
        if len(raw_descs) < 2:
            biz = campaign_data.get('biz_name', '') or campaign_data.get('company_name', 'Us')
            raw_descs.append(f'Expert {ad_group_name} from {biz}. Get in touch today.')
            raw_descs.append(f'Trusted by businesses across Australia. {campaign_data.get("offer","") or "Book a free consultation"}.')

        headlines_payload = [{'text': h[:30]} for h in raw_headlines if h][:15]
        descs_payload     = [{'text': d[:90]} for d in raw_descs if d][:4]

        _req('POST', f'customers/{cid}/adGroupAds:mutate', {
            'operations': [{'create': {
                'adGroup': ag_rn,
                'status':  'ENABLED',
                'ad': {
                    'finalUrls': [final_url],
                    'responsiveSearchAd': {
                        'headlines':    headlines_payload,
                        'descriptions': descs_payload,
                    }
                }
            }}]
        })
        print(f'  📢 RSA created with {len(headlines_payload)} headlines')

        return campaign_rn, None

    except Exception as e:
        err_str = str(e)
        # Try to extract Google error detail from response body
        try:
            import re as _re
            body_match = _re.search(r'\{.*\}', err_str, _re.DOTALL)
            if body_match:
                err_json = json.loads(body_match.group())
                google_err = err_json.get('error', {}).get('message', err_str)
                err_str = google_err
        except Exception:
            pass
        print(f'  ❌ Google Ads build error: {err_str}')
        return None, err_str[:300]


def _fetch_meta(account_id: str, token: str, days: int) -> dict:
    """Meta Marketing API v19."""
    clean_id = account_id.replace('act_', '').strip()
    since    = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    until    = datetime.date.today().isoformat()
    params   = urllib.parse.urlencode({
        'access_token': token,
        'fields': 'spend,impressions,clicks,ctr,reach,cpm,action_values',
        'time_range': json.dumps({'since': since, 'until': until}),
        'level': 'account',
    })
    url = f'https://graph.facebook.com/v19.0/act_{clean_id}/insights?{params}'
    with urllib.request.urlopen(url, timeout=30) as resp:
        r = json.loads(resp.read()).get('data', [{}])[0]
    sp  = float(r.get('spend', 0))
    cv  = sum(float(a.get('value',0)) for a in r.get('action_values',[])
              if a.get('action_type') == 'purchase')
    im  = int(r.get('impressions', 0))
    cl  = int(r.get('clicks', 0))
    trend = [round(sp * (0.8 + i*0.04), 2) for i in range(7)]
    return dict(platform='meta_ads', is_demo=False, days=days,
                spend=round(sp,2), impressions=im,
                reach=int(r.get('reach',0)), clicks=cl,
                ctr=round(float(r.get('ctr',0)),2),
                cpm=round(float(r.get('cpm',0)),2),
                roas=round(cv/max(0.01,sp),2), conv_value=round(cv,2), trend=trend)

def _fetch_ga4(property_id: str, token: str, days: int) -> dict:
    """GA4 Data API — requires OAuth2 access token."""
    body = json.dumps({
        'dateRanges': [{'startDate': f'{days}daysAgo', 'endDate': 'today'}],
        'metrics': [
            {'name':'sessions'},{'name':'totalUsers'},{'name':'newUsers'},
            {'name':'bounceRate'},{'name':'averageSessionDuration'},
            {'name':'conversions'},{'name':'totalRevenue'},
        ],
    }).encode()
    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport'
    req = urllib.request.Request(url, data=body, headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        row = json.loads(resp.read()).get('rows', [{}])[0].get('metricValues', [])
    def v(i): return float(row[i]['value']) if i < len(row) else 0
    sessions = int(v(0)); users = int(v(1))
    dur = int(v(4))
    trend = [int(sessions * (0.82 + i*0.04)) for i in range(7)]
    return dict(platform='ga4', is_demo=False, days=days,
                sessions=sessions, users=users, new_users=int(v(2)),
                bounce_rate=round(v(3)*100, 1),
                avg_session_duration=f'{dur//60}m {dur%60}s',
                conv_rate=round(v(5)/max(1,sessions)*100, 2),
                revenue=round(v(6), 2), trend=trend)

def fetch_platform_metrics(client: str, platform: str, days: int, budget: float = 10000,
                           workspace_id: str = '') -> dict:
    """
    Main entry point: try cache → try real API → fall back to demo data.
    Always returns a dict with is_demo flag.
    workspace_id: if provided, also try Google OAuth tokens for Google platforms.
    """
    # 1. Check 1-hour cache
    cached = _get_cached(client, platform, days)
    if cached:
        cached['from_cache'] = True
        return cached

    # 2. Try real API (requires stored credentials)
    account_id, token = _get_credential(client, platform)

    # 2b. Fall back to Google OAuth token for Google platforms
    if not token and workspace_id and platform in ('google_ads', 'ga4', 'search_console'):
        token = google_get_access_token(workspace_id)

    if account_id and token:
        try:
            if platform == 'google_ads':
                data = _fetch_google_ads(account_id, token, days)
            elif platform == 'meta_ads':
                data = _fetch_meta(account_id, token, days)
            elif platform == 'ga4':
                data = _fetch_ga4(account_id, token, days)
            else:
                data = None
            if data:
                _cache_metrics(client, platform, days, data)
                return data
        except Exception as e:
            print(f'  {platform} API error for {client}: {e}')

    # 3. Demo fallback — deterministic, proportional to client budget
    demo_fn = {
        'google_ads':     _demo_google_ads,
        'meta_ads':       _demo_meta,
        'ga4':            _demo_ga4,
        'search_console': _demo_search_console,
    }.get(platform)
    return demo_fn(client, budget, days) if demo_fn else {'platform': platform, 'is_demo': True, 'error': 'unsupported'}

# ── Memory helpers ───────────────────────────────────────────────────────────
def fetch_agent_memories(agent_key: str, client: str = 'General', limit: int = 12) -> list:
    """Pull relevant memories for agent + client (client-specific first, then general)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        path = (
            f'agent_memories?agent_key=eq.{agent_key}'
            f'&order=importance.desc,created_at.desc&limit={limit}'
        )
        rows = _supabase_req('GET', path)
        # Prioritise client-specific, pad with General
        specific = [r for r in rows if r.get('client') == client]
        general  = [r for r in rows if r.get('client') == 'General']
        return (specific + general)[:limit]
    except Exception as e:
        print(f'  Memory fetch error: {e}')
        return []

def inject_memories(system_prompt: str, memories: list) -> str:
    """Append memory block to system prompt."""
    if not memories:
        return system_prompt
    lines = '\n'.join(
        f'- [{m.get("memory_type","insight").upper()}] {m["content"]}'
        for m in memories
    )
    return (
        system_prompt
        + f'\n\n── Memory from past work (use when relevant) ──\n{lines}'
    )

# ── Notification helpers ──────────────────────────────────────────────────────
def _send_slack(text: str, webhook: str = '') -> bool:
    """POST a message to a Slack webhook. Returns True on success."""
    url = webhook or SLACK_WEBHOOK_URL
    if not url:
        return False
    try:
        payload = json.dumps({'text': text}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f'  Slack notify error: {e}')
        return False

def _send_email(to: str, subject: str, html: str) -> bool:
    """Send email — prefers Resend when configured, falls back to SMTP."""
    import smtplib, ssl as _ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not to:
        print('  ⚠️  _send_email: no recipient — skipped')
        return False

    # ── Resend (primary when API key is set — avoids SMTP firewall issues) ────
    api_key   = os.getenv('RESEND_API_KEY', '') or RESEND_API_KEY
    from_addr = os.getenv('RESEND_FROM', '') or RESEND_FROM
    if api_key:
        print(f'  📧 Resend → {to} | from={from_addr} | subject={subject[:60]}')
        try:
            payload = json.dumps({'from': from_addr, 'to': [to], 'subject': subject, 'html': html}).encode()
            req = urllib.request.Request(
                'https://api.resend.com/emails', data=payload,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'User-Agent': 'ClickPoint-HQ/2.6 (marketing platform)',
                },
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp_body = r.read().decode()
                print(f'  ✅ Email sent via Resend → {to} (status={r.status}) {resp_body[:120]}')
                return r.status in (200, 201)
        except urllib.error.HTTPError as e:
            err_body = ''
            try: err_body = e.read().decode()
            except Exception: pass
            print(f'  ❌ Resend HTTP {e.code} → {to}: {err_body[:200]}')
        except Exception as e:
            print(f'  ❌ Resend error → {to}: {e}')
        # Resend failed — fall through to SMTP
        print(f'  ⚠️  Resend failed, trying SMTP fallback...')

    # ── SMTP fallback (when Resend not configured or failed) ─────────────────
    smtp_host = os.getenv('SMTP_HOST', '') or SMTP_HOST
    smtp_user = os.getenv('SMTP_USER', '') or SMTP_USER
    smtp_pass = os.getenv('SMTP_PASS', '') or SMTP_PASS
    smtp_from = os.getenv('SMTP_FROM', '') or SMTP_FROM or smtp_user
    smtp_port = int(os.getenv('SMTP_PORT', '') or SMTP_PORT or 465)

    if smtp_host and smtp_user and smtp_pass:
        print(f'  📧 SMTP → {to} via {smtp_host}:{smtp_port}')
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = smtp_from
        msg['To']      = to
        msg.attach(MIMEText(html, 'html'))
        ctx = _ssl.create_default_context()
        try:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=8) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_from, [to], msg.as_string())
            print(f'  ✅ Email sent via SMTP_SSL → {to}')
            return True
        except Exception as e1:
            print(f'  ⚠️  SMTP_SSL failed → {to}: {e1} — trying STARTTLS')
            try:
                with smtplib.SMTP(smtp_host, 587, timeout=8) as server:
                    server.ehlo(); server.starttls(context=ctx)
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_from, [to], msg.as_string())
                print(f'  ✅ Email sent via STARTTLS → {to}')
                return True
            except Exception as e2:
                print(f'  ⚠️  SMTP also failed → {to}: {e2} — trying Resend fallback')

    print(f'  ❌ _send_email: all methods failed for {to}')
    return False

def _push_to_hubspot(contact_props: dict, company_props: dict = None, note: str = '') -> bool:
    """Create or update a HubSpot contact + company and associate them."""
    token = os.getenv('HUBSPOT_TOKEN', '') or HUBSPOT_TOKEN  # reads live each call
    if not token:
        return False
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
    }

    def hs_post(path, payload, method='POST'):
        req = urllib.request.Request(
            f'https://api.hubapi.com{path}',
            data=json.dumps(payload).encode(),
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    contact_id = None
    company_id = None

    # ── Upsert contact by email ───────────────────────────────────────────────
    try:
        email = contact_props.get('email', '')
        search = hs_post('/crm/v3/objects/contacts/search', {
            'filterGroups': [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': email}]}],
            'properties': ['email'], 'limit': 1,
        })
        if search.get('results'):
            contact_id = search['results'][0]['id']
            hs_post(f'/crm/v3/objects/contacts/{contact_id}', {'properties': contact_props}, method='PATCH')
        else:
            res = hs_post('/crm/v3/objects/contacts', {'properties': contact_props})
            contact_id = res.get('id')
        print(f'  📇 HubSpot contact {"updated" if search.get("results") else "created"}: {email} (id={contact_id})')
    except Exception as e:
        print(f'  ⚠️  HubSpot contact error: {e}')
        return False

    # ── Upsert company by name ────────────────────────────────────────────────
    if company_props and company_props.get('name'):
        try:
            search = hs_post('/crm/v3/objects/companies/search', {
                'filterGroups': [{'filters': [{'propertyName': 'name', 'operator': 'EQ', 'value': company_props['name']}]}],
                'properties': ['name'], 'limit': 1,
            })
            if search.get('results'):
                company_id = search['results'][0]['id']
                hs_post(f'/crm/v3/objects/companies/{company_id}', {'properties': company_props}, method='PATCH')
            else:
                res = hs_post('/crm/v3/objects/companies', {'properties': company_props})
                company_id = res.get('id')
            print(f'  🏢 HubSpot company {"updated" if search.get("results") else "created"}: {company_props["name"]} (id={company_id})')
        except Exception as e:
            print(f'  ⚠️  HubSpot company error: {e}')

    # ── Associate contact → company ───────────────────────────────────────────
    if contact_id and company_id:
        try:
            hs_post(
                f'/crm/v4/objects/contacts/{contact_id}/associations/companies/{company_id}',
                [{'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 279}],
                method='PUT',
            )
            print(f'  🔗 HubSpot associated contact {contact_id} → company {company_id}')
        except Exception as e:
            print(f'  ⚠️  HubSpot association error: {e}')

    # ── Add note if provided ──────────────────────────────────────────────────
    if note and contact_id:
        try:
            note_res = hs_post('/crm/v3/objects/notes', {
                'properties': {
                    'hs_note_body': note,
                    'hs_timestamp': str(int(__import__('time').time() * 1000)),
                }
            })
            note_id = note_res.get('id')
            if note_id:
                hs_post(f'/crm/v4/objects/notes/{note_id}/associations/contacts/{contact_id}',
                        [{'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 202}],
                        method='PUT')
        except Exception as e:
            print(f'  ⚠️  HubSpot note error: {e}')

    return bool(contact_id)

def _hash_password(password: str, email: str) -> str:
    """Deterministic password hash — pbkdf2_hmac with email-derived salt."""
    import hashlib
    salt = hashlib.sha256(email.lower().encode()).hexdigest()[:16]
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()

def _get_stripe_customer_email(customer_id: str) -> str:
    """Look up a Stripe customer's email by their customer ID."""
    sk = os.getenv('STRIPE_SECRET_KEY', '') or STRIPE_SECRET_KEY
    if not customer_id or not sk:
        return ''
    try:
        req = urllib.request.Request(
            f'https://api.stripe.com/v1/customers/{customer_id}',
            headers={'Authorization': f'Bearer {sk}'},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read()).get('email', '')
    except Exception as e:
        print(f'  ⚠️  Stripe customer lookup error: {e}')
        return ''

_PLAN_MRR    = {'growth': 299, 'pro': 599}
_PLAN_LABELS = {'growth': 'Growth — $299/mo AUD', 'pro': 'Pro — $599/mo AUD'}

def _hubspot_update_subscription(email: str, company_name: str, plan: str, status: str) -> None:
    """
    Update HubSpot when a subscription changes.
    status: 'active' | 'cancelled' | 'payment_failed'
    """
    token = os.getenv('HUBSPOT_TOKEN', '') or HUBSPOT_TOKEN
    if not token or not email:
        return

    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def hs(path, payload, method='POST'):
        req = urllib.request.Request(
            f'https://api.hubapi.com{path}',
            data=json.dumps(payload).encode(),
            headers=headers, method=method,
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    # ── Find contact ──────────────────────────────────────────────────────────
    contact_id = None
    try:
        res = hs('/crm/v3/objects/contacts/search', {
            'filterGroups': [{'filters': [{'propertyName': 'email', 'operator': 'EQ', 'value': email}]}],
            'properties': ['email'], 'limit': 1,
        })
        if res.get('results'):
            contact_id = res['results'][0]['id']
    except Exception as e:
        print(f'  ⚠️  HubSpot contact lookup error: {e}')
        return

    # ── Find company ─────────────────────────────────────────────────────────
    company_id = None
    if company_name:
        try:
            res = hs('/crm/v3/objects/companies/search', {
                'filterGroups': [{'filters': [{'propertyName': 'name', 'operator': 'EQ', 'value': company_name}]}],
                'properties': ['name'], 'limit': 1,
            })
            if res.get('results'):
                company_id = res['results'][0]['id']
        except Exception:
            pass

    # ── Update contact lifecycle ──────────────────────────────────────────────
    if contact_id:
        try:
            if status == 'active':
                props = {'lifecyclestage': 'customer', 'hs_lead_status': 'IN_PROGRESS'}
            elif status == 'cancelled':
                props = {'lifecyclestage': 'lead', 'hs_lead_status': 'OPEN'}
            else:
                props = {}
            if props:
                hs(f'/crm/v3/objects/contacts/{contact_id}', {'properties': props}, method='PATCH')
        except Exception as e:
            print(f'  ⚠️  HubSpot contact update error: {e}')

    # ── Create / update deal ─────────────────────────────────────────────────
    mrr        = _PLAN_MRR.get(plan, 0)
    plan_label = _PLAN_LABELS.get(plan, plan.title())
    deal_name  = f"{company_name or email} — {plan_label}"
    today_ms   = str(int(__import__('time').time() * 1000))

    deal_stage = 'closedwon' if status == 'active' else 'closedlost'

    try:
        # Search for existing deal by name to avoid duplicates
        res = hs('/crm/v3/objects/deals/search', {
            'filterGroups': [{'filters': [{'propertyName': 'dealname', 'operator': 'EQ', 'value': deal_name}]}],
            'properties': ['dealname'], 'limit': 1,
        })
        deal_id = None
        if res.get('results'):
            deal_id = res['results'][0]['id']
            hs(f'/crm/v3/objects/deals/{deal_id}', {
                'properties': {'dealstage': deal_stage, 'closedate': today_ms}
            }, method='PATCH')
            print(f'  📊 HubSpot deal updated: {deal_name} → {deal_stage}')
        else:
            deal_res = hs('/crm/v3/objects/deals', {'properties': {
                'dealname':   deal_name,
                'amount':     str(mrr),
                'dealstage':  deal_stage,
                'closedate':  today_ms,
                'pipeline':   'default',
                'description': f'Plan: {plan} | MRR: ${mrr}/mo AUD | Status: {status}',
            }})
            deal_id = deal_res.get('id')
            print(f'  💰 HubSpot deal created: {deal_name} (${mrr}/mo)')

        # Associate deal → contact + company
        if deal_id:
            if contact_id:
                try:
                    hs(f'/crm/v4/objects/deals/{deal_id}/associations/contacts/{contact_id}',
                       [{'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 3}], method='PUT')
                except Exception:
                    pass
            if company_id:
                try:
                    hs(f'/crm/v4/objects/deals/{deal_id}/associations/companies/{company_id}',
                       [{'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 5}], method='PUT')
                except Exception:
                    pass
    except Exception as e:
        print(f'  ⚠️  HubSpot deal error: {e}')

    # ── Add note ─────────────────────────────────────────────────────────────
    if contact_id:
        note_map = {
            'active':         f'✅ Subscription activated — {plan_label}',
            'cancelled':      f'❌ Subscription cancelled — was on {plan_label}. Re-marketing eligible.',
            'payment_failed': f'⚠️ Payment failed — {plan_label}. Follow up required.',
        }
        note_body = note_map.get(status, f'Subscription event: {status}')
        try:
            note_res = hs('/crm/v3/objects/notes', {'properties': {
                'hs_note_body': note_body,
                'hs_timestamp': today_ms,
            }})
            note_id = note_res.get('id')
            if note_id:
                hs(f'/crm/v4/objects/notes/{note_id}/associations/contacts/{contact_id}',
                   [{'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 202}], method='PUT')
        except Exception as e:
            print(f'  ⚠️  HubSpot note error: {e}')

def _notify(event: str, client: str = '', detail: str = '', webhook: str = '', email: str = '') -> dict:
    """
    Dispatch a notification for a named event.
    event: 'escalation' | 'overdue' | 'report' | 'custom'
    Returns {'slack': bool, 'email': bool}
    """
    icons = {'escalation': '🚨', 'overdue': '⏰', 'report': '📋', 'custom': '🔔'}
    icon  = icons.get(event, '🔔')
    titles = {
        'escalation': 'Escalation raised',
        'overdue':    'Task overdue',
        'report':     'Monthly report ready',
        'custom':     'ClickPoint Alert',
    }
    title = titles.get(event, 'ClickPoint Alert')
    slack_text = f'{icon} *{title}*' + (f' — {client}' if client else '') + (f'\n{detail}' if detail else '')
    email_html = f"""<div style="font-family:-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:28px;">
        <div style="font-size:20px;font-weight:800;color:#1C3A2E;margin-bottom:6px;">{icon} {title}</div>
        {f'<div style="font-size:16px;font-weight:700;color:#333;margin-bottom:12px;">{client}</div>' if client else ''}
        {f'<div style="font-size:14px;color:#555;line-height:1.6;">{detail}</div>' if detail else ''}
        <div style="margin-top:24px;font-size:11px;color:#aaa;">ClickPoint Marketing HQ · Automated Alert</div>
    </div>"""
    wh  = webhook or SLACK_WEBHOOK_URL
    em  = email   or NOTIFY_EMAIL
    return {
        'slack': _send_slack(slack_text, wh),
        'email': _send_email(em, f'[ClickPoint] {title}' + (f' — {client}' if client else ''), email_html),
    }

# ── Portal helpers ────────────────────────────────────────────────────────────
import random as _random
import string as _string

def _generate_access_code(length: int = 6) -> str:
    return ''.join(_random.choices(_string.digits, k=length))

# ── Report prompt builders ────────────────────────────────────────────────────
def _build_raj_report_prompt(client: str, period: str, metrics: dict) -> str:
    parts = [f'MONTHLY PERFORMANCE DATA — {client.upper()} — {period}\n']
    ga = metrics.get('google_ads', {})
    if ga:
        parts.append('GOOGLE ADS')
        parts.append(f'  Spend: ${ga.get("spend",0):,.0f}  |  ROAS: {ga.get("roas",0):.2f}x  |  Conversions: {int(ga.get("conversions",0)):,}')
        parts.append(f'  Clicks: {int(ga.get("clicks",0)):,}  |  Impressions: {int(ga.get("impressions",0)):,}  |  CTR: {ga.get("ctr",0):.2f}%  |  CPC: ${ga.get("cpc",0):.2f}')
        if ga.get('is_demo'): parts.append('  (estimated/demo data)')
    meta = metrics.get('meta_ads', {})
    if meta:
        parts.append('\nMETA ADS')
        parts.append(f'  Spend: ${meta.get("spend",0):,.0f}  |  ROAS: {meta.get("roas",0):.2f}x  |  Reach: {int(meta.get("reach",0)):,}')
        parts.append(f'  CTR: {meta.get("ctr",0):.2f}%  |  CPM: ${meta.get("cpm",0):.2f}  |  Conv Value: ${meta.get("conv_value",0):,.0f}')
        if meta.get('is_demo'): parts.append('  (estimated/demo data)')
    g4 = metrics.get('ga4', {})
    if g4:
        parts.append('\nGOOGLE ANALYTICS 4')
        parts.append(f'  Sessions: {int(g4.get("sessions",0)):,}  |  Users: {int(g4.get("users",0)):,}  |  New Users: {int(g4.get("new_users",0)):,}')
        parts.append(f'  Bounce: {g4.get("bounce_rate",0):.1f}%  |  Avg Duration: {g4.get("avg_session_duration","N/A")}  |  Conv Rate: {g4.get("conv_rate",0):.2f}%')
        if g4.get('is_demo'): parts.append('  (estimated/demo data)')
    sc = metrics.get('search_console', {})
    if sc:
        parts.append('\nSEARCH CONSOLE')
        parts.append(f'  Impressions: {int(sc.get("impressions",0)):,}  |  Clicks: {int(sc.get("clicks",0)):,}  |  CTR: {sc.get("ctr",0):.2f}%  |  Avg Position: {sc.get("avg_position",0):.1f}')
        if sc.get('is_demo'): parts.append('  (estimated/demo data)')
    data_block = '\n'.join(parts)
    return f"""{data_block}

You are writing the DATA ANALYSIS section for {client}'s {period} monthly report.

Write a thorough analysis with these clearly labelled sections:
**Performance Overview** — 2-3 sentences on the month's headline numbers and trajectory.
**Paid Search (Google Ads)** — spend efficiency, ROAS, conversion volume, what's driving results.
**Paid Social (Meta Ads)** — reach, ROAS, audience quality, what to watch.
**Organic Performance** — GA4 sessions trend, bounce rate context, Search Console position and CTR.
**Key Highlights** — exactly 3 specific wins, each with the exact number.
**Improvement Opportunities** — exactly 3 specific areas, each with the number at stake.

Write in professional prose with bold headers. Cite every number precisely. This is an internal analysis for the agency team — be direct and data-first."""


def _build_jess_report_prompt(client: str, period: str, raj_output: str) -> str:
    return f"""You are writing the CLIENT-FACING NARRATIVE for {client}'s {period} monthly marketing report.

Our analytics specialist Raj completed his internal analysis:
---
{raj_output[:2500]}
---

Write the narrative the CLIENT will read. Transform technical findings into clear, engaging business language.

Structure (no visible section headers — flowing prose only):
1. Opening paragraph — Warm, professional. Land the biggest win immediately with the exact number.
2. Campaign performance — What did the spend actually achieve? Connect numbers to real business outcomes. Zero jargon.
3. What's working well — Celebrate 2-3 wins with context about why they matter for this client's specific business.
4. Looking ahead — Frame any challenges as opportunities. Solution-focused, forward-looking.
5. Closing — One warm collaborative paragraph. Make the client feel excited about next month.

Tone: Smart colleague talking to a client they genuinely care about. Avoid: "I am pleased to report", "leverage", "synergies", "moving the needle". Write 4-5 flowing paragraphs."""


def _build_sarah_report_prompt(client: str, period: str, raj_output: str, jess_output: str) -> str:
    return f"""Review and finalise the {period} monthly report for {client}.

DATA ANALYSIS (Raj Nair):
{raj_output[:1800]}

CLIENT NARRATIVE (Jess Park):
{jess_output[:1200]}

Respond with ONLY valid JSON — no markdown fences, no explanation, no extra text:
{{
  "executive_summary": "3-4 sentence CMO-level summary. Lead with the single most important win and its number. End with the key strategic priority for next month.",
  "health_score": 8,
  "health_label": "Performing above target",
  "recommendations": [
    {{"priority": "HIGH", "action": "Specific action with the number that justifies it", "owner": "Derek"}},
    {{"priority": "HIGH", "action": "Specific action with the number that justifies it", "owner": "Cleo"}},
    {{"priority": "MED",  "action": "Specific action with the number that justifies it", "owner": "Raj"}},
    {{"priority": "LOW",  "action": "Specific action with the number that justifies it", "owner": "Zara"}}
  ],
  "budget_note": "1-2 sentences on budget allocation. Is the split optimal? Should anything shift next month?",
  "sarah_sign_off": "One warm, forward-looking sentence Sarah would personally write to this client."
}}"""


# ── Integration security helpers ──────────────────────────────────────────────
def encrypt_token(raw: str) -> str:
    """AES-256 encrypt a credential."""
    if not _FERNET_OK or not INTEGRATION_ENCRYPTION_KEY:
        return raw
    return _Fernet(INTEGRATION_ENCRYPTION_KEY.encode()).encrypt(raw.encode()).decode()

def decrypt_token(enc: str) -> str:
    """AES-256 decrypt a stored credential."""
    if not _FERNET_OK or not INTEGRATION_ENCRYPTION_KEY:
        return enc
    try:
        return _Fernet(INTEGRATION_ENCRYPTION_KEY.encode()).decrypt(enc.encode()).decode()
    except Exception:
        return ''

def _supabase_req(method: str, path: str, payload: dict = None, service_role: bool = True):
    """Call Supabase REST API. Uses service_role key (bypasses RLS) by default."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError('SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env')
    key = SUPABASE_SERVICE_KEY if service_role else ''
    url = f'{SUPABASE_URL}/rest/v1/{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'apikey':        key,
        'Authorization': f'Bearer {key}',
        'Content-Type':  'application/json',
        'Prefer':        'return=representation',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return json.loads(body) if body else []

# ── Canva OAuth + Design Generation ──────────────────────────────────────────
import base64 as _canva_b64
import urllib.parse as _canva_up
import threading as _canva_threading

_canva_token_lock  = _canva_threading.Lock()
# Per-workspace token cache: {workspace_id: {'access_token':..., 'refresh_token':..., 'expires_at': float}}
_canva_token_cache = {}
# PKCE + state store: {state: {'code_verifier': str, 'workspace_id': str}}
_canva_pkce_store  = {}

def _canva_basic_auth():
    creds = f'{_canva_client_id()}:{_canva_client_secret()}'
    return _canva_b64.b64encode(creds.encode()).decode()

def _canva_pkce_pair():
    """Generate a PKCE code_verifier + code_challenge (S256)."""
    import hashlib, secrets
    verifier = secrets.token_urlsafe(64)[:128]
    digest   = hashlib.sha256(verifier.encode()).digest()
    challenge = _canva_b64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    return verifier, challenge

def _canva_token_request(params: dict) -> dict:
    import urllib.error as _ue
    body = _canva_up.urlencode(params).encode()
    req = urllib.request.Request(
        'https://api.canva.com/rest/v1/oauth/token',
        data=body,
        headers={
            'Authorization': f'Basic {_canva_basic_auth()}',
            'Content-Type':  'application/x-www-form-urlencoded',
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except _ue.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f'  ❌ Canva token error {e.code}: {error_body}')
        raise

def canva_exchange_code(code: str, state: str) -> tuple:
    """Exchange auth code for tokens. Returns (token_data, workspace_id)."""
    import time as _t
    # Decode stateless payload (verifier + workspace_id encoded in state)
    try:
        padding = 4 - len(state) % 4
        decoded = _canva_b64.urlsafe_b64decode(state + '=' * (padding % 4))
        payload = json.loads(decoded)
        verifier = payload.get('v', '')
        wid      = payload.get('w', '')
    except Exception:
        # Fallback to legacy in-memory store
        entry    = _canva_pkce_store.pop(state, {})
        verifier = entry.get('code_verifier', '')
        wid      = entry.get('workspace_id', '')
    data = _canva_token_request({
        'grant_type':    'authorization_code',
        'code':          code,
        'redirect_uri':  CANVA_REDIRECT_URI,
        'code_verifier': verifier,
    })
    tokens = {
        'access_token':  data['access_token'],
        'refresh_token': data.get('refresh_token', ''),
        'expires_at':    _t.time() + data.get('expires_in', 3600) - 60,
    }
    with _canva_token_lock:
        _canva_token_cache[wid] = tokens
    _canva_persist_tokens(wid, tokens)
    return data, wid

def _canva_persist_tokens(workspace_id: str, tokens: dict):
    """Store per-workspace Canva tokens in Supabase platform_settings."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    setting_key = f'canva_tokens_{workspace_id}' if workspace_id else 'canva_tokens_agency'
    payload_val = json.dumps(tokens)
    try:
        key  = SUPABASE_SERVICE_KEY
        hdrs = {'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                'Prefer': 'resolution=merge-duplicates,return=representation'}
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/platform_settings',
            data=json.dumps({'key': setting_key, 'value': payload_val}).encode(),
            headers=hdrs, method='POST'
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        print(f'  ✅ Canva tokens persisted for workspace: {workspace_id or "agency"}')
    except Exception as e:
        print(f'  ⚠️  canva token persist error: {e}')

def _canva_load_tokens(workspace_id: str) -> dict:
    """Load Canva tokens for a workspace from Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    setting_key = f'canva_tokens_{workspace_id}' if workspace_id else 'canva_tokens_agency'
    try:
        key = SUPABASE_SERVICE_KEY
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/platform_settings?key=eq.{_canva_up.quote(setting_key)}',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if rows:
            return json.loads(rows[0]['value'])
    except Exception as e:
        print(f'  ⚠️  canva token load error: {e}')
    return {}

def canva_get_access_token(workspace_id: str = '') -> str:
    """Return a valid Canva access token for a workspace, refreshing if needed."""
    import time as _t
    wid = workspace_id or ''

    # Check in-memory cache first
    with _canva_token_lock:
        cached = _canva_token_cache.get(wid, {})

    if not cached:
        cached = _canva_load_tokens(wid)
        if cached:
            with _canva_token_lock:
                _canva_token_cache[wid] = cached

    at  = cached.get('access_token', '')
    rt  = cached.get('refresh_token', '')
    exp = cached.get('expires_at', 0)

    if not at and not rt:
        return ''
    if at and _t.time() < exp:
        return at
    if not rt:
        return ''

    # Refresh the token
    try:
        data   = _canva_token_request({'grant_type': 'refresh_token', 'refresh_token': rt})
        tokens = {
            'access_token':  data['access_token'],
            'refresh_token': data.get('refresh_token', rt),
            'expires_at':    _t.time() + data.get('expires_in', 3600) - 60,
        }
        with _canva_token_lock:
            _canva_token_cache[wid] = tokens
        _canva_persist_tokens(wid, tokens)
        return tokens['access_token']
    except Exception as e:
        print(f'  ⚠️  canva token refresh error ({wid}): {e}')
        return ''

# ── Brand Hub helpers ────────────────────────────────────────────────────────

def _save_brand_hub(workspace_id: str, data: dict) -> bool:
    """Persist brand hub data for a workspace to Supabase platform_settings."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    key = f'brand_hub_{workspace_id}'
    try:
        hdrs = {
            'apikey': SUPABASE_SERVICE_KEY,
            'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=representation',
        }
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/platform_settings',
            data=json.dumps({'key': key, 'value': json.dumps(data)}).encode(),
            headers=hdrs, method='POST',
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        print(f'  ✅ Brand hub saved for workspace: {workspace_id}')
        return True
    except Exception as e:
        print(f'  ⚠️  brand hub save error: {e}')
        return False

def _load_brand_hub(workspace_id: str) -> dict:
    """Load brand hub data for a workspace from Supabase platform_settings."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    key = f'brand_hub_{workspace_id}'
    try:
        import urllib.parse as _up_bhl
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/platform_settings?key=eq.{_up_bhl.quote(key)}',
            headers={
                'apikey': SUPABASE_SERVICE_KEY,
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            }
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if rows:
            return json.loads(rows[0]['value'])
    except Exception as e:
        print(f'  ⚠️  brand hub load error ({workspace_id}): {e}')
    return {}

def canva_generate_and_export(brief_text: str, brand_name: str, workspace_id: str = '', brand_hub: dict = None) -> list:
    """
    Create Canva designs for a campaign and return edit URLs.

    Strategy (in order of preference):
      1. If the workspace has brand templates → autofill the first one (exports PNG)
      2. Otherwise → create a blank 1080×1080 canvas → return edit URL with brand
         context in the title so the client knows what to build

    brand_hub: optional dict from platform brand hub (colors, tone, logo, etc.)
    Returns a list of URLs — PNG download URLs or 'canva:' prefixed edit URLs.
    """
    import time
    brand_hub = brand_hub or {}
    token = canva_get_access_token(workspace_id)
    if not token:
        print('  ⚠️  Canva: no access token — skipping design generation')
        return []

    def _canva_api(method, path, payload=None):
        url = f'https://api.canva.com/rest/v1/{path}'
        data = json.dumps(payload).encode() if payload else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            print(f'  ⚠️  Canva API {method} /{path} → {e.code}: {body[:200]}')
            raise

    def _get_design_edit_url(design):
        """Extract edit URL from a design object."""
        urls = design.get('urls', {})
        return urls.get('edit_url') or urls.get('view_url') or ''

    def _export_design(design_id):
        """Export a design as PNG and return the first download URL, or ''."""
        try:
            export_resp = _canva_api('POST', 'exports', {
                'design_id': design_id,
                'format':    {'type': 'png', 'export_quality': 'pro'},
            })
            exp_job_id = export_resp.get('job', {}).get('id') or export_resp.get('id')
            for _ in range(15):
                time.sleep(2)
                exp_status = _canva_api('GET', f'exports/{exp_job_id}')
                if (exp_status.get('job', {}).get('status') or exp_status.get('status')) == 'success':
                    urls = exp_status.get('job', {}).get('urls') or exp_status.get('urls', [])
                    return urls[0] if urls else ''
        except Exception as e:
            print(f'  ⚠️  Canva export error: {e}')
        return ''

    try:
        design_id   = None
        used_method = 'blank'

        # ── Strategy 1: autofill a brand template if one exists ──────────────
        try:
            templates_resp = _canva_api('GET', 'brand-templates?limit=5')
            templates = templates_resp.get('items', [])
            if templates:
                template_id = templates[0].get('id')
                print(f'  🎨 Canva: autofilling brand template {template_id}')
                dataset_resp = _canva_api('GET', f'brand-templates/{template_id}/dataset')
                dataset = dataset_resp.get('dataset', {})
                autofill_data = {
                    k: {'type': 'text', 'text': f'{brand_name} — {brief_text[:120]}'}
                    for k, v in dataset.items() if v.get('type') == 'text'
                }
                if autofill_data:
                    af_resp = _canva_api('POST', 'autofills', {
                        'brand_template_id': template_id,
                        'title': f'{brand_name} — Campaign Design',
                        'data': autofill_data,
                    })
                    job_id = af_resp.get('job', {}).get('id') or af_resp.get('id')
                    for _ in range(20):
                        time.sleep(3)
                        s = _canva_api('GET', f'autofills/{job_id}')
                        st = s.get('job', {}).get('status') or s.get('status')
                        if st == 'success':
                            design_id = (s.get('job', {}).get('result', {}).get('design', {}).get('id')
                                         or s.get('result', {}).get('design', {}).get('id'))
                            used_method = 'brand_template'
                            break
                        if st in ('failed', 'error'):
                            break
        except Exception as e:
            print(f'  ℹ️  Canva: no brand template autofill ({e}) — falling back to blank design')

        # ── Strategy 2: create a blank 1080×1080 Instagram Post design ─────────
        if not design_id:
            # Build a descriptive title using brand hub data so the client knows
            # what to create when they open the canvas in Canva
            colors  = ', '.join(filter(None, [brand_hub.get(f'bCol{n}','') for n in '12345']))
            tone    = brand_hub.get('bTone', '') or brand_hub.get('bFormality', '')
            tagline = brand_hub.get('bTagline', '')
            title_parts = [brand_name]
            if tagline: title_parts.append(tagline)
            canvas_title = ' — '.join(title_parts)[:80]
            print(f'  🎨 Canva: creating blank 1080×1080 canvas for {brand_name}'
                  + (f' (colours: {colors})' if colors else ''))
            create_resp = _canva_api('POST', 'designs', {
                'design_type': {'type': 'custom', 'width': 1080, 'height': 1080, 'unit': 'px'},
                'title': canvas_title,
            })
            design = create_resp.get('design', {})
            design_id = design.get('id')

        if not design_id:
            print('  ⚠️  Canva: could not create design')
            return []

        print(f'  ✅ Canva design created ({used_method}): {design_id}')

        # ── Brand template → try PNG export (has real content) ───────────────
        # Blank design → skip export (white canvas is useless), return edit URL
        if used_method == 'brand_template':
            png_url = _export_design(design_id)
            if png_url:
                print(f'  ✅ Canva: PNG exported from brand template')
                return [png_url]

        # Return edit URL — client opens their pre-sized canvas in Canva
        design_info = _canva_api('GET', f'designs/{design_id}')
        edit_url = _get_design_edit_url(design_info.get('design', design_info))
        if edit_url:
            print(f'  ✅ Canva: returning edit URL ({"brand template fallback" if used_method == "brand_template" else "blank canvas — client to design"})')
            return [f'canva:{edit_url}']

        return []

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f'  ⚠️  Canva API error {e.code}: {body[:300]}')
        return []
    except Exception as e:
        print(f'  ⚠️  Canva generate_and_export error: {e}')
        return []

def _canva_client_id() -> str:
    """Return Canva client ID — re-reads os.getenv at call time to handle late Railway injection."""
    return os.getenv('CANVA_CLIENT_ID', '') or CANVA_CLIENT_ID

def _canva_client_secret() -> str:
    """Return Canva client secret — re-reads os.getenv at call time."""
    return os.getenv('CANVA_CLIENT_SECRET', '') or CANVA_CLIENT_SECRET

def canva_auth_url(workspace_id: str = '') -> str:
    """Return the Canva OAuth authorization URL (PKCE + state) for a workspace.

    The state encodes the verifier + workspace_id as base64 JSON so it survives
    server restarts — no in-memory store needed.
    """
    if not _canva_client_id():
        return ''
    import secrets as _sec
    verifier, challenge = _canva_pkce_pair()
    nonce = _sec.token_urlsafe(16)
    # Encode verifier + workspace into state so callback is stateless
    state_payload = json.dumps({'v': verifier, 'w': workspace_id or '', 'n': nonce})
    state = _canva_b64.urlsafe_b64encode(state_payload.encode()).rstrip(b'=').decode()
    scopes = 'design:content:read design:content:write design:meta:read asset:read asset:write brandtemplate:meta:read brandtemplate:content:read profile:read'
    params = _canva_up.urlencode({
        'client_id':             _canva_client_id(),
        'redirect_uri':          CANVA_REDIRECT_URI,
        'response_type':         'code',
        'scope':                 scopes,
        'state':                 state,
        'code_challenge':        challenge,
        'code_challenge_method': 'S256',
    })
    return f'https://www.canva.com/api/oauth/authorize?{params}'

# ── Google OAuth helpers ──────────────────────────────────────────────────────

import secrets as _gsec
_google_state_store: dict = {}   # state → workspace_id  (in-memory; survives restarts via DB)

_GOOGLE_SCOPES = ' '.join([
    'openid',
    'email',
    'profile',
    'https://www.googleapis.com/auth/adwords',
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/webmasters.readonly',
])

def google_auth_url(workspace_id: str = '') -> str:
    """Return Google OAuth authorization URL.
    workspace_id is encoded INTO the state token so it survives server restarts.
    """
    if not GOOGLE_CLIENT_ID:
        return ''
    import base64 as _b64
    nonce = _gsec.token_urlsafe(16)
    # Encode workspace_id into state so we don't rely solely on in-memory dict
    raw_state   = f'{nonce}:{workspace_id or ""}'
    state       = _b64.urlsafe_b64encode(raw_state.encode()).decode().rstrip('=')
    _google_state_store[state] = workspace_id or ''   # keep in-memory as primary cache
    params = urllib.parse.urlencode({
        'client_id':     GOOGLE_CLIENT_ID,
        'redirect_uri':  GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope':         _GOOGLE_SCOPES,
        'access_type':   'offline',   # get refresh_token
        'prompt':        'consent',   # always return refresh_token
        'state':         state,
    })
    return f'https://accounts.google.com/o/oauth2/v2/auth?{params}'

def google_exchange_code(code: str, state: str) -> tuple[dict, str]:
    """Exchange auth code for tokens. Returns (tokens_dict, workspace_id)."""
    # Primary: in-memory store (fastest, works if same process)
    workspace_id = _google_state_store.pop(state, '')
    # Fallback: decode workspace_id from state token (survives server restarts)
    if not workspace_id:
        try:
            import base64 as _b64
            padding  = 4 - len(state) % 4
            decoded  = _b64.urlsafe_b64decode(state + '=' * (padding % 4)).decode()
            parts    = decoded.split(':', 1)
            if len(parts) == 2:
                workspace_id = parts[1]
                print(f'  ℹ️  google_exchange_code: decoded workspace_id from state token: {workspace_id!r}')
        except Exception as _se:
            print(f'  ⚠️  google_exchange_code: could not decode state: {_se}')
    body = urllib.parse.urlencode({
        'code':          code,
        'client_id':     GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri':  GOOGLE_REDIRECT_URI,
        'grant_type':    'authorization_code',
    }).encode()
    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        tokens = json.loads(resp.read())
    return tokens, workspace_id

def google_refresh_token(refresh_token_val: str) -> dict:
    """Get a fresh access token using a refresh token."""
    body = urllib.parse.urlencode({
        'refresh_token': refresh_token_val,
        'client_id':     GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'grant_type':    'refresh_token',
    }).encode()
    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def google_persist_tokens(workspace_id: str, tokens: dict):
    """Store Google OAuth tokens encrypted in client_integrations (uses 'client' column)."""
    if not workspace_id or not tokens:
        return
    payload   = json.dumps(tokens)
    encrypted = encrypt_token(payload)
    enc_ws    = urllib.parse.quote(workspace_id)
    try:
        # Upsert — filter by 'client' column (matches rest of the codebase)
        existing = _supabase_req(
            'GET',
            f'client_integrations?client=eq.{enc_ws}&platform=eq.google_oauth&select=id'
        )
        if existing:
            iid = existing[0]['id']
            _supabase_req('PATCH', f'client_integrations?id=eq.{iid}', {
                'status': 'connected', 'encrypted_token': encrypted,
                'last_synced': datetime.datetime.utcnow().isoformat(),
            })
        else:
            rows = _supabase_req('POST', 'client_integrations', {
                'client':          workspace_id,
                'platform':        'google_oauth',
                'status':          'connected',
                'encrypted_token': encrypted,
            })
            iid = rows[0]['id']
        # Mirror to integration_credentials (best-effort)
        try:
            _supabase_req('POST', 'integration_credentials', {
                'integration_id':  str(iid),
                'platform':        'google_oauth',
                'encrypted_token': encrypted,
            })
        except Exception:
            pass
        print(f'  ✅ Google tokens persisted for {workspace_id}')
    except Exception as e:
        print(f'  ⚠️  google_persist_tokens error: {e}')

def google_load_tokens(workspace_id: str) -> dict:
    """Load and decrypt Google OAuth tokens for a workspace."""
    if not workspace_id:
        return {}
    enc_ws = urllib.parse.quote(workspace_id)
    try:
        # Primary lookup via 'client' column
        rows = _supabase_req(
            'GET',
            f'client_integrations?client=eq.{enc_ws}&platform=eq.google_oauth&select=encrypted_token&status=eq.connected'
        )
        if not rows:
            # Fallback: old rows may have been saved under workspace_id column
            rows = _supabase_req(
                'GET',
                f'client_integrations?workspace_id=eq.{enc_ws}&platform=eq.google_oauth&select=encrypted_token'
            )
        if not rows:
            return {}
        raw = decrypt_token(rows[0]['encrypted_token'])
        return json.loads(raw) if raw else {}
    except Exception as e:
        print(f'  ⚠️  google_load_tokens error: {e}')
        return {}

def google_get_access_token(workspace_id: str) -> str:
    """Return a valid Google access token, refreshing if needed."""
    tokens = google_load_tokens(workspace_id)
    if not tokens:
        return ''
    access_token  = tokens.get('access_token', '')
    refresh_tok   = tokens.get('refresh_token', '')
    expires_at    = tokens.get('expires_at', 0)
    # Refresh if within 5 min of expiry or already expired
    if refresh_tok and (not expires_at or time.time() > expires_at - 300):
        try:
            new_tokens = google_refresh_token(refresh_tok)
            new_tokens['refresh_token'] = refresh_tok          # keep original refresh token
            new_tokens['expires_at']    = time.time() + new_tokens.get('expires_in', 3600)
            google_persist_tokens(workspace_id, new_tokens)
            access_token = new_tokens.get('access_token', '')
        except Exception as e:
            print(f'  ⚠️  Google token refresh error: {e}')
    return access_token

# ── Social Publishing helpers ─────────────────────────────────────────────────

def _social_publish_facebook(page_id: str, token: str, content: str, media_urls: list = None) -> str:
    """POST to Facebook Graph API. Returns post_id string."""
    params = {'message': content, 'access_token': token}
    if media_urls:
        params['link'] = media_urls[0]
    url = f'https://graph.facebook.com/v19.0/{page_id}/feed'
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read())
    return result.get('id', '')


def _social_publish_instagram(page_id: str, token: str, content: str, media_url: str = None) -> str:
    """Publish to Instagram via Graph API. Returns media_id string."""
    if not media_url:
        raise ValueError('Instagram requires an image URL')
    # Step 1: create media container
    container_url = (
        f'https://graph.facebook.com/v19.0/{page_id}/media'
        f'?image_url={urllib.parse.quote(media_url)}'
        f'&caption={urllib.parse.quote(content)}'
        f'&access_token={token}'
    )
    req = urllib.request.Request(container_url, data=b'', method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        container = json.loads(resp.read())
    creation_id = container.get('id', '')
    if not creation_id:
        raise ValueError(f'Instagram container creation failed: {container}')
    # Step 2: publish
    publish_url = (
        f'https://graph.facebook.com/v19.0/{page_id}/media_publish'
        f'?creation_id={creation_id}&access_token={token}'
    )
    req2 = urllib.request.Request(publish_url, data=b'', method='POST')
    with urllib.request.urlopen(req2, timeout=20) as resp2:
        result = json.loads(resp2.read())
    return result.get('id', '')


def _social_publish_linkedin(token: str, content: str) -> str:
    """POST to LinkedIn UGC Posts API. Returns post URN string."""
    # Get person ID
    me_req = urllib.request.Request(
        'https://api.linkedin.com/v2/me',
        headers={'Authorization': f'Bearer {token}'},
    )
    with urllib.request.urlopen(me_req, timeout=15) as resp:
        me = json.loads(resp.read())
    person_id = me.get('id', '')
    if not person_id:
        raise ValueError('Could not get LinkedIn person ID')
    payload = json.dumps({
        'author': f'urn:li:person:{person_id}',
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': {
                'shareCommentary': {'text': content},
                'shareMediaCategory': 'NONE',
            }
        },
        'visibility': {'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'},
    }).encode()
    post_req = urllib.request.Request(
        'https://api.linkedin.com/v2/ugcPosts',
        data=payload,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(post_req, timeout=20) as resp:
        result = json.loads(resp.read())
    return result.get('id', '')


def _social_publish_twitter(token: str, content: str) -> str:
    """POST tweet via Twitter API v2. Returns tweet_id string."""
    payload = json.dumps({'text': content[:280]}).encode()
    req = urllib.request.Request(
        'https://api.twitter.com/2/tweets',
        data=payload,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read())
    return result.get('data', {}).get('id', '')


def _publish_post_to_platforms(post: dict, accounts: dict) -> tuple:
    """
    Publish a social post to all its platforms.
    post: social_posts row dict
    accounts: dict of {platform: account_row}
    Returns (platform_ids: dict, failed: list)
    """
    platform_ids = {}
    failed = []
    for platform in (post.get('platforms') or []):
        acct = accounts.get(platform)
        if not acct:
            failed.append({'platform': platform, 'error': 'No connected account'})
            continue
        raw_token = decrypt_token(acct.get('encrypted_token', ''))
        if not raw_token:
            failed.append({'platform': platform, 'error': 'Token missing or decrypt failed'})
            continue
        content = post.get('content', '')
        media_urls = post.get('media_urls') or []
        try:
            if platform == 'facebook':
                pid = _social_publish_facebook(acct.get('page_id', ''), raw_token, content, media_urls)
                platform_ids['facebook'] = pid
            elif platform == 'instagram':
                pid = _social_publish_instagram(
                    acct.get('page_id', ''), raw_token, content,
                    media_urls[0] if media_urls else None
                )
                platform_ids['instagram'] = pid
            elif platform == 'linkedin':
                pid = _social_publish_linkedin(raw_token, content)
                platform_ids['linkedin'] = pid
            elif platform == 'twitter':
                pid = _social_publish_twitter(raw_token, content)
                platform_ids['twitter'] = pid
            else:
                failed.append({'platform': platform, 'error': f'Unsupported platform: {platform}'})
        except Exception as e:
            print(f'  [social] publish error {platform}: {e}')
            failed.append({'platform': platform, 'error': str(e)})
    return platform_ids, failed


def _publish_due_social_posts():
    """Query scheduled posts that are due and publish them."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    now_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    try:
        posts = _supabase_req(
            'GET',
            f'social_posts?status=eq.scheduled&scheduled_at=lte.{now_iso}&select=*'
        )
    except Exception as e:
        print(f'  [social scheduler] query error: {e}')
        return
    for post in (posts or []):
        post_id = post.get('id')
        workspace_id = post.get('workspace_id', '')
        try:
            acct_rows = _supabase_req(
                'GET',
                f'social_accounts?workspace_id=eq.{urllib.parse.quote(workspace_id)}&status=eq.connected'
            )
            accounts = {r['platform']: r for r in (acct_rows or [])}
            platform_ids, failed = _publish_post_to_platforms(post, accounts)
            if platform_ids:
                pub_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
                _supabase_req('PATCH', f'social_posts?id=eq.{post_id}', {
                    'status': 'published',
                    'published_at': pub_iso,
                    'platform_ids': platform_ids,
                    'error': json.dumps(failed) if failed else None,
                })
                print(f'  [social scheduler] published post {post_id}: {list(platform_ids.keys())}')
            else:
                _supabase_req('PATCH', f'social_posts?id=eq.{post_id}', {
                    'status': 'failed',
                    'error': json.dumps(failed),
                })
                print(f'  [social scheduler] post {post_id} failed: {failed}')
        except Exception as e:
            print(f'  [social scheduler] post {post_id} error: {e}')
            try:
                _supabase_req('PATCH', f'social_posts?id=eq.{post_id}', {
                    'status': 'failed', 'error': str(e)
                })
            except Exception:
                pass


def _social_scheduler_loop():
    """Background daemon: publish due social posts every 60 seconds."""
    while True:
        time.sleep(60)
        try:
            _publish_due_social_posts()
        except Exception as e:
            print(f'  [social scheduler] error: {e}')


# ── Anthropic API call ────────────────────────────────────────────────────────
def call_anthropic(api_key, system_prompt, messages, max_tokens=2000):
    payload = json.dumps({
        'model': 'claude-opus-4-5',
        'max_tokens': max_tokens,
        'system': system_prompt,
        'messages': messages,
    }).encode()

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=payload,
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        }
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
        return result['content'][0]['text']


# ── Request Handler ───────────────────────────────────────────────────────────
class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f'  {self.address_string()} → {format % args}')

    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-User-Api-Key')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def _client_ip(self) -> str:
        return (self.headers.get('X-Forwarded-For', '') or self.client_address[0]).split(',')[0].strip()

    def _effective_api_key(self):
        return self.headers.get('X-User-Api-Key', '').strip() or API_KEY

    def _read_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(content_length))

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_cors_headers()
            self.end_headers()
            effective = self._effective_api_key()
            self.wfile.write(json.dumps({
                'status': 'ok',
                'agents': list(AGENT_PROMPTS.keys()),
                'api_key_set': bool(effective),
                'integrations_ready': bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
                'encryption_ready':   _FERNET_OK and bool(INTEGRATION_ENCRYPTION_KEY),
                'endpoints': ['/health', '/api/agent', '/api/chain',
                              '/api/integrations/connect', '/api/integrations/disconnect'],
            }).encode())
        elif self.path.startswith('/api/ads-diag'):
            # Temporary diagnostic — checks credential chain for a workspace
            import urllib.parse as _up_diag
            qs = _up_diag.parse_qs(_up_diag.urlparse(self.path).query)
            ws = (qs.get('ws') or [''])[0].strip()
            result = {'ws': ws}
            # 1. Raw DB rows — bypass _get_credential to see exactly what's stored
            try:
                enc_ws = urllib.parse.quote(ws)
                raw_rows = _supabase_req('GET',
                    f'client_integrations?client=eq.{enc_ws}&select=id,client,platform,account_id,status')
                result['raw_rows'] = raw_rows
                # Also try without status filter to see all rows
                google_rows = [r for r in (raw_rows or []) if r.get('platform') in ('google_ads','google_oauth')]
                result['google_rows'] = google_rows
            except Exception as e:
                result['raw_rows_error'] = str(e)
            # 2. Replicate exact _get_credential query so we can catch errors
            try:
                enc_ws2 = urllib.parse.quote(ws)
                cred_query = (
                    f'client_integrations?client=eq.{enc_ws2}&platform=eq.google_ads'
                    f'&select=id,account_id&status=eq.connected'
                )
                result['cred_query'] = cred_query
                cred_rows = _supabase_req('GET', cred_query)
                result['cred_rows'] = cred_rows
            except Exception as e:
                result['cred_query_error'] = str(e)
            # 3. _get_credential result
            try:
                acc_id, acc_tok = _get_credential(ws, 'google_ads')
                result['google_ads_account_id'] = acc_id
                result['google_ads_has_token']  = bool(acc_tok)
            except Exception as e:
                result['google_ads_error'] = str(e)
            # 3. OAuth token
            try:
                oauth_tok = google_get_access_token(ws)
                result['oauth_token_ok'] = bool(oauth_tok)
                result['oauth_token_len'] = len(oauth_tok) if oauth_tok else 0
            except Exception as e:
                result['oauth_error'] = str(e)
            result['developer_token_set']    = bool(GOOGLE_ADS_DEVELOPER_TOKEN)
            result['login_customer_id_set']  = bool(GOOGLE_ADS_LOGIN_CUSTOMER_ID)
            result['login_customer_id']      = GOOGLE_ADS_LOGIN_CUSTOMER_ID
            self._json(200, result)
        elif self.path == '/api/env-check':
            # Diagnostic — admin-only, shows WHICH vars are set (booleans only, no values)
            if not self._is_admin():
                self._json(403, {'error': 'Admin credentials required'}); return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_cors_headers()
            self.end_headers()
            check_keys = ['HQ_ADMIN_EMAIL','HQ_ADMIN_PASS','HQ_PARTNER_EMAIL','HQ_PARTNER_PASS',
                          'RESEND_API_KEY','NOTIFY_EMAIL','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET',
                          'STRIPE_PRICE_GROWTH','STRIPE_PRICE_PRO','PLATFORM_URL',
                          'HUBSPOT_TOKEN','SUPABASE_URL','SUPABASE_SERVICE_KEY',
                          'INTEGRATION_ENCRYPTION_KEY','CANVA_CLIENT_ID','SMTP_HOST']
            # Show both os.getenv (live) and _ENV (startup) to diagnose Railway injection
            self.wfile.write(json.dumps({
                'live':    {k: bool(os.getenv(k, ''))   for k in check_keys},
                'startup': {k: bool(_ENV.get(k, ''))    for k in check_keys},
                'all_env_keys': sorted([k for k in os.environ.keys() if not k.startswith('_')]),
            }).encode())
        elif self.path == '/api/agents':
            self._handle_agents_list()
        elif self.path.startswith('/api/memories'):
            self._handle_memories_list()
        elif self.path.startswith('/api/metrics'):
            self._handle_metrics_get()
        elif self.path.startswith('/api/integrations/list'):
            self._handle_integrations_list()
        elif self.path.startswith('/api/reports') and not self.path.startswith('/api/reports/save'):
            self._handle_reports_list()
        elif self.path.startswith('/api/portal'):
            self._handle_portal_get()
        elif self.path == '/api/workspaces':
            if not self._is_admin():
                self._json(403, {'error': 'Admin credentials required'}); return
            self._handle_workspaces_list()
        elif self.path == '/api/admin/migrate':
            if not self._is_admin():
                self._json(403, {'error': 'Admin credentials required'}); return
            self._handle_admin_migrate()
        elif self.path.startswith('/api/partner/clients'):
            self._handle_partner_clients()
        elif self.path.startswith('/api/partner/summary'):
            self._handle_partner_summary()
        elif self.path.startswith('/api/partner/verify-reset'):
            self._handle_partner_verify_reset()
        elif self.path.startswith('/api/partner/escalations'):
            self._handle_partner_escalations_get()
        elif self.path.startswith('/api/hq/messages'):
            self._handle_hq_messages_get()
        elif self.path.startswith('/api/escalation'):
            self._handle_workspace_escalations_get()
        elif self.path.startswith('/api/campaigns'):
            self._handle_campaigns_list()
        elif self.path.startswith('/api/campaign/updates'):
            self._handle_campaign_updates_get()
        elif self.path.startswith('/api/google/auth'):
            self._handle_google_auth()
        elif self.path.startswith('/api/google/callback'):
            self._handle_google_callback()
        elif self.path.startswith('/api/canva/auth'):
            self._handle_canva_auth()
        elif self.path.startswith('/api/canva/callback'):
            self._handle_canva_callback()
        elif self.path.startswith('/api/workspace/tracking-status'):
            self._handle_workspace_tracking_status_get()
        elif self.path.startswith('/api/brand-hub/prefill'):
            self._handle_brand_hub_prefill_get()
        elif self.path.startswith('/api/brand-hub'):
            self._handle_brand_hub_get()
        elif self.path.startswith('/api/crm/contacts'):
            self._handle_crm_contacts_get()
        elif self.path.startswith('/api/crm/activities'):
            self._handle_crm_activities_get()
        elif self.path.startswith('/api/reputation'):
            self._handle_reputation_get()
        elif self.path.startswith('/api/local-seo'):
            self._handle_local_seo_get()
        elif self.path.startswith('/api/social/accounts'):
            self._handle_social_accounts_get()
        elif self.path.startswith('/api/social/posts'):
            self._handle_social_posts_get()
        elif self.path.startswith('/api/social/auth/'):
            self._handle_social_auth_get()
        elif self.path.startswith('/api/social/callback/'):
            self._handle_social_callback_get()
        else:
            # Serve static files from working directory
            import os as _os
            # Strip query string
            static_path = self.path.split('?')[0].lstrip('/')
            if not static_path:
                static_path = 'index.html'
            full_path = _os.path.join(_os.getcwd(), static_path)
            if _os.path.isfile(full_path):
                ext = _os.path.splitext(full_path)[1].lower()
                mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
                        '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
                        '.json': 'application/json'}.get(ext, 'application/octet-stream')
                with open(full_path, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(data)))
                # Prevent CDN/browser caching of HTML files so deploys take effect immediately
                if ext == '.html':
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                    self.send_header('Pragma', 'no-cache')
                    self.send_header('Expires', '0')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

    def do_POST(self):
        ip = self._client_ip()
        _auth_paths = {'/api/hq/auth', '/api/portal/auth', '/api/workspace/auth',
                       '/api/partner/register', '/api/partner/forgot-password',
                       '/api/partner/reset-password', '/api/partner/verify-reset'}
        if self.path in _auth_paths:
            if not _login_limiter.allow(f'{ip}:{self.path}'):
                self._error(429, 'Too many requests — please wait before trying again.'); return
        if self.path in ('/api/agent', '/api/chain'):
            if not _agent_limiter.allow(ip):
                self._error(429, 'Rate limit exceeded — slow down your requests.'); return

        if self.path == '/api/agent':
            self._handle_single_agent()
        elif self.path == '/api/chain':
            self._handle_chain()
        elif self.path == '/api/agents/save':
            if not self._is_admin():
                self._json(403, {'error': 'Admin credentials required'}); return
            self._handle_agents_save()
        elif self.path == '/api/metrics/fetch':
            self._handle_metrics_fetch()
        elif self.path == '/api/memories/extract':
            self._handle_memories_extract()
        elif self.path == '/api/memories/add':
            self._handle_memories_add()
        elif self.path == '/api/memories/delete':
            self._handle_memories_delete()
        elif self.path == '/api/integrations/connect':
            self._handle_integrations_connect()
        elif self.path == '/api/integrations/disconnect':
            self._handle_integrations_disconnect()
        elif self.path == '/api/report/generate':
            self._handle_report_generate()
        elif self.path == '/api/reports/save':
            self._handle_reports_save()
        elif self.path == '/api/portal/auth':
            self._handle_portal_auth()
        elif self.path == '/api/portal/access':
            self._handle_portal_access_create()
        elif self.path == '/api/notify':
            self._handle_notify()
        elif self.path == '/api/notify/test':
            self._handle_notify_test()
        elif self.path == '/api/workspace/auth':
            self._handle_workspace_auth()
        elif self.path == '/api/workspace/activity':
            self._handle_workspace_activity()
        elif self.path == '/api/workspace/create':
            self._handle_workspace_create()
        elif self.path == '/api/hq/auth':
            self._handle_hq_auth()
        elif self.path == '/api/workspace/subscribe':
            self._handle_workspace_subscribe()
        elif self.path == '/api/stripe/webhook':
            self._handle_stripe_webhook()
        elif self.path == '/api/partner/invite':
            self._handle_partner_invite()
        elif self.path == '/api/partner/register':
            self._handle_partner_register()
        elif self.path == '/api/partner/forgot-password':
            self._handle_partner_forgot_password()
        elif self.path == '/api/partner/reset-password':
            self._handle_partner_reset_password()
        elif self.path == '/api/hq/message':
            self._handle_hq_message_post()
        elif self.path == '/api/workspace/resend-code':
            self._handle_workspace_resend_code()
        elif self.path == '/api/workspace/tracking-status':
            self._handle_workspace_tracking_status_post()
        elif self.path == '/api/campaign/request':
            self._handle_campaign_request()
        elif self.path == '/api/campaign/reply':
            self._handle_campaign_reply()
        elif self.path == '/api/campaign/retry-build':
            self._handle_campaign_retry_build()
        elif self.path == '/api/escalation':
            self._handle_escalation_create()
        elif self.path.startswith('/api/escalation/'):
            self._handle_escalation_update()
        elif self.path.startswith('/api/brand-hub'):
            self._handle_brand_hub_post()
        elif self.path == '/api/crm/contacts':
            self._handle_crm_contacts_post()
        elif self.path == '/api/crm/activities':
            self._handle_crm_activities_post()
        elif self.path == '/api/crm/ai-score':
            self._handle_crm_ai_score()
        elif self.path == '/api/reputation/reviews':
            self._handle_reputation_reviews_post()
        elif self.path == '/api/reputation/respond':
            self._handle_reputation_respond()
        elif self.path == '/api/reputation/request':
            self._handle_reputation_request()
        elif self.path == '/api/local-seo/nap':
            self._handle_local_seo_nap()
        elif self.path == '/api/local-seo/audit':
            self._handle_local_seo_audit()
        elif self.path == '/api/social/draft':
            self._handle_social_draft()
        elif self.path == '/api/social/posts':
            self._handle_social_posts_post()
        elif self.path == '/api/social/publish':
            self._handle_social_publish()
        else:
            self.send_response(404)
            self.end_headers()

    def do_PATCH(self):
        if self.path.startswith('/api/escalation/'):
            self._handle_escalation_update()
        elif self.path == '/api/local-seo/listing':
            self._handle_local_seo_listing_patch()
        elif self.path == '/api/social/posts':
            self._handle_social_posts_patch()
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path == '/api/crm/contacts':
            self._handle_crm_contacts_delete()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_single_agent(self):
        """Single agent call — with memory injection."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON')
            return

        agent_id = body.get('agentId', 'sarah')
        messages  = body.get('messages', [])
        context   = body.get('context', '')
        client    = body.get('client', 'General')   # for memory scoping

        system_prompt = AGENT_PROMPTS.get(agent_id)
        if not system_prompt:
            self._error(400, f'Unknown agent: {agent_id}')
            return

        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'ANTHROPIC_API_KEY not set.')
            return

        if context:
            system_prompt += f'\n\nCurrent context:\n{context}'

        # Inject relevant memories — silently enriches the prompt
        memories = fetch_agent_memories(agent_id, client)
        if memories:
            system_prompt = inject_memories(system_prompt, memories)
            print(f'  🧠 {len(memories)} memories injected for {agent_id}/{client}')

        try:
            text = call_anthropic(effective_key, system_prompt, messages)
            self._json(200, {'content': text, 'agent': agent_id, 'memories_used': len(memories)})
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f'  Anthropic API error {e.code}: {err_body}')
            self._error(e.code, f'Anthropic API error: {err_body}')
        except Exception as e:
            print(f'  Server error: {e}')
            self._error(500, str(e))

    def _handle_chain(self):
        """
        Multi-agent chain — runs steps sequentially, passing outputs forward.

        Request body:
        {
          "steps": [
            {
              "agentId": "sarah",
              "prompt": "...",
              "label": "Strategy Brief",       // optional, for display
              "outputKey": "strategy"          // key to store this output under
            },
            {
              "agentId": "cleo",
              "prompt": "...",
              "label": "Month 1 Posts",
              "contextFrom": ["strategy"],     // inject prior outputs as context
              "outputKey": "month1_posts"
            }
          ],
          "maxTokens": 2000                    // optional per-step token limit
        }

        Response:
        {
          "results": {
            "strategy": { "agent": "sarah", "label": "...", "content": "..." },
            "month1_posts": { "agent": "cleo", "label": "...", "content": "..." }
          },
          "order": ["strategy", "month1_posts"]
        }
        """
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON')
            return

        steps = body.get('steps', [])
        max_tokens = body.get('maxTokens', 2000)

        if not steps:
            self._error(400, 'No steps provided')
            return

        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'ANTHROPIC_API_KEY not set.')
            return

        results = {}
        order = []

        for i, step in enumerate(steps):
            agent_id   = step.get('agentId', 'sarah')
            prompt     = step.get('prompt', '')
            label      = step.get('label', f'Step {i+1}')
            output_key = step.get('outputKey', f'step_{i+1}')
            context_from = step.get('contextFrom', [])

            system_prompt = AGENT_PROMPTS.get(agent_id)
            if not system_prompt:
                self._error(400, f'Unknown agent: {agent_id}')
                return

            # Build context from prior step outputs
            if context_from:
                context_parts = []
                for key in context_from:
                    if key in results:
                        prior = results[key]
                        context_parts.append(
                            f"[{prior['label']} — from {prior['agent']}]\n{prior['content']}"
                        )
                if context_parts:
                    system_prompt += '\n\nContext from prior team members:\n\n' + '\n\n---\n\n'.join(context_parts)

            print(f'  Chain step {i+1}/{len(steps)}: {agent_id} → {label}')

            try:
                text = call_anthropic(
                    effective_key,
                    system_prompt,
                    [{'role': 'user', 'content': prompt}],
                    max_tokens=max_tokens
                )
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                print(f'  Anthropic API error {e.code}: {err_body}')
                self._error(e.code, f'Step "{label}" failed: {err_body}')
                return
            except Exception as e:
                print(f'  Step "{label}" error: {e}')
                self._error(500, f'Step "{label}" failed: {str(e)}')
                return

            results[output_key] = {
                'agent': agent_id,
                'label': label,
                'content': text,
            }
            order.append(output_key)

        self._json(200, {'results': results, 'order': order})

    # ── Agent management handlers ─────────────────────────────────────────────

    def _handle_agents_list(self):
        """Return all user-facing agent profiles (excludes internal agents)."""
        agents = [
            {'key': k, **v}
            for k, v in AGENT_PROFILES.items()
            if k not in _INTERNAL_AGENTS
        ]
        self._json(200, {'agents': agents})

    def _handle_agents_save(self):
        """Create or update an agent profile + hot-reload the Claude prompt."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        key    = (body.get('key')   or '').strip().lower().replace(' ', '_')
        name   = (body.get('name')  or '').strip()
        role   = (body.get('role')  or '').strip()
        skills = body.get('skills') or []
        extra  = (body.get('extraContext') or '').strip()
        custom = (body.get('systemPrompt') or '').strip()

        if not key or not name:
            self._error(400, 'key and name are required'); return

        prompt = custom or build_agent_prompt(name, role, skills, extra)

        try:
            if SUPABASE_URL and SUPABASE_SERVICE_KEY:
                # Check if agent exists in DB
                existing = _supabase_req('GET', f'agents?key=eq.{key}&select=id')
                if existing:
                    _supabase_req('PATCH', f'agents?key=eq.{key}', {
                        'name': name, 'role': role, 'skills': skills,
                        'extra_context': extra, 'system_prompt': prompt, 'active': True,
                    })
                else:
                    _supabase_req('POST', 'agents', {
                        'key': key, 'name': name, 'role': role, 'skills': skills,
                        'extra_context': extra, 'system_prompt': prompt, 'active': True,
                    })

            # Hot-reload in-memory — takes effect on next chat message instantly
            AGENT_PROMPTS[key]  = prompt
            AGENT_PROFILES[key] = {'name': name, 'role': role, 'skills': skills}

            print(f'  ✅ Agent hot-reloaded: {key} ({name}) | {len(skills)} skills')
            self._json(200, {
                'success': True, 'key': key,
                'message': f'{name} updated — Claude prompt live immediately',
            })
        except Exception as e:
            print(f'  Agent save error: {e}')
            self._error(500, str(e))

    # ── Analytics / Metrics handlers ─────────────────────────────────────────

    def _handle_metrics_get(self):
        """Return cached metrics for a client across all platforms."""
        params    = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        client    = params.get('client', [''])[0]
        days      = int(params.get('days', ['30'])[0])
        if not client:
            self._error(400, 'client param required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'metrics': []}); return
        try:
            rows = _supabase_req('GET',
                f'client_metrics?client=eq.{urllib.parse.quote(client)}'
                f'&days=eq.{days}&order=fetched_at.desc')
            # Return latest per platform
            seen, result = set(), []
            for r in rows:
                if r['platform'] not in seen:
                    seen.add(r['platform'])
                    result.append(r['metrics'])
            self._json(200, {'metrics': result})
        except Exception as e:
            self._error(500, str(e))

    def _handle_metrics_fetch(self):
        """Fetch fresh metrics for a client + platform (cache → real API → demo)."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        client   = (body.get('client')   or '').strip()
        platform = (body.get('platform') or '').strip()
        days     = int(body.get('days', 30))
        budget   = float(body.get('budget', 10000))

        if not client or not platform:
            self._error(400, 'client and platform required'); return

        data = fetch_platform_metrics(client, platform, days, budget)
        print(f'  📊 Metrics: {platform}/{client}/{days}d — demo={data.get("is_demo")}')
        self._json(200, data)

    # ── Memory handlers ───────────────────────────────────────────────────────

    def _handle_memories_list(self):
        """Return memories, optionally filtered by agent and/or client."""
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        agent_key = params.get('agentId', params.get('agent', ['']))[0]
        client    = params.get('client', [''])[0]
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'memories': []}); return
        try:
            path = 'agent_memories?order=importance.desc,created_at.desc&limit=100'
            if agent_key: path += f'&agent_key=eq.{agent_key}'
            if client:    path += f'&client=eq.{client}'
            rows = _supabase_req('GET', path)
            self._json(200, {'memories': rows or []})
        except Exception as e:
            self._error(500, str(e))

    def _handle_memories_extract(self):
        """Ask Claude to extract learnings from a conversation and save them."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        agent_key = (body.get('agentKey') or '').strip()
        client    = (body.get('client')   or 'General').strip()
        messages  = body.get('messages', [])

        if not agent_key or len(messages) < 4:
            # Not enough conversation to extract from
            self._json(200, {'memories': [], 'count': 0}); return

        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'API key not set'); return

        # Format conversation for Claude
        convo = '\n'.join(
            f'[{m.get("role","").upper()}]: {m.get("content","")[:400]}'
            for m in messages[-8:]   # last 8 msgs to keep tokens low
        )
        try:
            raw = call_anthropic(
                effective_key,
                AGENT_PROMPTS['memory_extractor'],
                [{'role': 'user', 'content':
                  f'Agent: {agent_key}\nClient: {client}\n\nConversation:\n{convo}'}],
                max_tokens=400,
            )
            cleaned  = raw.replace('```json','').replace('```','').strip()
            memories = json.loads(cleaned) if cleaned.startswith('[') else []
            if not isinstance(memories, list):
                memories = []

            saved = []
            for mem in memories[:3]:
                if not mem.get('content'):
                    continue
                row = {
                    'agent_key':   agent_key,
                    'client':      client,
                    'content':     mem['content'][:500],
                    'memory_type': mem.get('memory_type', 'insight'),
                    'importance':  min(5, max(1, int(mem.get('importance', 3)))),
                    'source':      'auto',
                }
                if SUPABASE_URL and SUPABASE_SERVICE_KEY:
                    rows = _supabase_req('POST', 'agent_memories', row)
                    saved.append(rows[0] if rows else row)
                else:
                    saved.append(row)

            print(f'  🧠 Extracted {len(saved)} memories  ({agent_key}/{client})')
            self._json(200, {'memories': saved, 'count': len(saved)})
        except Exception as e:
            print(f'  Memory extract error: {e}')
            self._json(200, {'memories': [], 'count': 0})  # soft fail

    def _handle_memories_add(self):
        """Manually add a memory."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        row = {
            'agent_key':   (body.get('agentKey') or '').strip(),
            'client':      (body.get('client')   or 'General').strip(),
            'content':     (body.get('content')  or '').strip(),
            'memory_type': body.get('memoryType', 'insight'),
            'importance':  min(5, max(1, int(body.get('importance', 3)))),
            'source':      'manual',
        }
        if not row['agent_key'] or not row['content']:
            self._error(400, 'agentKey and content required'); return
        try:
            rows = _supabase_req('POST', 'agent_memories', row)
            self._json(200, {'success': True, 'memory': rows[0] if rows else row})
        except Exception as e:
            self._error(500, str(e))

    def _handle_memories_delete(self):
        """Delete a memory by id."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        mid = body.get('id')
        if not mid:
            self._error(400, 'id required'); return
        try:
            _supabase_req('DELETE', f'agent_memories?id=eq.{mid}')
            self._json(200, {'success': True})
        except Exception as e:
            self._error(500, str(e))

    # ── Integration handlers ──────────────────────────────────────────────────

    def _handle_integrations_connect(self):
        """Encrypt credential → save metadata to client_integrations
           → save encrypted token to integration_credentials (service role).

        Special case: when platform='google_ads' and token='google_oauth',
        we save only the account_id (Customer ID) without re-encrypting the
        placeholder string — the actual token is already stored under google_oauth.
        """
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        client     = (body.get('client')    or '').strip()
        platform   = (body.get('platform')  or '').strip()
        account_id = (body.get('accountId') or '').strip()
        raw_token  = (body.get('token')     or '').strip()

        if not client or not platform or not raw_token:
            self._error(400, 'client, platform, and token are required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._error(500, 'SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env'); return

        # Google Ads via OAuth: token is a placeholder — upsert the account_id row
        is_google_oauth_ref = (platform == 'google_ads' and raw_token == 'google_oauth')

        try:
            if is_google_oauth_ref:
                # Upsert: update existing row if one exists, otherwise insert
                enc_client = urllib.parse.quote(client)
                existing = _supabase_req('GET',
                    f'client_integrations?client=eq.{enc_client}&platform=eq.google_ads&select=id')
                if existing:
                    # Only patch columns guaranteed to exist in the schema
                    patch = {'account_id': account_id, 'status': 'connected'}
                    try:
                        patch['last_synced'] = datetime.datetime.utcnow().isoformat()
                    except Exception:
                        pass
                    try:
                        _supabase_req('PATCH',
                            f'client_integrations?id=eq.{existing[0]["id"]}', patch)
                    except Exception:
                        # last_synced column may not exist — retry without it
                        _supabase_req('PATCH',
                            f'client_integrations?id=eq.{existing[0]["id"]}',
                            {'account_id': account_id, 'status': 'connected'})
                    integration_id = existing[0]['id']
                else:
                    rows = _supabase_req('POST', 'client_integrations', {
                        'client':     client,
                        'platform':   'google_ads',
                        'account_id': account_id,
                        'status':     'connected',
                    })
                    integration_id = rows[0]['id']
                print(f'  ✅ Google Ads Customer ID saved for {client}: {account_id}')
                self._json(200, {'success': True, 'id': integration_id, 'encrypted': False, 'masked': account_id})
                return
            # 1) Public metadata row — visible to frontend (no credentials)
            encrypted = encrypt_token(raw_token)
            rows = _supabase_req('POST', 'client_integrations', {
                'client':           client,
                'platform':         platform,
                'account_id':       account_id,
                'status':           'connected',
                'encrypted_token':  encrypted,
            })
            integration_id = rows[0]['id']
        except Exception as e:
            print(f'  Integration connect error (client_integrations): {e}')
            self._error(500, str(e)); return

        # 2) Encrypted credential in separate table (best-effort — table may not exist yet)
        enc_ok = False
        try:
            _supabase_req('POST', 'integration_credentials', {
                'integration_id':  str(integration_id),
                'platform':        platform,
                'encrypted_token': encrypted,
            })
            enc_ok = _FERNET_OK and bool(INTEGRATION_ENCRYPTION_KEY)
        except Exception as ce:
            print(f'  integration_credentials insert skipped (table may not exist yet): {ce}')

        masked = '●' * max(0, len(raw_token) - 4) + raw_token[-4:] if len(raw_token) >= 4 else '●●●●'
        print(f'  ✅ Integration saved: {platform} → {client} (AES-256={enc_ok})')
        self._json(200, {'success': True, 'id': integration_id,
                         'encrypted': enc_ok, 'masked': masked})

    def _handle_integrations_disconnect(self):
        """Delete integration by id — cascade removes encrypted credentials."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        iid = body.get('id')
        if not iid:
            self._error(400, 'id is required'); return

        try:
            _supabase_req('DELETE', f'client_integrations?id=eq.{iid}', service_role=True)
            print(f'  ✅ Integration disconnected: id={iid}')
            self._json(200, {'success': True})
        except Exception as e:
            print(f'  Integration disconnect error: {e}')
            self._error(500, str(e))

    # ── Portal handlers ───────────────────────────────────────────────────────

    def _handle_portal_auth(self):
        """Validate client portal email + access code."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        email = (body.get('email') or '').strip().lower()
        code  = (body.get('code')  or '').strip()
        if not email or not code:
            self._error(400, 'email and code required'); return

        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            # Dev mode: accept any code for demo
            self._json(200, {'client': 'Demo Client', 'email': email}); return

        try:
            rows = _supabase_req(
                'GET',
                f'portal_access?email=eq.{urllib.parse.quote(email)}'
                f'&access_code=eq.{code}&active=eq.true&select=client,email',
                service_role=True,
            )
            if not rows:
                self._error(401, 'Invalid email or access code'); return
            access = rows[0]
            # Record last login
            try:
                _supabase_req('PATCH',
                    f'portal_access?email=eq.{urllib.parse.quote(email)}&access_code=eq.{code}',
                    {'last_login': datetime.datetime.utcnow().isoformat()},
                    service_role=True)
            except Exception:
                pass
            print(f'  🔐 Portal login: {email} → {access["client"]}')
            self._json(200, {'client': access['client'], 'email': email})
        except Exception as e:
            self._error(500, str(e))

    def _handle_portal_get(self):
        """Generic portal GET — currently just returns 200 for health checks."""
        self._json(200, {'status': 'ok', 'portal': True})

    def _handle_portal_access_create(self):
        """Generate (or regenerate) portal access credentials for a client."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        client = (body.get('client') or '').strip()
        email  = (body.get('email')  or '').strip().lower()
        if not client or not email:
            self._error(400, 'client and email required'); return

        code = _generate_access_code(6)

        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                # Upsert — replace existing access for this client+email
                _supabase_req('DELETE',
                    f'portal_access?client=eq.{urllib.parse.quote(client)}&email=eq.{urllib.parse.quote(email)}',
                    service_role=True)
                _supabase_req('POST', 'portal_access', {
                    'client': client, 'email': email,
                    'access_code': code, 'active': True,
                }, service_role=True)
            except Exception as e:
                print(f'  Portal access DB error: {e}')

        # Send email notification if configured
        email_sent = False
        if body.get('send_email', False):
            subject = f'Your ClickPoint Portal Access — {client}'
            html = f"""<div style="font-family:-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:32px;">
                <div style="font-size:22px;font-weight:800;color:#1C3A2E;margin-bottom:4px;">✦ ClickPoint</div>
                <div style="font-size:18px;font-weight:700;color:#333;margin-bottom:20px;">Your client portal is ready</div>
                <p style="font-size:14px;color:#555;line-height:1.6;margin-bottom:24px;">
                  Hi, your ClickPoint Marketing portal is now set up. Log in to view your campaign performance, analytics, and monthly reports.
                </p>
                <div style="background:#F4F3EE;border-radius:14px;padding:20px 24px;margin-bottom:24px;">
                  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#999;margin-bottom:8px;">Your access code</div>
                  <div style="font-size:36px;font-weight:800;color:#1C3A2E;letter-spacing:8px;">{code}</div>
                  <div style="font-size:12px;color:#aaa;margin-top:8px;">Use this with your email address: {email}</div>
                </div>
                <div style="font-size:11px;color:#aaa;">ClickPoint Marketing · Confidential</div>
            </div>"""
            email_sent = _send_email(email, subject, html)

        print(f'  🔐 Portal access created: {client} / {email} / code={code}')
        self._json(200, {
            'client': client, 'email': email,
            'access_code': code, 'email_sent': email_sent,
        })

    # ── Notification handlers ─────────────────────────────────────────────────

    def _handle_notify(self):
        """Send a Slack + email notification for an event."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        event   = body.get('event', 'custom')
        client  = body.get('client', '')
        detail  = body.get('detail', '')
        webhook = body.get('webhook', '')
        email   = body.get('email', '')
        result  = _notify(event, client, detail, webhook, email)
        print(f'  🔔 Notify [{event}] {client}: slack={result["slack"]} email={result["email"]}')
        self._json(200, {**result, 'event': event})

    def _handle_notify_test(self):
        """Test a notification channel."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        channel = body.get('channel', 'slack')
        webhook = body.get('webhook', '')
        email   = body.get('email', '')

        if channel == 'slack':
            ok = _send_slack('✅ *ClickPoint test notification* — Slack alerts are working!', webhook)
            self._json(200, {'ok': ok, 'channel': 'slack'})
        elif channel == 'email':
            import smtplib, ssl as _ssl_diag
            smtp_host = os.getenv('SMTP_HOST', '') or SMTP_HOST
            smtp_user = os.getenv('SMTP_USER', '') or SMTP_USER
            smtp_pass = os.getenv('SMTP_PASS', '') or SMTP_PASS
            smtp_port = int(os.getenv('SMTP_PORT', '') or SMTP_PORT or 465)
            diag = {
                'smtp_host': smtp_host or '(not set)',
                'smtp_user': smtp_user or '(not set)',
                'smtp_pass_set': bool(smtp_pass),
                'smtp_port': smtp_port,
                'resend_set': bool(os.getenv('RESEND_API_KEY', '') or RESEND_API_KEY),
            }
            # Try SMTP_SSL and capture exact error
            smtp_error = None
            starttls_error = None
            if smtp_host and smtp_user and smtp_pass:
                try:
                    ctx = _ssl_diag.create_default_context()
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=8) as srv:
                        srv.login(smtp_user, smtp_pass)
                    diag['smtp_ssl_login'] = 'ok'
                except Exception as e:
                    smtp_error = str(e)
                    diag['smtp_ssl_error'] = smtp_error
                    try:
                        ctx2 = _ssl_diag.create_default_context()
                        with smtplib.SMTP(smtp_host, 587, timeout=8) as srv:
                            srv.ehlo(); srv.starttls(context=ctx2)
                            srv.login(smtp_user, smtp_pass)
                        diag['starttls_login'] = 'ok'
                    except Exception as e2:
                        starttls_error = str(e2)
                        diag['starttls_error'] = starttls_error
            ok = _send_email(
                email or NOTIFY_EMAIL,
                'ClickPoint — Test Notification',
                '<div style="font-family:sans-serif;padding:24px;"><b>✅ ClickPoint test email</b><p style="color:#555;margin-top:12px;">Email notifications are working correctly.</p></div>',
            )
            self._json(200, {'ok': ok, 'channel': 'email', 'diag': diag})
        else:
            self._error(400, 'channel must be slack or email')

    # ── Workspace handlers ────────────────────────────────────────────────────

    def _handle_workspace_subscribe(self):
        """Create a Stripe Checkout Session for a workspace subscription."""
        if not STRIPE_SECRET_KEY:
            self._json(200, {'error': 'stripe_not_configured'}); return

        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        plan         = body.get('plan', '')
        workspace_id = body.get('workspaceId', '')
        email        = body.get('email', '')
        company_name = body.get('companyName', '')

        price_map = {'growth': STRIPE_PRICE_GROWTH, 'pro': STRIPE_PRICE_PRO}
        price_id  = price_map.get(plan)
        if not price_id:
            self._error(400, 'Invalid plan or price not configured'); return

        success_url = f"{PLATFORM_URL}/workspace.html?w={workspace_id}&plan_success=1"
        cancel_url  = f"{PLATFORM_URL}/workspace.html"

        payload = json.dumps({
            'mode': 'subscription',
            'customer_email': email,
            'line_items': [{'price': price_id, 'quantity': 1}],
            'success_url': success_url,
            'cancel_url': cancel_url,
            'metadata': {'workspace_id': workspace_id, 'company_name': company_name, 'plan': plan},
            'subscription_data': {
                'trial_period_days': 14,
                'metadata': {'workspace_id': workspace_id, 'plan': plan},
            },
        }).encode()

        try:
            req = urllib.request.Request(
                'https://api.stripe.com/v1/checkout/sessions',
                data=payload,
                headers={
                    'Authorization': f'Bearer {STRIPE_SECRET_KEY}',
                    'Content-Type': 'application/json',
                },
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                session = json.loads(r.read())
            self._json(200, {'url': session['url'], 'sessionId': session['id']})
        except Exception as e:
            print(f'  Stripe error: {e}')
            self._error(500, 'Stripe checkout failed')

    def _handle_stripe_webhook(self):
        """Handle Stripe webhook events (payment succeeded → update plan in Supabase)."""
        import hmac as _hmac, hashlib as _hs

        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        sig_header = self.headers.get('Stripe-Signature', '')

        # Require signature when secret is configured — reject unsigned requests
        if STRIPE_WEBHOOK_SECRET and not sig_header:
            self._error(400, 'Missing Stripe-Signature header'); return

        # Verify webhook signature if secret is configured
        if STRIPE_WEBHOOK_SECRET and sig_header:
            try:
                parts = {p.split('=')[0]: p.split('=')[1] for p in sig_header.split(',')}
                ts    = parts.get('t', '')
                sig   = parts.get('v1', '')
                payload_to_sign = f"{ts}.{raw.decode()}"
                expected = _hmac.new(
                    STRIPE_WEBHOOK_SECRET.encode(),
                    payload_to_sign.encode(),
                    _hs.sha256,
                ).hexdigest()
                if not _hmac.compare_digest(expected, sig):
                    self._error(400, 'Invalid signature'); return
            except Exception:
                self._error(400, 'Signature verification failed'); return

        try:
            event = json.loads(raw)
        except Exception:
            self._error(400, 'Invalid JSON'); return

        event_type = event.get('type', '')
        obj        = event.get('data', {}).get('object', {})

        # ── Subscription activated / payment received ──────────────────────────
        if event_type == 'checkout.session.completed':
            metadata     = obj.get('metadata', {})
            workspace_id = metadata.get('workspace_id', '')
            company_name = metadata.get('company_name', workspace_id)
            plan         = metadata.get('plan', '')
            email        = obj.get('customer_email', '')

            # Update Supabase
            if workspace_id and plan and SUPABASE_URL and SUPABASE_SERVICE_KEY:
                try:
                    patch_url = f"{SUPABASE_URL}/rest/v1/workspace_access?workspace_id=eq.{workspace_id}"
                    urllib.request.urlopen(urllib.request.Request(
                        patch_url,
                        data=json.dumps({'plan': plan, 'subscription_active': True}).encode(),
                        headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                                 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                        method='PATCH'), timeout=6)
                    print(f'  ✅ Workspace {workspace_id} upgraded to {plan}')
                except Exception as e:
                    print(f'  Supabase plan update error: {e}')

            # Push to HubSpot
            if email and plan:
                _hubspot_update_subscription(email, company_name, plan, 'active')

        # ── Recurring payment succeeded ───────────────────────────────────────
        elif event_type == 'invoice.payment_succeeded':
            metadata     = obj.get('subscription_details', {}).get('metadata', {}) or obj.get('metadata', {})
            workspace_id = metadata.get('workspace_id', '')
            plan         = metadata.get('plan', '')
            email        = obj.get('customer_email', '')
            company_name = workspace_id.replace('-', ' ').title() if workspace_id else ''
            if email and plan:
                _hubspot_update_subscription(email, company_name, plan, 'active')

        # ── Subscription cancelled ────────────────────────────────────────────
        elif event_type == 'customer.subscription.deleted':
            metadata     = obj.get('metadata', {})
            workspace_id = metadata.get('workspace_id', '')
            plan         = metadata.get('plan', '')
            customer_id  = obj.get('customer', '')
            email        = _get_stripe_customer_email(customer_id)
            company_name = workspace_id.replace('-', ' ').title() if workspace_id else ''

            # Update Supabase
            if workspace_id and SUPABASE_URL and SUPABASE_SERVICE_KEY:
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"{SUPABASE_URL}/rest/v1/workspace_access?workspace_id=eq.{workspace_id}",
                        data=json.dumps({'subscription_active': False}).encode(),
                        headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                                 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                        method='PATCH'), timeout=6)
                    print(f'  ❌ Workspace {workspace_id} subscription cancelled')
                except Exception as e:
                    print(f'  Supabase cancel update error: {e}')

            # Push to HubSpot — demote lifecycle for re-marketing
            if email:
                _hubspot_update_subscription(email, company_name, plan or 'unknown', 'cancelled')

        # ── Payment failed ────────────────────────────────────────────────────
        elif event_type == 'invoice.payment_failed':
            metadata     = obj.get('subscription_details', {}).get('metadata', {}) or obj.get('metadata', {})
            plan         = metadata.get('plan', '')
            email        = obj.get('customer_email', '')
            workspace_id = metadata.get('workspace_id', '')
            company_name = workspace_id.replace('-', ' ').title() if workspace_id else ''
            if email:
                _hubspot_update_subscription(email, company_name, plan or 'unknown', 'payment_failed')

        self._json(200, {'received': True})

    # ── Escalations ──────────────────────────────────────────────────────────────

    def _handle_workspace_escalations_get(self):
        """GET /api/escalation?workspaceId=X — fetch escalations for a workspace."""
        import urllib.parse as _up
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        workspace_id = qs.get('workspaceId', '').strip()
        if not workspace_id:
            self._json(200, {'escalations': []}); return
        if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
            self._json(200, {'escalations': []}); return
        try:
            rows = _supabase_req('GET',
                f'cmd_escalations?workspace_id=eq.{_up.quote(workspace_id)}&order=created_at.desc&limit=50',
                service_role=True) or []
            self._json(200, {'escalations': rows})
        except Exception as e:
            print(f'  ⚠️  workspace escalations GET error: {e}')
            self._json(200, {'escalations': []})

    def _handle_partner_escalations_get(self):
        """GET /api/partner/escalations?partnerId=X&status=open|all"""
        import urllib.parse as _up
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        partner_id = qs.get('partnerId', '').strip()
        status     = qs.get('status', 'open')   # 'open' | 'all'
        if not partner_id:
            self._error(400, 'partnerId required'); return
        if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
            self._json(200, {'escalations': []}); return
        try:
            q = f'cmd_escalations?partner_id=eq.{_up.quote(partner_id)}&order=created_at.desc&limit=100'
            if status == 'open':
                q += '&resolved=eq.false'
            rows = _supabase_req('GET', q, service_role=True) or []
            self._json(200, {'escalations': rows})
        except Exception as e:
            print(f'  ⚠️  escalations GET error: {e}')
            self._json(200, {'escalations': []})

    def _handle_escalation_create(self):
        """POST /api/escalation — create escalation from workspace (client) or agent."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        partner_id    = body.get('partnerId', '').strip()
        workspace_id  = body.get('workspaceId', '').strip()
        client        = body.get('client', '').strip()
        title         = body.get('title', '').strip()
        desc          = body.get('body', '').strip()
        priority      = body.get('priority', 'MEDIUM').strip().upper()
        source        = body.get('source', 'client')   # 'client' | 'agent'
        campaign_name = body.get('campaignName', '').strip()
        raised_by     = body.get('raisedBy', 'client').strip()

        if not title or not workspace_id:
            self._error(400, 'title and workspaceId required'); return
        if priority not in ('HIGH', 'MEDIUM', 'LOW'):
            priority = 'MEDIUM'

        row = {
            'priority':      priority,
            'client':        client or workspace_id,
            'title':         title,
            'body':          desc,
            'raised_by':     raised_by,
            'raised_time':   'Just now',
            'source':        source,
            'partner_id':    partner_id or None,
            'workspace_id':  workspace_id,
            'campaign_name': campaign_name or None,
            'resolved':      False,
        }

        esc_id = None
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                result = _supabase_req('POST', 'cmd_escalations', row, service_role=True)
                if result and isinstance(result, list):
                    esc_id = result[0].get('id')
            except Exception as e:
                print(f'  ⚠️  escalation insert error: {e}')

        # Notify partner via email if we have their details
        if partner_id and SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                import urllib.parse as _up2
                pa = _supabase_req('GET',
                    f'partner_accounts?id=eq.{_up2.quote(str(partner_id))}&select=email,agency_name',
                    service_role=True)
                if pa and pa[0].get('email'):
                    p_email = pa[0]['email']
                    agency  = pa[0].get('agency_name', 'Your agency')
                    _notify('escalation', client=client or workspace_id,
                            detail=f'<strong>{title}</strong><br>{desc[:300]}',
                            email=p_email)
            except Exception as e:
                print(f'  ⚠️  escalation notify error: {e}')

        print(f'  🚨 Escalation created: [{priority}] "{title}" ws={workspace_id} src={source}')
        self._json(201, {'ok': True, 'id': esc_id})

    def _handle_escalation_update(self):
        """PATCH /api/escalation/:id — resolve and/or respond."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        import re as _re2
        m = _re2.search(r'/api/escalation/(\d+)', self.path)
        if not m:
            self._error(400, 'Invalid escalation id'); return
        esc_id = m.group(1)

        patch = {}
        if 'resolved' in body:
            patch['resolved'] = bool(body['resolved'])
        if 'response' in body and body['response']:
            patch['response']     = str(body['response']).strip()
            patch['responded_at'] = datetime.datetime.utcnow().isoformat()

        if not patch:
            self._error(400, 'Nothing to update'); return

        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                import urllib.parse as _up3
                _supabase_req('PATCH',
                    f'cmd_escalations?id=eq.{_up3.quote(esc_id)}',
                    patch, service_role=True)
            except Exception as e:
                print(f'  ⚠️  escalation PATCH error: {e}')
                self._error(500, 'Update failed'); return

        action = 'resolved' if patch.get('resolved') else 'responded'
        print(f'  ✅ Escalation #{esc_id} {action}')
        self._json(200, {'ok': True})

    def _handle_partner_invite(self):
        """Send a branded onboarding email to a new client and a copy to the partner."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        client_name    = body.get('clientName', '').strip()
        client_email   = body.get('email', '').strip()
        workspace_id   = body.get('workspaceId', '').strip()
        access_code    = body.get('accessCode', '').strip()
        plan           = body.get('plan', 'growth').strip()
        workspace_link = body.get('workspaceLink', '').strip()
        agency_name    = body.get('agencyName', 'ClickPoint').strip() or 'ClickPoint'
        agency_color   = body.get('agencyColor', '#1C3A2E').strip() or '#1C3A2E'
        agency_logo    = body.get('agencyLogo', '').strip()
        partner_email  = body.get('partnerEmail', '').strip()

        if not client_name or not client_email or not workspace_id or not access_code:
            self._error(400, 'clientName, email, workspaceId and accessCode required'); return

        plan_label = {'starter': 'Starter (Free)', 'growth': 'Growth — $299/mo AUD', 'pro': 'Pro — $599/mo AUD', 'agency': 'Agency Managed'}.get(plan, plan.title())
        logo_html  = f'<img src="{agency_logo}" alt="{agency_name}" style="height:28px;width:auto;display:block;margin-bottom:20px;">' if agency_logo else f'<div style="font-size:18px;font-weight:800;color:{agency_color};margin-bottom:20px;">{agency_name}</div>'

        # ── Client invite email ────────────────────────────────────────────────
        client_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F5F4EF;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F4EF;padding:40px 20px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

  <!-- Header -->
  <tr><td style="background:{agency_color};padding:28px 36px;">
    {logo_html}
    <div style="font-size:22px;font-weight:800;color:#ffffff;margin-bottom:6px;">Your marketing workspace is ready</div>
    <div style="font-size:14px;color:rgba(255,255,255,0.65);">Sign in to start managing your campaigns</div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:32px 36px;">
    <p style="font-size:15px;color:#444;line-height:1.7;margin:0 0 24px;">Hi {client_name},</p>
    <p style="font-size:15px;color:#444;line-height:1.7;margin:0 0 24px;">
      Your dedicated marketing workspace has been set up by <strong>{agency_name}</strong>.
      Use the access details below to sign in and start exploring your campaigns, AI insights, and performance reports.
    </p>

    <!-- Credentials box -->
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0E1F17;border-radius:14px;padding:24px;margin-bottom:28px;">
      <tr><td>
        <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-bottom:16px;">Your Access Details</div>
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.45);padding:6px 0;width:40%;">Workspace ID</td>
            <td style="font-size:13px;font-weight:700;color:#ffffff;font-family:monospace;padding:6px 0;">{workspace_id}</td>
          </tr>
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.45);padding:6px 0;">Email</td>
            <td style="font-size:13px;font-weight:700;color:#ffffff;padding:6px 0;">{client_email}</td>
          </tr>
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.45);padding:6px 0;vertical-align:middle;">Access Code</td>
            <td style="padding:6px 0;">
              <span style="font-size:26px;font-weight:800;color:#D4622A;letter-spacing:0.15em;font-family:monospace;">{access_code}</span>
            </td>
          </tr>
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.45);padding:6px 0;">Plan</td>
            <td style="font-size:13px;font-weight:600;color:rgba(255,255,255,0.8);padding:6px 0;">{plan_label}</td>
          </tr>
        </table>
      </td></tr>
    </table>

    <!-- CTA -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
      <tr><td align="center">
        <a href="{workspace_link}" style="display:inline-block;background:{agency_color};color:#ffffff;text-decoration:none;padding:15px 36px;border-radius:12px;font-weight:700;font-size:15px;letter-spacing:0.01em;">Open My Workspace →</a>
      </td></tr>
    </table>

    <p style="font-size:13px;color:#999;line-height:1.6;margin:0 0 8px;">Keep this email safe — you'll need your access code each time you sign in.</p>
    <p style="font-size:13px;color:#999;line-height:1.6;margin:0;">Questions? Reply to this email or reach out to your {agency_name} account manager.</p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#F5F4EF;padding:20px 36px;border-top:1px solid #E2E1DB;">
    <div style="font-size:11px;color:#bbb;text-align:center;">
      Powered by <strong style="color:#888;">ClickPoint</strong> · {agency_name}
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

        client_sent = _send_email(client_email, f'Your {agency_name} workspace is ready — sign in now', client_html)

        # ── Partner notification email ─────────────────────────────────────────
        partner_sent = False
        if partner_email:
            partner_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#F5F4EF;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F4EF;padding:40px 20px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
  <tr><td style="background:#1C3A2E;padding:24px 32px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.5);margin-bottom:8px;">ClickPoint Partner Portal</div>
    <div style="font-size:20px;font-weight:800;color:#ffffff;">✅ New client onboarded</div>
  </td></tr>
  <tr><td style="padding:28px 32px;">
    <p style="font-size:14px;color:#555;line-height:1.6;margin:0 0 20px;">A workspace invite was just sent to a new client from your partner portal.</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F4EF;border-radius:12px;padding:20px;margin-bottom:24px;">
      <tr><td>
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:11px;color:#999;padding:5px 0;width:40%;">Client name</td>
            <td style="font-size:13px;font-weight:700;color:#1A1A1A;padding:5px 0;">{client_name}</td>
          </tr>
          <tr>
            <td style="font-size:11px;color:#999;padding:5px 0;">Email sent to</td>
            <td style="font-size:13px;font-weight:600;color:#1A1A1A;padding:5px 0;">{client_email}</td>
          </tr>
          <tr>
            <td style="font-size:11px;color:#999;padding:5px 0;">Workspace</td>
            <td style="font-size:13px;color:#1A1A1A;font-family:monospace;padding:5px 0;">{workspace_id}</td>
          </tr>
          <tr>
            <td style="font-size:11px;color:#999;padding:5px 0;">Access code</td>
            <td style="font-size:20px;font-weight:800;color:#D4622A;font-family:monospace;padding:5px 0;letter-spacing:0.1em;">{access_code}</td>
          </tr>
          <tr>
            <td style="font-size:11px;color:#999;padding:5px 0;">Plan</td>
            <td style="font-size:13px;color:#1A1A1A;padding:5px 0;">{plan_label}</td>
          </tr>
        </table>
      </td></tr>
    </table>
    <a href="{workspace_link}" style="display:inline-block;background:#1C3A2E;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:10px;font-weight:700;font-size:13px;">Open client workspace →</a>
  </td></tr>
  <tr><td style="background:#F5F4EF;padding:16px 32px;border-top:1px solid #E2E1DB;">
    <div style="font-size:11px;color:#bbb;text-align:center;">ClickPoint Partner Portal · Automated notification</div>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""
            partner_sent = _send_email(partner_email, f'[ClickPoint] New client onboarded — {client_name}', partner_html)

        print(f'  📧 Invite sent → client:{client_sent} partner:{partner_sent} | {client_name} <{client_email}>')
        self._json(200, {
            'ok': True,
            'clientEmailSent': client_sent,
            'partnerEmailSent': partner_sent,
        })

    def _handle_partner_register(self):
        """Self-serve partner registration — creates account and sends welcome email."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        agency_name = body.get('agencyName', '').strip()
        name        = body.get('name', '').strip()
        email       = body.get('email', '').strip().lower()
        website     = body.get('website', '').strip()
        password    = body.get('password', '')

        if not agency_name or not name or not email or not password:
            self._error(400, 'agencyName, name, email and password are required'); return
        if len(password) < 8:
            self._json(200, {'ok': False, 'error': 'Password must be at least 8 characters'}); return

        import hashlib, time as _time
        partner_id = 'pt-' + hashlib.md5(email.encode()).hexdigest()[:8]
        initials   = ''.join(p[0].upper() for p in name.split()[:2]) or 'PA'
        ts         = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        # ── Welcome email to new partner ──────────────────────────────────────
        welcome_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#F5F4EF;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F4EF;padding:40px 20px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
  <tr><td style="background:#1C3A2E;padding:28px 36px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.5);margin-bottom:8px;">ClickPoint Partner Network</div>
    <div style="font-size:22px;font-weight:800;color:#ffffff;margin-bottom:6px;">Welcome to ClickPoint Partners 🎉</div>
    <div style="font-size:14px;color:rgba(255,255,255,0.6);">Your agency partner account is active</div>
  </td></tr>
  <tr><td style="padding:32px 36px;">
    <p style="font-size:15px;color:#444;line-height:1.7;margin:0 0 20px;">Hi {name},</p>
    <p style="font-size:15px;color:#444;line-height:1.7;margin:0 0 24px;">
      Thank you for joining the ClickPoint partner network. Your account for <strong>{agency_name}</strong> is now active and ready to use.
      Sign in to the partner portal to start onboarding clients, track performance, and earn your 20% commission.
    </p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0E1F17;border-radius:14px;padding:24px;margin-bottom:28px;">
      <tr><td>
        <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-bottom:14px;">Your Partner Account</div>
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.4);padding:6px 0;width:40%;">Email</td>
            <td style="font-size:13px;font-weight:700;color:#ffffff;padding:6px 0;">{email}</td>
          </tr>
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.4);padding:6px 0;">Partner ID</td>
            <td style="font-size:13px;font-weight:700;color:#D4622A;font-family:monospace;padding:6px 0;">{partner_id}</td>
          </tr>
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.4);padding:6px 0;">Agency</td>
            <td style="font-size:13px;color:rgba(255,255,255,0.8);padding:6px 0;">{agency_name}</td>
          </tr>
          <tr>
            <td style="font-size:12px;color:rgba(255,255,255,0.4);padding:6px 0;">Commission</td>
            <td style="font-size:13px;font-weight:700;color:#30D158;padding:6px 0;">20% recurring</td>
          </tr>
        </table>
      </td></tr>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr><td align="center">
        <a href="https://platform.clickpointconsulting.com.au/partner.html" style="display:inline-block;background:#D4622A;color:#ffffff;text-decoration:none;padding:15px 36px;border-radius:12px;font-weight:700;font-size:15px;">Open Partner Portal →</a>
      </td></tr>
    </table>
    <p style="font-size:13px;color:#999;line-height:1.6;margin:0;">
      A member of the ClickPoint team will review your application and reach out shortly with onboarding details and access to commission reporting.
    </p>
  </td></tr>
  <tr><td style="background:#F5F4EF;padding:20px 36px;border-top:1px solid #E2E1DB;">
    <div style="font-size:11px;color:#bbb;text-align:center;">ClickPoint Consulting · Partner Network</div>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

        email_sent = _send_email(email, 'Welcome to ClickPoint Partners — your account is active', welcome_html)

        # ── Notify HQ ─────────────────────────────────────────────────────────
        notify_email = _ENV.get('NOTIFY_EMAIL', '')
        if notify_email:
            notify_html = f"""<p>New partner self-registration at {ts}:</p>
<ul>
  <li><strong>Name:</strong> {name}</li>
  <li><strong>Agency:</strong> {agency_name}</li>
  <li><strong>Email:</strong> {email}</li>
  <li><strong>Website:</strong> {website or '—'}</li>
  <li><strong>Partner ID:</strong> {partner_id}</li>
</ul>"""
            _send_email(notify_email, f'[ClickPoint] New partner registration — {agency_name}', notify_html)

        # ── Push to HubSpot ───────────────────────────────────────────────────
        name_parts = name.split(None, 1)
        _push_to_hubspot(
            contact_props={
                'email':          email,
                'firstname':      name_parts[0] if name_parts else '',
                'lastname':       name_parts[1] if len(name_parts) > 1 else '',
                'company':        agency_name,
                'website':        website,
                'lifecyclestage': 'lead',
                'hs_lead_status': 'NEW',
            },
            company_props={
                'name':    agency_name,
                'website': website,
                'type':    'PARTNER',
            },
            note=f'Partner registration — ID: {partner_id} | Registered: {ts}',
        )

        # ── Save credentials to Supabase ─────────────────────────────────────
        sb_url = os.getenv('SUPABASE_URL', '') or SUPABASE_URL
        sb_key = os.getenv('SUPABASE_SERVICE_KEY', '') or SUPABASE_SERVICE_KEY
        if sb_url and sb_key:
            try:
                pw_hash = _hash_password(password, email)
                row = {
                    'partner_id':    partner_id,
                    'email':         email,
                    'password_hash': pw_hash,
                    'name':          name,
                    'agency_name':   agency_name,
                    'website':       website,
                }
                req = urllib.request.Request(
                    f'{sb_url}/rest/v1/partner_accounts',
                    data=json.dumps(row).encode(),
                    headers={
                        'apikey':        sb_key,
                        'Authorization': f'Bearer {sb_key}',
                        'Content-Type':  'application/json',
                        'Prefer':        'return=minimal,resolution=merge-duplicates',
                    },
                    method='POST',
                )
                urllib.request.urlopen(req, timeout=6)
                print(f'  ✅ Partner credentials saved to Supabase: {email}')
            except Exception as e:
                print(f'  ⚠️  Supabase partner save error: {e}')

        print(f'  🤝 Partner registered: {name} <{email}> ({agency_name}) — id:{partner_id} email_sent:{email_sent}')
        self._json(200, {
            'ok': True,
            'partnerId': partner_id,
            'emailSent': email_sent,
        })

    def _handle_partner_forgot_password(self):
        """Send a real password-reset link to a partner email (token stored in DB)."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        email = body.get('email', '').strip().lower()
        if not email:
            self._json(200, {'ok': True}); return  # Silent

        # Verify this email has a partner account before issuing a token
        account_exists = False
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                import urllib.parse as _up_fp
                qurl = (f"{SUPABASE_URL}/rest/v1/partner_accounts"
                        f"?email=eq.{_up_fp.quote(email)}&active=eq.true&select=id&limit=1")
                req = urllib.request.Request(qurl, headers={
                    'apikey': SUPABASE_SERVICE_KEY,
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                })
                with urllib.request.urlopen(req, timeout=6) as r:
                    rows = json.loads(r.read())
                account_exists = bool(rows)
            except Exception as ex:
                print(f'  ⚠️  forgot-password DB check: {ex}')
        else:
            account_exists = True  # Dev mode — always allow

        if account_exists and SUPABASE_URL and SUPABASE_SERVICE_KEY:
            import secrets as _sec
            import datetime as _dt
            token = _sec.token_urlsafe(32)
            expires = (_dt.datetime.utcnow() + _dt.timedelta(hours=2)).isoformat() + 'Z'
            try:
                import urllib.parse as _up_fp2
                # Expire any existing unused tokens for this email
                del_url = (f"{SUPABASE_URL}/rest/v1/partner_reset_tokens"
                           f"?email=eq.{_up_fp2.quote(email)}&used=eq.false")
                del_req = urllib.request.Request(del_url, method='DELETE', headers={
                    'apikey': SUPABASE_SERVICE_KEY,
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                    'Prefer': 'return=minimal',
                })
                urllib.request.urlopen(del_req, timeout=5)
                # Store new token
                _supabase_req('POST', 'partner_reset_tokens', {
                    'email': email, 'token': token, 'expires_at': expires
                }, service_role=True)
            except Exception as ex:
                print(f'  ⚠️  forgot-password token store: {ex}')
                self._json(200, {'ok': True}); return

            base_url = os.getenv('APP_BASE_URL', 'https://platform.clickpointconsulting.com.au')
            reset_link = f"{base_url}/partner.html?action=reset&token={token}"
            reset_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#F5F4EF;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F4EF;padding:40px 20px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
  <tr><td style="background:#1C3A2E;padding:28px 36px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.5);margin-bottom:8px;">ClickPoint Partner Network</div>
    <div style="font-size:22px;font-weight:800;color:#ffffff;margin-bottom:6px;">Reset Your Password</div>
  </td></tr>
  <tr><td style="padding:32px 36px;">
    <p style="font-size:15px;color:#444;line-height:1.7;margin:0 0 20px;">Hi,</p>
    <p style="font-size:15px;color:#444;line-height:1.7;margin:0 0 24px;">
      We received a password reset request for the partner account associated with <strong>{email}</strong>.
      Click the button below to set a new password — this link expires in <strong>2 hours</strong>.
    </p>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr><td align="center">
        <a href="{reset_link}" style="display:inline-block;background:#D4622A;color:#ffffff;text-decoration:none;padding:15px 36px;border-radius:12px;font-weight:700;font-size:15px;">Reset Password →</a>
      </td></tr>
    </table>
    <p style="font-size:13px;color:#999;line-height:1.6;">
      If the button doesn't work, copy this link:<br>
      <a href="{reset_link}" style="color:#D4622A;word-break:break-all;">{reset_link}</a>
    </p>
    <p style="font-size:13px;color:#aaa;line-height:1.6;margin-top:20px;">
      If you didn't request this, you can safely ignore this email — your account remains secure.
    </p>
  </td></tr>
  <tr><td style="background:#F5F4EF;padding:20px 36px;border-top:1px solid #E2E1DB;">
    <div style="font-size:11px;color:#bbb;text-align:center;">ClickPoint Consulting · Partner Network</div>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""
            _send_email(email, 'ClickPoint — Reset your partner portal password', reset_html)
            print(f'  🔑 Partner reset token issued for {email}')
        elif not account_exists:
            print(f'  ⚠️  forgot-password: no account for {email} — silent skip')

        self._json(200, {'ok': True})

    def _handle_partner_verify_reset(self):
        """GET /api/partner/verify-reset?token=XXX — validate a reset token."""
        import urllib.parse as _up_vr
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = qs.get('token', [None])[0]
        if not token:
            self._json(200, {'valid': False, 'error': 'Missing token'}); return

        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'valid': True, 'demo': True}); return

        try:
            import datetime as _dt
            qurl = (f"{SUPABASE_URL}/rest/v1/partner_reset_tokens"
                    f"?token=eq.{_up_vr.quote(token)}&used=eq.false&select=email,expires_at&limit=1")
            req = urllib.request.Request(qurl, headers={
                'apikey': SUPABASE_SERVICE_KEY,
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                rows = json.loads(r.read())
            if not rows:
                self._json(200, {'valid': False, 'error': 'Invalid or expired token'}); return
            row = rows[0]
            expires_at = row.get('expires_at', '')
            try:
                exp = _dt.datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if exp < _dt.datetime.now(_dt.timezone.utc):
                    self._json(200, {'valid': False, 'error': 'Token has expired'}); return
            except Exception:
                pass
            self._json(200, {'valid': True, 'email': row.get('email', '')})
        except Exception as ex:
            print(f'  ⚠️  verify-reset error: {ex}')
            self._json(200, {'valid': False, 'error': 'Server error'})

    def _handle_partner_reset_password(self):
        """POST /api/partner/reset-password — set a new password using a valid token."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        token       = body.get('token', '').strip()
        new_password = body.get('password', '').strip()

        if not token or not new_password:
            self._json(200, {'ok': False, 'error': 'Token and password required'}); return
        if len(new_password) < 8:
            self._json(200, {'ok': False, 'error': 'Password must be at least 8 characters'}); return

        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'ok': True, 'demo': True}); return

        import urllib.parse as _up_rp
        import datetime as _dt
        try:
            # Validate token
            qurl = (f"{SUPABASE_URL}/rest/v1/partner_reset_tokens"
                    f"?token=eq.{_up_rp.quote(token)}&used=eq.false&select=id,email,expires_at&limit=1")
            req = urllib.request.Request(qurl, headers={
                'apikey': SUPABASE_SERVICE_KEY,
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                rows = json.loads(r.read())
            if not rows:
                self._json(200, {'ok': False, 'error': 'Invalid or expired token'}); return
            row = rows[0]
            try:
                exp = _dt.datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))
                if exp < _dt.datetime.now(_dt.timezone.utc):
                    self._json(200, {'ok': False, 'error': 'Token has expired'}); return
            except Exception:
                pass

            email    = row['email']
            token_id = row['id']
            new_hash = _hash_password(new_password, email)

            # Update partner_accounts password
            pu_url = (f"{SUPABASE_URL}/rest/v1/partner_accounts"
                      f"?email=eq.{_up_rp.quote(email)}")
            pu_req = urllib.request.Request(pu_url,
                data=json.dumps({'password_hash': new_hash}).encode(),
                headers={'apikey': SUPABASE_SERVICE_KEY,
                         'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                         'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                method='PATCH')
            urllib.request.urlopen(pu_req, timeout=6)

            # Mark token as used
            tu_url = f"{SUPABASE_URL}/rest/v1/partner_reset_tokens?id=eq.{token_id}"
            tu_req = urllib.request.Request(tu_url,
                data=json.dumps({'used': True}).encode(),
                headers={'apikey': SUPABASE_SERVICE_KEY,
                         'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                         'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                method='PATCH')
            urllib.request.urlopen(tu_req, timeout=5)

            print(f'  🔑 Partner password reset complete for {email}')
            self._json(200, {'ok': True})
        except Exception as ex:
            print(f'  ⚠️  reset-password error: {ex}')
            self._json(200, {'ok': False, 'error': 'Server error'})

    def _handle_workspace_resend_code(self):
        """Resend a client's access code to their registered email."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        email = body.get('email', '').strip().lower()
        if not email:
            self._json(200, {'ok': True}); return

        code = None
        workspace_id = None
        company_name = None

        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                import urllib.parse as _up
                qurl = f"{SUPABASE_URL}/rest/v1/workspace_access?email=eq.{_up.quote(email)}&select=workspace_id,company_name,access_code&order=created_at.desc&limit=1"
                req = urllib.request.Request(qurl, headers={
                    'apikey': SUPABASE_SERVICE_KEY,
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                })
                with urllib.request.urlopen(req, timeout=6) as r:
                    rows = json.loads(r.read())
                if rows:
                    code         = rows[0].get('access_code')
                    workspace_id = rows[0].get('workspace_id')
                    company_name = rows[0].get('company_name', workspace_id)
            except Exception as e:
                print(f'  ⚠️  resend-code Supabase error: {e}')

        if code and workspace_id:
            portal_link = f"https://platform.clickpointconsulting.com.au/workspace.html?w={workspace_id}"
            email_html = f"""<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
<div style="font-size:22px;font-weight:800;color:#1C3A2E;margin-bottom:4px;">ClickPoint</div>
<div style="font-size:14px;color:#999;margin-bottom:28px;">Your access code</div>
<h2 style="font-size:20px;color:#1A1A1A;font-weight:700;margin-bottom:8px;">Here's your workspace access code</h2>
<p style="color:#555;font-size:14px;line-height:1.6;margin-bottom:20px;">As requested, here are your sign-in details for the <strong>{company_name}</strong> workspace.</p>
<div style="background:#F4F3EE;border-radius:12px;padding:20px;margin-bottom:20px;">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:8px;">Your Access Details</div>
  <div style="font-size:14px;color:#1A1A1A;margin-bottom:4px;">Workspace ID: <strong>{workspace_id}</strong></div>
  <div style="font-size:14px;color:#1A1A1A;margin-bottom:4px;">Email: <strong>{email}</strong></div>
  <div style="font-size:14px;color:#1A1A1A;">Access Code: <strong style="font-size:26px;letter-spacing:0.2em;">{code}</strong></div>
</div>
<a href="{portal_link}" style="display:block;background:#1C3A2E;color:#fff;text-decoration:none;padding:14px;border-radius:10px;text-align:center;font-weight:700;font-size:15px;margin-bottom:24px;">Open My Workspace →</a>
<p style="font-size:12px;color:#999;">Didn't request this? You can safely ignore this email.</p>
</div>"""
            _send_email(email, f'Your ClickPoint access code — {company_name}', email_html)
            print(f'  🔑 Resent access code to {email} for workspace {workspace_id}')
        else:
            print(f'  ⚠️  resend-code: no workspace found for {email}')

        self._json(200, {'ok': True})  # Always return ok — don't reveal if email exists

    def _handle_hq_auth(self):
        """Authenticate an Agency HQ user (superadmin or partner)."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        email    = body.get('email', '').strip().lower()
        password = body.get('password', '').strip()

        if not email or not password:
            self._json(200, {'success': False, 'error': 'Email and password are required'}); return

        _admin_emails = {HQ_ADMIN_EMAIL.lower()} if HQ_ADMIN_EMAIL else set()
        _admin_passes = {HQ_ADMIN_PASS} if HQ_ADMIN_PASS else set()
        _env_email    = os.getenv('HQ_ADMIN_EMAIL', '')
        _env_pass     = os.getenv('HQ_ADMIN_PASS',  '')
        if _env_email: _admin_emails.add(_env_email.lower())
        if _env_pass:  _admin_passes.add(_env_pass)
        _pt_email     = os.getenv('HQ_PARTNER_EMAIL', '') or HQ_PARTNER_EMAIL
        _pt_pass      = os.getenv('HQ_PARTNER_PASS',  '') or HQ_PARTNER_PASS

        if email in _admin_emails and password in _admin_passes:
            self._json(200, {
                'success': True, 'role': 'superadmin',
                'name': 'ClickPoint Admin', 'initials': 'CP',
                'email': email, 'partnerId': None,
            }); return

        # Partner check
        if _pt_email and _pt_pass:
            if email == _pt_email.lower() and password == _pt_pass:
                self._json(200, {
                    'success': True, 'role': 'partner',
                    'name': 'Agency Partner', 'initials': 'AP',
                    'email': email, 'partnerId': 'partner-demo',
                }); return
        # ── Supabase partner_accounts lookup ─────────────────────────────────
        sb_url = os.getenv('SUPABASE_URL', '') or SUPABASE_URL
        sb_key = os.getenv('SUPABASE_SERVICE_KEY', '') or SUPABASE_SERVICE_KEY
        if sb_url and sb_key:
            try:
                import urllib.parse as _up
                qurl = (f'{sb_url}/rest/v1/partner_accounts'
                        f'?email=eq.{_up.quote(email)}&active=eq.true&select=*&limit=1')
                req = urllib.request.Request(qurl, headers={
                    'apikey':        sb_key,
                    'Authorization': f'Bearer {sb_key}',
                })
                with urllib.request.urlopen(req, timeout=6) as r:
                    rows = json.loads(r.read())
                if rows:
                    row = rows[0]
                    expected_hash = _hash_password(password, email)
                    if row.get('password_hash') == expected_hash:
                        name        = row.get('name', 'Partner')
                        agency_name = row.get('agency_name', '')
                        partner_id  = row.get('partner_id', '')
                        website     = row.get('website', '')
                        initials    = ''.join(p[0].upper() for p in name.split()[:2]) or 'PA'
                        self._json(200, {
                            'success':   True,
                            'role':      'partner',
                            'name':      name,
                            'initials':  initials,
                            'email':     email,
                            'partnerId': partner_id,
                            'agencyName': agency_name,
                            'website':   website,
                            'createdAt': row.get('created_at', ''),
                        }); return
            except Exception as e:
                print(f'  ⚠️  Supabase partner auth error: {e}')

        self._json(200, {'success': False, 'error': 'Invalid email or password'})

    # ── Partner portal endpoints ──────────────────────────────────────────────

    _PARTNER_DEMO_CLIENTS = [
        {'id': 'apex-dynamics',    'name': 'Apex Dynamics',    'health': 8.2, 'mrr': 4200,  'status': 'active',   'lastActive': '2 hours ago',   'campaigns': 5},
        {'id': 'orbital-labs',     'name': 'Orbital Labs',     'health': 7.1, 'mrr': 3100,  'status': 'active',   'lastActive': '1 day ago',     'campaigns': 4},
        {'id': 'crestwave-foods',  'name': 'Crestwave Foods',  'health': 9.0, 'mrr': 5800,  'status': 'active',   'lastActive': '3 hours ago',   'campaigns': 7},
        {'id': 'dataforge-ai',     'name': 'DataForge AI',     'health': 6.8, 'mrr': 2900,  'status': 'active',   'lastActive': '5 hours ago',   'campaigns': 3},
        {'id': 'helix-biomedical', 'name': 'Helix Biomedical', 'health': 7.5, 'mrr': 3400,  'status': 'active',   'lastActive': '1 day ago',     'campaigns': 4},
        {'id': 'luminary-health',  'name': 'Luminary Health',  'health': 5.9, 'mrr': 1800,  'status': 'at-risk',  'lastActive': '3 days ago',    'campaigns': 2},
        {'id': 'cobalt-security',  'name': 'Cobalt Security',  'health': 7.3, 'mrr': 2200,  'status': 'active',   'lastActive': '2 days ago',    'campaigns': 3},
        {'id': 'meridian-retail',  'name': 'Meridian Retail',  'health': 6.4, 'mrr': 1400,  'status': 'at-risk',  'lastActive': '4 days ago',    'campaigns': 2},
    ]

    def _handle_partner_clients(self):
        """Return list of clients for the authenticated partner, filtered by partner_id."""
        import urllib.parse as _up_pc
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        partner_id = qs.get('partnerId', [None])[0] or qs.get('partner_id', [None])[0]

        # Try real DB first
        if SUPABASE_URL and SUPABASE_SERVICE_KEY and partner_id and partner_id != 'partner-demo':
            try:
                qurl = (f"{SUPABASE_URL}/rest/v1/workspace_access"
                        f"?partner_id=eq.{_up_pc.quote(partner_id)}"
                        f"&select=workspace_id,company_name,contact_name,email,plan,active,last_login,created_at"
                        f"&order=created_at.desc")
                req = urllib.request.Request(qurl, headers={
                    'apikey': SUPABASE_SERVICE_KEY,
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                })
                with urllib.request.urlopen(req, timeout=8) as r:
                    rows = json.loads(r.read())

                # Enrich with campaign counts from campaigns table
                clients = []
                commission_rate = 0.20
                plan_mrr = {'starter': 500, 'growth': 1200, 'scale': 2500, 'enterprise': 5000}
                for row in rows:
                    ws = row.get('workspace_id', '')
                    # Count campaigns for this workspace
                    cmpgn_count = 0
                    try:
                        curl = (f"{SUPABASE_URL}/rest/v1/campaigns"
                                f"?client=eq.{_up_pc.quote(ws)}&select=id&status=eq.Active")
                        creq = urllib.request.Request(curl, headers={
                            'apikey': SUPABASE_SERVICE_KEY,
                            'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                            'Prefer': 'count=exact',
                        })
                        with urllib.request.urlopen(creq, timeout=5) as cr:
                            cmpgn_count = len(json.loads(cr.read()))
                    except Exception:
                        pass

                    mrr = plan_mrr.get(row.get('plan', 'starter'), 500)
                    last_login = row.get('last_login') or ''
                    if last_login:
                        try:
                            import datetime as _dt
                            then = _dt.datetime.fromisoformat(last_login.replace('Z', '+00:00'))
                            now  = _dt.datetime.now(_dt.timezone.utc)
                            diff = now - then
                            if diff.days == 0:
                                hrs = diff.seconds // 3600
                                last_active = f'{hrs} hour{"s" if hrs != 1 else ""} ago'
                            elif diff.days == 1:
                                last_active = '1 day ago'
                            else:
                                last_active = f'{diff.days} days ago'
                        except Exception:
                            last_active = 'Recently'
                    else:
                        last_active = 'Never'

                    clients.append({
                        'id':         ws,
                        'name':       row.get('company_name', ws),
                        'email':      row.get('email', ''),
                        'plan':       row.get('plan', 'starter'),
                        'health':     7.0,   # placeholder — no health metric yet
                        'mrr':        mrr,
                        'commission': round(mrr * commission_rate, 2),
                        'status':     'active' if row.get('active', True) else 'inactive',
                        'lastActive': last_active,
                        'campaigns':  cmpgn_count,
                    })

                self._json(200, {'success': True, 'clients': clients, 'total': len(clients), 'source': 'db'})
                return
            except Exception as ex:
                print(f'  ⚠️  partner/clients DB error: {ex}')

        # Fallback: demo data (used when Supabase not configured or partner_id is demo)
        commission_rate = 0.20
        clients = [{**c, 'commission': round(c['mrr'] * commission_rate, 2)}
                   for c in self._PARTNER_DEMO_CLIENTS]
        self._json(200, {'success': True, 'clients': clients, 'total': len(clients), 'source': 'demo'})

    def _handle_partner_summary(self):
        """Return aggregate KPIs for the partner dashboard."""
        import urllib.parse as _up_ps
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        partner_id = qs.get('partnerId', [None])[0] or qs.get('partner_id', [None])[0]

        if SUPABASE_URL and SUPABASE_SERVICE_KEY and partner_id and partner_id != 'partner-demo':
            try:
                qurl = (f"{SUPABASE_URL}/rest/v1/workspace_access"
                        f"?partner_id=eq.{_up_ps.quote(partner_id)}"
                        f"&select=workspace_id,plan,active,last_login"
                        f"&order=created_at.desc")
                req = urllib.request.Request(qurl, headers={
                    'apikey': SUPABASE_SERVICE_KEY,
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                })
                with urllib.request.urlopen(req, timeout=8) as r:
                    rows = json.loads(r.read())

                commission_rate = 0.20
                plan_mrr = {'starter': 500, 'growth': 1200, 'scale': 2500, 'enterprise': 5000}
                total_mrr   = sum(plan_mrr.get(r.get('plan', 'starter'), 500) for r in rows)
                active       = sum(1 for r in rows if r.get('active', True))
                commission   = round(total_mrr * commission_rate, 2)
                self._json(200, {
                    'success':        True,
                    'activeClients':  active,
                    'totalClients':   len(rows),
                    'totalMrr':       total_mrr,
                    'commission':     commission,
                    'avgHealth':      7.5,   # placeholder
                    'totalCampaigns': 0,
                    'source':         'db',
                })
                return
            except Exception as ex:
                print(f'  ⚠️  partner/summary DB error: {ex}')

        # Fallback demo data
        clients     = self._PARTNER_DEMO_CLIENTS
        total_mrr   = sum(c['mrr'] for c in clients)
        active      = sum(1 for c in clients if c['status'] == 'active')
        avg_health  = round(sum(c['health'] for c in clients) / len(clients), 1)
        total_cmpgn = sum(c['campaigns'] for c in clients)
        commission  = round(total_mrr * 0.20, 2)
        self._json(200, {
            'success':         True,
            'activeClients':   active,
            'totalClients':    len(clients),
            'totalMrr':        total_mrr,
            'commission':      commission,
            'avgHealth':       avg_health,
            'totalCampaigns':  total_cmpgn,
            'source':          'demo',
        })

    # ── HQ messaging (agency ↔ admin) ────────────────────────────────────────────

    def _handle_hq_messages_get(self):
        """GET /api/hq/messages?partnerId=X&role=partner|admin — fetch thread messages."""
        import urllib.parse as _up_hm
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        partner_id = qs.get('partnerId', [None])[0]
        role       = qs.get('role', ['partner'])[0]
        limit      = int(qs.get('limit', ['50'])[0])

        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'success': True, 'messages': [], 'demo': True}); return

        try:
            filters = f"select=*&order=created_at.asc&limit={limit}"
            if partner_id:
                filters = f"partner_id=eq.{_up_hm.quote(partner_id)}&{filters}"
            elif role != 'admin':
                self._json(200, {'success': False, 'error': 'partnerId required'}); return

            qurl = f"{SUPABASE_URL}/rest/v1/hq_messages?{filters}"
            req = urllib.request.Request(qurl, headers={
                'apikey': SUPABASE_SERVICE_KEY,
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
            })
            with urllib.request.urlopen(req, timeout=8) as r:
                messages = json.loads(r.read())

            # Mark messages as read for the requesting role
            if messages and partner_id:
                try:
                    unread_ids = [m['id'] for m in messages
                                  if not m.get('read') and m.get('from_role') != role]
                    for mid in unread_ids:
                        ru_url = f"{SUPABASE_URL}/rest/v1/hq_messages?id=eq.{mid}"
                        ru_req = urllib.request.Request(ru_url,
                            data=json.dumps({'read': True}).encode(),
                            headers={'apikey': SUPABASE_SERVICE_KEY,
                                     'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                                     'Content-Type': 'application/json',
                                     'Prefer': 'return=minimal'},
                            method='PATCH')
                        urllib.request.urlopen(ru_req, timeout=5)
                except Exception:
                    pass

            self._json(200, {'success': True, 'messages': messages, 'total': len(messages)})
        except Exception as ex:
            print(f'  hq/messages GET error: {ex}')
            self._json(200, {'success': False, 'error': str(ex)})

    def _handle_hq_message_post(self):
        """POST /api/hq/message — send a message in a partner<->admin thread."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        partner_id = body.get('partnerId', '').strip()
        from_role  = body.get('fromRole', 'partner').strip()   # 'partner' | 'admin'
        from_email = body.get('fromEmail', '').strip()
        subject    = body.get('subject', '').strip()
        msg_body   = body.get('body', '').strip()

        if not msg_body or not from_email:
            self._json(200, {'ok': False, 'error': 'body and fromEmail required'}); return
        if from_role not in ('partner', 'admin'):
            self._json(200, {'ok': False, 'error': 'fromRole must be partner or admin'}); return

        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'ok': True, 'demo': True}); return

        try:
            row = {
                'from_role':  from_role,
                'from_email': from_email,
                'partner_id': partner_id or None,
                'subject':    subject,
                'body':       msg_body,
                'read':       False,
            }
            result = _supabase_req('POST', 'hq_messages', row, service_role=True)
            print(f'  HQ message from {from_role}/{from_email} re partner {partner_id}')

            # Email notification to admin when a partner sends a message
            if from_role == 'partner':
                notify_email = _ENV.get('NOTIFY_EMAIL', '')
                if notify_email:
                    notif_html = (
                        f'<div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px;">'
                        f'<div style="font-size:20px;font-weight:800;color:#1C3A2E;margin-bottom:4px;">ClickPoint HQ</div>'
                        f'<div style="font-size:13px;color:#999;margin-bottom:24px;">New message from a partner</div>'
                        f'<p style="color:#555;font-size:14px;">From: <strong>{from_email}</strong>'
                        f'{" (Partner: " + partner_id + ")" if partner_id else ""}</p>'
                        + (f'<p style="color:#555;font-size:14px;">Subject: <strong>{subject}</strong></p>' if subject else '')
                        + f'<div style="background:#f5f7fa;border-radius:8px;padding:16px;margin:16px 0;'
                          f'font-size:14px;color:#333;line-height:1.6;">{msg_body}</div>'
                        f'<p style="font-size:12px;color:#aaa;">Reply via the Admin HQ portal.</p></div>'
                    )
                    _send_email(
                        notify_email,
                        f'[ClickPoint] Partner message{" — " + subject if subject else ""} from {from_email}',
                        notif_html
                    )

            self._json(200, {'ok': True, 'id': result[0].get('id') if isinstance(result, list) and result else None})
        except Exception as ex:
            print(f'  hq/message POST error: {ex}')
            self._json(200, {'ok': False, 'error': str(ex)})

    def _handle_workspace_auth(self):
        """Authenticate a workspace user. Falls back to demo mode."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        workspace_id = body.get('workspaceId', '').strip().lower()
        email        = body.get('email', '').strip()
        code         = body.get('code', '').strip()

        if not workspace_id or not email:
            self._json(200, {'success': False, 'error': 'Missing workspaceId or email'}); return

        # Try Supabase workspace_access table
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                import urllib.parse as _up
                qurl = f"{SUPABASE_URL}/rest/v1/workspace_access?workspace_id=eq.{_up.quote(workspace_id)}&email=eq.{_up.quote(email)}&select=*&limit=1"
                req = urllib.request.Request(qurl, headers={
                    'apikey': SUPABASE_SERVICE_KEY,
                    'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                })
                with urllib.request.urlopen(req, timeout=6) as r:
                    rows = json.loads(r.read())
                if rows and rows[0].get('access_code') == code:
                    row = rows[0]
                    company_name  = row.get('company_name', workspace_id)
                    contact_name  = row.get('contact_name', '')
                    # Update last login
                    try:
                        patch_url = f"{SUPABASE_URL}/rest/v1/workspace_access?id=eq.{row['id']}"
                        patch_req = urllib.request.Request(patch_url,
                            data=json.dumps({'last_login': datetime.datetime.utcnow().isoformat()}).encode(),
                            headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                                     'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                            method='PATCH')
                        urllib.request.urlopen(patch_req, timeout=5)
                    except Exception:
                        pass
                    self._json(200, {
                        'success':     True,
                        'companyName': company_name,
                        'contactName': contact_name,
                        'workspaceId': workspace_id,
                        'createdAt':   row.get('created_at', ''),
                    })
                    return
            except Exception:
                pass

        # If Supabase is reachable but code didn't match, reject
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            self._json(200, {'success': False, 'error': 'Invalid access code'}); return

        # Dev-only fallback when Supabase is not configured — accept any 6-digit code
        company_name = ' '.join(w.capitalize() for w in workspace_id.split('-'))
        self._json(200, {'success': True, 'demo': True, 'companyName': company_name, 'workspaceId': workspace_id})

    def _handle_workspace_activity(self):
        """Log workspace activity — syncs to admin HQ view."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        workspace_id  = body.get('workspaceId', '')
        company_name  = body.get('companyName', workspace_id)
        activity_type = body.get('type', 'event')
        detail        = body.get('detail', '')
        timestamp     = body.get('timestamp', datetime.datetime.utcnow().isoformat())

        # Store in Supabase if configured
        if SUPABASE_URL and SUPABASE_SERVICE_KEY and workspace_id:
            try:
                payload = json.dumps({
                    'workspace_id': workspace_id, 'company_name': company_name,
                    'activity_type': activity_type, 'detail': detail, 'created_at': timestamp
                }).encode()
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/workspace_activity",
                    data=payload,
                    headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                             'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                    method='POST')
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass

        self._json(200, {'ok': True})

    def _handle_workspace_create(self):
        """Create a new client workspace and generate access credentials."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        company_name  = body.get('companyName', '').strip()
        email         = body.get('email', '').strip()
        # contactName (self-signup) or name (partner-created) — store as contact_name
        contact_name  = (body.get('contactName') or body.get('name') or '').strip()
        # Partner portal may pass a pre-generated workspaceId and accessCode
        preset_ws_id  = body.get('workspaceId', '').strip()
        preset_code   = body.get('accessCode', '').strip()
        # If companyName is missing, derive from the workspace ID
        if not company_name and preset_ws_id:
            company_name = ' '.join(w.capitalize() for w in preset_ws_id.split('-'))
        if not company_name or not email:
            self._error(400, 'companyName and email required'); return

        import re as _re
        workspace_id = preset_ws_id or _re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')
        code = preset_code or _generate_access_code(6)

        partner_id_val = body.get('partnerId', '').strip()
        row = {
            'workspace_id': workspace_id, 'company_name': company_name,
            'email': email, 'access_code': code,
            'contact_name': contact_name,
            'created_at': datetime.datetime.utcnow().isoformat()
        }
        if partner_id_val:
            row['partner_id'] = partner_id_val

        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                payload = json.dumps(row).encode()
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/workspace_access",
                    data=payload,
                    headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                             'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                    method='POST')
                urllib.request.urlopen(req, timeout=6)
            except Exception as e:
                pass  # Still return the credentials even if DB write fails

        # Send access email if Resend configured
        portal_link = f"https://platform.clickpointconsulting.com.au/workspace.html?w={workspace_id}"
        email_html = f"""<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
<div style="font-size:22px;font-weight:800;color:#1C3A2E;margin-bottom:4px;">ClickPoint</div>
<div style="font-size:14px;color:#999;margin-bottom:28px;">Your workspace is ready</div>
<h2 style="font-size:20px;color:#1A1A1A;font-weight:700;margin-bottom:8px;">Welcome to your ClickPoint Workspace</h2>
<p style="color:#555;font-size:14px;line-height:1.6;margin-bottom:20px;">Hi! Your dedicated marketing workspace for <strong>{company_name}</strong> is ready. Sign in below to see your campaigns, analytics, and reports — and chat directly with your AI marketing assistant.</p>
<div style="background:#F4F3EE;border-radius:12px;padding:20px;margin-bottom:20px;">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:8px;">Your Access Details</div>
  <div style="font-size:14px;color:#1A1A1A;margin-bottom:4px;">Workspace ID: <strong>{workspace_id}</strong></div>
  <div style="font-size:14px;color:#1A1A1A;margin-bottom:4px;">Email: <strong>{email}</strong></div>
  <div style="font-size:14px;color:#1A1A1A;">Access Code: <strong style="font-size:22px;letter-spacing:0.2em;">{code}</strong></div>
</div>
<a href="{portal_link}" style="display:block;background:#1C3A2E;color:#fff;text-decoration:none;padding:14px;border-radius:10px;text-align:center;font-weight:700;font-size:15px;margin-bottom:24px;">Open My Workspace →</a>
<p style="font-size:12px;color:#999;">Questions? Reply to this email or contact your ClickPoint account manager.</p>
</div>"""
        _send_email(email, f'Your ClickPoint Workspace is ready — {company_name}', email_html)

        # ── Notify partner if this came via their self-signup link ────────────
        if partner_id_val and SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                p_req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/partner_accounts?partner_id=eq.{_up.quote(partner_id_val)}&select=email,agency_name&limit=1",
                    headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'})
                with urllib.request.urlopen(p_req, timeout=5) as pr:
                    partners = json.loads(pr.read())
                if partners:
                    p_email     = partners[0].get('email', '')
                    p_agency    = partners[0].get('agency_name', 'Your agency')
                    notif_html  = f"""<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
<div style="font-size:22px;font-weight:800;color:#1C3A2E;margin-bottom:4px;">ClickPoint</div>
<div style="font-size:14px;color:#999;margin-bottom:28px;">New client signed up via your link</div>
<h2 style="font-size:20px;color:#1A1A1A;font-weight:700;margin-bottom:8px;">🎉 New client: {company_name}</h2>
<p style="color:#555;font-size:14px;line-height:1.6;margin-bottom:20px;">A new client just signed up through your {p_agency} self-signup link. Their workspace is live and ready.</p>
<div style="background:#F4F3EE;border-radius:12px;padding:20px;margin-bottom:20px;">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:8px;">Client Details</div>
  <div style="font-size:14px;color:#1A1A1A;margin-bottom:4px;">Company: <strong>{company_name}</strong></div>
  <div style="font-size:14px;color:#1A1A1A;margin-bottom:4px;">Contact: <strong>{contact_name or 'Not provided'}</strong></div>
  <div style="font-size:14px;color:#1A1A1A;">Email: <strong>{email}</strong></div>
</div>
<a href="https://platform.clickpointconsulting.com.au/partner.html" style="display:block;background:#1C3A2E;color:#fff;text-decoration:none;padding:14px;border-radius:10px;text-align:center;font-weight:700;font-size:15px;margin-bottom:24px;">View in Partner Portal →</a>
<p style="font-size:12px;color:#999;">Powered by ClickPoint Partner Network</p>
</div>"""
                    if p_email:
                        _send_email(p_email, f'🎉 New client signed up — {company_name}', notif_html)
            except Exception:
                pass

        # ── Push to HubSpot ───────────────────────────────────────────────────
        _push_to_hubspot(
            contact_props={
                'email':          email,
                'company':        company_name,
                'lifecyclestage': 'customer',
            },
            company_props={
                'name': company_name,
            },
            note=f'Client workspace created — ID: {workspace_id}',
        )

        self._json(200, {
            'ok': True, 'workspaceId': workspace_id, 'companyName': company_name,
            'email': email, 'code': code, 'link': portal_link
        })

    def _handle_admin_migrate(self):
        """Returns migration SQL that needs to be run in the Supabase SQL editor."""
        self._json(200, {
            'ok': True,
            'message': 'Run the following SQL in your Supabase SQL Editor',
            'sql': 'ALTER TABLE workspace_access ADD COLUMN IF NOT EXISTS partner_id TEXT DEFAULT NULL;',
            'url': 'https://supabase.com/dashboard/project/banelvzjttdqkwmbvybm/sql/new'
        })

    def _handle_workspaces_list(self):
        """List all workspaces — admin only."""
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                # Try fetching with partner_id; fall back if the column doesn't exist yet
                workspaces = None
                for fields in ('workspace_id,company_name,email,created_at,last_login,partner_id',
                               'workspace_id,company_name,email,created_at,last_login'):
                    try:
                        req = urllib.request.Request(
                            f"{SUPABASE_URL}/rest/v1/workspace_access?select={fields}&order=created_at.desc",
                            headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'})
                        with urllib.request.urlopen(req, timeout=6) as r:
                            result = json.loads(r.read())
                            if isinstance(result, list):
                                workspaces = result
                                break
                    except Exception:
                        continue
                if workspaces is None:
                    raise Exception('Could not fetch workspaces')

                # Build a partner_id → agency_name lookup map
                partner_map = {}
                try:
                    p_req = urllib.request.Request(
                        f"{SUPABASE_URL}/rest/v1/partner_accounts?select=partner_id,agency_name",
                        headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'})
                    with urllib.request.urlopen(p_req, timeout=5) as pr:
                        for p in json.loads(pr.read()):
                            if p.get('partner_id'):
                                partner_map[p['partner_id']] = p.get('agency_name', '')
                except Exception:
                    pass

                # Enrich with recent activity and partner name
                for ws in workspaces:
                    pid = ws.get('partner_id', '')
                    ws['partner_name'] = partner_map.get(pid, '') if pid else ''
                    try:
                        act_req = urllib.request.Request(
                            f"{SUPABASE_URL}/rest/v1/workspace_activity?workspace_id=eq.{ws['workspace_id']}&order=created_at.desc&limit=1",
                            headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'})
                        with urllib.request.urlopen(act_req, timeout=4) as ar:
                            acts = json.loads(ar.read())
                            ws['last_activity'] = acts[0]['detail'] if acts else None
                    except Exception:
                        ws['last_activity'] = None
                self._json(200, {'workspaces': workspaces})
                return
            except Exception:
                pass

        # Demo fallback
        self._json(200, {'workspaces': [], 'demo': True})

    # ── Report handlers ───────────────────────────────────────────────────────

    def _handle_report_generate(self):
        """Run one step of the report chain: raj | jess | sarah."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        step     = (body.get('step') or '').strip()
        client   = (body.get('client') or '').strip()
        period   = (body.get('period') or '').strip()
        budget   = float(body.get('budget', 10000))
        metrics  = body.get('metrics', {})
        raj_out  = body.get('raj_output', '')
        jess_out = body.get('jess_output', '')

        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'API key not set'); return

        if step == 'raj':
            # Auto-fetch metrics if not supplied
            if not metrics:
                for p in ['google_ads', 'meta_ads', 'ga4', 'search_console']:
                    try:
                        metrics[p] = fetch_platform_metrics(client, p, 30, budget)
                    except Exception:
                        pass
            prompt = _build_raj_report_prompt(client, period, metrics)
            try:
                output = call_anthropic(effective_key, AGENT_PROMPTS.get('raj', ''),
                                        [{'role':'user','content':prompt}], max_tokens=1400)
                print(f'  📋 Report/raj done for {client} ({period})')
                self._json(200, {'step':'raj', 'output':output, 'metrics':metrics})
            except Exception as e:
                self._error(500, str(e))

        elif step == 'jess':
            prompt = _build_jess_report_prompt(client, period, raj_out)
            try:
                output = call_anthropic(effective_key, AGENT_PROMPTS.get('jess', ''),
                                        [{'role':'user','content':prompt}], max_tokens=1200)
                print(f'  📋 Report/jess done for {client} ({period})')
                self._json(200, {'step':'jess', 'output':output})
            except Exception as e:
                self._error(500, str(e))

        elif step == 'sarah':
            prompt = _build_sarah_report_prompt(client, period, raj_out, jess_out)
            try:
                raw = call_anthropic(effective_key, AGENT_PROMPTS.get('sarah', ''),
                                     [{'role':'user','content':prompt}], max_tokens=700)
                cleaned = raw.strip()
                # Strip any accidental markdown fences
                if cleaned.startswith('```'):
                    cleaned = '\n'.join(cleaned.split('\n')[1:])
                if cleaned.endswith('```'):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                try:
                    sarah_data = json.loads(cleaned)
                except Exception:
                    sarah_data = {
                        'executive_summary': cleaned[:500],
                        'health_score': 7,
                        'health_label': 'Performing well',
                        'recommendations': [],
                        'budget_note': '',
                        'sarah_sign_off': '',
                    }
                print(f'  📋 Report/sarah done for {client} ({period}) — score={sarah_data.get("health_score")}')
                self._json(200, {'step':'sarah', 'output':raw, 'data':sarah_data})
            except Exception as e:
                self._error(500, str(e))
        else:
            self._error(400, f'Unknown step: {step}')

    def _handle_reports_list(self):
        """Return list of saved reports (newest first)."""
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'reports': []}); return
        try:
            rows = _supabase_req(
                'GET',
                'client_reports?select=id,client,period,health_score,health_label,generated_at,status'
                '&order=generated_at.desc&limit=50',
            )
            self._json(200, {'reports': rows or []})
        except Exception as e:
            self._error(500, str(e))

    def _handle_reports_save(self):
        """Persist a completed report to Supabase."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        if not body.get('client') or not body.get('period'):
            self._error(400, 'client and period are required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'saved': False, 'reason': 'Supabase not configured'}); return
        try:
            row = {
                'client':       body.get('client', ''),
                'period':       body.get('period', ''),
                'status':       'complete',
                'exec_summary': body.get('exec_summary', ''),
                'health_score': body.get('health_score', 0),
                'health_label': body.get('health_label', ''),
                'raj_analysis': body.get('raj_analysis', ''),
                'jess_narrative': body.get('jess_narrative', ''),
                'sarah_json':   body.get('sarah_data', {}),
                'metrics':      body.get('metrics', {}),
            }
            result = _supabase_req('POST', 'client_reports', row, service_role=True)
            rid = result[0].get('id') if result else None
            print(f'  📋 Report saved: {row["client"]} {row["period"]} id={rid}')
            # Auto-notify team via Slack when report is published
            score = body.get('health_score', 0)
            label = body.get('health_label', '')
            _notify('report', row['client'],
                    f'{row["period"]} report ready — health {score}/10 · {label}')
            self._json(200, {'saved': True, 'id': rid})
        except Exception as e:
            print(f'  Report save error: {e}')
            self._error(500, str(e))

    def _handle_integrations_list(self):
        """Return metadata only — never returns tokens. Filter by workspaceId if provided."""
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            workspace_id = (qs.get('workspaceId') or [''])[0].strip()
            filter_clause = f'&client=eq.{workspace_id}' if workspace_id else ''
            rows = _supabase_req(
                'GET',
                f'client_integrations?select=id,client,platform,account_id,status,last_synced'
                f'&order=created_at.desc{filter_clause}',
            ) or []
            # Canva tokens live in platform_settings, not client_integrations — inject a synthetic row
            if workspace_id and not any(r.get('platform') == 'canva' for r in rows):
                canva_tokens = _canva_load_tokens(workspace_id)
                if canva_tokens.get('access_token'):
                    rows.append({
                        'id': f'canva_{workspace_id}',
                        'client': workspace_id,
                        'platform': 'canva',
                        'account_id': canva_tokens.get('user_id', ''),
                        'status': 'connected',
                        'last_synced': None,
                    })
            self._json(200, {'integrations': rows})
        except Exception as e:
            # Table may not exist yet — return empty list so UI degrades gracefully
            print(f'  integrations_list: {e}')
            self._json(200, {'integrations': [], 'note': str(e)})

    # ── Campaign Pipeline ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_assigned_agent(sarah_text, channel):
        """Determine which specialist Sarah assigned based on her response + channel."""
        t = (sarah_text or '').lower()
        # Look for primary lead assignment phrases first (most reliable)
        import re as _re
        lead_match = _re.search(
            r'lead assignment[^\n]*?(emma|derek|jess|zara|cleo|raj)|'
            r'(emma|derek|jess|zara|cleo|raj)[^\n]*?will own',
            t
        )
        if lead_match:
            name = (lead_match.group(1) or lead_match.group(2) or '').strip()
            if name: return name
        # Fall back to first-name-mentioned order — Emma before Jess (Jess is often mentioned as support)
        ch_lower = (channel or '').lower()
        _is_organic = any(x in ch_lower for x in ['organic social', 'organic'])
        if 'emma'  in t: return 'emma'
        if 'derek' in t: return 'derek'
        if 'cleo'  in t: return 'cleo' if _is_organic else 'cleo_paid'
        if 'raj'   in t: return 'raj'
        if 'zara'  in t: return 'zara'
        if 'jess'  in t: return 'jess'
        ch = (channel or '').lower()
        if any(x in ch for x in ['email','newsletter','drip','nurture','klaviyo','mailchimp','activecampaign']): return 'emma'
        if any(x in ch for x in ['google ads','microsoft','bing','ppc','search ads']): return 'derek'
        if any(x in ch for x in ['organic social','organic']): return 'cleo'
        if any(x in ch for x in ['meta','facebook','instagram','tiktok','linkedin','paid social']): return 'cleo_paid'
        if any(x in ch for x in ['seo','content','blog']): return 'jess'
        if any(x in ch for x in ['display','youtube','creative','brand']): return 'zara'
        return 'derek'

    @staticmethod
    def _agent_display_name(a):
        return {
            'derek':     'Derek Wu · Paid Search',
            'jess':      'Jess Park · Content & SEO',
            'zara':      'Zara Osei · Creative',
            'cleo':      'Cleo Chan · Social Media',
            'cleo_paid': 'Cleo Chan · Paid Social',
            'raj':       'Raj Nair · SEO & Analytics',
            'emma':      'Emma Ross · Email Marketing',
        }.get(a, a)

    @staticmethod
    def _agent_deliverable_type(a):
        return {'derek':'Ads','jess':'Content','zara':'Design','cleo':'Content','cleo_paid':'Ads','raj':'SEO','emma':'Email'}.get(a,'Strategy')

    SPECIALIST_PROMPTS = {
        'derek': (
            "You've just been assigned lead on this paid search campaign.\n"
            "Produce your actual deliverable — not a plan, not a framework. Real work, ready to use.\n\n"
            "Deliver:\n"
            "1. KEYWORD LIST — 15–20 keywords with: match type, estimated CPC range, search intent (info/commercial/transactional), and ad group assignment\n"
            "2. CAMPAIGN STRUCTURE — campaign name, 3–4 ad group names, bidding strategy recommendation and why\n"
            "3. AD COPY — 3 complete Responsive Search Ad variants:\n"
            "   • 5 headlines each (max 30 chars), 2 descriptions each (max 90 chars)\n"
            "   • Each variant uses a different angle: pain point / benefit / proof\n\n"
            "Use real copy. Every headline and description must be ready to upload to Google Ads."
        ),
        'jess': (
            "You've just been assigned content & copy on this campaign.\n"
            "Produce the actual deliverable — real copy, ready to use.\n\n"
            "Deliver:\n"
            "1. MESSAGING FRAMEWORK — primary value prop (1 sentence), 3 core messages, key differentiator vs competitors\n"
            "2. AD COPY — 5 headline variants + 3 description variants (Google Ads format, char limits respected)\n"
            "3. LANDING PAGE COPY BRIEF — H1, subheadline, 3 bullet benefits (specific, benefit-led), primary CTA text, secondary CTA text\n"
            "4. EMAIL SUBJECT LINES — 5 subject line variants for a nurture sequence targeting this audience\n\n"
            "All copy should be on-brand for the client brief. No placeholder text."
        ),
        'zara': (
            "You've just been assigned creative direction on this campaign.\n"
            "Produce the actual deliverable — specific specs, not vague direction.\n\n"
            "Deliver:\n"
            "1. VISUAL DIRECTION — overall aesthetic, mood, colour palette (hex codes), typography guidance\n"
            "2. AD CREATIVE SPECS — for each format (static image 1200x628, square 1080x1080, story 1080x1920):\n"
            "   • Message hierarchy (what goes where)\n"
            "   • Headline on creative, supporting copy, CTA button style\n"
            "3. CREATIVE DO'S AND DON'TS — 5 specific rules for this campaign\n"
            "4. VIDEO BRIEF (if applicable) — hook (first 3 seconds), narrative arc, closing CTA\n\n"
            "Be precise — a freelance designer should be able to build from this brief alone."
        ),
        'cleo_paid': (
            "You've just been assigned paid social on this campaign.\n"
            "Produce the actual deliverable — real campaign setup, ready to build.\n\n"
            "Deliver:\n"
            "1. CAMPAIGN STRUCTURE — objective, campaign type (Advantage+ / manual), budget split recommendation\n"
            "2. AUDIENCE TARGETING — 3 distinct ad set audiences:\n"
            "   • Core: interests/demographics with specifics\n"
            "   • Lookalike: based on what existing data (explain the seed)\n"
            "   • Retargeting: trigger and window\n"
            "3. AD COPY — 3 primary text variants + 3 headline variants (Meta format)\n"
            "4. CREATIVE RECOMMENDATION — format (video/image/carousel), hook style, first-3-second hook script\n\n"
            "Real targeting parameters, real copy. Ready to build in Ads Manager."
        ),
        'cleo': (
            "You've just been assigned organic social media on this campaign.\n"
            "Produce the actual deliverable — a complete, ready-to-execute organic content plan.\n\n"
            "Deliver:\n"
            "1. CONTENT STRATEGY — primary content pillars (3–4), tone of voice, posting cadence\n"
            "2. 14-DAY CONTENT CALENDAR — for each day: post type (Reel/carousel/story/static), caption (full, ready to post), "
            "hashtag set (5–8), best time to post, engagement hook or CTA\n"
            "3. COMMUNITY MANAGEMENT GUIDE — how to respond to comments, DM templates for common enquiries, "
            "rules for handling negative feedback\n"
            "4. GROWTH TACTICS — 3 specific organic growth plays for this brand (e.g. collabs, UGC seeding, story polls)\n\n"
            "All captions must be full and ready to post. No placeholder text. Emoji-friendly where appropriate."
        ),
        'emma': (
            "You've just been assigned email marketing on this campaign.\n"
            "Produce the actual deliverable — complete, send-ready email copy.\n\n"
            "If CONNECTED_PLATFORMS lists an email tool (Klaviyo, Mailchimp, ActiveCampaign, etc.) — reference it by name.\n"
            "If NO email platform is connected — produce the copy anyway and end with a note:\n"
            "  '📋 Ready to load: Once [Mailchimp / Klaviyo / ActiveCampaign] is connected in Settings → Connected Accounts, "
            "your team can import this sequence directly. The copy is ready now.'\n\n"
            "Deliver:\n"
            "1. CAMPAIGN STRATEGY — goal, campaign type (promotional / nurture / welcome / re-engagement), send frequency, "
            "list segment to target, key message per send\n"
            "2. EMAIL SEQUENCE — write 3 complete emails:\n"
            "   Email 1: Subject line + preview text + full body copy + CTA\n"
            "   Email 2: Subject line + preview text + full body copy + CTA (different angle)\n"
            "   Email 3: Subject line + preview text + full body copy + CTA (urgency / proof)\n"
            "3. SUBJECT LINE VARIANTS — 5 A/B test options for Email 1 (different hooks: curiosity / benefit / social proof / "
            "urgency / personalisation)\n"
            "4. SEND SCHEDULE — recommended send days/times, delay between emails, trigger conditions\n\n"
            "Write real copy — no [PLACEHOLDER] text. Every email must be ready to paste into an email platform."
        ),
        'raj': (
            "You've just been assigned SEO & analytics on this campaign.\n"
            "Produce the actual deliverable — ready to implement.\n\n"
            "Deliver:\n"
            "1. KEYWORD RESEARCH — 20 target keywords: monthly volume (est.), KD (1–100), intent, recommended page\n"
            "2. CONTENT PRIORITIES — top 3 pages to create or optimise:\n"
            "   • Target keyword, recommended title, word count, top 3 competitors to outrank\n"
            "3. TRACKING SETUP — UTM structure for this campaign (exact URL format), GA4 events to track, conversion event definition\n"
            "4. QUICK WINS — 3 immediate technical/on-page actions with specific instructions (which page, what to change)\n\n"
            "Real keywords, real UTMs, real page recommendations. Ready to implement."
        ),
    }

    def _handle_campaign_request(self):
        """Client submits a campaign brief → Supabase campaigns table → Sarah + specialist async.

        Uses the existing 'campaigns' table with column remapping:
          client   → workspace_id
          types    → campaign type
          assigned → channel
          brief    → JSON blob with {brief, channel, budget, partner_id, company_name, sarah_reply}

        The AI pipeline (Sarah review + specialist deliverable) runs in a background thread so
        the HTTP response is returned immediately, avoiding Railway's ~30s gateway timeout.
        The frontend polls /api/campaign/updates?campaignId=X for the results.
        """
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return

        import urllib.parse as _up_cmp
        workspace_id          = body.get('workspaceId', '').strip()
        company_name          = body.get('companyName', '').strip()
        name                  = body.get('name', '').strip()
        ctype                 = body.get('type', '').strip()
        channel               = body.get('channel', '').strip()
        budget                = body.get('budget', '').strip()
        audience              = body.get('audience', '').strip()
        brief                 = body.get('brief', '').strip()
        # Integration status passed from the frontend — comma-separated platform labels
        connected_platforms   = body.get('connectedPlatforms', '').strip()  # e.g. "Klaviyo, Google Ads"
        # ── Extended brief fields from the new campaign form ──────────────────
        url          = body.get('url', '').strip()
        conversion   = body.get('conversion', '').strip()
        tracking     = body.get('tracking', '').strip()
        target_cpa   = body.get('targetCpa', '').strip()
        bid_strategy = body.get('bidStrategy', 'recommend').strip()
        locations    = body.get('locations', '').strip()
        loc_intent   = body.get('locIntent', '').strip()
        schedule     = body.get('schedule', '').strip()
        services     = body.get('services', '').strip()
        usps         = [u for u in (body.get('usps') or []) if isinstance(u, str) and u.strip()]
        offer        = body.get('offer', '').strip()
        competitors  = body.get('competitors', '').strip()
        headlines    = [h for h in (body.get('headlines') or []) if isinstance(h, str) and h.strip()]
        descriptions = [d for d in (body.get('descriptions') or []) if isinstance(d, str) and d.strip()]
        biz_name     = body.get('bizName', '').strip()
        phone        = body.get('phone', '').strip()

        if not workspace_id or not name:
            self._error(400, 'workspaceId and name required'); return

        # ── Look up partner_id from workspace_access ──────────────────────────
        partner_id = ''
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/workspace_access?workspace_id=eq.{_up_cmp.quote(workspace_id)}&select=partner_id&limit=1",
                    headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'})
                with urllib.request.urlopen(req, timeout=5) as r:
                    rows = json.loads(r.read())
                    if rows:
                        partner_id = rows[0].get('partner_id', '') or ''
            except Exception:
                pass

        # ── Save to Supabase immediately (status = 'processing') ──────────────
        brief_blob_initial = json.dumps({
            'brief':          brief,
            'channel':        channel,
            'budget':         budget,
            'partner_id':     partner_id,
            'company_name':   company_name or workspace_id,
            'sarah_reply':    '',
            'assigned_agent': '',
            'deliverables':   [],
            # Extended brief fields
            'url': url, 'conversion': conversion, 'tracking': tracking,
            'target_cpa': target_cpa, 'bid_strategy': bid_strategy,
            'locations': locations, 'loc_intent': loc_intent, 'schedule': schedule,
            'services': services, 'usps': usps, 'offer': offer, 'competitors': competitors,
            'headlines': headlines, 'descriptions': descriptions,
            'biz_name': biz_name, 'phone': phone,
            'ads_build_status': '', 'ads_resource_name': '', 'ads_build_detail': '',
        })
        campaign_id = None
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                row = {
                    'name':     name,
                    'client':   workspace_id,
                    'types':    ctype,
                    'audience': audience,
                    'assigned': channel,
                    'brief':    brief_blob_initial,
                    'status':   'processing',
                }
                result = _supabase_req('POST', 'campaigns', row, service_role=True)
                if result and isinstance(result, list) and result[0].get('id'):
                    campaign_id = result[0]['id']
                    print(f'  📥 Campaign saved (async): id={campaign_id} ws={workspace_id}')
            except Exception as e:
                print(f'  ⚠️  campaigns insert error: {e}')

        # ── Respond to client immediately (no blocking on AI calls) ───────────
        self._json(200, {
            'ok':            True,
            'campaignId':    campaign_id,
            'partnerId':     partner_id,
            'status':        'processing',
            'sarahReply':    None,
            'assignedAgent': None,
            'deliverable':   None,
        })

        # ── Background thread: Sarah review + specialist deliverable ──────────
        # Capture all locals needed by the thread (avoid closure over mutable state)
        _cid  = campaign_id
        _name = name; _ctype = ctype; _ch = channel; _bud = budget
        _aud  = audience; _br = brief; _co = company_name or workspace_id
        _plat = connected_platforms; _pid = partner_id; _ws = workspace_id
        # Extended campaign brief fields for Google Ads build
        _url = url; _conv = conversion; _tracking = tracking
        _tcpa = target_cpa; _bid = bid_strategy; _locs = locations
        _svcs = services; _usps = usps; _offer = offer; _comps = competitors
        _heads = headlines; _descs = descriptions; _biz = biz_name; _phone = phone

        def _bg_process():
            sarah_reply      = ''
            assigned_agent   = 'derek'
            deliverable_text = ''

            # — Sarah review —
            if API_KEY:
                try:
                    budget_str  = f'${_bud}/mo' if _bud else 'Not specified'
                    sarah_prompt = (
                        f"A new campaign request has just come in from {_co}. "
                        f"Review this brief and provide your initial strategic assessment.\n\n"
                        f"Campaign Name: {_name}\nType: {_ctype}\nChannel: {_ch}\n"
                        f"Budget: {budget_str}\nTarget Audience: {_aud or 'Not specified'}\n"
                        f"Brief: {_br or 'No brief provided'}\n\n"
                        f"Respond as Sarah Lin, CMO. Give a warm but professional acknowledgement, "
                        f"your immediate strategic read on this brief, which team member you're assigning "
                        f"to lead execution, and 2–3 clear next steps. Keep it concise — this goes "
                        f"directly into the client's campaign dashboard."
                    )
                    sarah_reply = call_anthropic(
                        API_KEY, AGENT_PROMPTS.get('sarah', ''),
                        [{'role': 'user', 'content': sarah_prompt}], max_tokens=500
                    )
                    print(f'  🤖 Sarah reviewed "{_name}" for {_co}')
                except Exception as e:
                    print(f'  ⚠️  Sarah async error: {e}')

            assigned_agent = AgentHandler._parse_assigned_agent(sarah_reply, _ch)

            # — Specialist deliverable —
            if sarah_reply and API_KEY:
                try:
                    integration_line = (
                        f"CONNECTED_PLATFORMS: {_plat}"
                        if _plat else
                        "CONNECTED_PLATFORMS: None — no third-party platforms are currently connected for this client."
                    )
                    specialist_context = (
                        f"Campaign: {_name}\nClient: {_co}\n"
                        f"Type: {_ctype}\nChannel: {_ch}\n"
                        f"Budget: {'$'+_bud+'/mo' if _bud else 'TBD'}\n"
                        f"Target Audience: {_aud or 'Not specified'}\n"
                        f"Brief: {_br or 'No brief provided'}\n"
                        f"{integration_line}\n\n"
                        f"CMO Assessment (Sarah Lin):\n{sarah_reply[:800]}"
                    )
                    spec_prompt      = AgentHandler.SPECIALIST_PROMPTS.get(assigned_agent, '')
                    deliverable_text = call_anthropic(
                        API_KEY, AGENT_PROMPTS.get(assigned_agent, ''),
                        [{'role': 'user', 'content': spec_prompt + '\n\n--- CONTEXT ---\n' + specialist_context}],
                        max_tokens=1500
                    )
                    print(f'  📋 {assigned_agent} deliverable ready for "{_name}"')
                except Exception as e:
                    print(f'  ⚠️  {assigned_agent} async deliverable error: {e}')

            # — Load brand hub for this workspace (used by Zara + Canva) —
            brand_hub = _load_brand_hub(_ws) if _ws else {}
            brand_hub_summary = ''
            if brand_hub:
                colors  = ', '.join(filter(None, [brand_hub.get(f'bCol{n}','') for n in '12345']))
                tone    = brand_hub.get('bTone', '') or brand_hub.get('bFormality', '')
                fonts   = f"{brand_hub.get('bHFont','')} / {brand_hub.get('bBFont','')}".strip(' /')
                logo    = brand_hub.get('bLogoUrl', '')
                tagline = brand_hub.get('bTagline', '')
                mission = brand_hub.get('bMission', '')
                img_sty = brand_hub.get('bImgStyle', '')
                traits  = ', '.join(brand_hub.get('bTraits', []))
                vp      = brand_hub.get('bValueProp', '')
                words_u = brand_hub.get('bWordsUse', '')
                words_a = brand_hub.get('bWordsAvoid', '')
                parts = []
                if tagline:  parts.append(f'Tagline: {tagline}')
                if mission:  parts.append(f'Mission: {mission}')
                if colors:   parts.append(f'Brand colours: {colors}')
                if fonts:    parts.append(f'Fonts: {fonts}')
                if tone:     parts.append(f'Tone: {tone}')
                if traits:   parts.append(f'Brand personality: {traits}')
                if img_sty:  parts.append(f'Image style: {img_sty}')
                if vp:       parts.append(f'Value proposition: {vp}')
                if words_u:  parts.append(f'Use words: {words_u}')
                if words_a:  parts.append(f'Avoid words: {words_a}')
                if logo:     parts.append(f'Logo URL: {logo}')
                brand_hub_summary = '\n'.join(parts)
                print(f'  🎨 Brand hub loaded for {_ws}: {len(parts)} fields')

            # — Design deliverable: auto-generate when brief mentions design assets —
            design_deliverable_text = ''
            design_keywords = ['design asset', 'design assets', 'creative asset', 'creative assets',
                               'graphic', 'banner', 'visual', 'instagram post', 'reel', 'story slide',
                               'feed post', 'canva', 'figma', 'mockup', '1080x', '1920x', '1200x']
            brief_lower = (_br or '').lower()
            needs_design = any(kw in brief_lower for kw in design_keywords) or assigned_agent == 'zara'
            if needs_design and sarah_reply and API_KEY and assigned_agent != 'zara':
                try:
                    brand_hub_block = (
                        f'\n\nBRAND GUIDELINES (from client brand hub):\n{brand_hub_summary}'
                        if brand_hub_summary else
                        '\n\nBRAND GUIDELINES: Not yet completed by client — use brief and campaign context.'
                    )
                    zara_context = (
                        f"Campaign: {_name}\nClient: {_co}\n"
                        f"Type: {_ctype}\nChannel: {_ch}\n"
                        f"Target Audience: {_aud or 'Not specified'}\n"
                        f"Brief: {_br or 'No brief provided'}\n\n"
                        f"CMO Assessment (Sarah Lin):\n{sarah_reply[:600]}"
                        f"{brand_hub_block}"
                    )
                    design_text = call_anthropic(
                        API_KEY, AGENT_PROMPTS.get('zara', ''),
                        [{'role': 'user', 'content': AgentHandler.SPECIALIST_PROMPTS.get('zara', '') + '\n\n--- CONTEXT ---\n' + zara_context}],
                        max_tokens=1200
                    )
                    design_deliverable_text = design_text
                    print(f'  🎨 Zara design brief ready for "{_name}"')
                except Exception as e:
                    print(f'  ⚠️  Zara design async error: {e}')

            # — Build updated brief blob —
            deliverable_entry = {
                'agent':      assigned_agent,
                'agentName':  AgentHandler._agent_display_name(assigned_agent),
                'type':       AgentHandler._agent_deliverable_type(assigned_agent),
                'content':    deliverable_text,
                'created_at': datetime.datetime.utcnow().isoformat(),
            } if deliverable_text else None

            # — Auto-generate Canva design (brand template → blank canvas edit URL) —
            canva_urls = []
            if design_deliverable_text and _canva_client_id() and _canva_client_secret():
                try:
                    brand = _co or _name or 'Brand'
                    canva_urls = canva_generate_and_export(
                        design_deliverable_text, brand, _ws, brand_hub=brand_hub
                    )
                    if canva_urls:
                        print(f'  🖼️  Canva: {len(canva_urls)} design(s) for "{_name}"')
                except Exception as e:
                    print(f'  ⚠️  Canva pipeline error: {e}')

            design_entry = {
                'agent':      'zara',
                'agentName':  'Zara Osei · Creative',
                'type':       'Design',
                'content':    design_deliverable_text,
                'design_urls': canva_urls,
                'created_at': datetime.datetime.utcnow().isoformat(),
            } if design_deliverable_text else None

            all_deliverables = [d for d in [deliverable_entry, design_entry] if d]

            # ── Google Ads live campaign build (Derek campaigns only) ─────────
            ads_build_status  = ''
            ads_resource_name = ''
            ads_build_detail  = ''

            is_google_ads_campaign = 'google ads' in _ch.lower()
            if is_google_ads_campaign and GOOGLE_ADS_DEVELOPER_TOKEN:
                try:
                    # Mark as queued in Supabase so the card shows ⏳ immediately
                    if _cid and SUPABASE_URL and SUPABASE_SERVICE_KEY:
                        import urllib.parse as _up_q
                        _supabase_req('PATCH', f'campaigns?id=eq.{_up_q.quote(str(_cid))}',
                            {'brief': json.dumps({
                                'brief': _br, 'channel': _ch, 'budget': _bud,
                                'partner_id': _pid, 'company_name': _co,
                                'sarah_reply': sarah_reply, 'assigned_agent': assigned_agent,
                                'deliverables': all_deliverables,
                                'url': _url, 'services': _svcs, 'usps': _usps,
                                'locations': _locs, 'offer': _offer, 'competitors': _comps,
                                'headlines': _heads, 'descriptions': _descs,
                                'biz_name': _biz, 'phone': _phone,
                                'target_cpa': _tcpa, 'bid_strategy': _bid,
                                'ads_build_status': 'queued',
                                'ads_resource_name': '', 'ads_build_detail': '',
                            })}, service_role=True)

                    # Get the Google Ads customer ID and OAuth token for this workspace
                    ads_account_id, _ = _get_credential(_ws, 'google_ads')
                    ads_token = google_get_access_token(_ws) if _ws else None

                    if not ads_account_id:
                        ads_build_status  = 'error'
                        ads_build_detail  = 'Google Ads Customer ID not connected — go to Settings → Integrations to add it.'
                    elif not ads_token:
                        ads_build_status  = 'error'
                        ads_build_detail  = 'Google OAuth token missing or expired — reconnect Google in Settings → Integrations.'
                    else:
                        campaign_data = {
                            'name':         _name,
                            'budget':       _bud,
                            'url':          _url,
                            'bid_strategy': _bid,
                            'target_cpa':   _tcpa,
                            'locations':    _locs,
                            'services':     _svcs,
                            'usps':         _usps,
                            'headlines':    _heads,
                            'descriptions': _descs,
                            'biz_name':     _biz,
                            'offer':        _offer,
                            'competitors':  _comps,
                            'company_name': _co,
                        }
                        rn, err = _create_google_ads_campaign_live(
                            _ws, campaign_data, ads_token, ads_account_id,
                            GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_LOGIN_CUSTOMER_ID
                        )
                        if rn:
                            ads_build_status  = 'live'
                            ads_resource_name = rn
                            ads_build_detail  = f'Campaign created PAUSED — review in Google Ads then enable when ready.'
                            print(f'  ✅ Google Ads campaign live: {rn}')
                        else:
                            ads_build_status  = 'error'
                            ads_build_detail  = err or 'Unknown Google Ads API error'
                except Exception as e:
                    ads_build_status = 'error'
                    ads_build_detail = str(e)[:200]
                    print(f'  ❌ Google Ads build exception: {e}')
            elif is_google_ads_campaign and not GOOGLE_ADS_DEVELOPER_TOKEN:
                ads_build_status = 'error'
                ads_build_detail = 'GOOGLE_ADS_DEVELOPER_TOKEN not set — add to Railway environment variables.'

            new_brief_blob = json.dumps({
                'brief':          _br,
                'channel':        _ch,
                'budget':         _bud,
                'partner_id':     _pid,
                'company_name':   _co,
                'sarah_reply':    sarah_reply,
                'assigned_agent': assigned_agent,
                'deliverables':   all_deliverables,
                # Extended fields
                'url': _url, 'services': _svcs, 'usps': _usps, 'locations': _locs,
                'offer': _offer, 'competitors': _comps, 'headlines': _heads,
                'descriptions': _descs, 'biz_name': _biz, 'phone': _phone,
                'target_cpa': _tcpa, 'bid_strategy': _bid,
                # Ads build result
                'ads_build_status':  ads_build_status,
                'ads_resource_name': ads_resource_name,
                'ads_build_detail':  ads_build_detail,
            })

            # — PATCH Supabase with results —
            if _cid and SUPABASE_URL and SUPABASE_SERVICE_KEY:
                try:
                    patch_payload = {
                        'brief':  new_brief_blob,
                        'status': 'reviewing' if sarah_reply else 'pending',
                    }
                    import urllib.parse as _up_bg
                    _supabase_req(
                        'PATCH',
                        f'campaigns?id=eq.{_up_bg.quote(str(_cid))}',
                        patch_payload,
                        service_role=True
                    )
                    print(f'  ✅ Campaign #{_cid} updated with Sarah+{assigned_agent} output')
                except Exception as e:
                    print(f'  ⚠️  Campaign async PATCH error: {e}')

        t = threading.Thread(target=_bg_process, daemon=True)
        t.start()

    def _handle_campaigns_list(self):
        """List all campaign requests for a workspace (reads from campaigns table)."""
        import urllib.parse as _up_cl
        parsed = _up_cl.urlparse(self.path)
        params = _up_cl.parse_qs(parsed.query)
        workspace_id = params.get('workspaceId', [''])[0].strip()

        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'campaigns': []}); return
        try:
            # Filter by client = workspace_id (our repurposed column)
            rows = _supabase_req(
                'GET',
                f'campaigns?client=eq.{_up_cl.quote(workspace_id)}&order=created_at.desc',
            )
            # Unpack each row: parse brief JSON blob and remap columns
            out = []
            for c in (rows or []):
                blob = {}
                try:
                    blob = json.loads(c.get('brief', '{}'))
                except Exception:
                    pass
                out.append({
                    'id':             c['id'],
                    'name':           c.get('name', ''),
                    'type':           c.get('types', ''),
                    'channel':        blob.get('channel') or c.get('assigned', ''),
                    'budget':         blob.get('budget', ''),
                    'audience':       c.get('audience', ''),
                    'brief':          blob.get('brief', ''),
                    'status':         c.get('status', 'pending'),
                    'created_at':     c.get('created_at', ''),
                    'workspace_id':   c.get('client', ''),
                    'company_name':   blob.get('company_name', ''),
                    'sarah_reply':      blob.get('sarah_reply', ''),
                    'client_reply':     blob.get('client_reply') or c.get('client_reply', ''),
                    'sarah_followup':   blob.get('sarah_followup', ''),
                    'sarah_followup_at':blob.get('sarah_followup_at', ''),
                    'assigned_agent':   blob.get('assigned_agent', ''),
                    'deliverables':     blob.get('deliverables', []),
                    # Google Ads build status (stored in brief blob)
                    'ads_build_status':  blob.get('ads_build_status', ''),
                    'ads_resource_name': blob.get('ads_resource_name', ''),
                    'ads_build_detail':  blob.get('ads_build_detail', ''),
                })
            self._json(200, {'campaigns': out})
        except Exception as e:
            self._error(500, str(e))

    def _handle_campaign_updates_get(self):
        """Return agency updates for a campaign.

        Updates are stored in the 'brief' JSON blob of the campaigns table.
        Sarah's reply is returned as the first (and currently only) update.
        Future agents can be added by appending to a 'updates' array in the blob.
        """
        import urllib.parse as _up_cu
        parsed = _up_cu.urlparse(self.path)
        params = _up_cu.parse_qs(parsed.query)
        campaign_id = params.get('campaignId', [''])[0].strip()

        if not campaign_id:
            self._error(400, 'campaignId required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'updates': []}); return
        try:
            rows = _supabase_req(
                'GET',
                f'campaigns?id=eq.{_up_cu.quote(str(campaign_id))}&select=brief,created_at&limit=1',
            )
            if not rows:
                self._json(200, {'updates': []}); return
            blob = {}
            try:
                blob = json.loads(rows[0].get('brief', '{}'))
            except Exception:
                pass
            updates = []
            sarah_reply    = blob.get('sarah_reply', '')
            client_reply   = blob.get('client_reply', '')
            sarah_followup = blob.get('sarah_followup', '')
            followup_at    = blob.get('sarah_followup_at', '')

            if sarah_reply:
                updates.append({
                    'author':     'Sarah Lin · CMO',
                    'text':       sarah_reply,
                    'created_at': rows[0].get('created_at', ''),
                    'role':       'agency',
                })
            if client_reply:
                updates.append({
                    'author':     'You',
                    'text':       client_reply,
                    'created_at': blob.get('client_replied_at', ''),
                    'role':       'client',
                })
            if sarah_followup:
                updates.append({
                    'author':     'Sarah Lin · CMO',
                    'text':       sarah_followup,
                    'created_at': followup_at,
                    'role':       'agency',
                })
            # Additional agent updates
            for u in blob.get('updates', []):
                updates.append(u)
            self._json(200, {
                'updates':        updates,
                'sarah_followup': sarah_followup,
                'client_reply':   client_reply,
            })
        except Exception as e:
            self._error(500, str(e))

    def _handle_campaign_reply(self):
        """Store client's reply and trigger Sarah's follow-up response in background."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Bad request'); return

        campaign_id = body.get('campaignId', '').strip()
        reply_text  = body.get('reply', '').strip()

        if not campaign_id:
            self._error(400, 'campaignId required'); return
        if not reply_text:
            self._error(400, 'reply required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'ok': True, 'offline': True}); return

        try:
            import datetime as _dt
            now_iso = _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

            # 1. Fetch the existing campaign row
            rows = _supabase_req(
                'GET',
                f'campaigns?id=eq.{urllib.parse.quote(str(campaign_id))}&select=brief&limit=1',
            )
            blob = {}
            if rows:
                try:
                    blob = json.loads(rows[0].get('brief', '{}'))
                except Exception:
                    pass

            blob['client_reply']      = reply_text
            blob['client_replied_at'] = now_iso
            # Clear any previous followup so UI shows "typing…" while Sarah composes
            blob.pop('sarah_followup', None)
            blob.pop('sarah_followup_at', None)

            # 2a. Update brief blob immediately
            _supabase_req(
                'PATCH',
                f'campaigns?id=eq.{urllib.parse.quote(str(campaign_id))}',
                payload={'brief': json.dumps(blob)},
            )

            # 2b. Try dedicated columns (schema may not have them yet)
            try:
                _supabase_req(
                    'PATCH',
                    f'campaigns?id=eq.{urllib.parse.quote(str(campaign_id))}',
                    payload={'client_reply': reply_text, 'client_replied_at': now_iso},
                )
            except Exception:
                pass

            self._json(200, {'ok': True, 'replied_at': now_iso})

            # 3. Background: Sarah reads the client reply and responds
            _cid         = campaign_id
            _reply       = reply_text
            _blob_snap   = dict(blob)

            def _sarah_followup_bg():
                if not API_KEY:
                    return
                try:
                    sarah_orig   = _blob_snap.get('sarah_reply', '')
                    company      = _blob_snap.get('company_name', 'the client')
                    channel      = _blob_snap.get('channel', '')
                    budget       = _blob_snap.get('budget', '')
                    orig_brief   = _blob_snap.get('brief', '')
                    assigned     = _blob_snap.get('assigned_agent', 'the specialist')
                    cmp_name     = _blob_snap.get('name', orig_brief[:40])

                    followup_prompt = (
                        f"You are Sarah Lin, CMO at the agency. You previously sent a campaign "
                        f"assessment to {company} and they have now replied.\n\n"
                        f"Campaign: {cmp_name}\n"
                        f"Channel: {channel}\n"
                        f"Budget: {'$'+budget+'/mo' if budget else 'TBD'}\n"
                        f"Original brief: {orig_brief}\n\n"
                        f"Your original assessment:\n{sarah_orig}\n\n"
                        f"Client's reply:\n{_reply}\n\n"
                        f"Respond as Sarah. Be warm and direct. "
                        f"Specifically acknowledge what they said. "
                        f"If they've requested any changes, confirm exactly what you're updating and tell them it will be passed to {assigned} immediately. "
                        f"If they've asked questions, answer them clearly. "
                        f"Close with a clear next step so they know what happens from here. "
                        f"Keep it under 150 words — punchy, not fluffy."
                    )
                    followup = call_anthropic(
                        API_KEY, AGENT_PROMPTS.get('sarah', ''),
                        [{'role': 'user', 'content': followup_prompt}],
                        max_tokens=400,
                    )
                    if not followup:
                        return

                    import datetime as _dt2
                    fu_at = _dt2.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

                    # Fetch fresh blob (it may have been updated since we last read)
                    fresh_rows = _supabase_req(
                        'GET',
                        f'campaigns?id=eq.{urllib.parse.quote(str(_cid))}&select=brief&limit=1',
                    )
                    fresh_blob = {}
                    if fresh_rows:
                        try:
                            fresh_blob = json.loads(fresh_rows[0].get('brief', '{}'))
                        except Exception:
                            pass

                    fresh_blob['sarah_followup']    = followup
                    fresh_blob['sarah_followup_at'] = fu_at

                    _supabase_req(
                        'PATCH',
                        f'campaigns?id=eq.{urllib.parse.quote(str(_cid))}',
                        payload={'brief': json.dumps(fresh_blob)},
                    )
                    print(f'  💬 Sarah followup saved for campaign #{_cid}')
                except Exception as e:
                    print(f'  ⚠️  Sarah followup bg error: {e}')

            threading.Thread(target=_sarah_followup_bg, daemon=True).start()

        except Exception as e:
            print(f'[campaign/reply] error: {e}')
            self._error(500, str(e))

    def _handle_campaign_retry_build(self):
        """Re-trigger Google Ads campaign build for a campaign that previously failed."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Bad request'); return

        campaign_id = body.get('campaignId', '').strip()
        if not campaign_id:
            self._error(400, 'campaignId required'); return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            self._json(200, {'ok': True, 'offline': True}); return

        try:
            rows = _supabase_req(
                'GET',
                f'campaigns?id=eq.{urllib.parse.quote(str(campaign_id))}&select=brief,client,name&limit=1',
            )
            if not rows:
                self._error(404, 'Campaign not found'); return

            row  = rows[0]
            blob = {}
            try:
                blob = json.loads(row.get('brief', '{}'))
            except Exception:
                pass

            workspace_id = row.get('client', '')
            campaign_name = row.get('name', blob.get('name', 'Campaign'))

            # Mark as queued immediately (service_role bypasses RLS)
            blob['ads_build_status']  = 'queued'
            blob['ads_build_detail']  = 'Retry queued…'
            blob['ads_resource_name'] = ''
            _supabase_req('PATCH',
                f'campaigns?id=eq.{urllib.parse.quote(str(campaign_id))}',
                {'brief': json.dumps(blob)},
                service_role=True,
            )

            self._json(200, {'ok': True, 'status': 'queued'})

            # Background: actually run the build
            _cid = campaign_id
            _ws  = workspace_id
            _name = campaign_name

            def _retry_bg():
                try:
                    ads_account_id, _ = _get_credential(_ws, 'google_ads')
                    ads_token         = google_get_access_token(_ws) if _ws else None

                    print(f'  🔄 Retry build: ws={_ws!r} account={ads_account_id!r} token_ok={bool(ads_token)}')

                    fresh = _supabase_req('GET',
                        f'campaigns?id=eq.{urllib.parse.quote(str(_cid))}&select=brief&limit=1',
                        service_role=True)
                    fresh_blob = {}
                    if fresh:
                        try: fresh_blob = json.loads(fresh[0].get('brief', '{}'))
                        except Exception: pass

                    if not ads_account_id:
                        fresh_blob['ads_build_status'] = 'error'
                        fresh_blob['ads_build_detail'] = 'Google Ads Customer ID not connected — go to Settings → Integrations to add it.'
                    elif not ads_token:
                        fresh_blob['ads_build_status'] = 'error'
                        fresh_blob['ads_build_detail'] = 'Google OAuth token missing or expired — reconnect Google in Settings → Integrations.'
                    else:
                        campaign_data = {
                            'name':         _name,
                            'budget':       fresh_blob.get('budget', ''),
                            'url':          fresh_blob.get('url', ''),
                            'bid_strategy': fresh_blob.get('bid_strategy', ''),
                            'target_cpa':   fresh_blob.get('target_cpa', ''),
                            'locations':    fresh_blob.get('locations', ''),
                            'services':     fresh_blob.get('services', ''),
                            'usps':         fresh_blob.get('usps', ''),
                            'headlines':    fresh_blob.get('headlines', ''),
                            'descriptions': fresh_blob.get('descriptions', ''),
                            'biz_name':     fresh_blob.get('biz_name', ''),
                            'offer':        fresh_blob.get('offer', ''),
                            'competitors':  fresh_blob.get('competitors', ''),
                            'company_name': fresh_blob.get('company_name', ''),
                        }
                        rn, err = _create_google_ads_campaign_live(
                            _ws, campaign_data, ads_token, ads_account_id,
                            GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_LOGIN_CUSTOMER_ID,
                        )
                        if rn:
                            fresh_blob['ads_build_status']  = 'live'
                            fresh_blob['ads_resource_name'] = rn
                            fresh_blob['ads_build_detail']  = 'Campaign created PAUSED — review in Google Ads then enable when ready.'
                            print(f'  ✅ Retry build succeeded: {rn}')
                        else:
                            fresh_blob['ads_build_status'] = 'error'
                            fresh_blob['ads_build_detail'] = err or 'Unknown Google Ads API error'
                            print(f'  ❌ Retry build failed: {err}')

                    _supabase_req('PATCH',
                        f'campaigns?id=eq.{urllib.parse.quote(str(_cid))}',
                        {'brief': json.dumps(fresh_blob)},
                        service_role=True,
                    )
                except Exception as e:
                    print(f'  ⚠️  retry-build bg error: {e}')

            threading.Thread(target=_retry_bg, daemon=True).start()

        except Exception as e:
            print(f'[campaign/retry-build] error: {e}')
            self._error(500, str(e))

    # ── Brand Hub ─────────────────────────────────────────────────────────────

    def _handle_brand_hub_get(self):
        """GET /api/brand-hub?workspaceId=X — load brand hub data from Supabase."""
        import urllib.parse as _up_bh
        params = _up_bh.parse_qs(_up_bh.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        data = _load_brand_hub(workspace_id)
        self._json(200, {'data': data})

    def _handle_brand_hub_post(self):
        """POST /api/brand-hub — save brand hub data to Supabase."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = body.get('workspaceId', '').strip()
        data = body.get('data', {})
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        if not isinstance(data, dict):
            self._error(400, 'data must be an object'); return
        ok = _save_brand_hub(workspace_id, data)
        self._json(200, {'ok': ok})

    def _handle_brand_hub_prefill_get(self):
        """GET /api/brand-hub/prefill?url=... — scrape URL and return Brand Hub fields via AI."""
        import urllib.parse as _up_bhp
        import re as _re_bhp
        params = _up_bhp.parse_qs(_up_bhp.urlparse(self.path).query)
        url = params.get('url', [''])[0].strip()
        if not url:
            self._error(400, 'url required'); return
        if not url.startswith('http'):
            url = 'https://' + url

        # Fetch website HTML
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; ClickPoint/1.0; brand-discovery)',
                'Accept': 'text/html,application/xhtml+xml',
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                raw_html = r.read(200_000).decode('utf-8', errors='ignore')
        except Exception as e:
            self._error(502, f'Could not fetch URL: {e}'); return

        # Strip HTML tags and collapse whitespace
        text = _re_bhp.sub(r'<[^>]+>', ' ', raw_html)
        text = _re_bhp.sub(r'\s+', ' ', text).strip()[:12000]

        prompt = f"""You are a brand analyst. Based on the website text below, extract brand information and return ONLY a valid JSON object. Include every key you can confidently determine — omit keys you cannot. Use these exact key names:

IDENTITY:
- bBrandName: brand/company name
- bTagline: tagline or slogan
- bIndustry: industry (e.g. Digital Marketing, SaaS, Healthcare)
- bWebsite: the URL
- bMission: mission statement or why they exist
- bStory: origin story or brand background
- bVal1, bVal2, bVal3: up to 3 core brand values (single words or short phrases)

SERVICES:
- bServices: services or products, one per line (\\n separated)
- bUsp1, bUsp2, bUsp3: top 3 unique selling points, max 30 chars each

VOICE & MESSAGING:
- bTone: exactly one of: Professional & authoritative | Friendly & conversational | Bold & direct | Empathetic & human | Technical & precise | Playful & creative | Luxury & aspirational | Urgent & action-oriented
- bFormality: exactly one of: Very formal | Formal | Neutral | Casual | Very casual / colloquial
- bValueProp: 1-2 sentence value proposition
- bPillar1Title, bPillar1Desc: first messaging pillar name and description
- bPillar2Title, bPillar2Desc: second messaging pillar name and description
- bPillar3Title, bPillar3Desc: third messaging pillar name and description
- bWordsUse: comma-separated words/phrases that match the brand voice
- bWordsAvoid: comma-separated words/phrases that feel off-brand
- bHeadlineStyle: describe the headline writing style (length, punctuation, verb usage)
- bBodyStyle: describe body copy style (sentence length, register, any patterns)
- bSocialNotes: notes on social media tone or content style

AUDIENCE:
- bBizType: exactly one of: B2C (direct to consumer) | B2B (business to business) | Both B2C and B2B | D2C (direct to consumer, e-commerce) | Marketplace / Platform
- bGeo: geographic focus (city, state, country, or global)
- bAudience: primary audience description (demographics, job titles, interests)
- bAudSecondary: secondary audience if applicable
- bPersonaName: buyer persona name and role (e.g. "Sarah — Head of Marketing")
- bPersonaDemog: age range and background of persona
- bPersonaDesc: what this persona cares about day-to-day
- bPainPoints: key pain points or frustrations the brand solves
- bCompetitors: comma-separated competitor names if mentioned

GUIDELINES:
- bCPillar1, bCPillar2, bCPillar3: content pillars (themes every piece of content should cover)
- bDo1, bDo2, bDo3: brand do's (communication behaviours to always follow)
- bDont1, bDont2, bDont3: brand don'ts (things to never do or say)
- bComplianceNotes: any legal, regulatory, or disclaimer requirements

Website URL: {url}

Website text:
{text}

Return ONLY the JSON object, no explanation, no markdown fences."""

        try:
            ai_resp = call_anthropic(
                API_KEY,
                'You extract structured brand data from websites. Return only valid JSON.',
                [{'role': 'user', 'content': prompt}],
                max_tokens=4000,
            )
            # Strip any markdown fences if model adds them anyway
            cleaned = _re_bhp.sub(r'^```[a-z]*\n?|\n?```$', '', ai_resp.strip(), flags=_re_bhp.MULTILINE).strip()
            data = json.loads(cleaned)
            # Ensure bWebsite is set
            if not data.get('bWebsite'):
                data['bWebsite'] = url
            self._json(200, {'ok': True, 'data': data})
        except Exception as e:
            print(f'  ⚠️  brand-hub prefill AI error: {e}')
            self._error(500, f'AI extraction failed: {e}')

    def _handle_workspace_tracking_status_get(self):
        """GET /api/workspace/tracking-status?workspaceId=X"""
        import urllib.parse as _up_ts
        params = _up_ts.parse_qs(_up_ts.urlparse(self.path).query)
        wid = params.get('workspaceId', [''])[0].strip()
        if not wid:
            self._error(400, 'workspaceId required'); return
        key = f'conv_tracking_{wid}'
        status = 'unsure'
        try:
            import urllib.parse as _up_ts2
            req = urllib.request.Request(
                f'{SUPABASE_URL}/rest/v1/platform_settings?key=eq.{_up_ts2.quote(key)}',
                headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                rows = json.loads(r.read())
            if rows:
                status = rows[0]['value']
        except Exception as e:
            print(f'  ⚠️  tracking-status GET error: {e}')
        self._json(200, {'status': status})

    def _handle_workspace_tracking_status_post(self):
        """POST /api/workspace/tracking-status — save conversion tracking status."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        wid = body.get('workspaceId', '').strip()
        status = body.get('status', 'unsure').strip()
        if not wid:
            self._error(400, 'workspaceId required'); return
        if status not in ('unsure', 'working', 'needs_setup'):
            self._error(400, 'Invalid status'); return
        key = f'conv_tracking_{wid}'
        try:
            hdrs = {
                'apikey': SUPABASE_SERVICE_KEY,
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                'Content-Type': 'application/json',
                'Prefer': 'resolution=merge-duplicates,return=representation',
            }
            req = urllib.request.Request(
                f'{SUPABASE_URL}/rest/v1/platform_settings',
                data=json.dumps({'key': key, 'value': status}).encode(),
                headers=hdrs, method='POST',
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as e:
            print(f'  ⚠️  tracking-status POST error: {e}')
            self._json(500, {'ok': False}); return
        self._json(200, {'ok': True})

    def _handle_google_auth(self):
        """GET /api/google/auth?workspace_id=X — return Google OAuth URL."""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspace_id', [''])[0].strip()
        url = google_auth_url(workspace_id)
        if not url:
            self._error(503, 'Google OAuth not configured — set GOOGLE_CLIENT_ID'); return
        self._json(200, {'auth_url': url})

    def _handle_google_callback(self):
        """GET /api/google/callback — exchange code, store tokens, close popup."""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code  = params.get('code',  [''])[0]
        state = params.get('state', [''])[0]
        error = params.get('error', [''])[0]

        def _result_page(status: str, detail: str = '') -> str:
            colour = '#22c55e' if status == 'success' else '#ef4444'
            msg    = 'Google connected successfully!' if status == 'success' else f'Connection failed: {detail}'
            return f"""<!DOCTYPE html><html><head><title>Google OAuth</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
height:100vh;margin:0;background:#0f172a;color:#fff}}
.box{{text-align:center;padding:2rem;border-radius:12px;background:#1e293b;max-width:400px}}
.icon{{font-size:3rem;margin-bottom:1rem}}
.msg{{color:{colour};font-size:1.1rem;margin-bottom:1.5rem}}
button{{background:#6366f1;color:#fff;border:none;padding:.75rem 2rem;border-radius:8px;
cursor:pointer;font-size:1rem}}
</style></head><body><div class="box">
<div class="icon">{'✅' if status == 'success' else '❌'}</div>
<div class="msg">{msg}</div>
<button onclick="window.close()">Close</button>
</div><script>
if('{status}'==='success'){{
  setTimeout(()=>{{
    if(window.opener){{window.opener.postMessage({{type:'google_oauth_success',
      workspace:'{detail}'}}, '*');}}
    window.close();
  }}, 1500);
}}
</script></body></html>"""

        if error:
            self._html(400, _result_page('error', error)); return
        if not code:
            self._html(400, _result_page('error', 'No authorisation code received.')); return
        try:
            tokens, workspace_id = google_exchange_code(code, state)
            # Add expiry timestamp
            tokens['expires_at'] = time.time() + tokens.get('expires_in', 3600)
            google_persist_tokens(workspace_id, tokens)
            print(f'  ✅ Google OAuth complete for workspace: {workspace_id or "unknown"}')
            self._html(200, _result_page('success', workspace_id))
        except Exception as e:
            print(f'  ⚠️  Google callback error: {e}')
            self._html(500, _result_page('error', str(e)))

    def _handle_canva_auth(self):
        """Return the Canva OAuth authorization URL for a workspace."""
        import urllib.parse as _up_ca
        parsed = _up_ca.urlparse(self.path)
        params = _up_ca.parse_qs(parsed.query)
        workspace_id = params.get('workspace_id', [''])[0].strip()
        url = canva_auth_url(workspace_id)
        if not url:
            self._error(503, 'Canva client ID not configured'); return
        self._json(200, {'auth_url': url})

    def _handle_canva_callback(self):
        """Handle Canva OAuth callback — exchange code, store tokens, close popup."""
        import urllib.parse as _up_cb
        parsed = _up_cb.urlparse(self.path)
        params = _up_cb.parse_qs(parsed.query)
        code  = params.get('code',  [''])[0]
        state = params.get('state', [''])[0]
        error = params.get('error', [''])[0]

        if error:
            self._html(400, self._canva_result_page('error', error)); return
        if not code:
            self._html(400, self._canva_result_page('error', 'No code received from Canva.')); return
        try:
            _, workspace_id = canva_exchange_code(code, state)
            print(f'  ✅ Canva OAuth complete for workspace: {workspace_id or "unknown"}')
            self._html(200, self._canva_result_page('success', workspace_id))
        except Exception as e:
            print(f'  ⚠️  Canva callback error: {e}')
            self._html(500, self._canva_result_page('error', str(e)))

    # ── CRM handlers ──────────────────────────────────────────────────────────

    def _handle_crm_contacts_get(self):
        """GET /api/crm/contacts?workspaceId=X"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        try:
            rows = _supabase_req(
                'GET',
                f'crm_contacts?workspace_id=eq.{urllib.parse.quote(workspace_id)}'
                f'&order=created_at.desc'
            )
            self._json(200, {'contacts': rows or []})
        except Exception as e:
            self._error(500, str(e))

    def _handle_crm_contacts_post(self):
        """POST /api/crm/contacts — upsert contact"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        name = (body.get('name') or '').strip()
        if not workspace_id or not name:
            self._error(400, 'workspaceId and name required'); return
        contact_id = body.get('id')
        payload = {
            'workspace_id': workspace_id,
            'name': name,
            'email': body.get('email') or None,
            'phone': body.get('phone') or None,
            'company': body.get('company') or None,
            'title': body.get('title') or None,
            'tags': body.get('tags') or None,
            'notes': body.get('notes') or None,
            'deal_stage': body.get('deal_stage') or 'prospect',
            'deal_value': body.get('deal_value') or None,
        }
        try:
            if contact_id:
                rows = _supabase_req('PATCH', f'crm_contacts?id=eq.{contact_id}', payload)
            else:
                rows = _supabase_req('POST', 'crm_contacts', payload)
            contact = rows[0] if rows else payload
            self._json(200, {'ok': True, 'contact': contact})
        except Exception as e:
            self._error(500, str(e))

    def _handle_crm_contacts_delete(self):
        """DELETE /api/crm/contacts — body: {id}"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        contact_id = body.get('id')
        if not contact_id:
            self._error(400, 'id required'); return
        try:
            _supabase_req('DELETE', f'crm_contacts?id=eq.{contact_id}')
            self._json(200, {'ok': True})
        except Exception as e:
            self._error(500, str(e))

    def _handle_crm_activities_get(self):
        """GET /api/crm/activities?workspaceId=X&contactId=Y"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        contact_id = params.get('contactId', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        path = (f'crm_activities?workspace_id=eq.{urllib.parse.quote(workspace_id)}'
                f'&order=created_at.desc')
        if contact_id:
            path += f'&contact_id=eq.{contact_id}'
        try:
            rows = _supabase_req('GET', path)
            self._json(200, {'activities': rows or []})
        except Exception as e:
            self._error(500, str(e))

    def _handle_crm_activities_post(self):
        """POST /api/crm/activities — log activity"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        contact_id = body.get('contactId')
        activity_type = (body.get('type') or '').strip()
        summary = (body.get('summary') or '').strip()
        if not workspace_id or not contact_id or not activity_type:
            self._error(400, 'workspaceId, contactId, and type required'); return
        try:
            rows = _supabase_req('POST', 'crm_activities', {
                'workspace_id': workspace_id,
                'contact_id': contact_id,
                'type': activity_type,
                'summary': summary,
            })
            # Update last_contact timestamp on the contact
            now_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            try:
                _supabase_req('PATCH', f'crm_contacts?id=eq.{contact_id}',
                              {'last_contact': now_iso})
            except Exception:
                pass
            self._json(200, {'ok': True, 'activity': rows[0] if rows else {}})
        except Exception as e:
            self._error(500, str(e))

    def _handle_crm_ai_score(self):
        """POST /api/crm/ai-score — score a deal with Claude"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        contact_id = body.get('contactId')
        if not workspace_id or not contact_id:
            self._error(400, 'workspaceId and contactId required'); return
        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'ANTHROPIC_API_KEY not set'); return
        try:
            contacts = _supabase_req('GET', f'crm_contacts?id=eq.{contact_id}&select=*')
            if not contacts:
                self._error(404, 'Contact not found'); return
            contact = contacts[0]
            activities = _supabase_req(
                'GET',
                f'crm_activities?contact_id=eq.{contact_id}&order=created_at.desc&limit=10'
            )
            activity_summary = '\n'.join(
                f'- [{a.get("type","note")}] {a.get("summary","")}'
                for a in (activities or [])
            ) or 'No activities recorded'
            contact_info = (
                f'Name: {contact.get("name","")}\n'
                f'Company: {contact.get("company","")}\n'
                f'Title: {contact.get("title","")}\n'
                f'Deal Stage: {contact.get("deal_stage","prospect")}\n'
                f'Deal Value: {contact.get("deal_value","")}\n'
                f'Notes: {contact.get("notes","")}\n'
                f'Tags: {", ".join(contact.get("tags") or [])}\n'
                f'\nRecent Activities:\n{activity_summary}'
            )
            system = (
                'You are Sarah Lin, CMO. Review this contact\'s deal stage, notes, and activity history. '
                'Score the deal 1-10 (10=ready to close) and recommend one specific next action. '
                'Respond as JSON only: {"score": <int>, "next_action": "<string>", "reasoning": "<string>"}'
            )
            raw = call_anthropic(effective_key, system,
                                 [{'role': 'user', 'content': contact_info}], max_tokens=400)
            cleaned = raw.replace('```json', '').replace('```', '').strip()
            result = json.loads(cleaned)
            score = int(result.get('score', 5))
            next_action = str(result.get('next_action', ''))
            reasoning = str(result.get('reasoning', ''))
            # Save back to contact
            _supabase_req('PATCH', f'crm_contacts?id=eq.{contact_id}',
                          {'ai_score': score, 'next_action': next_action})
            self._json(200, {'ok': True, 'score': score,
                             'next_action': next_action, 'reasoning': reasoning})
        except Exception as e:
            print(f'  CRM AI score error: {e}')
            self._error(500, str(e))

    # ── Reputation Management handlers ────────────────────────────────────────

    def _handle_reputation_get(self):
        """GET /api/reputation?workspaceId=X"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        try:
            reviews = _supabase_req(
                'GET',
                f'reputation_reviews?workspace_id=eq.{urllib.parse.quote(workspace_id)}'
                f'&order=created_at.desc'
            )
            reviews = reviews or []
            # Compute stats
            total = len(reviews)
            avg_rating = round(sum(r.get('rating', 0) for r in reviews) / max(1, total), 2)
            by_platform = {}
            pending_count = 0
            for r in reviews:
                plat = r.get('platform', 'other')
                by_platform[plat] = by_platform.get(plat, 0) + 1
                if r.get('status') == 'pending':
                    pending_count += 1
            stats = {
                'total': total,
                'avg_rating': avg_rating,
                'by_platform': by_platform,
                'pending_count': pending_count,
            }
            self._json(200, {'reviews': reviews, 'stats': stats})
        except Exception as e:
            self._error(500, str(e))

    def _handle_reputation_reviews_post(self):
        """POST /api/reputation/reviews — add review"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        platform = (body.get('platform') or '').strip()
        if not workspace_id or not platform:
            self._error(400, 'workspaceId and platform required'); return
        payload = {
            'workspace_id': workspace_id,
            'platform': platform,
            'reviewer_name': body.get('reviewer_name') or None,
            'rating': body.get('rating') or None,
            'content': body.get('content') or None,
            'review_date': body.get('review_date') or None,
            'external_id': body.get('external_id') or None,
            'status': 'pending',
        }
        try:
            rows = _supabase_req('POST', 'reputation_reviews', payload)
            self._json(200, {'ok': True, 'review': rows[0] if rows else payload})
        except Exception as e:
            self._error(500, str(e))

    def _handle_reputation_respond(self):
        """POST /api/reputation/respond — AI-draft response and save"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        review_id = body.get('reviewId')
        workspace_id = (body.get('workspaceId') or '').strip()
        tone = (body.get('tone') or 'professional and warm').strip()
        if not review_id or not workspace_id:
            self._error(400, 'reviewId and workspaceId required'); return
        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'ANTHROPIC_API_KEY not set'); return
        try:
            reviews = _supabase_req('GET', f'reputation_reviews?id=eq.{review_id}&select=*')
            if not reviews:
                self._error(404, 'Review not found'); return
            review = reviews[0]
            rating = review.get('rating', 3)
            content = review.get('content', '')
            reviewer = review.get('reviewer_name', 'Customer')
            system = (
                f'You are a professional reputation manager. Write a concise, genuine, '
                f'brand-appropriate response to this {rating}-star review. Tone: {tone}. '
                f'Be specific to what they wrote. Don\'t be sycophantic. Under 80 words.'
            )
            user_msg = (
                f'Reviewer: {reviewer}\nRating: {rating}/5\nReview: {content}'
            )
            response_text = call_anthropic(effective_key, system,
                                           [{'role': 'user', 'content': user_msg}],
                                           max_tokens=200)
            # Save response and update status
            _supabase_req('PATCH', f'reputation_reviews?id=eq.{review_id}', {
                'response': response_text,
                'status': 'responded',
            })
            self._json(200, {'ok': True, 'response': response_text})
        except Exception as e:
            print(f'  Reputation respond error: {e}')
            self._error(500, str(e))

    def _handle_reputation_request(self):
        """POST /api/reputation/request — send review request email"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        customer_email = (body.get('customerEmail') or '').strip()
        customer_name = (body.get('customerName') or 'Valued Customer').strip()
        business_name = (body.get('businessName') or 'our business').strip()
        review_url = (body.get('reviewUrl') or '').strip()
        if not workspace_id or not customer_email:
            self._error(400, 'workspaceId and customerEmail required'); return
        link_html = (f'<a href="{review_url}" style="display:inline-block;margin-top:16px;'
                     f'padding:12px 24px;background:#1C3A2E;color:#fff;border-radius:6px;'
                     f'text-decoration:none;font-weight:600;">Leave a Review</a>'
                     if review_url else '')
        html = f"""<div style="font-family:-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:28px;">
        <h2 style="color:#1C3A2E;margin-bottom:12px;">Hi {customer_name},</h2>
        <p style="color:#444;line-height:1.7;font-size:15px;">
            Thank you so much for choosing {business_name}. We really hope you had a great experience,
            and we'd love to hear what you think.
        </p>
        <p style="color:#444;line-height:1.7;font-size:15px;">
            If you have a moment, sharing your honest feedback helps others find us — and helps us keep
            improving. It only takes a minute.
        </p>
        {link_html}
        <p style="margin-top:24px;color:#888;font-size:13px;">
            If you have any questions or concerns, just reply to this email. We're always happy to help.
        </p>
        <p style="color:#444;font-size:14px;">Warm regards,<br>The {business_name} team</p>
        </div>"""
        ok = _send_email(customer_email, f'How was your experience with {business_name}?', html)
        self._json(200, {'ok': ok})

    # ── Local SEO handlers ────────────────────────────────────────────────────

    def _handle_local_seo_get(self):
        """GET /api/local-seo?workspaceId=X"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        try:
            rows = _supabase_req(
                'GET',
                f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}&limit=1'
            )
            self._json(200, {'listing': rows[0] if rows else None})
        except Exception as e:
            self._error(500, str(e))

    def _handle_local_seo_nap(self):
        """POST /api/local-seo/nap — upsert listing data"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        now_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        payload = {
            'workspace_id': workspace_id,
            'business_name': body.get('business_name') or None,
            'address': body.get('address') or None,
            'city': body.get('city') or None,
            'state': body.get('state') or None,
            'postcode': body.get('postcode') or None,
            'phone': body.get('phone') or None,
            'website': body.get('website') or None,
            'categories': body.get('categories') or None,
            'description': body.get('description') or None,
            'hours': body.get('hours') or {},
            'updated_at': now_iso,
        }
        try:
            # Check existing
            rows = _supabase_req(
                'GET',
                f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}&limit=1'
            )
            if rows:
                result = _supabase_req('PATCH',
                    f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}',
                    payload)
            else:
                result = _supabase_req('POST', 'local_listings', payload)
            listing = result[0] if result else payload
            self._json(200, {'ok': True, 'listing': listing})
        except Exception as e:
            self._error(500, str(e))

    def _handle_local_seo_audit(self):
        """POST /api/local-seo/audit — run AI audit on listing data"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'ANTHROPIC_API_KEY not set'); return
        try:
            rows = _supabase_req(
                'GET',
                f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}&limit=1'
            )
            listing = rows[0] if rows else {}
            listing_text = (
                f'Business Name: {listing.get("business_name","")}\n'
                f'Address: {listing.get("address","")}, {listing.get("city","")}, '
                f'{listing.get("state","")} {listing.get("postcode","")}\n'
                f'Phone: {listing.get("phone","")}\n'
                f'Website: {listing.get("website","")}\n'
                f'Categories: {", ".join(listing.get("categories") or [])}\n'
                f'Description: {listing.get("description","")}\n'
                f'Hours: {json.dumps(listing.get("hours") or {})}\n'
                f'Listing Status: {json.dumps(listing.get("listing_status") or {})}'
            )
            system = (
                'You are Raj Nair, SEO & Analytics Specialist. Audit this local business listing data '
                'for completeness and local SEO best practice. '
                'Return JSON only: {"score": <int 1-10>, "issues": [{"severity": "high"|"medium"|"low", '
                '"issue": "<string>", "fix": "<string>"}], "opportunities": ["<string>"], '
                '"next_steps": ["<string>"]}'
            )
            raw = call_anthropic(effective_key, system,
                                 [{'role': 'user', 'content': listing_text}], max_tokens=800)
            cleaned = raw.replace('```json', '').replace('```', '').strip()
            audit = json.loads(cleaned)
            # Save last_audit timestamp
            now_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            try:
                if rows:
                    _supabase_req('PATCH',
                        f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}',
                        {'last_audit': now_iso})
            except Exception:
                pass
            self._json(200, {'ok': True, 'audit': audit})
        except Exception as e:
            print(f'  Local SEO audit error: {e}')
            self._error(500, str(e))

    def _handle_local_seo_listing_patch(self):
        """PATCH /api/local-seo/listing — update a platform's listing status"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        platform = (body.get('platform') or '').strip()
        status = (body.get('status') or '').strip()
        valid_platforms = {'google', 'bing', 'apple_maps', 'yelp', 'facebook',
                           'yellow_pages', 'foursquare', 'true_local'}
        valid_statuses = {'claimed', 'unclaimed', 'pending', 'na'}
        if not workspace_id or not platform or not status:
            self._error(400, 'workspaceId, platform, and status required'); return
        if platform not in valid_platforms:
            self._error(400, f'Invalid platform. Must be one of: {", ".join(valid_platforms)}'); return
        if status not in valid_statuses:
            self._error(400, f'Invalid status. Must be one of: {", ".join(valid_statuses)}'); return
        try:
            rows = _supabase_req(
                'GET',
                f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}&limit=1'
            )
            if not rows:
                self._error(404, 'Listing not found'); return
            current_status = rows[0].get('listing_status') or {}
            current_status[platform] = status
            _supabase_req('PATCH',
                f'local_listings?workspace_id=eq.{urllib.parse.quote(workspace_id)}',
                {'listing_status': current_status})
            self._json(200, {'ok': True, 'listing_status': current_status})
        except Exception as e:
            self._error(500, str(e))

    # ── Social Publishing handlers ────────────────────────────────────────────

    def _handle_social_accounts_get(self):
        """GET /api/social/accounts?workspaceId=X"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        try:
            rows = _supabase_req(
                'GET',
                f'social_accounts?workspace_id=eq.{urllib.parse.quote(workspace_id)}'
                f'&select=id,workspace_id,platform,account_name,account_id,page_id,'
                f'token_type,expires_at,status,created_at'
            )
            self._json(200, {'accounts': rows or []})
        except Exception as e:
            self._error(500, str(e))

    def _handle_social_posts_get(self):
        """GET /api/social/posts?workspaceId=X&status=X"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        status_filter = params.get('status', [''])[0].strip()
        if not workspace_id:
            self._error(400, 'workspaceId required'); return
        path = (f'social_posts?workspace_id=eq.{urllib.parse.quote(workspace_id)}'
                f'&order=created_at.desc')
        if status_filter:
            path += f'&status=eq.{urllib.parse.quote(status_filter)}'
        try:
            rows = _supabase_req('GET', path)
            self._json(200, {'posts': rows or []})
        except Exception as e:
            self._error(500, str(e))

    def _handle_social_auth_get(self):
        """GET /api/social/auth/:platform?workspaceId=X — return OAuth URL"""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        workspace_id = params.get('workspaceId', [''])[0].strip()
        # Extract platform from path: /api/social/auth/facebook
        parts = parsed.path.rstrip('/').split('/')
        platform = parts[-1] if parts else ''
        callback_base = PLATFORM_URL
        if platform == 'facebook' or platform == 'instagram':
            if not META_APP_ID:
                self._error(503, 'META_APP_ID not configured'); return
            callback = f'{callback_base}/api/social/callback/facebook'
            params_str = urllib.parse.urlencode({
                'client_id': META_APP_ID,
                'redirect_uri': callback,
                'scope': 'pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish',
                'state': workspace_id,
                'response_type': 'code',
            })
            auth_url = f'https://www.facebook.com/dialog/oauth?{params_str}'
        elif platform == 'linkedin':
            if not LINKEDIN_CLIENT_ID:
                self._error(503, 'LINKEDIN_CLIENT_ID not configured'); return
            callback = f'{callback_base}/api/social/callback/linkedin'
            params_str = urllib.parse.urlencode({
                'response_type': 'code',
                'client_id': LINKEDIN_CLIENT_ID,
                'redirect_uri': callback,
                'scope': 'w_member_social,r_liteprofile',
                'state': workspace_id,
            })
            auth_url = f'https://www.linkedin.com/oauth/v2/authorization?{params_str}'
        elif platform == 'twitter':
            if not TWITTER_CLIENT_ID:
                self._error(503, 'TWITTER_CLIENT_ID not configured'); return
            callback = f'{callback_base}/api/social/callback/twitter'
            params_str = urllib.parse.urlencode({
                'response_type': 'code',
                'client_id': TWITTER_CLIENT_ID,
                'redirect_uri': callback,
                'scope': 'tweet.write tweet.read users.read',
                'state': workspace_id,
                'code_challenge': 'challenge',
                'code_challenge_method': 'plain',
            })
            auth_url = f'https://twitter.com/i/oauth2/authorize?{params_str}'
        else:
            self._error(400, f'Unsupported platform: {platform}'); return
        self._json(200, {'auth_url': auth_url, 'platform': platform})

    def _handle_social_callback_get(self):
        """GET /api/social/callback/:platform — handle OAuth callback"""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        parts = parsed.path.rstrip('/').split('/')
        platform = parts[-1] if parts else ''
        code = params.get('code', [''])[0]
        workspace_id = params.get('state', [''])[0]
        error = params.get('error', [''])[0]
        if error:
            self._html(400, f'<html><body>OAuth error: {error}</body></html>'); return
        if not code:
            self._html(400, '<html><body>No code received.</body></html>'); return
        callback_base = PLATFORM_URL
        callback = f'{callback_base}/api/social/callback/{platform}'
        try:
            if platform == 'facebook':
                token_url = (
                    f'https://graph.facebook.com/v19.0/oauth/access_token'
                    f'?client_id={META_APP_ID}&redirect_uri={urllib.parse.quote(callback)}'
                    f'&client_secret={META_APP_SECRET}&code={code}'
                )
                with urllib.request.urlopen(token_url, timeout=15) as r:
                    token_data = json.loads(r.read())
                access_token = token_data.get('access_token', '')
                # Get page info
                pages_url = (f'https://graph.facebook.com/v19.0/me/accounts'
                             f'?access_token={access_token}')
                with urllib.request.urlopen(pages_url, timeout=15) as r:
                    pages_data = json.loads(r.read())
                pages = pages_data.get('data', [])
                if pages:
                    page = pages[0]
                    encrypted = encrypt_token(page.get('access_token', access_token))
                    _supabase_req('POST', 'social_accounts', {
                        'workspace_id': workspace_id,
                        'platform': 'facebook',
                        'account_name': page.get('name', ''),
                        'page_id': page.get('id', ''),
                        'encrypted_token': encrypted,
                        'token_type': 'page',
                        'status': 'connected',
                    })
            elif platform == 'linkedin':
                token_data_raw = urllib.parse.urlencode({
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': callback,
                    'client_id': LINKEDIN_CLIENT_ID,
                    'client_secret': LINKEDIN_CLIENT_SECRET,
                }).encode()
                req = urllib.request.Request(
                    'https://www.linkedin.com/oauth/v2/accessToken',
                    data=token_data_raw,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    token_data = json.loads(r.read())
                access_token = token_data.get('access_token', '')
                me_req = urllib.request.Request(
                    'https://api.linkedin.com/v2/me',
                    headers={'Authorization': f'Bearer {access_token}'},
                )
                with urllib.request.urlopen(me_req, timeout=15) as r:
                    me_data = json.loads(r.read())
                encrypted = encrypt_token(access_token)
                _supabase_req('POST', 'social_accounts', {
                    'workspace_id': workspace_id,
                    'platform': 'linkedin',
                    'account_name': f'{me_data.get("localizedFirstName","")} {me_data.get("localizedLastName","")}'.strip(),
                    'account_id': me_data.get('id', ''),
                    'encrypted_token': encrypted,
                    'token_type': 'user',
                    'status': 'connected',
                })
            elif platform == 'twitter':
                token_data_raw = urllib.parse.urlencode({
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': callback,
                    'client_id': TWITTER_CLIENT_ID,
                    'code_verifier': 'challenge',
                }).encode()
                req = urllib.request.Request(
                    'https://api.twitter.com/2/oauth2/token',
                    data=token_data_raw,
                    headers={
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Authorization': 'Basic ' + __import__('base64').b64encode(
                            f'{TWITTER_CLIENT_ID}:{TWITTER_CLIENT_SECRET}'.encode()
                        ).decode(),
                    },
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    token_data = json.loads(r.read())
                access_token = token_data.get('access_token', '')
                encrypted = encrypt_token(access_token)
                _supabase_req('POST', 'social_accounts', {
                    'workspace_id': workspace_id,
                    'platform': 'twitter',
                    'account_name': 'Twitter Account',
                    'encrypted_token': encrypted,
                    'token_type': 'user',
                    'status': 'connected',
                })
            redirect_url = f'{PLATFORM_URL}/workspace.html?social={platform}&connected=1'
            self._html(200, (
                f'<html><head><meta http-equiv="refresh" content="2;url={redirect_url}"></head>'
                f'<body style="font-family:sans-serif;text-align:center;padding:40px;">'
                f'<h2>Connected!</h2><p>Returning to your workspace...</p></body></html>'
            ))
        except Exception as e:
            print(f'  Social callback error ({platform}): {e}')
            self._html(500, f'<html><body>Connection error: {e}</body></html>')

    def _handle_social_draft(self):
        """POST /api/social/draft — generate platform-optimized copy via Claude (Cleo)"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        brief = (body.get('brief') or '').strip()
        platforms = body.get('platforms') or ['facebook', 'instagram', 'linkedin', 'twitter']
        tone = (body.get('tone') or 'engaging and brand-appropriate').strip()
        if not workspace_id or not brief:
            self._error(400, 'workspaceId and brief required'); return
        effective_key = self._effective_api_key()
        if not effective_key:
            self._error(500, 'ANTHROPIC_API_KEY not set'); return
        system = AGENT_PROMPTS.get('cleo', (
            'You are Cleo Chan, Social Media Specialist. You write platform-native social copy. '
            'Twitter: max 280 chars, punchy and direct. '
            'LinkedIn: professional, insight-led, no hashtag spam. '
            'Instagram: visual storytelling, 3-5 relevant hashtags. '
            'Facebook: conversational, community-focused.'
        ))
        platforms_list = ', '.join(platforms)
        user_msg = (
            f'Brief: {brief}\nTone: {tone}\n'
            f'Write optimized post copy for these platforms: {platforms_list}\n\n'
            f'Return JSON only with platform keys: '
            f'{{"facebook": "...", "instagram": "...", "linkedin": "...", "twitter": "..."}}\n'
            f'Only include platforms that were requested: {platforms_list}'
        )
        try:
            raw = call_anthropic(effective_key, system,
                                 [{'role': 'user', 'content': user_msg}], max_tokens=800)
            cleaned = raw.replace('```json', '').replace('```', '').strip()
            # Find JSON object
            start = cleaned.find('{')
            end = cleaned.rfind('}') + 1
            if start >= 0 and end > start:
                drafts = json.loads(cleaned[start:end])
            else:
                drafts = {}
            self._json(200, {'ok': True, 'drafts': drafts})
        except Exception as e:
            print(f'  Social draft error: {e}')
            self._error(500, str(e))

    def _handle_social_posts_post(self):
        """POST /api/social/posts — create a post"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        workspace_id = (body.get('workspaceId') or '').strip()
        platforms = body.get('platforms') or []
        content = (body.get('content') or '').strip()
        if not workspace_id or not platforms or not content:
            self._error(400, 'workspaceId, platforms, and content required'); return
        scheduled_at = body.get('scheduled_at') or None
        status = 'scheduled' if scheduled_at else 'draft'
        payload = {
            'workspace_id': workspace_id,
            'platforms': platforms,
            'content': content,
            'media_urls': body.get('media_urls') or None,
            'scheduled_at': scheduled_at,
            'status': status,
            'created_by': body.get('created_by') or None,
        }
        try:
            rows = _supabase_req('POST', 'social_posts', payload)
            self._json(200, {'ok': True, 'post': rows[0] if rows else payload})
        except Exception as e:
            self._error(500, str(e))

    def _handle_social_publish(self):
        """POST /api/social/publish — immediately publish a post"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        post_id = body.get('postId')
        workspace_id = (body.get('workspaceId') or '').strip()
        if not post_id or not workspace_id:
            self._error(400, 'postId and workspaceId required'); return
        try:
            posts = _supabase_req('GET', f'social_posts?id=eq.{post_id}&select=*')
            if not posts:
                self._error(404, 'Post not found'); return
            post = posts[0]
            acct_rows = _supabase_req(
                'GET',
                f'social_accounts?workspace_id=eq.{urllib.parse.quote(workspace_id)}&status=eq.connected'
            )
            accounts = {r['platform']: r for r in (acct_rows or [])}
            platform_ids, failed = _publish_post_to_platforms(post, accounts)
            pub_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            new_status = 'published' if platform_ids else 'failed'
            _supabase_req('PATCH', f'social_posts?id=eq.{post_id}', {
                'status': new_status,
                'published_at': pub_iso if platform_ids else None,
                'platform_ids': platform_ids,
                'error': json.dumps(failed) if failed else None,
            })
            self._json(200, {
                'ok': bool(platform_ids),
                'platform_ids': platform_ids,
                'failed': failed,
            })
        except Exception as e:
            print(f'  Social publish error: {e}')
            self._error(500, str(e))

    def _handle_social_posts_patch(self):
        """PATCH /api/social/posts — update post (cancel, reschedule, edit draft)"""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON'); return
        post_id = body.get('id')
        if not post_id:
            self._error(400, 'id required'); return
        update = {}
        if 'status' in body:
            update['status'] = body['status']
        if 'content' in body:
            update['content'] = body['content']
        if 'scheduled_at' in body:
            update['scheduled_at'] = body['scheduled_at']
            if body['scheduled_at'] and update.get('status') not in ('cancelled', 'draft'):
                update['status'] = 'scheduled'
        if not update:
            self._error(400, 'Nothing to update'); return
        try:
            rows = _supabase_req('PATCH', f'social_posts?id=eq.{post_id}', update)
            self._json(200, {'ok': True, 'post': rows[0] if rows else {}})
        except Exception as e:
            self._error(500, str(e))

    def _canva_result_page(self, result: str, detail: str = '') -> str:
        """Return an HTML page that redirects back to the platform after OAuth."""
        if result == 'success':
            redirect = f'{PLATFORM_URL}/workspace.html?canva=connected&w={detail}'
            script = f"setTimeout(() => window.location.href = '{redirect}', 1200);"
            body = (
                '<div style="text-align:center;">'
                '<div style="font-size:56px;margin-bottom:12px;">✅</div>'
                '<h2 style="font-size:22px;font-weight:700;margin-bottom:8px;">Canva connected!</h2>'
                '<p style="color:rgba(255,255,255,0.65);font-size:14px;">Your design tool is now active.<br>Taking you back to your workspace…</p>'
                '</div>'
            )
        else:
            redirect = f'{PLATFORM_URL}/workspace.html?canva=error'
            script = f"setTimeout(() => window.location.href = '{redirect}', 2500);"
            body   = f'<div style="text-align:center;"><div style="font-size:56px;margin-bottom:12px;">❌</div><h2>Connection failed</h2><p style="color:rgba(255,255,255,0.6);">{detail}<br><br>Taking you back…</p></div>'

        return (
            '<html><head><meta charset="utf-8">'
            '<style>*{box-sizing:border-box;margin:0;padding:0;}'
            'body{font-family:-apple-system,BlinkMacSystemFont,"DM Sans",sans-serif;'
            'display:flex;align-items:center;justify-content:center;'
            'min-height:100vh;background:#1C3A2E;color:#fff;}</style>'
            f'<script>{script}</script></head>'
            f'<body>{body}</body></html>'
        )

    def _html(self, code, html: str):
        body = html.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, msg):
        self._json(code, {'error': msg})

    def _is_admin(self) -> bool:
        """
        Check that the request carries valid admin credentials.
        Accepts either:
          - X-Admin-Key header matching HQ_ADMIN_PASS env var, or
          - Authorization: Bearer <HQ_ADMIN_PASS>
        Falls back to True in dev mode (no SUPABASE_URL configured).
        """
        admin_pass = os.getenv('HQ_ADMIN_PASS', '') or HQ_ADMIN_PASS
        if not admin_pass:
            return True  # dev/unconfigured — allow all
        supplied = (self.headers.get('X-Admin-Key', '')
                    or self.headers.get('Authorization', '').removeprefix('Bearer ').strip())
        return supplied == admin_pass


# ── Main ──────────────────────────────────────────────────────────────────────
def _run_db_migrations():
    """Run lightweight schema migrations on startup (idempotent)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    migrations = [
        "ALTER TABLE workspace_access ADD COLUMN IF NOT EXISTS partner_id TEXT DEFAULT NULL;",
        "CREATE TABLE IF NOT EXISTS platform_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT now());",
    ]
    for sql in migrations:
        try:
            import urllib.request as _ur2, json as _json2
            req = _ur2.Request(
                f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
                data=_json2.dumps({'query': sql}).encode(),
                headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                         'Content-Type': 'application/json'},
                method='POST')
            _ur2.urlopen(req, timeout=6)
            print(f'  ✅ Migration OK: {sql[:60]}')
        except Exception as e:
            # exec_sql may not exist — that's fine, column may already exist
            print(f'  ℹ️  Migration skipped (run manually if needed): {sql[:60]}')

if __name__ == '__main__':
    _run_db_migrations()   # add partner_id column etc.
    _auto_migrate()        # ensure all required tables exist
    load_db_agents()       # merge Supabase agent overrides/additions into AGENT_PROMPTS
    # Start social post scheduler daemon
    _sched_thread = threading.Thread(target=_social_scheduler_loop, daemon=True, name='social-scheduler')
    _sched_thread.start()
    print('  ✅ Social post scheduler started (60s interval)')
    server = HTTPServer(('0.0.0.0', PORT), AgentHandler)
    print(f'\n🎯 ClickPoint Agent API')
    print(f'   Running on http://0.0.0.0:{PORT}')
    print(f'   Agents: {", ".join(AGENT_PROMPTS.keys())}')
    print(f'   Endpoints: /health  /api/agent  /api/chain  /api/integrations/*')
    print(f'   Anthropic key  : {"✅ set" if API_KEY else "❌ missing — add ANTHROPIC_API_KEY to .env"}')
    print(f'   Supabase URL   : {"✅ set" if SUPABASE_URL else "⚠️  missing — add SUPABASE_URL to .env"}')
    print(f'   Supabase svc   : {"✅ set" if SUPABASE_SERVICE_KEY else "⚠️  missing — add SUPABASE_SERVICE_KEY to .env"}')
    print(f'   Encryption key : {"✅ AES-256 ready" if (_FERNET_OK and INTEGRATION_ENCRYPTION_KEY) else "⚠️  missing — run setup steps below" if not INTEGRATION_ENCRYPTION_KEY else "⚠️  pip3 install cryptography"}')
    if not INTEGRATION_ENCRYPTION_KEY:
        print(f'\n   📋 Integration setup:')
        print(f'      1. pip3 install cryptography')
        print(f'      2. python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
        print(f'      3. Add output as INTEGRATION_ENCRYPTION_KEY= in .env')
        print(f'      4. Add SUPABASE_URL= and SUPABASE_SERVICE_KEY= from Supabase Dashboard → Settings → API')
    print(f'\n   Press Ctrl+C to stop\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n   Stopped.')
        sys.exit(0)
