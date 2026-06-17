#!/usr/bin/env python3
"""Quota Intelligence Monitor.

Checks both GitHub accounts daily, emails status, and applies conservative
failover if one account approaches exhaustion while the other still has space.
"""

import urllib.request, json, os, smtplib, datetime, base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BOT_PAT  = os.environ["BOT_PAT"]
SUDO_PAT = os.environ["SUDO_PAT"]
GMAIL    = os.environ["GMAIL_USER"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
QUOTA    = 2000
WARNING_PCT = 70
URGENT_PCT = 90
SAFE_PCT = 60

SUDO_WF_IDS = [294443577, 296678866, 296797907, 295675661,
               296225808, 295668728, 296067250, 295182223]

def api_get(token, url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"API error {url}: {e}")
        return {}

def api_call(token, url, method="GET", data=None):
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return r.status, json.loads(body) if body else {}
    except Exception as e:
        print(f"API call failed {method} {url}: {e}")
        return 0, {}

def get_workflow_ids(token, owner, repo):
    _status, data = api_call(token, f"https://api.github.com/repos/{owner}/{repo}/actions/workflows")
    return [(w["id"], w["name"], w["state"]) for w in data.get("workflows", [])
            if "clear-sheet" not in w["path"] and "protected" not in w["name"].lower()]

def enable_workflows(token, owner, repo, wf_ids):
    results = []
    for wid in wf_ids:
        status, _data = api_call(
            token,
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{wid}/enable",
            method="PUT",
        )
        results.append((wid, status))
    return results

def disable_workflows(token, owner, repo, wf_ids):
    results = []
    for wid in wf_ids:
        status, _data = api_call(
            token,
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{wid}/disable",
            method="PUT",
        )
        results.append((wid, status))
    return results

def calc_minutes_this_month(token, owner, repos):
    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since = month_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    total = 0.0
    run_count = 0
    for repo in repos:
        page = 1
        while True:
            data = api_get(token,
                f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
                f"?per_page=100&page={page}&created=>={since}")
            runs = data.get("workflow_runs", [])
            if not runs:
                break
            for r in runs:
                try:
                    s = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                    e = datetime.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
                    mins = (e - s).total_seconds() / 60
                    if mins > 0:
                        total += mins
                        run_count += 1
                except Exception:
                    pass
            if len(runs) < 100:
                break
            page += 1
    return round(total, 1), run_count

now           = datetime.datetime.now(datetime.timezone.utc)
today         = now.strftime("%Y-%m-%d")
month         = now.strftime("%B %Y")
day_of_month  = now.day
weekday       = now.isoweekday()
days_in_month = 30 if now.month in [4, 6, 9, 11] else (28 if now.month == 2 else 31)
days_left     = days_in_month - day_of_month

print("Checking hm0163983-sudo ...")
sudo_mins, sudo_runs = calc_minutes_this_month(
    SUDO_PAT, "hm0163983-sudo", ["lead-scraper", "sunzone-indexing"])

print("Checking hmehta4851-bot ...")
bot_mins, bot_runs = calc_minutes_this_month(
    BOT_PAT, "hmehta4851-bot", ["lead-scraper", "sunzone-indexing", "claude-auto-sync"])

sudo_pct = round(sudo_mins / QUOTA * 100, 1)
bot_pct  = round(bot_mins  / QUOTA * 100, 1)
sudo_rem = round(QUOTA - sudo_mins, 1)
bot_rem  = round(QUOTA - bot_mins,  1)

sudo_rate    = sudo_mins / day_of_month if day_of_month > 0 else 0
bot_rate     = bot_mins  / day_of_month if day_of_month > 0 else 0
sudo_forecast = round(sudo_mins + sudo_rate * days_left, 0)
bot_forecast  = round(bot_mins  + bot_rate  * days_left, 0)

def status_label(pct):
    if pct >= URGENT_PCT: return "URGENT"
    if pct >= WARNING_PCT: return "WARNING"
    return "HEALTHY"

sudo_status = status_label(sudo_pct)
bot_status  = status_label(bot_pct)

state = {
    "updated": today,
    "month": month,
    "hm0163983-sudo": {
        "minutes_used": sudo_mins, "runs": sudo_runs, "pct": sudo_pct,
        "remaining": sudo_rem, "forecast_eom": sudo_forecast, "status": sudo_status
    },
    "hmehta4851-bot": {
        "minutes_used": bot_mins, "runs": bot_runs, "pct": bot_pct,
        "remaining": bot_rem, "forecast_eom": bot_forecast, "status": bot_status
    },
    "split": "bot=Mon/Wed/Fri; sudo=Tue/Thu/Sat after reset; emergency failover active",
    "total_available_minutes": 4000,
    "total_used": round(sudo_mins + bot_mins, 1),
    "combined_pct": round((sudo_mins + bot_mins) / 4000 * 100, 1),
    "automation_action": "none",
    "automation_details": [],
}

def set_action(action, details):
    state["automation_action"] = action
    state["automation_details"].extend(details)

# Conservative automatic failover:
# - If sudo is high and bot has room, pause sudo workflows and keep bot active.
# - If bot is high and sudo has room, pause bot workflows and keep sudo active.
# - If both are high, do not bounce work around; alert only.
if sudo_pct >= WARNING_PCT and bot_pct < SAFE_PCT:
    details = [f"sudo at {sudo_pct}% and bot at {bot_pct}%; moving lead-scraper load to bot."]
    sudo_results = disable_workflows(SUDO_PAT, "hm0163983-sudo", "lead-scraper", SUDO_WF_IDS)
    bot_ids = [wid for wid, _name, _state in get_workflow_ids(BOT_PAT, "hmehta4851-bot", "lead-scraper")]
    bot_results = enable_workflows(BOT_PAT, "hmehta4851-bot", "lead-scraper", bot_ids)
    details.append(f"disabled sudo workflows: {sudo_results}")
    details.append(f"enabled bot workflows: {bot_results}")
    set_action("pause-sudo-enable-bot", details)
elif bot_pct >= WARNING_PCT and sudo_pct < SAFE_PCT:
    details = [f"bot at {bot_pct}% and sudo at {sudo_pct}%; moving lead-scraper load to sudo."]
    bot_ids = [wid for wid, _name, state in get_workflow_ids(BOT_PAT, "hmehta4851-bot", "lead-scraper")
               if state == "active"]
    bot_results = disable_workflows(BOT_PAT, "hmehta4851-bot", "lead-scraper", bot_ids)
    sudo_results = enable_workflows(SUDO_PAT, "hm0163983-sudo", "lead-scraper", SUDO_WF_IDS)
    details.append(f"disabled bot workflows: {bot_results}")
    details.append(f"enabled sudo workflows: {sudo_results}")
    set_action("pause-bot-enable-sudo", details)
elif sudo_pct >= WARNING_PCT and bot_pct >= WARNING_PCT:
    set_action("alert-only-both-high", [
        f"both accounts are high: sudo={sudo_pct}%, bot={bot_pct}%. No automatic shift made."
    ])

with open("quota-state.json", "w") as f:
    json.dump(state, f, indent=2)
print(f"State: sudo={sudo_pct}%  bot={bot_pct}%  combined={state['combined_pct']}%")
print(f"Automation action: {state['automation_action']}")

is_monday = (weekday == 1)
is_urgent = (sudo_pct >= WARNING_PCT or bot_pct >= WARNING_PCT or state["automation_action"] != "none")
if not (is_monday or is_urgent):
    print("No email today (not Monday and no alerts). Done.")
    raise SystemExit(0)

def bar(pct, width=25):
    filled = int(pct / 100 * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"

report_type = "URGENT ALERT" if is_urgent else "Weekly Report"

lines = [
    f"GitHub Quota Intelligence Report — {report_type}",
    f"Date: {today}",
    "",
    "=" * 60,
    f"ACCOUNT STATUS — {month}",
    "=" * 60,
    "",
    f"[{sudo_status}] hm0163983-sudo",
    f"  Used     : {sudo_mins:.0f} / {QUOTA} min  ({sudo_pct}%)",
    f"  {bar(sudo_pct)}",
    f"  Remaining: {sudo_rem:.0f} min",
    f"  Forecast : {sudo_forecast:.0f} min by end of {month}",
    f"  Runs     : {sudo_runs} workflow runs this month",
    f"  Schedule : Lead scraper Tue/Thu/Sat when split is active",
    "",
    f"[{bot_status}] hmehta4851-bot",
    f"  Used     : {bot_mins:.0f} / {QUOTA} min  ({bot_pct}%)",
    f"  {bar(bot_pct)}",
    f"  Remaining: {bot_rem:.0f} min",
    f"  Forecast : {bot_forecast:.0f} min by end of {month}",
    f"  Runs     : {bot_runs} workflow runs this month",
    f"  Schedule : Lead scraper Mon/Wed/Fri when split is active",
    "",
    "=" * 60,
    "COMBINED SUMMARY",
    "=" * 60,
    f"  Total pool  : 4,000 min / month (2 accounts x 2,000)",
    f"  Used        : {sudo_mins + bot_mins:.0f} min  ({state['combined_pct']}% of total pool)",
    f"  Free buffer : {4000 - sudo_mins - bot_mins:.0f} min for new automations",
    "",
    "  Split: hmehta4851-bot = Mon/Wed/Fri lead scraper",
    "         hm0163983-sudo = Tue/Thu/Sat lead scraper after quota reset",
    "         sunzone-indexing is separate and must not be assumed migrated",
    "",
    f"  Automation action today: {state['automation_action']}",
    *[f"    - {line}" for line in state["automation_details"]],
    "",
    "=" * 60,
]

if is_urgent:
    lines += [
        "ACTION REQUIRED:",
        f"  One or both accounts above 70% quota.",
        "  Automatic failover has been attempted when safe, but check for runaway workflows or unexpected long runs.",
        "  Monitor: https://github.com/hmehta4851-bot/claude-auto-sync/blob/main/quota-state.json",
        "",
    ]
else:
    lines += ["All systems healthy. No action required.", ""]

lines += [
    "Quota resets: 1st of every month (both accounts)",
    "Next July 1 auto-rebalance: true split mode re-enables both account workflows",
    "",
    "Sent by: hmehta4851-bot/claude-auto-sync (PUBLIC repo = 0 quota cost)",
]

body    = "\n".join(lines)
subject = f"[GitHub Quota {report_type}] sudo:{sudo_pct}%  bot:{bot_pct}%  ({today})"

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = f"Quota Intelligence <{GMAIL}>"
msg["To"]      = GMAIL
msg.attach(MIMEText(body, "plain"))

for attempt in range(1, 4):
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(GMAIL, GMAIL_PW)
            s.sendmail(GMAIL, [GMAIL], msg.as_string())
        print(f"Email sent: {subject}")
        break
    except Exception as e:
        print(f"Email attempt {attempt}/3 failed: {e}")
        if attempt < 3:
            import time; time.sleep(10)
