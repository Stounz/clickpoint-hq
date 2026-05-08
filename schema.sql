-- ClickPoint Marketing HQ — Supabase Schema
-- Run this in your Supabase SQL Editor (single paste, then Execute)

-- ── Tables ────────────────────────────────────────────────────────────────────

create table if not exists clients (
  id          bigint generated always as identity primary key,
  name        text not null,
  industry    text,
  type        text check (type in ('Retainer','Project')),
  value       text,
  status      text check (status in ('Active','Onboarding','Review')),
  initials    text,
  bg          text,
  created_at  timestamptz default now()
);

create table if not exists deliverables (
  id          bigint generated always as identity primary key,
  name        text not null,
  client      text not null,
  type        text,
  size        text,
  owner       text,
  status      text check (status in ('Pending','Sent','Approved','Draft')),
  sent_date   text,
  storage     text,
  link        text,
  notes       text,
  created_at  timestamptz default now()
);

create table if not exists team_members (
  id          bigint generated always as identity primary key,
  key         text unique not null,
  name        text not null,
  title       text,
  initials    text,
  color       text,
  avatar_bg   text,
  skills      text[],
  status_text text,
  metric_label text,
  metric_value text,
  metric_pct  int,
  metric_color text,
  created_at  timestamptz default now()
);

create table if not exists schedule_events (
  id          bigint generated always as identity primary key,
  day         text,
  date_num    int,
  title       text not null,
  type        text,
  time        text,
  client      text,
  owner       text,
  attendees   text[],
  description text,
  created_at  timestamptz default now()
);

create table if not exists cmd_feed (
  id          bigint generated always as identity primary key,
  agent       text,
  action      text,
  client      text,
  time        text,
  type        text check (type in ('info','warn','success','error')),
  created_at  timestamptz default now()
);

create table if not exists cmd_threads (
  id          bigint generated always as identity primary key,
  subject     text,
  client      text,
  from_agent  text,
  to_agent    text,
  resolved    boolean default false,
  created_at  timestamptz default now()
);

create table if not exists cmd_messages (
  id          bigint generated always as identity primary key,
  thread_id   bigint references cmd_threads(id) on delete cascade,
  from_agent  text,
  text        text,
  time        text,
  created_at  timestamptz default now()
);

create table if not exists cmd_escalations (
  id             bigint generated always as identity primary key,
  priority       text check (priority in ('HIGH','MEDIUM','LOW')),
  client         text,
  title          text,
  body           text,
  raised_by      text,
  raised_time    text,
  suggestion     text,
  resolved       boolean default false,
  created_at     timestamptz default now(),
  -- Partner escalation fields (null = HQ-only)
  partner_id     text,
  workspace_id   text,
  source         text default 'client',   -- 'client' | 'agent'
  campaign_name  text,
  response       text,
  responded_at   timestamptz
);

-- Migration: add partner escalation columns if upgrading existing table
alter table cmd_escalations add column if not exists partner_id    text;
alter table cmd_escalations add column if not exists workspace_id  text;
alter table cmd_escalations add column if not exists source        text default 'client';
alter table cmd_escalations add column if not exists campaign_name text;
alter table cmd_escalations add column if not exists response      text;
alter table cmd_escalations add column if not exists responded_at  timestamptz;

create table if not exists campaigns (
  id               bigint generated always as identity primary key,
  name             text not null,
  client           text,
  types            text,
  audience         text,
  brief            text,
  assigned         text,
  status           text default 'Active',
  client_reply     text,
  client_replied_at timestamptz,
  created_at       timestamptz default now()
);

-- Migration: add client reply columns if upgrading existing table
alter table campaigns add column if not exists client_reply      text;
alter table campaigns add column if not exists client_replied_at timestamptz;

-- ── Enable Row Level Security (read-only for anon) ────────────────────────────

alter table clients          enable row level security;
alter table deliverables     enable row level security;
alter table team_members     enable row level security;
alter table schedule_events  enable row level security;
alter table cmd_feed         enable row level security;
alter table cmd_threads      enable row level security;
alter table cmd_messages     enable row level security;
alter table cmd_escalations  enable row level security;
alter table campaigns        enable row level security;

-- Block all direct anon/public access. The server uses the service_role key
-- which bypasses RLS entirely, so these policies have no effect on the server.
-- They prevent anyone with the anon key from reading/writing data directly.
create policy "deny_anon" on clients          for all using (false);
create policy "deny_anon" on deliverables     for all using (false);
create policy "deny_anon" on team_members     for all using (false);
create policy "deny_anon" on schedule_events  for all using (false);
create policy "deny_anon" on cmd_feed         for all using (false);
create policy "deny_anon" on cmd_threads      for all using (false);
create policy "deny_anon" on cmd_messages     for all using (false);
create policy "deny_anon" on cmd_escalations  for all using (false);

-- ── Seed: Clients ─────────────────────────────────────────────────────────────

insert into clients (name, industry, type, value, status, initials, bg) values
('Apex Dynamics',      'SaaS / B2B',              'Retainer', '$18K/mo', 'Active',     'AD', 'linear-gradient(135deg,#003a7a,#0055cc)'),
('Northfield Group',   'Financial Services',       'Retainer', '$22K/mo', 'Active',     'NG', 'linear-gradient(135deg,#003a20,#007040)'),
('Orbital Labs',       'Deep Tech / R&D',          'Retainer', '$31K/mo', 'Active',     'OL', 'linear-gradient(135deg,#3a1870,#6e2cf0)'),
('DataForge AI',       'AI / ML SaaS',             'Retainer', '$25K/mo', 'Active',     'DF', 'linear-gradient(135deg,#301a00,#603500)'),
('Crestwave Foods',    'FMCG / Consumer',          'Retainer', '$14K/mo', 'Active',     'CF', 'linear-gradient(135deg,#4a0015,#a00030)'),
('Meridian Retail',    'eCommerce / D2C',          'Retainer', '$11K/mo', 'Active',     'MR', 'linear-gradient(135deg,#001535,#00305a)'),
('Vanta Studios',      'Entertainment / Media',    'Retainer', '$8K/mo',  'Active',     'VS', 'linear-gradient(135deg,#2a0050,#5a00a0)'),
('Luminary Health',    'HealthTech / Wellness',    'Retainer', '$13K/mo', 'Active',     'LH', 'linear-gradient(135deg,#002010,#004520)'),
('SkyBridge Capital',  'FinTech / Investments',    'Project',  '$42K',    'Active',     'SC', 'linear-gradient(135deg,#1a1060,#3a2aa0)'),
('Helix Biomedical',   'Life Sciences',            'Retainer', '$19K/mo', 'Active',     'HB', 'linear-gradient(135deg,#003a20,#006040)'),
('Pinecrest Homes',    'Real Estate / PropTech',   'Retainer', '$9K/mo',  'Active',     'PH', 'linear-gradient(135deg,#301a00,#502800)'),
('Clearpath Legal',    'Professional Services',    'Project',  '$18K',    'Active',     'CL', 'linear-gradient(135deg,#001540,#003090)'),
('Ironclad Logistics', 'Supply Chain / Ops',       'Retainer', '$12K/mo', 'Active',     'IL', 'linear-gradient(135deg,#201010,#502020)'),
('Wavefront Energy',   'CleanTech / Energy',       'Retainer', '$16K/mo', 'Active',     'WE', 'linear-gradient(135deg,#002030,#004060)'),
('Solaris EdTech',     'Education / SaaS',         'Retainer', '$7K/mo',  'Active',     'SE', 'linear-gradient(135deg,#401500,#703000)'),
('Cobalt Security',    'Cybersecurity / B2B',      'Retainer', '$21K/mo', 'Active',     'CS', 'linear-gradient(135deg,#001535,#002060)'),
('Prism Analytics',    'Data / Business Intel',    'Project',  '$27K',    'Active',     'PA', 'linear-gradient(135deg,#2a0030,#550060)'),
('Redwood Ventures',   'VC / Private Equity',      'Project',  '$15K',    'Active',     'RV', 'linear-gradient(135deg,#3a1000,#702000)'),
('NovaMed Clinics',    'Healthcare / Clinics',     'Retainer', '$10K/mo', 'Active',     'NM', 'linear-gradient(135deg,#002015,#004530)'),
('Ember Hospitality',  'Travel / Hospitality',     'Retainer', '$8K/mo',  'Active',     'EH', 'linear-gradient(135deg,#3a0800,#701500)'),
('Quanta Robotics',    'Robotics / Automation',    'Retainer', '$28K/mo', 'Onboarding', 'QR', 'linear-gradient(135deg,#001040,#002590)'),
('Bloom Commerce',     'eCommerce / Retail',       'Retainer', '$6K/mo',  'Onboarding', 'BC', 'linear-gradient(135deg,#300030,#600060)'),
('Trident Maritime',   'Shipping / Logistics',     'Project',  '$11K',    'Onboarding', 'TM', 'linear-gradient(135deg,#001a30,#003560)'),
('Axiom Consulting',   'Management Consulting',    'Project',  '$9K',     'Review',     'AC', 'linear-gradient(135deg,#251500,#4a2a00)');

-- ── Seed: Deliverables ────────────────────────────────────────────────────────

insert into deliverables (name, client, type, size, owner, status, sent_date, storage, link) values
('Q2 Campaign Report.pdf',           'Apex Dynamics',    'Report',       '2.4 MB',  'cmo',        'Sent',    'Apr 18', 'gdrive',   '#'),
('Creative Assets Pack.zip',         'Orbital Labs',     'Assets',       '18.7 MB', 'designer',   'Pending', null,     'gdrive',   '#'),
('SEO Audit Full Report.pdf',        'DataForge AI',     'Report',       '3.1 MB',  'seo',        'Sent',    'Apr 15', 'gdrive',   '#'),
('Ad Copy Variations.docx',          'Crestwave Foods',  'Copy',         '0.8 MB',  'writer',     'Approved','Apr 12', 'onedrive', '#'),
('Paid Social Strategy Q2.pdf',      'Northfield Group', 'Strategy',     '1.6 MB',  'digital',    'Sent',    'Apr 17', 'gdrive',   '#'),
('Brand Guidelines v3.pdf',          'Vanta Studios',    'Report',       '4.2 MB',  'brand',      'Approved','Apr 10', 'gdrive',   '#'),
('Monthly Analytics Report.pdf',     'Luminary Health',  'Report',       '1.9 MB',  'analytics',  'Sent',    'Apr 19', 'gdrive',   '#'),
('PR Media Coverage Summary.pdf',    'SkyBridge Capital','Report',       '0.6 MB',  'prdir',      'Pending', null,     'onedrive', '#'),
('Influencer Brief Pack.pdf',        'Meridian Retail',  'Strategy',     '2.8 MB',  'influencer', 'Draft',   null,     null,       '#'),
('Google Ads Performance Deck.pptx', 'Apex Dynamics',    'Presentation', '5.3 MB',  'paidsearch', 'Sent',    'Apr 16', 'gdrive',   '#'),
('Email Campaign Templates.zip',     'Helix Biomedical', 'Assets',       '3.7 MB',  'writer',     'Pending', null,     'gdrive',   '#'),
('Content Calendar May 2026.xlsx',   'Cobalt Security',  'Strategy',     '0.3 MB',  'content',    'Approved','Apr 11', 'onedrive', '#');

-- ── Seed: Escalations ─────────────────────────────────────────────────────────

insert into cmd_escalations (priority, client, title, body, raised_by, raised_time, suggestion, resolved) values
('HIGH',   'Crestwave Foods',  'Budget reallocation approval needed',            'Crestwave Foods has requested a 40% increase in social media ad spend mid-campaign (£4,200 → £5,880/mo). This requires reallocating £1,680 from their SEO retainer. Cleo and Sarah have both reviewed it and recommend approval, but need your sign-off as it changes contracted scope.', 'cleo', '15m ago', 'Approve — social ROI is outperforming SEO this quarter and client momentum is strong.', false),
('MEDIUM', 'DataForge AI',     'Technical SEO changes need client authorisation', 'The audit found critical issues requiring direct changes to the DataForge AI website: canonical tag restructure, Core Web Vitals fixes, and schema markup. These changes require client dev team access. Raj needs authorisation to proceed — estimated 3-day implementation window.', 'raj',  '42m ago', 'Approve and cc the DataForge AI CTO on the access request email.', false),
('LOW',    'Vanta Studios',    'Creative brief sign-off overdue by 3 days',       'The Q2 creative brief for Vanta Studios was sent for approval on Apr 18 and has not been signed off. Zara cannot begin production without it. Two chaser emails sent — no response. May need a direct call from Sarah.', 'zara', '2h ago',  'Call the Vanta Studios account lead directly — this is blocking 4 assets.', false);

-- ── Additional tables (added post-QA audit) ─────────────────────────────────

create table if not exists client_integrations (
  id              bigserial primary key,
  client          text not null,
  platform        text not null,
  account_id      text default '',
  status          text default 'connected',
  encrypted_token text default '',
  last_synced     timestamptz default now(),
  created_at      timestamptz default now()
);

create table if not exists client_metrics (
  id         bigserial primary key,
  client     text not null,
  platform   text not null,
  days       integer default 30,
  metrics    jsonb default '{}',
  fetched_at timestamptz default now()
);

create table if not exists agents (
  id             bigserial primary key,
  key            text unique not null,
  name           text default '',
  role           text default '',
  skills         jsonb default '[]',
  system_prompt  text default '',
  extra_context  text default '',
  active         boolean default true,
  created_at     timestamptz default now()
);

create table if not exists workspace_activity (
  id           bigserial primary key,
  workspace_id text not null,
  company_name text default '',
  type         text not null,
  detail       text default '',
  timestamp    timestamptz default now()
);

-- ── RLS migration: drop open anon policies and replace with deny ──────────────
-- Run this block in Supabase SQL Editor on existing databases.
do $$
declare
  t text;
begin
  foreach t in array array['clients','deliverables','team_members','schedule_events',
                            'cmd_feed','cmd_threads','cmd_messages','cmd_escalations',
                            'campaigns','client_integrations','client_metrics',
                            'agents','workspace_activity'] loop
    execute format('drop policy if exists "anon_all" on %I', t);
    execute format('drop policy if exists "deny_anon" on %I', t);
    execute format('create policy "deny_anon" on %I for all using (false)', t);
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;
