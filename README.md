# Personal Financial & Global Intelligence Telegram Bot

A single-user Telegram bot that delivers a daily 09:00 America/Toronto briefing covering crude oil, crypto, global stock indices, the US yield curve, CAD forex crosses, Canadian/US/Moroccan/European economic indicators, local weather, and RSS headlines — plus on-demand commands for each section.

Runs on an Ubuntu VPS (Oracle Cloud Always Free ARM tier tested) with no database, no AI/LLM dependency, and no web framework.

## Layout

```
financial-telegram-bot/
├── main.py                    # entry point
├── config.py                  # pydantic-settings schema
├── requirements.txt           # 8 runtime deps, no DB/AI/web
├── .env.example               # full configuration template
├── telegram_bot/
│   ├── authorization.py       # numeric user-ID allow list
│   ├── commands.py            # /briefing, /oil, /crypto, ...
│   └── formatter.py           # Telegram message formatting
├── data_sources/
│   ├── base.py                # async HTTP, retries, TTL cache
│   ├── oil.py                 # Brent, WTI (Yahoo Finance)
│   ├── crypto.py              # BTC/ETH/SOL (CoinGecko)
│   ├── stocks.py              # 10 global indices (Yahoo, Finnhub fallback)
│   ├── bonds.py               # US curve, 2s10s spread (Yahoo, FRED)
│   ├── forex.py               # CAD crosses + ECB reference
│   ├── economic.py            # FRED macro series
│   ├── news.py                # RSS via feedparser
│   └── weather.py             # Open-Meteo
├── briefing/
│   └── generator.py           # assembles the daily report
├── scheduler/
│   └── daily.py               # APScheduler CronTrigger
└── deploy/
    └── financial-bot.service  # systemd unit
```

## Install on Ubuntu

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip
sudo useradd --system --home /opt/financial-telegram-bot financebot
sudo mkdir -p /opt/financial-telegram-bot/state
sudo chown financebot:financebot /opt/financial-telegram-bot

sudo -u financebot cp -r . /opt/financial-telegram-bot/
cd /opt/financial-telegram-bot
sudo -u financebot python3.12 -m venv venv
sudo -u financebot venv/bin/pip install -r requirements.txt

sudo -u financebot cp .env.example .env
sudo -u financebot chmod 600 .env
sudo -u financebot nano .env   # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_USER_IDS, FRED_API_KEY, etc.
```

Get your numeric Telegram user ID by messaging [@userinfobot](https://t.me/userinfobot) on Telegram.

## Optional API keys

| Key | Used by | Without it |
|---|---|---|
| `FRED_API_KEY` | economic indicators, international bonds | section labelled unavailable |
| `COINGECKO_API_KEY` | crypto (raises rate limit) | works keyless at lower limit |
| `FINNHUB_API_KEY` | stocks fallback | Yahoo-only |
| `ALPHA_VANTAGE_API_KEY` | reserved for future use | unused |

## Run

```bash
# foreground
sudo -u financebot /opt/financial-telegram-bot/venv/bin/python -m main

# as a systemd service
sudo cp deploy/financial-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now financial-bot
sudo journalctl -u financial-bot -f
```

## Commands

| Command | What it does |
|---|---|
| `/briefing` | full daily report (same as 09:00 scheduled) |
| `/oil` | Brent + WTI |
| `/crypto` | BTC, ETH, SOL |
| `/stocks` | 10 global indices |
| `/bonds` | US curve + 2s10s spread |
| `/forex` | CAD crosses, ECB cross-check |
| `/canada` `/us` `/morocco` `/europe` | regional macro |
| `/weather` | local forecast + alerts |
| `/news` | RSS headlines by region |
| `/status` | cache stats, configured API keys |
| `/settings` | current config |

## Design notes

- **Single-user by construction.** `authorization.is_authorized` rejects every update whose numeric user ID is not in `TELEGRAM_USER_IDS` before any data source is touched. Groups, channels, and strangers are dropped silently.
- **No database.** State is one file: `state/last_delivery.date`. The in-memory TTL cache in `data_sources.base` is the only other persistence, and it is rebuilt on every restart.
- **Every number carries a freshness label.** `LIVE`, `CACHED`, `STALE`, or `UNAVAILABLE` — so a quote from yesterday's close is never printed as if it moved this morning.
- **Failures surface, they don't disappear.** A missing API key prints "unavailable - no API key"; a failed fetch prints the reason. A briefing that silently drops a section is worse than one that says what went wrong.
- **DST-correct scheduling.** APScheduler is given a `ZoneInfo("America/Toronto")` object, so 09:00 stays 09:00 local through the November and March clock changes.

## License

Private. Not for redistribution.