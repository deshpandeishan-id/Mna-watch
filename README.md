# mna-watch

Emails you when a **listed company becomes a takeover target** in the US, UK, Europe or India.

It polls free official sources, throws away the noise with a multilingual keyword filter,
asks an AI model to confirm the deal and pull out the terms, and sends one email per new deal.
Everything here runs at no cost.

```
 sources ──► keyword pre-filter ──► AI check + extraction ──► "is target listed?" check ──► dedupe ──► email
```

## What it watches

| Market | Source | What it catches | AI needed? |
|---|---|---|---|
| 🇺🇸 US | SEC EDGAR latest filings: `SC TO-C`, `SC TO-T`, `SC 14D9(-C)`, `425`, `DEFA14A`, `8-K` (Item 1.01 only) | Tender offers, stock mergers, cash mergers (targets file the press release as DEFA14A) | Yes |
| 🇬🇧 UK | Takeover Panel **Disclosure Table** (official XML) | Every new offer period, every newly named or competing bidder, possible → firm offers | **No** – structured data |
| 🇩🇪 DE / DACH | EQS News (WpÜG takeover notices, ad-hoc releases) | §10 decisions to make an offer, attainment of control, target ad-hocs | Yes |
| 🇫🇷 🇳🇱 🇧🇪 🇵🇹 🇮🇪 🇳🇴 + some 🇮🇹 | Euronext company regulated news RSS | Releases tagged "Takeover bids" / "Mergers, Acquisitions", OPA/OPAS/openbaar bod etc. | Yes |
| 🌍 Global | GlobeNewswire M&A feed, PR Newswire M&A feed | US/European deal press releases, many Italian/Spanish/Nordic issuers' English releases | Yes |
| 🇮🇳 India | BSE corporate announcements | SEBI SAST **open offers**, schemes of amalgamation, delisting offers | Yes |

You can add any other RSS feed without touching code (see `EXTRA_RSS_FEEDS` below).

### Known gaps (be aware)
- **Italy**: Borsa Italiana / CONSOB have no free real-time feed. Italian deals are caught when the
  issuer publishes via Euronext, GlobeNewswire, PR Newswire or EQS – most mid/large caps do, small caps may not.
- **India**: BSE's endpoint is unofficial and sometimes blocks cloud IPs (GitHub Actions runs in US data centres).
  Run `python -m mna_watch check-sources` on your server to see if it works there. NSE is not included.
- **EQS** shows only its ~30 newest items, so with 10-minute polling a busy evening burst can push an item off
  the page before it's read. The always-on server option (below) polls every 90 seconds and avoids this.
- The AI can misread a release. Every email says how the "listed target" status was checked and links to the source.

## Setup (about 20 minutes)

You need: a GitHub account, a Gmail account (or any email you can send from via SMTP), and optionally a free
Google AI Studio key for the AI step.

### 1. Get the three keys

1. **Gmail App Password** (lets the script send email as you):
   Google Account → Security → turn on **2-Step Verification** → search "App passwords" → create one called
   `mna-watch` → copy the 16-character password.
2. **Gemini API key** (free tier, for the AI check): go to <https://aistudio.google.com/apikey> → *Create API key*.
   The free tier has daily limits; the keyword filter keeps usage well inside them for normal days.
   Note Google may use free-tier inputs to improve its models – fine for public press releases.
   Alternative: an Anthropic API key (`ANTHROPIC_API_KEY`) – costs a few cents a day with Claude Haiku.
   With **no** AI key the system still runs, but in keyword mode: more false alarms, marked `[unverified]`.
3. **SEC User-Agent**: just your name and email, e.g. `Jane Smith jane@example.com` (the SEC requires it).

### 2. Option A – GitHub Actions (easiest, no server)

Checks every ~10 minutes. Free and unlimited for **public** repositories.

1. Create a new **public** repository on GitHub and upload this folder's contents (keep the `.github` folder).
   Only code and the state file (a list of public announcements already seen) go in the repo; your keys stay in
   encrypted secrets.
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**, add:

   | Secret | Value |
   |---|---|
   | `SEC_USER_AGENT` | `Your Name your@email.com` |
   | `GEMINI_API_KEY` | your Gemini key (or set `ANTHROPIC_API_KEY` instead) |
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASSWORD` | the 16-character App Password |
   | `EMAIL_TO` | where alerts go (comma-separate several) |

3. **Actions** tab → enable workflows → open **mna-watch** → **Run workflow**.
   The first run quietly records everything currently in the feeds (so you aren't flooded with old news).
   From then on, new deals are emailed.
4. To check email works before a real deal arrives, run locally: `python -m mna_watch test-email` (see Option C).

Notes: GitHub can start scheduled runs a few minutes late, so expect alerts within roughly 10–20 minutes.
You can change `*/10` to `*/5` in `.github/workflows/watch.yml`. GitHub pauses schedules in repos with no
activity for 60 days; the state commits this tool makes normally count as activity, but if alerts stop,
check the Actions tab and re-enable.

### 3. Option B – Always-on free server (closest to real time, ~90 s)

Oracle Cloud's **Always Free** tier includes small Linux VMs that never expire (sign-up asks for a card for
identity checks; Always Free resources aren't charged). Any always-on Linux box or Raspberry Pi works the same way.

```bash
# on the server (Ubuntu)
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/<you>/<your-repo>.git mna-watch && cd mna-watch
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # fill in your keys
.venv/bin/python -m mna_watch check-sources   # every source should list items
.venv/bin/python -m mna_watch test-email      # you should receive a test email
sudo cp deploy/mna-watch.service /etc/systemd/system/   # edit User/paths if not 'ubuntu'
sudo systemctl daemon-reload && sudo systemctl enable --now mna-watch
journalctl -u mna-watch -f                    # live log
```

If you use this option, disable the GitHub workflow so you don't get two emails per deal.

### 4. Option C – Your own computer (for testing)

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in keys
python -m mna_watch check-sources
python -m mna_watch test-email
python -m mna_watch once        # one cycle (first one is silent)
python -m mna_watch loop        # keep polling
```

## Settings

All settings are environment variables (GitHub: Secrets for keys, *Variables* for the rest; server: `.env`).

| Variable | Default | Meaning |
|---|---|---|
| `SOURCES` | all | Comma list from `sec,uk_panel,euronext,eqs,globenewswire,prnewswire,bse` |
| `ALERT_STAGES` | `rumour,possible_offer,definitive_agreement,tender_offer,revised_offer,competing_offer` | Which deal stages email you. Also available: `update`, `completed` |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Any Gemini model your key can use (pick one marked free on Google's pricing page) |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Used when `ANTHROPIC_API_KEY` is set and no Gemini key |
| `LLM_PROVIDER` | `auto` | `gemini`, `anthropic` or `none` |
| `MAX_LLM_CALLS_PER_CYCLE` | `40` | Safety cap; extra items wait for the next cycle |
| `POLL_SECONDS` | `90` | Loop mode only |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `465` | Use port 587 for STARTTLS servers |
| `EXTRA_RSS_FEEDS` | – | `name|market|url;name2|market|url2` e.g. a regulator's RSS |
| `BOOTSTRAP_SILENTLY` | `true` | First run records existing items without emailing |
| `DRY_RUN` | `false` | Log alerts instead of emailing |

## What an alert looks like

Subject: `🇬🇧 M&A: Possible offer – First Tin plc (GB00BNR45554) ← Metals X Limited`

Body: a one-line summary, then target, ticker, exchange, acquirer, stage, price per share, premium,
consideration, how the listing was verified, confidence, and a link to the original document.

The same target only emails once per stage, so a possible offer, the firm offer and a competing bid each
alert once, but the French and English copies of one release (or an 8-K plus a DEFA14A) don't double up.

## How it decides

1. **Pre-filter** (`mna_watch/keywords.py`): strong phrases in English, German, French, Italian, Dutch,
   Spanish, Nordic and Indian SEBI terminology (`Rule 2.7`, `tender offer`, `Übernahmeangebot`,
   `offre publique d'achat`, `offerta pubblica di acquisto`, `open offer`...). Dealing disclosures, buy-backs and
   voting-rights notices are dropped. Items from M&A-tagged feeds with weaker words ("acquires") also go to the AI,
   which is where private-company acquisitions get rejected.
2. **AI check** (`mna_watch/extract.py`): reads up to ~14k characters of the document and returns structured JSON:
   is it M&A, is the target listed, the stage, parties and terms.
3. **Listing check** (`mna_watch/pipeline.py`): US targets are matched against the SEC's official ticker list;
   UK targets come from the Takeover Panel; WpÜG notices and BSE filings only exist for listed companies;
   everything else is labelled "per AI reading".
4. **Throttling**: companies that file dozens of SEC 425s per deal are only re-checked every 14 days per form type.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The tests run offline against saved samples of each feed's real format.

## Legal / etiquette

The tool reads public pages at a modest rate and identifies itself to the SEC as required. Check each site's
terms if you scale up or redistribute content. Alerts are informational, not investment advice.
