#!/usr/bin/env bash
set -euo pipefail

param_name="${DISCORD_WEBHOOK_SSM_NAME:-/codex/discord-webhook}"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"

if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "Discord notification skipped: AWS credentials are missing"
  exit 0
fi

if [ -z "$region" ]; then
  echo "Discord notification skipped: AWS_REGION is missing"
  exit 0
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "Discord notification skipped: aws CLI is unavailable"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "Discord notification skipped: curl is unavailable"
  exit 0
fi

webhook_url="$(
  aws ssm get-parameter \
    --name "$param_name" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text \
    --region "$region" 2>/dev/null || true
)"

if [ -z "$webhook_url" ] || [ "$webhook_url" = "None" ]; then
  echo "Discord notification skipped: SSM parameter '$param_name' is missing or empty"
  exit 0
fi

payload="$(
  python3 - <<'PY'
import json
import os

needs = json.loads(os.environ.get("NEEDS_JSON", "{}"))
results = [(name, item.get("result", "unknown")) for name, item in needs.items()]

if results and any(result == "failure" for _, result in results):
    overall = "failure"
elif results and any(result == "cancelled" for _, result in results):
    overall = "cancelled"
elif results and all(result == "skipped" for _, result in results):
    overall = "skipped"
else:
    overall = "success"

emoji_map = {
    "success": "✅",
    "failure": "❌",
    "cancelled": "⚪",
    "skipped": "⚠️",
}

workflow_name = os.environ.get("WORKFLOW_NAME", "GitHub Actions Workflow")
repo = os.environ.get("GITHUB_REPOSITORY", "unknown/repo")
ref_name = os.environ.get("GITHUB_REF_NAME", "")
sha = os.environ.get("GITHUB_SHA", "")[:7]
actor = os.environ.get("GITHUB_ACTOR", "unknown")
run_url = os.environ.get("RUN_URL") or (
    f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
    f"{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
)

lines = [
    f"{emoji_map.get(overall, 'ℹ️')} **{workflow_name}** {overall}",
    f"Repo: `{repo}`",
    f"Ref: `{ref_name}` `{sha}`",
    f"Actor: `{actor}`",
    f"Run: {run_url}",
]

if results:
    lines.append("Jobs: " + ", ".join(f"{name}={result}" for name, result in results))

print(json.dumps({"content": "\n".join(lines)}))
PY
)"

curl -fsS -H 'Content-Type: application/json' -d "$payload" "$webhook_url" >/dev/null
echo "Discord notification sent via SSM parameter $param_name"
