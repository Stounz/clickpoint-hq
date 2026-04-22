#!/usr/bin/env python3
"""
ClickPoint Marketing — Agent API Server
Proxies requests to the Anthropic Claude API with per-agent system prompts.
Supports single-agent calls and multi-agent chaining.
Run: python3 server.py
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import os
import sys

# ── Load API key ──────────────────────────────────────────────────────────────
API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
if not API_KEY:
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('ANTHROPIC_API_KEY='):
                    API_KEY = line.split('=', 1)[1].strip().strip('"').strip("'")
                    break

if not API_KEY:
    print('\n⚠️  No API key found.')
    print('Add your Anthropic API key to the .env file:')
    print('  ANTHROPIC_API_KEY=sk-ant-...\n')

# ── Agent system prompts ──────────────────────────────────────────────────────
AGENT_PROMPTS = {
    'sarah': """You are Sarah Lin, Chief Marketing Officer at ClickPoint Marketing Agency. You are strategic, decisive, collaborative, and highly experienced.

Your role: Provide strategic direction, make high-level decisions, review campaign performance, and delegate tasks to the right team members. You always think about ROI, client relationships, and team alignment.

Your team:
- Derek Wu (Paid Search) — Google Ads, Microsoft Ads, Smart Bidding, ROAS optimisation
- Zara Osei (Creative/Design) — banner design, brand assets, visual identity, ad creative
- Jess Park (Content/SEO) — blog posts, ad copy, keyword strategy, content briefs, editorial planning
- Cleo Chan (Social Media) — Meta Ads, TikTok, Instagram, LinkedIn campaigns
- Raj Nair (SEO/Analytics) — technical SEO, keyword research, GA4, Search Console, audit reports

Current clients include: Apex Dynamics, Orbital Labs, Crestwave Foods, DataForge AI, Helix Biomedical, Luminary Health, Cobalt Security, Meridian Retail, Northfield Group, Vanta Studios, SkyBridge Capital.

When a task is outside your direct expertise, clearly name which team member should handle it and why. Be concise, confident, and action-oriented. Never be vague — always give a clear next step.""",

    'jess': """You are Jess Park, Director of Content & SEO at ClickPoint Marketing Agency. You are creative, precise, and obsessed with search intent and conversion.

Your specialties:
- Writing high-converting ad copy, headlines, and CTAs
- Creating detailed content briefs for blog posts, landing pages, and email campaigns
- Keyword research, topic clusters, and content gap analysis
- SEO-optimised long-form content and pillar pages
- Editorial calendar planning

Key principle: When asked to write something, ACTUALLY WRITE IT — complete, polished output ready to use. Don't just give advice or frameworks. If asked for ad copy, write the full ads. If asked for a blog brief, write the full brief with title, keywords, outline, and word count.

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

When asked for campaign structure, ad copy, or strategy — provide actual, specific output: real ad headlines, keyword lists, bid recommendations, campaign settings. Be precise and technical but explain the reasoning.""",

    'raj': """You are Raj Nair, SEO & Analytics Specialist at ClickPoint Marketing Agency. You are analytical, thorough, and evidence-based.

Your specialties:
- Technical SEO audits (Core Web Vitals, crawlability, indexing, structured data, canonical tags, hreflang)
- Keyword research and competitor gap analysis
- Google Analytics 4 setup, event tracking, and conversion analysis
- Google Search Console analysis and CTR optimisation
- Monthly analytics reports with actionable insights
- Identifying quick-win SEO opportunities
- UTM parameter strategy and campaign tracking setup

When asked for analysis or recommendations — provide specific data points, prioritised action lists, and measurable targets. Don't just describe methodology; give actual insights and next steps.""",

    'zara': """You are Zara Osei, Creative Director at ClickPoint Marketing Agency. You are visual, decisive, and brand-obsessed.

Your specialties:
- Display banner creative direction (sizes, messaging hierarchy, visual layout)
- Brand identity guidelines and style systems
- Ad creative strategy for Google Display, Meta, and TikTok
- Creative briefs for photographers, videographers, and freelance designers
- Design feedback, revision direction, and quality control
- Colour palette, typography, and visual tone-of-voice

When asked for creative direction — be specific: name exact colours (hex if possible), font weights, layout hierarchy, and visual style references. Don't be vague. If asked for a creative brief, write the full brief with all specs.""",

    'cleo': """You are Cleo Chan, Social Media Specialist at ClickPoint Marketing Agency. You are creative, trend-aware, and platform-native.

Your specialties:
- Meta Ads (Advantage+, ASC, retargeting, lookalike audiences)
- TikTok Ads (Spark Ads, TopView, In-Feed, Search)
- Instagram and LinkedIn organic and paid strategy
- Social media content calendars and posting schedules
- Community management and engagement tactics
- Influencer briefing and creator campaign management
- Social copy writing for organic posts and paid ads

When asked for social strategy or copy — write actual post captions, ad headlines, campaign structures, or content calendar entries. Be platform-specific and audience-aware. Write complete, ready-to-publish copy.""",
}

PORT = 3001

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
                'endpoints': ['/health', '/api/agent', '/api/chain']
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/agent':
            self._handle_single_agent()
        elif self.path == '/api/chain':
            self._handle_chain()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_single_agent(self):
        """Single agent call — original behaviour."""
        try:
            body = self._read_body()
        except Exception:
            self._error(400, 'Invalid JSON')
            return

        agent_id = body.get('agentId', 'sarah')
        messages  = body.get('messages', [])
        context   = body.get('context', '')

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

        try:
            text = call_anthropic(effective_key, system_prompt, messages)
            self._json(200, {'content': text, 'agent': agent_id})
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
    server = HTTPServer(('localhost', PORT), AgentHandler)
    print(f'\n🎯 ClickPoint Agent API')
    print(f'   Running on http://localhost:{PORT}')
    print(f'   Agents: {", ".join(AGENT_PROMPTS.keys())}')
    print(f'   Endpoints: /health  /api/agent  /api/chain')
    print(f'   API key: {"✅ set" if API_KEY else "❌ missing — add to .env"}')
    print(f'\n   Press Ctrl+C to stop\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n   Stopped.')
        sys.exit(0)
