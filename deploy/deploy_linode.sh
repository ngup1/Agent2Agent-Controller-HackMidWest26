#!/usr/bin/env bash
# SSA-021: deploy the app on a Linode behind Caddy, using Akamai Managed PostgreSQL.
# Adapted from the workshop's scripts/deploy_linode.sh at revision e362ae2. Differences:
# HTTPS/WSS through Caddy instead of public port 8000, SSH limited to named addresses,
# the Linode goes in the database's region, and nothing is created without `apply`.
#
#   bash deploy/deploy_linode.sh plan      # default: read-only, shows what apply would do
#   bash deploy/deploy_linode.sh allow-me  # add this machine's /32 to the database allow list
#   bash deploy/deploy_linode.sh env       # write the managed DATABASE_URL into .env.deploy
#   bash deploy/deploy_linode.sh apply     # create firewall and Linode (billed), then deploy
#   bash deploy/deploy_linode.sh status    # resource IDs and health
#
# apply is safe to run again: it reuses the firewall and Linode and redeploys the code.
# Written for bash 3.2 too, because that is what macOS ships.
set -euo pipefail
cd "$(dirname "$0")/.."

DB_LABEL=${DB_LABEL:-ssa-demo-pg}
FIREWALL_LABEL=${FIREWALL_LABEL:-ssa-demo-fw}
LINODE_LABEL=${LINODE_LABEL:-ssa-demo-app}
LINODE_TYPE=${LINODE_TYPE:-g6-standard-1}
LINODE_IMAGE=${LINODE_IMAGE:-linode/ubuntu24.04}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/id_ed25519}
ENV_FILE=${ENV_FILE:-.env.deploy}  # the server's .env; .gitignore covers .env.*
REMOTE_DIR=/opt/ssa
# DOMAIN: defaults to <linode-ip>.sslip.io, which resolves without a DNS record.
# SSH_ALLOW: space-separated CIDRs allowed to SSH; defaults to this machine's /32.

command -v linode-cli >/dev/null || { echo "linode-cli not found. Run: pip install linode-cli && linode-cli configure"; exit 1; }
start=$(date +%s)
elapsed() { echo "$(( ($(date +%s) - start) / 60 )) min $(( ($(date +%s) - start) % 60 )) s"; }
die() { echo "$*" >&2; exit 1; }

# field PATH: read a dotted field from JSON on stdin (first item of a list). Lists print space-separated.
field() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)
if isinstance(d, list):
    d = d[0] if d else {}
for key in sys.argv[1].split("."):
    d = d.get(key) if isinstance(d, dict) else None
print(" ".join(map(str, d)) if isinstance(d, list) else ("" if d is None else d))' "$1"
}
id_of() { linode-cli "$1" list --label "$2" --text --no-headers --format id; }
my_ip() { curl -s --max-time 10 https://api.ipify.org || curl -s --max-time 10 https://ipv4.icanhazip.com; }
get_env() { { grep "^$1=" "$ENV_FILE" || true; } | tail -n 1 | tr -d '\r' | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//"; }

# wait_for SECONDS WHAT COMMAND...: run COMMAND every 5 seconds until it succeeds.
wait_for() {
  local limit=$1 what=$2 waited=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    (( waited >= limit )) && { echo "Gave up after $limit seconds waiting for $what."; return 1; }
    sleep 5
    waited=$((waited + 5))
  done
}

load_db() {
  db_json=$(linode-cli databases postgresql-list --label "$DB_LABEL" --json)
  db_id=$(field id <<<"$db_json")
  [[ -n $db_id ]] || die "No managed PostgreSQL database labeled $DB_LABEL on this account."
  db_status=$(field status <<<"$db_json")
  db_region=$(field region <<<"$db_json")
  db_allow=$(field allow_list <<<"$db_json")
}
require_active() { [[ $db_status == active ]] || die "$DB_LABEL is '$db_status', not active yet. Try again when provisioning finishes."; }

# allow_add CIDR: add one address to the allow list, keeping the ones already there.
allow_add() {
  local address args=()
  case " $db_allow " in
    *" 0.0.0.0/0 "*) die "$DB_LABEL is open to 0.0.0.0/0. Remove that entry first; SSA-021 allows /32 addresses only." ;;
    *" $1 "*) echo "$1 is already on the allow list of $DB_LABEL."; return ;;
  esac
  for address in $db_allow "$1"; do args+=(--allow_list "$address"); done
  linode-cli databases postgresql-update "$db_id" "${args[@]}" >/dev/null
  db_allow="$db_allow $1"
  echo "Added $1 to the allow list of $DB_LABEL."
}

firewall_rules() {
  local ssh_list
  ssh_list=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1].split()))' "$1")
  echo "[{\"label\": \"ssh\", \"action\": \"ACCEPT\", \"protocol\": \"TCP\", \"ports\": \"22\", \"addresses\": {\"ipv4\": $ssh_list}},
    {\"label\": \"https\", \"action\": \"ACCEPT\", \"protocol\": \"TCP\", \"ports\": \"80,443\", \"addresses\": {\"ipv4\": [\"0.0.0.0/0\"], \"ipv6\": [\"::/0\"]}}]"
}

linode_ip() {
  local ip
  ip=$(linode-cli linodes view "$1" --text --no-headers --format ipv4)
  echo "${ip%% *}"
}

plan() {
  load_db
  local fw_id linode_id type_json
  fw_id=$(id_of firewalls "$FIREWALL_LABEL")
  linode_id=$(id_of linodes "$LINODE_LABEL")
  type_json=$(linode-cli linodes type-view "$LINODE_TYPE" --json)
  echo "Account:   $(linode-cli profile view --text --no-headers --format username)"
  echo "Database:  $DB_LABEL (id $db_id) $(field engine <<<"$db_json")/$(field version <<<"$db_json") $(field type <<<"$db_json") in $db_region, status $db_status"
  echo "           allow list: ${db_allow:-<empty>}"
  [[ " $db_allow " == *" 0.0.0.0/0 "* ]] && echo "           WARNING: open to all addresses; SSA-021 requires /32 entries only."
  if [[ -n $fw_id ]]; then echo "Firewall:  $FIREWALL_LABEL exists (id $fw_id); apply resets its rules"
  else echo "Firewall:  would create $FIREWALL_LABEL (no charge)"; fi
  echo "           inbound: 22 from ${SSH_ALLOW:-the /32 of this machine}; 80 and 443 from anywhere; everything else dropped"
  if [[ -n $linode_id ]]; then echo "Linode:    $LINODE_LABEL exists (id $linode_id) at $(linode_ip "$linode_id"); apply redeploys to it"
  else
    echo "Linode:    would create $LINODE_LABEL, $LINODE_TYPE, $LINODE_IMAGE, in $db_region"
    echo "           price: \$$(field price.hourly <<<"$type_json")/hour, \$$(field price.monthly <<<"$type_json")/month (billed until deleted)"
  fi
  echo "Env file:  $ENV_FILE $([[ -f $ENV_FILE ]] && echo present || echo "missing; run: bash deploy/deploy_linode.sh env")"
  echo "SSH key:   $SSH_KEY.pub $([[ -f $SSH_KEY.pub ]] && echo present || echo "missing; run: ssh-keygen -t ed25519")"
  echo
  echo "Nothing was changed. Review the account, region and price, then run: bash deploy/deploy_linode.sh apply"
}

allow_me() {
  load_db
  require_active
  allow_add "$(my_ip)/32"
}

write_env() {
  load_db
  require_active
  local url tmp
  if [[ ! -f $ENV_FILE ]]; then
    [[ -f .env ]] || die "No .env to start from. Run: cp .env.example .env"
    cp .env "$ENV_FILE"
    echo "Created $ENV_FILE from .env."
  fi
  chmod 600 "$ENV_FILE"
  # Percent-encode the credentials; the password never reaches the terminal.
  local creds encode='import sys; from urllib.parse import quote; print(quote(sys.stdin.read().rstrip("\n"), safe=""))'
  creds=$(linode-cli databases postgresql-creds-view "$db_id" --json)
  url="postgresql://$(field username <<<"$creds" | python3 -c "$encode"):$(field password <<<"$creds" | python3 -c "$encode")"
  url="$url@$(field hosts.primary <<<"$db_json"):$(field port <<<"$db_json")/defaultdb?sslmode=require"
  tmp=$(mktemp)
  VALUE="$url" awk '
    index($0, "DATABASE_URL=") == 1 { if (!done) print "DATABASE_URL=" ENVIRON["VALUE"]; done = 1; next }
    { print }
    END { if (!done) print "DATABASE_URL=" ENVIRON["VALUE"] }
  ' "$ENV_FILE" > "$tmp" && cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
  echo "Wrote the managed DATABASE_URL to $ENV_FILE (not shown; the file is ignored by git)."
  echo "Review the rest of $ENV_FILE before apply: provider modes, API keys, DASHBOARD_PASSWORD, CHAT_SESSION_SECRET."
}

apply() {
  load_db
  require_active
  [[ -f $SSH_KEY.pub ]] || die "No SSH key at $SSH_KEY.pub. Make one with: ssh-keygen -t ed25519"
  [[ -f $ENV_FILE ]] || die "No $ENV_FILE. Run: bash deploy/deploy_linode.sh env"
  local url
  url=$(get_env DATABASE_URL)
  [[ $url == *sslmode=require* || $url == *sslmode=verify-* ]] || die "DATABASE_URL in $ENV_FILE must use sslmode=require."
  [[ $url != *@localhost* && $url != *@db:* && $url != *@127.0.0.1* ]] || die "DATABASE_URL in $ENV_FILE points at a local database, not $DB_LABEL."
  local ssh_opts=(-i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o BatchMode=yes)
  local ssh_allow=${SSH_ALLOW:-$(my_ip)/32}

  # 1. The Cloud Firewall: SSH from named addresses, 80/443 from anywhere, the rest dropped.
  local fw_id rules
  rules=$(firewall_rules "$ssh_allow")
  fw_id=$(id_of firewalls "$FIREWALL_LABEL")
  if [[ -z $fw_id ]]; then
    fw_id=$(linode-cli firewalls create --label "$FIREWALL_LABEL" \
      --rules.inbound_policy DROP --rules.outbound_policy ACCEPT --rules.inbound "$rules" \
      --text --no-headers --format id)
    echo "Created firewall $FIREWALL_LABEL (id $fw_id)."
  else
    linode-cli firewalls rules-update "$fw_id" --inbound_policy DROP --outbound_policy ACCEPT --inbound "$rules" >/dev/null
    echo "Firewall $FIREWALL_LABEL already exists (id $fw_id); rules reset, SSH from $ssh_allow."
  fi

  # 2. The Linode, in the database's region. cloud-init installs Docker on first boot.
  local linode_id ip
  linode_id=$(id_of linodes "$LINODE_LABEL")
  if [[ -z $linode_id ]]; then
    linode_id=$(linode-cli linodes create --label "$LINODE_LABEL" --region "$db_region" --type "$LINODE_TYPE" \
      --image "$LINODE_IMAGE" --root_pass "$(openssl rand -base64 32)" --authorized_keys "$(cat "$SSH_KEY.pub")" \
      --firewall_id "$fw_id" --metadata.user_data "$(base64 < deploy/cloud-init.yaml | tr -d '\n')" \
      --text --no-headers --format id)
    echo "Created Linode $LINODE_LABEL (id $linode_id)."
  else
    echo "Linode $LINODE_LABEL already exists (id $linode_id)."
  fi
  ip=$(linode_ip "$linode_id")
  running() { [[ $(linode-cli linodes view "$linode_id" --text --no-headers --format status) == running ]]; }
  wait_for 600 "$LINODE_LABEL to boot" running
  echo "$(elapsed): $LINODE_LABEL is running at $ip. Waiting for SSH."
  wait_for 300 "SSH on $ip" ssh "${ssh_opts[@]}" "root@$ip" true
  echo "$(elapsed): SSH works. Waiting for cloud-init to finish installing Docker."
  ssh "${ssh_opts[@]}" "root@$ip" cloud-init status --wait

  # 3. Let the Linode into the database.
  allow_add "$ip/32"

  # 4. Copy the code and the server's .env, then build and start. The build log stays on the Linode.
  local domain=${DOMAIN:-${ip//./-}.sslip.io}
  COPYFILE_DISABLE=1 tar czf - --no-xattrs --exclude __pycache__ --exclude '*.pyc' \
    backend tests requirements.txt pytest.ini Dockerfile .dockerignore deploy/compose.yaml deploy/Caddyfile \
    | ssh "${ssh_opts[@]}" "root@$ip" "mkdir -p $REMOTE_DIR && tar xzf - -C $REMOTE_DIR"
  scp "${ssh_opts[@]}" -q "$ENV_FILE" "root@$ip:$REMOTE_DIR/.env"
  ssh "${ssh_opts[@]}" "root@$ip" "chmod 600 $REMOTE_DIR/.env && echo DOMAIN=$domain > $REMOTE_DIR/deploy/.env"
  echo "$(elapsed): code and .env copied. Building and starting the containers."
  # The allow list change can still be rolling out, which fails the migration step; retry.
  local up="cd $REMOTE_DIR && docker compose -f deploy/compose.yaml up -d --build > deploy.log 2>&1" attempt
  for attempt in 1 2 3 4 5 6; do
    ssh "${ssh_opts[@]}" "root@$ip" "$up" && break
    ssh "${ssh_opts[@]}" "root@$ip" "tail -n 15 $REMOTE_DIR/deploy.log" || true
    [[ $attempt == 6 ]] && die "docker compose failed six times; see $REMOTE_DIR/deploy.log on the Linode."
    echo "Start failed (attempt $attempt of 6). Retrying in 30 seconds."
    sleep 30
  done

  # 5. Wait for a certificate and a healthy answer over HTTPS.
  healthy() { [[ $(curl -sf --max-time 5 "https://$domain/healthz") == *'"database":"reachable"'* ]]; }
  wait_for 600 "https://$domain/healthz" healthy
  echo
  curl -s "https://$domain/healthz"
  echo
  echo "Deployed after $(elapsed): https://$domain"
  echo "Record these in the resource inventory in docs/engineering-notes.md:"
  echo "  database $DB_LABEL id $db_id | firewall $FIREWALL_LABEL id $fw_id | Linode $LINODE_LABEL id $linode_id ($ip, $db_region)"
  echo "SSH in with: ssh -i $SSH_KEY root@$ip"
}

status() {
  load_db
  local fw_id linode_id ip domain
  fw_id=$(id_of firewalls "$FIREWALL_LABEL")
  linode_id=$(id_of linodes "$LINODE_LABEL")
  echo "Database:  $DB_LABEL id $db_id, $db_region, status $db_status, allow list: ${db_allow:-<empty>}"
  echo "Firewall:  $FIREWALL_LABEL id ${fw_id:-<none>}"
  if [[ -z $linode_id ]]; then echo "Linode:    $LINODE_LABEL <none>"; return; fi
  ip=$(linode_ip "$linode_id")
  domain=${DOMAIN:-${ip//./-}.sslip.io}
  echo "Linode:    $LINODE_LABEL id $linode_id at $ip"
  echo "Health:    https://$domain/healthz -> $(curl -s --max-time 8 "https://$domain/healthz" || echo "no answer")"
}

case ${1:-plan} in
  plan) plan ;;
  allow-me) allow_me ;;
  env) write_env ;;
  apply) apply ;;
  status) status ;;
  *) die "Usage: bash deploy/deploy_linode.sh [plan|allow-me|env|apply|status]" ;;
esac
