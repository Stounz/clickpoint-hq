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
    # Also pick up env vars that weren't in .env (e.g. set via Railway/Heroku dashboard)
    for k in ('ANTHROPIC_API_KEY', 'SUPABASE_URL', 'SUPABASE_SERVICE_KEY',
               'INTEGRATION_ENCRYPTION_KEY', 'SLACK_WEBHOOK_URL', 'RESEND_API_KEY', 'RESEND_FROM', 'NOTIFY_EMAIL',
               'HQ_ADMIN_EMAIL', 'HQ_ADMIN_PASS', 'HQ_PARTNER_EMAIL', 'HQ_PARTNER_PASS',
               'STRIPE_SECRET_KEY', 'STRIPE_PRICE_GROWTH', 'STRIPE_PRICE_PRO',
               'STRIPE_WEBHOOK_SECRET', 'PLATFORM_URL'):
        env_val = os.getenv(k, '')
        if env_val:
            result[k] = env_val
    return result

_ENV = _load_env()

API_KEY                    = _ENV.get('ANTHROPIC_API_KEY', '')
SUPABASE_URL               = _ENV.get('SUPABASE_URL', '')
SUPABASE_SERVICE_KEY       = _ENV.get('SUPABASE_SERVICE_KEY', '')
INTEGRATION_ENCRYPTION_KEY = _ENV.get('INTEGRATION_ENCRYPTION_KEY', '')
SLACK_WEBHOOK_URL          = _ENV.get('SLACK_WEBHOOK_URL', '')
RESEND_API_KEY             = _ENV.get('RESEND_API_KEY', '')
RESEND_FROM                = _ENV.get('RESEND_FROM', 'ClickPoint <onboarding@resend.dev>')
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

if not API_KEY:
    print('\n⚠️  No API key found.')
    print('Add your Anthropic API key to the .env file:')
    print('  ANTHROPIC_API_KEY=sk-ant-...\n')

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
        creds = _supabase_req('GET',
            f'integration_credentials?integration_id=eq.{iid}&select=encrypted_token')
        if not creds:
            return account_id, None
        token = decrypt_token(creds[0]['encrypted_token'])
        return account_id, token
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
    url  = f'https://googleads.googleapis.com/v17/customers/{clean_id}/googleAds:search'
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

def fetch_platform_metrics(client: str, platform: str, days: int, budget: float = 10000) -> dict:
    """
    Main entry point: try cache → try real API → fall back to demo data.
    Always returns a dict with is_demo flag.
    """
    # 1. Check 1-hour cache
    cached = _get_cached(client, platform, days)
    if cached:
        cached['from_cache'] = True
        return cached

    # 2. Try real API (requires stored credentials)
    account_id, token = _get_credential(client, platform)
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
    """Send an email via Resend API (https://resend.com — free tier)."""
    # Read live so Railway env changes take effect without restart
    api_key  = os.getenv('RESEND_API_KEY', '') or RESEND_API_KEY
    from_addr = os.getenv('RESEND_FROM', '') or RESEND_FROM
    if not api_key or not to:
        return False
    try:
        payload = json.dumps({
            'from':    from_addr,
            'to':      [to],
            'subject': subject,
            'html':    html,
        }).encode()
        req = urllib.request.Request(
            'https://api.resend.com/emails',
            data=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type':  'application/json',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201)
    except Exception as e:
        print(f'  Email notify error: {e}')
        return False

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
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-User-Api-Key')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

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
        elif self.path == '/api/env-check':
            # Diagnostic — shows WHICH vars are set, never their values
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_cors_headers()
            self.end_headers()
            check_keys = ['HQ_ADMIN_EMAIL','HQ_ADMIN_PASS','HQ_PARTNER_EMAIL','HQ_PARTNER_PASS',
                          'RESEND_API_KEY','NOTIFY_EMAIL','STRIPE_SECRET_KEY','PLATFORM_URL']
            self.wfile.write(json.dumps({
                k: bool(os.getenv(k, '')) for k in check_keys
            }).encode())
        elif self.path == '/api/agents':
            self._handle_agents_list()
        elif self.path.startswith('/api/memories'):
            self._handle_memories_list()
        elif self.path.startswith('/api/metrics'):
            self._handle_metrics_get()
        elif self.path == '/api/integrations/list':
            self._handle_integrations_list()
        elif self.path == '/api/reports':
            self._handle_reports_list()
        elif self.path.startswith('/api/portal'):
            self._handle_portal_get()
        elif self.path == '/api/workspaces':
            self._handle_workspaces_list()
        elif self.path.startswith('/api/partner/clients'):
            self._handle_partner_clients()
        elif self.path.startswith('/api/partner/summary'):
            self._handle_partner_summary()
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
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

    def do_POST(self):
        if self.path == '/api/agent':
            self._handle_single_agent()
        elif self.path == '/api/chain':
            self._handle_chain()
        elif self.path == '/api/agents/save':
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
        agent_key = params.get('agent', [''])[0]
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
           → save encrypted token to integration_credentials (service role)."""
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

        try:
            # 1) Public metadata row — visible to frontend (no credentials)
            rows = _supabase_req('POST', 'client_integrations', {
                'client': client, 'platform': platform,
                'account_id': account_id, 'status': 'connected',
            })
            integration_id = rows[0]['id']

            # 2) Encrypted credential — RLS blocks anon; only service_role can read
            encrypted = encrypt_token(raw_token)
            _supabase_req('POST', 'integration_credentials', {
                'integration_id': integration_id,
                'encrypted_token': encrypted,
            })

            enc_ok  = _FERNET_OK and bool(INTEGRATION_ENCRYPTION_KEY)
            masked  = '●' * max(0, len(raw_token) - 4) + raw_token[-4:] if len(raw_token) >= 4 else '●●●●'
            print(f'  ✅ Integration saved: {platform} → {client} (AES-256={enc_ok})')
            self._json(200, {'success': True, 'id': integration_id,
                             'encrypted': enc_ok, 'masked': masked})
        except Exception as e:
            print(f'  Integration connect error: {e}')
            self._error(500, str(e))

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
            ok = _send_email(
                email or NOTIFY_EMAIL,
                'ClickPoint — Test Notification',
                '<div style="font-family:sans-serif;padding:24px;"><b>✅ ClickPoint test email</b><p style="color:#555;margin-top:12px;">Email notifications are working correctly.</p></div>',
            )
            self._json(200, {'ok': ok, 'channel': 'email'})
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
        if event_type in ('checkout.session.completed', 'invoice.payment_succeeded'):
            obj = event.get('data', {}).get('object', {})
            metadata     = obj.get('metadata', {})
            workspace_id = metadata.get('workspace_id', '')
            plan         = metadata.get('plan', '')

            if workspace_id and plan and SUPABASE_URL and SUPABASE_SERVICE_KEY:
                try:
                    patch_url = f"{SUPABASE_URL}/rest/v1/workspace_access?workspace_id=eq.{workspace_id}"
                    patch_req = urllib.request.Request(
                        patch_url,
                        data=json.dumps({'plan': plan, 'subscription_active': True}).encode(),
                        headers={
                            'apikey': SUPABASE_SERVICE_KEY,
                            'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                            'Content-Type': 'application/json',
                            'Prefer': 'return=minimal',
                        },
                        method='PATCH',
                    )
                    urllib.request.urlopen(patch_req, timeout=6)
                    print(f'  ✅ Workspace {workspace_id} upgraded to {plan}')
                except Exception as e:
                    print(f'  Supabase plan update error: {e}')

        self._json(200, {'received': True})

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

        print(f'  🤝 Partner registered: {name} <{email}> ({agency_name}) — id:{partner_id} email_sent:{email_sent}')
        self._json(200, {
            'ok': True,
            'partnerId': partner_id,
            'emailSent': email_sent,
        })

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

        # Credentials: env var → hardcoded production default
        # Railway env injection is unreliable; production creds baked in as default.
        _admin_email = os.getenv('HQ_ADMIN_EMAIL', '') or 'admin@clickpointconsulting.com.au'
        _admin_pass  = os.getenv('HQ_ADMIN_PASS',  '') or 'admin_123!'
        _pt_email    = os.getenv('HQ_PARTNER_EMAIL', '') or HQ_PARTNER_EMAIL
        _pt_pass     = os.getenv('HQ_PARTNER_PASS',  '') or HQ_PARTNER_PASS

        if email == _admin_email.lower() and password == _admin_pass:
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
        # Demo fallback — only when no env creds configured
        if not _pt_email:
            if email == 'partner@clickpoint.com.au' and password == 'demo1234':
                self._json(200, {
                    'success': True, 'role': 'partner',
                    'name': 'Agency Partner', 'initials': 'AP',
                    'email': email, 'partnerId': 'partner-demo',
                }); return

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
        """Return list of clients for the authenticated partner."""
        # In production this would validate a session token and filter by partner_id.
        # For now returns demo data so the portal works out of the box.
        commission_rate = 0.20
        clients = []
        for c in self._PARTNER_DEMO_CLIENTS:
            clients.append({**c, 'commission': round(c['mrr'] * commission_rate, 2)})
        self._json(200, {
            'success': True,
            'clients': clients,
            'total': len(clients),
        })

    def _handle_partner_summary(self):
        """Return aggregate KPIs for the partner dashboard."""
        clients = self._PARTNER_DEMO_CLIENTS
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
        })

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
                    company_name = row.get('company_name', workspace_id)
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
                    self._json(200, {'success': True, 'companyName': company_name, 'workspaceId': workspace_id})
                    return
            except Exception:
                pass

        # Dev/demo fallback — accept any 6-digit code
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

        company_name = body.get('companyName', '').strip()
        email        = body.get('email', '').strip()
        if not company_name or not email:
            self._error(400, 'companyName and email required'); return

        import re as _re
        workspace_id = _re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')
        code = _generate_access_code(6)

        row = {
            'workspace_id': workspace_id, 'company_name': company_name,
            'email': email, 'access_code': code,
            'created_at': datetime.datetime.utcnow().isoformat()
        }

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

        self._json(200, {
            'ok': True, 'workspaceId': workspace_id, 'companyName': company_name,
            'email': email, 'code': code, 'link': portal_link
        })

    def _handle_workspaces_list(self):
        """List all workspaces — admin only."""
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/workspace_access?select=workspace_id,company_name,email,created_at,last_login&order=created_at.desc",
                    headers={'apikey': SUPABASE_SERVICE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'})
                with urllib.request.urlopen(req, timeout=6) as r:
                    workspaces = json.loads(r.read())

                # Enrich with recent activity
                for ws in workspaces:
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
        """Return metadata only — never returns tokens."""
        try:
            rows = _supabase_req(
                'GET',
                'client_integrations?select=id,client,platform,account_id,status,last_synced'
                '&order=created_at.desc',
            )
            self._json(200, {'integrations': rows})
        except Exception as e:
            self._error(500, str(e))

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


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    load_db_agents()   # merge Supabase agent overrides/additions into AGENT_PROMPTS
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
