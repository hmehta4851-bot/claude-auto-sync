#!/usr/bin/env python3
"""July 1 Auto-Rebalance — switches lead-scraper between accounts."""

import urllib.request, json, os, smtplib, datetime
from email.mime.text import MIMEText

BOT_PAT   = os.environ["BOT_PAT"]
SUDO_PAT  = os.environ["SUDO_PAT"]
GMAIL     = os.environ["GMAIL_USER"]
GMAIL_PW  = os.environ["GMAIL_APP_PASSWORD"]
ACTION    = os.environ.get("ACTION_INPUT", "enable-sudo-disable-bot")
EVENT     = os.environ.get("EVENT_NAME", "schedule")
TODAY     = datetime.date.today().isoformat()

SUDO_WF_IDS = [294443577, 296678866, 296797907, 295675661,
               296225808, 295668728, 296067250, 295182223]

def api_call(token, url, method="GET", data=None):
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, {}

def get_workflow_ids(token, owner, repo):
    _, data = api_call(token, f"https://api.github.com/repos/{owner}/{repo}/actions/workflows")
    return [(w["id"], w["name"], w["state"]) for w in data.get("workflows", [])
            if "clear-sheet" not in w["path"] and "protected" not in w["name"].lower()]

def enable_workflows(token, owner, repo, wf_ids):
    results = []
    for wid in wf_ids:
        status, _ = api_call(token,
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{wid}/enable",
            method="PUT")
        results.append((wid, status))
    return results

def disable_workflows(token, owner, repo, wf_ids):
    results = []
    for wid in wf_ids:
        status, _ = api_call(token,
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{wid}/disable",
            method="PUT")
        results.append((wid, status))
    return results

log_lines = [
    f"=== Quota Rebalance — {TODAY} ===",
    f"Action: {ACTION}",
    f"Trigger: {EVENT}",
    "",
]
changes = []

if ACTION == "status-check-only":
    sudo_wfs = get_workflow_ids(SUDO_PAT, "hm0163983-sudo", "lead-scraper")
    bot_wfs  = get_workflow_ids(BOT_PAT,  "hmehta4851-bot", "lead-scraper")
    log_lines.append("hm0163983-sudo/lead-scraper:")
    for wid, name, state in sudo_wfs:
        log_lines.append(f"  {wid}  {name[:40]}  [{state}]")
    log_lines.append("hmehta4851-bot/lead-scraper:")
    for wid, name, state in bot_wfs:
        log_lines.append(f"  {wid}  {name[:40]}  [{state}]")

elif ACTION == "enable-sudo-disable-bot":
    log_lines.append("Enabling hm0163983-sudo/lead-scraper (Tue/Thu/Sat) ...")
    for wid, status in enable_workflows(SUDO_PAT, "hm0163983-sudo", "lead-scraper", SUDO_WF_IDS):
        line = f"  ENABLED  wf {wid}  HTTP {status}"
        log_lines.append(line); changes.append(line)

    log_lines.append("")
    log_lines.append("Disabling hmehta4851-bot/lead-scraper ...")
    bot_wfs  = get_workflow_ids(BOT_PAT, "hmehta4851-bot", "lead-scraper")
    bot_ids  = [wid for wid, name, state in bot_wfs if state == "active"]
    for wid, status in disable_workflows(BOT_PAT, "hmehta4851-bot", "lead-scraper", bot_ids):
        line = f"  DISABLED wf {wid}  HTTP {status}"
        log_lines.append(line); changes.append(line)

    log_lines += [
        "",
        "New split (active after this run):",
        "  hm0163983-sudo  ->  lead-scraper Tue/Thu/Sat",
        "  hmehta4851-bot  ->  sunzone-indexing + future automations",
    ]

elif ACTION == "enable-bot-disable-sudo":
    log_lines.append("Enabling hmehta4851-bot/lead-scraper ...")
    bot_wfs = get_workflow_ids(BOT_PAT, "hmehta4851-bot", "lead-scraper")
    bot_ids = [wid for wid, name, state in bot_wfs if state != "active"]
    for wid, status in enable_workflows(BOT_PAT, "hmehta4851-bot", "lead-scraper", bot_ids):
        line = f"  ENABLED  wf {wid}  HTTP {status}"
        log_lines.append(line); changes.append(line)

    log_lines.append("Disabling hm0163983-sudo/lead-scraper ...")
    for wid, status in disable_workflows(SUDO_PAT, "hm0163983-sudo", "lead-scraper", SUDO_WF_IDS):
        line = f"  DISABLED wf {wid}  HTTP {status}"
        log_lines.append(line); changes.append(line)

log_text = "\n".join(log_lines)
print(log_text)

body_lines = [
    "GitHub Quota Auto-Rebalance Completed",
    "=" * 45,
    "",
    f"Date   : {TODAY}",
    f"Action : {ACTION}",
    f"Trigger: {EVENT}",
    "",
    "CHANGES MADE:",
]
body_lines += (changes if changes else ["  (status check only - no changes)"])
body_lines += [
    "",
    "CURRENT SPLIT (after this run):",
    "  hmehta4851-bot  ->  sunzone-indexing + future automations",
    "                      lead-scraper Mon/Wed/Fri (if active)",
    "  hm0163983-sudo  ->  lead-scraper Tue/Thu/Sat (enabled at quota reset)",
    "",
    "Each account target: 600-800 min/month (30-40% of 2000 limit)",
    "Combined free pool : 4000 min/month",
    "",
    "Full log:",
    log_text,
    "",
    "Sent by: hmehta4851-bot/claude-auto-sync (PUBLIC repo = 0 quota cost)",
]

body    = "\n".join(body_lines)
subject = f"[Auto-Rebalance] GitHub quota split updated — {TODAY}"

msg = MIMEText(body, "plain")
msg["Subject"] = subject
msg["From"]    = f"Quota Intelligence <{GMAIL}>"
msg["To"]      = GMAIL

for attempt in range(1, 4):
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(GMAIL, GMAIL_PW)
            s.sendmail(GMAIL, [GMAIL], msg.as_string())
        print("Notification email sent.")
        break
    except Exception as e:
        print(f"Email attempt {attempt} failed: {e}")
        import time; time.sleep(10)
