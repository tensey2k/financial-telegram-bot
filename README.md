# Financial Intelligence Telegram Bot

A Python-based financial intelligence and market-monitoring platform that gathers information from multiple financial data sources, processes it, and delivers consolidated financial briefings through Telegram and email.

The project is designed to bring different areas of the financial markets together in one automated reporting workflow, covering equities, cryptocurrencies, bonds, foreign exchange, macroeconomic indicators, financial news, and additional data sources.

The application supports both scheduled and manually requested briefings, with Telegram notifications and detailed email delivery through Brevo.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Key Features](#key-features)
3. [Supported Data Categories](#supported-data-categories)
4. [How the Application Works](#how-the-application-works)
5. [Project Architecture](#project-architecture)
6. [Technology Stack](#technology-stack)
7. [Prerequisites](#prerequisites)
8. [Installation](#installation)
9. [Configuration](#configuration)
10. [Telegram Bot Usage](#telegram-bot-usage)
11. [Financial Briefing Workflow](#financial-briefing-workflow)
12. [Email Delivery](#email-delivery)
13. [Automated Scheduling](#automated-scheduling)
14. [Running the Application](#running-the-application)
15. [Production Deployment with systemd](#production-deployment-with-systemd)
16. [Testing](#testing)
17. [Troubleshooting](#troubleshooting)
18. [Security and Best Practices](#security-and-best-practices)
19. [Development and Maintenance](#development-and-maintenance)

---

## Project Overview

Financial Intelligence Telegram Bot is an automated financial information aggregation and reporting application.

Rather than relying on a single source of financial information, the application separates data collection into dedicated modules. Each module handles a particular category of information and makes its results available to the briefing-generation workflow.

The application then processes the collected information and generates a consolidated financial briefing.

The delivery architecture separates two purposes:

- **Telegram:** Provides access to the bot and concise notifications about briefing generation and email delivery.
- **Email:** Delivers the complete financial briefing in a structured format.

This separation keeps Telegram notifications concise while allowing the detailed report to be delivered through email.

The application can operate continuously on a Linux server, with systemd managing the production process and an integrated scheduler handling automated briefing generation.

## Key Features

### Multi-source financial information

The application organizes financial information into dedicated data-source modules, making it easier to maintain and extend individual market categories.

### Consolidated financial briefings

Information from enabled data sources is assembled into a single financial report rather than requiring users to consult multiple individual sources.

### Telegram integration

Telegram provides the user-facing interface for interacting with the application and requesting manual briefings.

### Email reporting through Brevo

The application supports email delivery through Brevo, allowing the complete financial briefing to be delivered separately from Telegram notifications.

### Manual briefing generation

Users can request a briefing directly through Telegram using `/briefing`.

### Automated daily scheduling

The scheduler supports automatic briefing generation and delivery without requiring a manual request.

### Modular architecture

Data collection, briefing generation, email delivery, scheduling, configuration, and Telegram command handling are separated into dedicated modules.

### Automated testing

The project includes tests covering formatting, data calculations, scheduling, email behavior, and manual briefing delivery.

---

## Supported Data Categories

The application contains dedicated modules for the following information categories:

| Category | Module | Purpose |
|---|---|---|
| Bonds | `data_sources/bonds.py` | Bond-market information |
| Cryptocurrencies | `data_sources/crypto.py` | Cryptocurrency market information |
| Economic indicators | `data_sources/economic.py` | Macroeconomic data and calculations |
| Foreign exchange | `data_sources/forex.py` | Currency-market information |
| Financial news | `data_sources/news.py` | Financial and economic news |
| Equities | `data_sources/stocks.py` | Stock-market information |
| Weather | `data_sources/weather.py` | Weather information |

The common data-source functionality is located in `data_sources/base.py`.

The application is structured to keep individual data-source implementations separate from the briefing-generation and delivery logic.

---

## How the Application Works

The application follows a modular processing workflow.

```text
                 FINANCIAL DATA SOURCES
                           |
          +----------------+----------------+
          |                |                |
        Stocks           Crypto           Bonds
          |                |                |
        Forex           Economic           News
          |                |                |
        Weather        Other Sources        |
          +----------------+----------------+
                           |
                           v
                  DATA COLLECTION
                           |
                           v
                 BRIEFING GENERATION
                           |
                           v
                  FORMATTING PROCESS
                           |
                +----------+----------+
                |                     |
                v                     v
             TELEGRAM                EMAIL
          Short status              Full report
                                    via Brevo
```

### Processing stages

**1. Data collection**

The application retrieves information through its enabled data-source modules.

**2. Data processing**

Collected information is processed and prepared for inclusion in the financial briefing.

**3. Briefing generation**

The briefing generator combines the available information into a consolidated report.

**4. Formatting**

The report is formatted for its intended delivery channel.

Telegram and email have separate presentation requirements.

**5. Delivery**

The complete report is delivered through email when email delivery is enabled and successful.

Telegram provides a concise confirmation or an appropriate warning when delivery cannot be completed.

---

## Project Architecture

The repository is organized by application responsibility.

```text
financial-telegram-bot/
│
├── briefing/
│   ├── __init__.py
│   ├── generator.py
│   └── mailer.py
│
├── data_sources/
│   ├── base.py
│   ├── bonds.py
│   ├── crypto.py
│   ├── economic.py
│   ├── forex.py
│   ├── news.py
│   ├── stocks.py
│   └── weather.py
│
├── scheduler/
│   └── daily.py
│
├── telegram_bot/
│   └── commands.py
│
├── tests/
│   └── test_briefing.py
│
├── config.py
├── main.py
├── .gitignore
└── README.md
```

### Main application files

| File | Responsibility |
|---|---|
| `main.py` | Application entry point and initialization |
| `config.py` | Central application configuration |
| `briefing/generator.py` | Data aggregation, briefing generation, and formatting |
| `briefing/mailer.py` | Email delivery functionality |
| `scheduler/daily.py` | Automated briefing scheduling |
| `telegram_bot/commands.py` | Telegram command handlers |
| `data_sources/base.py` | Shared data-source functionality |
| `tests/test_briefing.py` | Automated application tests |

### Architecture principles

The project follows several important design principles:

- Separation of concerns between data collection and report generation.
- Independent modules for different financial data categories.
- Centralized application configuration.
- Separate formatting and delivery responsibilities.
- Reusable briefing-generation functionality.
- Automated testing for critical workflows.

This structure makes it easier to introduce additional data sources or modify existing functionality without unnecessarily changing unrelated application components.

---

## Technology Stack

The project is built primarily with Python and integrates the following technologies:

| Technology | Role |
|---|---|
| Python | Main application language |
| Telegram Bot API | User interaction and notifications |
| Brevo | Email delivery |
| systemd | Linux production process management |
| Python unittest | Automated testing |
| Git | Version control |
| GitHub | Source-code repository |

The application also relies on the external data providers configured for its individual data-source modules.

---

## Prerequisites

Before installing the application, ensure that the deployment environment has:

- Linux or another supported Python environment.
- Python 3.12 or later.
- Git.
- Access to the required financial data providers.
- A Telegram bot and its API credentials.
- Brevo account credentials if email delivery is required.
- Internet connectivity.

For production deployment, a Linux VPS with systemd is recommended.

---

## Installation

### Step 1 — Clone the repository

```bash
git clone https://github.com/tensey2k/financial-telegram-bot.git
```

Navigate into the project directory:

```bash
cd financial-telegram-bot
```

### Step 2 — Create a Python virtual environment

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

### Step 3 — Install project dependencies

Install the dependencies defined by the project's dependency manifest.

If the repository includes `requirements.txt`:

```bash
pip install -r requirements.txt
```

If dependencies are managed through another manifest, use the corresponding installation method.

### Step 4 — Configure environment variables

Create a local `.env` file in the project root if required by the application's configuration.

The required variables and their expected names must match the configuration implemented in `config.py`.

Do not copy credentials from another deployment or commit a populated `.env` file.

### Step 5 — Verify the configuration

Before starting the application, verify that:

- Required Telegram configuration is present.
- Required financial data-provider credentials are configured.
- Email configuration is correct if email delivery is enabled.
- Optional data sources are configured according to their requirements.

### Step 6 — Start the application

```bash
python main.py
```

For production, use the systemd deployment instructions below.

---

## Configuration

Application configuration is centralized in:

```text
config.py
```

Sensitive configuration should be supplied through environment variables rather than hardcoded directly into the source code.

### Configuration categories

| Configuration category | Purpose |
|---|---|
| Telegram | Bot authentication and Telegram integration |
| Financial data providers | Access to enabled financial information sources |
| Email | Brevo email-delivery configuration |
| Briefing | Report-generation settings |
| Scheduler | Automated briefing scheduling |
| Application | General runtime configuration |

The exact variable names, required values, and defaults should be taken from the current implementation of `config.py`.

### Environment configuration recommendations

- Keep credentials in the local `.env` file or the production environment.
- Never commit `.env` to GitHub.
- Keep development and production credentials separate.
- Rotate credentials if they have been exposed.
- Avoid printing sensitive environment variables in application logs.

---

## Telegram Bot Usage

The Telegram bot provides the interface for requesting and receiving briefing-related notifications.

### Manual financial briefing

To request a briefing, send:

```text
/briefing
```

The application then executes the manual briefing workflow.

### Expected workflow

1. Telegram receives the `/briefing` command.
2. The application initiates briefing generation.
3. Enabled data sources are collected and processed.
4. The complete briefing is generated.
5. The application attempts to send the report through email.
6. Telegram receives a concise status notification.

The Telegram response is intentionally kept short because the complete report is intended to be delivered by email.

---

## Financial Briefing Workflow

The manual and scheduled briefing workflows use the briefing-generation functionality.

The main implementation is located in:

```text
briefing/generator.py
```

### Manual briefing behavior

The manual `/briefing` command generates the report and attempts email delivery.

The expected behavior is:

| Situation | Telegram behavior | Email behavior |
|---|---|---|
| Briefing generated and email sent | Success confirmation | Complete report delivered |
| Email delivery disabled | Warning that email delivery is disabled | No delivery attempt |
| Email provider reports failure | Delivery failure notification | Report not confirmed as delivered |
| Email delivery raises an exception | Error notification | Delivery unsuccessful |
| Briefing generation fails | Generation failure notification | No report delivery |

The application does not send a preliminary Telegram message announcing that the briefing is being built.

### Important delivery principle

A successful briefing-generation process does not automatically mean that the email was successfully delivered.

The application distinguishes between report generation and email delivery so that Telegram notifications accurately reflect the delivery outcome.

---

## Email Delivery

Email functionality is implemented separately in:

```text
briefing/mailer.py
```

Brevo is used as the email delivery provider.

### Email responsibilities

The mailer handles the email-delivery operation separately from the briefing-generation process.

This separation makes it possible to test email behavior independently and handle delivery failures without confusing them with report-generation failures.

### Email and Telegram have different purposes

| Channel | Purpose |
|---|---|
| Email | Complete financial briefing |
| Telegram | Manual command interface and concise status notifications |

The detailed email presentation is kept separate from the shorter Telegram presentation.

### Email delivery failure handling

The manual briefing workflow accounts for:

- Email delivery being disabled.
- A delivery function returning an unsuccessful result.
- An exception occurring during email delivery.
- A failure occurring before email delivery because briefing generation did not complete.

No successful delivery should be reported unless the email-delivery operation confirms success.

---

## Automated Scheduling

Automated scheduling is implemented in:

```text
scheduler/daily.py
```

The scheduler is part of the application startup workflow.

Its purpose is to execute the configured daily briefing process automatically.

### Scheduled workflow

1. The application starts.
2. The scheduler is initialized.
3. The configured briefing schedule is registered.
4. The scheduled task runs at its configured time.
5. Financial data is collected and processed.
6. The briefing is generated.
7. The scheduled delivery workflow executes.

The manual and scheduled workflows are designed to reuse briefing-generation functionality rather than maintaining separate implementations of the report itself.

---

## Running the Application

### Development mode

Activate the virtual environment:

```bash
source venv/bin/activate
```

Start the application:

```bash
python main.py
```

### Stop the application

Use:

```text
Ctrl + C
```

when running interactively.

### Production mode

For a production server, use systemd to manage the application process.

---

## Production Deployment with systemd

The production deployment uses the following service:

```text
financial-bot
```

systemd provides process supervision and allows the application to run independently of an interactive SSH session.

### Check service status

```bash
sudo systemctl status financial-bot
```

### Start the service

```bash
sudo systemctl start financial-bot
```

### Stop the service

```bash
sudo systemctl stop financial-bot
```

### Restart the service

```bash
sudo systemctl restart financial-bot
```

### Enable startup at boot

```bash
sudo systemctl enable financial-bot
```

### View recent logs

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

### Follow live logs

```bash
sudo journalctl -u financial-bot -f
```

### Production deployment checklist

Before considering a deployment complete:

- Confirm the correct Python virtual environment is used.
- Confirm the application configuration is available to the service.
- Confirm the service starts without errors.
- Confirm the Telegram bot initializes successfully.
- Confirm the scheduler initializes.
- Review the application logs.
- Verify the manual briefing workflow when appropriate.

---

## Testing

Automated tests are located in:

```text
tests/test_briefing.py
```

The project uses Python's built-in `unittest` framework.

### Run the complete test suite

```bash
python -m unittest discover -s tests -v
```

If the virtual environment is used:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

### Current test coverage

The existing test suite covers several important application behaviors.

| Test area | Coverage |
|---|---|
| Telegram formatting | Message length and formatting |
| HTML formatting | Escaping untrusted headline content |
| Telegram news formatting | Escaping dynamic text while preserving URLs |
| Article formatting | Keeping news articles together across message boundaries |
| URL validation | Restricting links to absolute HTTPS URLs |
| Data collection | Avoiding duplicate source fetching |
| Scheduler | Scheduled briefing behavior |
| Email delivery | Disabled email configuration |
| Email failure handling | Exceptions and unsuccessful delivery results |
| Manual briefing | Generation, email delivery, and Telegram confirmation |
| Economic calculations | Correct comparison periods and missing-data handling |
| Presentation | Different presentation requirements for email and Telegram |

The latest verified test run during development completed successfully:

```text
Ran 18 tests
OK
```

All 18 tests passed.

This result reflects the test suite at the time of that run; rerun the tests after subsequent code changes.

---

## Troubleshooting

### The Telegram bot does not start

Check the systemd service:

```bash
sudo systemctl status financial-bot
```

Review the logs:

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

Check that the required configuration and dependencies are available.

### The `/briefing` command does not complete

Review the application logs and determine whether the problem occurred during:

- Data collection.
- Briefing generation.
- Formatting.
- Email delivery.

These are separate stages and should be investigated independently.

### Email delivery is disabled

Check the Brevo-related configuration and confirm that email delivery is enabled.

The manual briefing workflow is designed to notify Telegram when email delivery is disabled rather than falsely reporting that the report was sent.

### Email delivery fails

Check:

- Brevo configuration.
- API credentials.
- Email provider response.
- Application logs.

Telegram should report the delivery failure.

### Scheduled briefings do not run

Check:

```bash
sudo systemctl status financial-bot
```

Then inspect the logs:

```bash
sudo journalctl -u financial-bot -f
```

Confirm that scheduler initialization completed successfully and review the configured schedule.

### Tests fail

Run:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

Use the individual failure messages to identify the affected component.

---

## Security and Best Practices

Security is particularly important because the application integrates multiple external services.

### Protect credentials

Never commit:

- Telegram bot tokens.
- Brevo API credentials.
- Financial data-provider API keys.
- Passwords.
- Private configuration files.
- Other authentication secrets.

The `.env` file is excluded through `.gitignore`.

### Git practices

Before committing changes:

```bash
git status
```

Review the changes:

```bash
git diff
```

Check for whitespace errors:

```bash
git diff --check
```

Run the test suite before committing:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

### Production practices

- Keep the VPS operating system and Python environment maintained.
- Restrict access to sensitive configuration files.
- Monitor systemd logs.
- Keep backups of important configuration.
- Avoid exposing credentials in error messages.
- Test application changes before deploying them.

---

## Development and Maintenance

The modular architecture allows individual parts of the application to be maintained independently.

### Adding a new data source

A typical extension should:

1. Identify the new financial information category.
2. Create a dedicated module under `data_sources/`.
3. Reuse shared functionality from `data_sources/base.py` where appropriate.
4. Implement the data retrieval and processing logic.
5. Integrate the source into the briefing-generation workflow.
6. Handle unavailable or incomplete data appropriately.
7. Add automated tests.
8. Verify both Telegram and email presentation where applicable.

### Modifying briefing formatting

Briefing generation and formatting are primarily handled in:

```text
briefing/generator.py
```

Changes should preserve the distinct presentation requirements of email and Telegram.

In particular, Telegram messages must respect platform message-length limits and safely format dynamic content.

### Modifying email delivery

Email-delivery changes belong in:

```text
briefing/mailer.py
```

The email-delivery workflow should remain separate from report generation.

### Modifying Telegram commands

Telegram command handlers are located in:

```text
telegram_bot/commands.py
```

### Modifying scheduled tasks

Scheduled briefing behavior is located in:

```text
scheduler/daily.py
```

### General development workflow

1. Review the existing implementation.
2. Make a focused change.
3. Run the automated tests.
4. Review the Git diff.
5. Check that no credentials or generated files are staged.
6. Commit the changes.
7. Deploy the updated application.
8. Verify the application and review its logs.

---

## Repository

**GitHub:**  
https://github.com/tensey2k/financial-telegram-bot

---

## Project Philosophy

The goal of this project is to make financial information easier to collect, consolidate, and review through an automated reporting workflow.

By separating data sources, briefing generation, scheduling, and delivery, the application provides a modular foundation that can evolve as additional financial information sources and reporting capabilities are introduced.

**Financial Intelligence Telegram Bot**  
*One consolidated workflow for financial market information and automated briefings.*
