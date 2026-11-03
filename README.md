# Financial Telegram Bot

A Python-based financial briefing application that generates financial intelligence reports, delivers brief confirmations through Telegram, and sends the full briefing by email using Brevo.

This repository contains the application deployed on an Ubuntu VPS and managed through `systemd`.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Features](#2-features)
3. [Project Architecture](#3-project-architecture)
4. [Technology Stack](#4-technology-stack)
5. [Prerequisites](#5-prerequisites)
6. [Deployment Overview](#6-deployment-overview)
7. [Step 1 — Prepare the VPS](#step-1--prepare-the-vps)
8. [Step 2 — Clone the Repository](#step-2--clone-the-repository)
9. [Step 3 — Create the Python Environment](#step-3--create-the-python-environment)
10. [Step 4 — Configure Environment Variables](#step-4--configure-environment-variables)
11. [Step 5 — Configure the systemd Service](#step-5--configure-the-systemd-service)
12. [Step 6 — Start and Verify the Application](#step-6--start-and-verify-the-application)
13. [Telegram Commands](#13-telegram-commands)
14. [Financial Briefing Workflow](#14-financial-briefing-workflow)
15. [Email Delivery and Error Handling](#15-email-delivery-and-error-handling)
16. [Automated Scheduling](#16-automated-scheduling)
17. [Updating the Application](#17-updating-the-application)
18. [Running the Tests](#18-running-the-tests)
19. [Logs and Monitoring](#19-logs-and-monitoring)
20. [Troubleshooting](#20-troubleshooting)
21. [Security](#21-security)
22. [Backup and Recovery](#22-backup-and-recovery)

---

## 1. Project Overview

The Financial Telegram Bot is designed to automate the generation and distribution of financial briefings.

The application runs continuously on an Ubuntu VPS. Telegram provides the user interface for interacting with the bot, while email is used to deliver the complete financial briefing.

The project separates briefing generation, email delivery, Telegram interaction, and scheduling into dedicated modules.

### Deployment information

| Component | Configuration |
|---|---|
| Operating system | Ubuntu Linux |
| VPS user | `ubuntu` |
| Application directory | `/opt/financial-telegram-bot` |
| Python environment | `venv` |
| Application entry point | `main.py` |
| Process manager | systemd |
| systemd service | `financial-bot` |
| Application interface | Telegram |
| Email provider | Brevo |
| Application timezone | `America/Toronto` |
| Source control | Git |
| Repository | `tensey2k/financial-telegram-bot` |

---

## 2. Features

### Financial briefing generation

The application generates a financial briefing using its dedicated briefing-generation module.

The generation logic is separated from the Telegram interface and email delivery components.

### Telegram integration

Telegram is the application's interaction interface.

The `/briefing` command triggers the briefing workflow and provides a short status confirmation to the user.

### Email delivery

The complete financial briefing is delivered by email through Brevo.

The email remains the primary delivery channel for the full report.

### Delivery status handling

The briefing workflow distinguishes between several possible outcomes:

- Successful email delivery.
- Email delivery disabled because Brevo is unavailable or disabled.
- Email delivery returning an unsuccessful result.
- An exception occurring during email delivery.
- An error occurring while generating the briefing.

Telegram provides an appropriate status or warning rather than reporting a successful delivery when the email was not sent.

### Automated execution

The application includes a scheduler module for automated tasks.

The scheduler is integrated into the application rather than requiring the user to manually launch the briefing each time.

### Process supervision

The application runs as a systemd service, allowing it to start automatically with the VPS and be monitored through standard Linux service-management commands.

---

## 3. Project Architecture

The application uses a modular Python structure.

The following components have been identified in the deployed project:

| File or directory | Responsibility |
|---|---|
| `main.py` | Main application entry point |
| `briefing/` | Financial briefing functionality |
| `briefing/generator.py` | Briefing generation and formatting |
| `briefing/mailer.py` | Email delivery functionality |
| `scheduler/` | Automated scheduling functionality |
| `scheduler/daily.py` | Daily scheduler component |
| `tests/` | Automated tests |
| `tests/test_briefing.py` | Briefing workflow tests |
| `.gitignore` | Excludes local environments, caches, logs, and environment files |
| `.env` | Local environment configuration; not committed to Git |

The repository may contain additional modules for Telegram commands, financial data retrieval, configuration, and supporting utilities. Consult the actual repository tree when modifying or extending the architecture.

---

## 4. Technology Stack

The confirmed technologies and services include:

- **Python** — application language.
- **Telegram Bot API** — user interaction.
- **Brevo** — email delivery.
- **systemd** — process management and automatic startup.
- **Git and GitHub** — source control and version management.
- **Ubuntu** — VPS operating system.
- **Python unittest** — automated testing.

The exact Python version and third-party package versions should be taken from the deployment environment and dependency manifest.

---

## 5. Prerequisites

Before deploying the application, prepare the following:

### VPS

- An Ubuntu VPS.
- SSH access.
- A user with permission to install packages and manage the application service.
- An available application directory under `/opt`.

### External services

The application requires the credentials and configuration associated with the integrations enabled in its source code.

These may include:

- Telegram bot configuration.
- Financial data-source credentials, where applicable.
- Brevo email configuration.
- Email recipient and sender settings.

Use the variable names defined by the application's configuration code. Do not assume that example names from another deployment apply to this project.

### Local computer

You will need:

- Git.
- An SSH client.
- Access to the GitHub repository.
- A way to edit files on the VPS.

---

## 6. Deployment Overview

A new deployment follows this general sequence:

1. Prepare the Ubuntu VPS.
2. Install Python and Git.
3. Clone the GitHub repository.
4. Create a Python virtual environment.
5. Install the project's dependencies.
6. Configure the environment variables.
7. Configure the systemd service.
8. Start the application.
9. Verify that the service and Telegram bot are operational.
10. Check the application logs.

The application is deployed under:

```bash
/opt/financial-telegram-bot
```

The Python virtual environment is located at:

```bash
/opt/financial-telegram-bot/venv
```

---

## Step 1 — Prepare the VPS

Connect to the VPS over SSH:

```bash
ssh ubuntu@YOUR_SERVER_IP
```

Update the package index:

```bash
sudo apt update
```

Upgrade installed packages:

```bash
sudo apt upgrade -y
```

Install Git, Python, and the virtual-environment support package:

```bash
sudo apt install -y git python3 python3-venv python3-pip
```

Check the installed versions:

```bash
python3 --version
```

```bash
git --version
```

Confirm that the commands return valid version information before continuing.

---

## Step 2 — Clone the Repository

Create the application directory:

```bash
sudo mkdir -p /opt/financial-telegram-bot
```

Assign ownership to the deployment user:

```bash
sudo chown -R ubuntu:ubuntu /opt/financial-telegram-bot
```

Move into the application directory:

```bash
cd /opt/financial-telegram-bot
```

Clone the repository:

```bash
git clone https://github.com/tensey2k/financial-telegram-bot.git .
```

If GitHub requires authentication for the repository, authenticate using an appropriate Git credential method. Do not place a personal access token directly in a command that may be saved in shell history.

Verify the cloned repository:

```bash
git status
```

Check the current branch:

```bash
git branch --show-current
```

The production branch is:

```text
main
```

---

## Step 3 — Create the Python Environment

Move into the project directory:

```bash
cd /opt/financial-telegram-bot
```

Create the virtual environment:

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

### Install project dependencies

Use the dependency manifest actually maintained by the repository.

If the project contains `requirements.txt`:

```bash
pip install -r requirements.txt
```

If dependencies are managed through another manifest, use the installation procedure specified by that manifest.

Verify the Python environment:

```bash
which python
```

The returned path should point to:

```text
/opt/financial-telegram-bot/venv/bin/python
```

This ensures that the application uses its own isolated Python environment rather than the system-wide Python installation.

---

## Step 4 — Configure Environment Variables

The application uses a local `.env` file for environment-specific configuration.

The `.env` file must remain on the VPS and must not be committed to GitHub.

### Create the configuration file

From the project directory:

```bash
nano .env
```

Add the environment variables required by the application's configuration module.

The exact variable names and required values must match the configuration code in the repository.

Typical configuration categories include:

- Telegram bot credentials and settings.
- Financial data-provider credentials, where required.
- Brevo API configuration.
- Email sender information.
- Email recipient information.
- Application-specific settings.

Save the file and exit the editor.

### Important security check

The repository's `.gitignore` excludes `.env` and other environment-specific files.

Verify that Git ignores the file:

```bash
git check-ignore .env
```

The command should identify `.env` as an ignored file.

Never commit API keys, bot tokens, passwords, or other credentials.

---

## Step 5 — Configure the systemd Service

The application is managed by the systemd service:

```text
financial-bot
```

The service is responsible for running the application in the background and managing its lifecycle.

### Inspect the existing service configuration

On an already deployed VPS, inspect the active unit:

```bash
sudo systemctl cat financial-bot
```

Check the unit-file location:

```bash
systemctl show -p FragmentPath --value financial-bot
```

The service configuration should use:

- The application directory as its working directory.
- The project's virtual-environment Python interpreter.
- The application's actual entry point.
- The local environment configuration.
- A restart policy appropriate for a continuously running application.

### Example systemd configuration

For a fresh deployment, the following is a template based on the known deployment paths and entry point. Confirm the actual Python startup command and configuration-loading behavior in the source code before using it.

```ini
[Unit]
Description=Financial Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/financial-telegram-bot
EnvironmentFile=/opt/financial-telegram-bot/.env
ExecStart=/opt/financial-telegram-bot/venv/bin/python /opt/financial-telegram-bot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

If creating or changing the unit file, use the path returned by `systemctl show` or the path selected for the new deployment.

After saving a new or modified unit file, reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable automatic startup:

```bash
sudo systemctl enable financial-bot
```

---

## Step 6 — Start and Verify the Application

Start the service:

```bash
sudo systemctl start financial-bot
```

Check its status:

```bash
sudo systemctl status financial-bot
```

The expected healthy state is:

```text
Active: active (running)
```

Check the recent application logs:

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

Follow the logs live:

```bash
sudo journalctl -u financial-bot -f
```

Once the service is running, verify the Telegram integration using the commands supported by the application.

Confirm that:

- The bot responds.
- The `/briefing` workflow can be triggered.
- The briefing generation completes.
- Email delivery reports the correct outcome.
- No unexpected exceptions appear in the logs.

---

## 13. Telegram Commands

The confirmed briefing command is:

### `/briefing`

Triggers the financial briefing workflow.

The workflow generates the report and attempts to deliver the complete briefing through the configured email provider.

Telegram receives a concise status confirmation or an appropriate warning based on the result.

Other Telegram commands should be documented here once their exact command names and behavior have been verified against the Telegram command implementation.

---

## 14. Financial Briefing Workflow

The briefing workflow separates report generation from delivery.

At a high level:

1. A briefing request is received.
2. The application generates the financial briefing.
3. The email-delivery component attempts to send the complete report.
4. The workflow evaluates the delivery result.
5. Telegram receives the corresponding status message.

This separation prevents the Telegram interface from treating report generation and successful email delivery as the same operation.

The full report is delivered through email; Telegram provides the short operational confirmation.

---

## 15. Email Delivery and Error Handling

Email delivery is handled by:

```text
briefing/mailer.py
```

The briefing workflow accounts for the following situations:

| Situation | Expected behavior |
|---|---|
| Email sent successfully | Telegram confirms successful delivery |
| Brevo disabled | Warning indicates that email delivery is disabled; briefing is not reported as sent |
| Email operation returns failure | Telegram reports the delivery failure |
| Email operation raises an exception | Telegram reports an email-delivery error |
| Briefing generation fails | Telegram reports a generation failure |

The application should never report successful email delivery when the email provider has not confirmed success.

### Important design principle

The Telegram status message is separate from the email content.

Changes to Telegram confirmation messages should not unintentionally modify the email subject, HTML body, text body, formatting, or delivery call.

---

## 16. Automated Scheduling

Automated scheduling is handled through the scheduler components, including:

```text
scheduler/daily.py
```

The scheduler is integrated into the application lifecycle.

The application timezone is:

```text
America/Toronto
```

When modifying scheduled tasks, verify:

- The configured execution time.
- The timezone used by the scheduler.
- Whether the task runs once or repeatedly.
- Whether a previous execution is still active.
- Whether errors are recorded in the application logs.

Do not assume that the VPS operating-system timezone and the application's scheduler timezone are identical.

---

## 17. Updating the Application

The following procedure updates an existing deployment from the GitHub `main` branch.

### Step 1 — Connect to the VPS

```bash
ssh ubuntu@YOUR_SERVER_IP
```

### Step 2 — Navigate to the project

```bash
cd /opt/financial-telegram-bot
```

### Step 3 — Check the working tree

```bash
git status
```

Resolve or preserve any local changes before pulling updates. Do not overwrite uncommitted production changes unintentionally.

### Step 4 — Retrieve the latest code

```bash
git pull origin main
```

### Step 5 — Update dependencies

If the dependency manifest has changed and the project uses `requirements.txt`:

```bash
./venv/bin/pip install -r requirements.txt
```

Otherwise, follow the repository's dependency-management procedure.

### Step 6 — Run the tests

```bash
./venv/bin/python -m unittest discover -s tests -v
```

Do not deploy a change that introduces failing tests without first investigating the failure.

### Step 7 — Restart the service

Once the code and tests have been checked:

```bash
sudo systemctl restart financial-bot
```

### Step 8 — Verify the service

```bash
sudo systemctl status financial-bot
```

### Step 9 — Review the logs

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

### Step 10 — Perform a Telegram verification

Trigger the relevant Telegram workflow and verify that the application behaves as expected.

---

## 18. Running the Tests

The project includes automated tests.

Run the test suite from the application directory:

```bash
cd /opt/financial-telegram-bot
```

```bash
./venv/bin/python -m unittest discover -s tests -v
```

The briefing-related test suite has previously completed successfully with:

```text
Ran 18 tests
OK
```

The tests cover the briefing workflow and its delivery outcomes.

Run the tests after modifying:

- Briefing generation.
- Email delivery.
- Telegram status handling.
- Error handling.
- Other components covered by the test suite.

A successful test run does not replace verification of the live Telegram and email integrations.

---

## 19. Logs and Monitoring

The application is managed through systemd, so its standard output and error logs can be inspected through the system journal.

### Check service status

```bash
sudo systemctl status financial-bot
```

### Display the latest 100 log entries

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

### Follow logs in real time

```bash
sudo journalctl -u financial-bot -f
```

### Display logs from the current boot

```bash
sudo journalctl -u financial-bot -b --no-pager
```

### Restart the application

```bash
sudo systemctl restart financial-bot
```

### Stop the application

```bash
sudo systemctl stop financial-bot
```

### Start the application

```bash
sudo systemctl start financial-bot
```

### Check whether automatic startup is enabled

```bash
sudo systemctl is-enabled financial-bot
```

---

## 20. Troubleshooting

### Service fails to start

Check the service status:

```bash
sudo systemctl status financial-bot
```

Inspect the logs:

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

Check that:

- The application directory exists.
- The virtual environment exists.
- The Python entry point is correct.
- The systemd unit uses the correct paths.
- Required environment variables are configured.
- The service user has permission to access the project files.

### Python dependency errors

Check the virtual-environment interpreter:

```bash
/opt/financial-telegram-bot/venv/bin/python --version
```

Check installed packages:

```bash
/opt/financial-telegram-bot/venv/bin/pip list
```

If a required dependency is missing, install it through the project's dependency manifest.

### Telegram bot does not respond

Check the service logs:

```bash
sudo journalctl -u financial-bot -n 100 --no-pager
```

Verify that the Telegram configuration is present and that the application can reach the Telegram API.

Do not publish bot tokens or credentials when reporting the issue.

### Email is not delivered

Inspect the application logs and verify:

- Brevo is enabled.
- The required Brevo configuration is present.
- Sender and recipient settings are valid.
- The application reports a successful delivery result.

A generated briefing does not necessarily mean that an email was successfully delivered.

### Application stops unexpectedly

Check the service status and recent logs:

```bash
sudo systemctl status financial-bot
```

```bash
sudo journalctl -u financial-bot -n 200 --no-pager
```

Look for Python exceptions, configuration errors, dependency problems, or external-service failures.

---

## 21. Security

The application uses credentials to communicate with external services. Protect those credentials.

### Environment files

The local `.env` file is excluded from Git.

Never:

- Commit `.env`.
- Publish API keys or Telegram bot tokens.
- Include credentials in screenshots or logs.
- Paste secret values into support requests.
- Store personal access tokens directly in shell commands.

### Git security

The repository includes ignore rules for:

- `.env` and environment-specific files.
- Python virtual environments.
- Python bytecode and cache directories.
- Test and tooling caches.
- Log files.

### Git history

Removing a secret file from the latest commit does not automatically remove it from earlier Git history.

If credentials were ever committed or exposed, rotate the affected credentials with their respective providers.

---

## 22. Backup and Recovery

A deployment backup should preserve the information needed to restore the application without exposing credentials.

At minimum, maintain secure copies of:

- The Git repository and its committed source code.
- The production environment configuration.
- Any required application data or state stored outside Git.
- The systemd unit configuration.
- Any deployment-specific configuration not stored in the repository.

Keep backups in a secure location separate from the VPS.

Do not commit production environment files or credentials to GitHub.

### Basic recovery sequence

1. Provision a new Ubuntu VPS.
2. Install Git and the required Python version.
3. Clone the repository.
4. Recreate the virtual environment.
5. Install dependencies.
6. Restore the environment configuration securely.
7. Restore the systemd unit.
8. Enable and start the service.
9. Verify the application logs.
10. Test the Telegram workflow and email delivery.

---

## Maintenance Checklist

For routine maintenance:

- [ ] Check the systemd service status.
- [ ] Review application logs for unexpected errors.
- [ ] Verify that the Telegram bot responds.
- [ ] Verify that briefing generation completes.
- [ ] Confirm email delivery behavior.
- [ ] Review automated scheduler execution.
- [ ] Keep dependencies up to date.
- [ ] Run automated tests after code changes.
- [ ] Keep `.env` out of Git.
- [ ] Maintain secure backups.

---

## Repository

**GitHub:** https://github.com/tensey2k/financial-telegram-bot

**Production directory:** `/opt/financial-telegram-bot`

**Production service:** `financial-bot`

**Application entry point:** `main.py`

**Python environment:** `venv`

