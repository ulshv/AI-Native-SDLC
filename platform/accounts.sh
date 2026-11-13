#!/usr/bin/env bash
# The people's accounts in Plane and Gitea, kept as ~/.config/ai-native-sdlc/accounts.yaml says
# (design decision 10; the format is in accounts.yaml.example, with Keycloak's field names, so
# that SSO imports the file as it is: docs/identity-and-access.md). The file is not in git, since
# it holds e-mail addresses, and it holds no roles: those are each project file's `people`.
#   platform/accounts.sh                  create missing accounts, with a first password shown
#                                         once, and converge names, e-mail addresses and status
#   platform/accounts.sh --dry-run        print the plan and change nothing
#   platform/accounts.sh --reset <user>   a new password in both tools, shown once
# Run it before pushing a project file that names a new person; it needs no sudo. The platform's
# own accounts (the admins, agent, the CI accounts) belong to the bootstrap and are refused here.
set -euo pipefail
# shellcheck source=bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/bootstrap.sh"

ACCOUNTS_FILE=$SECRETS_DIR/accounts.yaml
RESERVED=(admin gitea_admin "${SERVICE_ACCOUNTS[@]}")
declare -A PASSWORDS=() # username -> the password given in this run, shown at the end
ACCOUNT='' USER_NAME=''  # the account the steps are about, and its username

usage() { sed -n '2,11s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

# --- The file -------------------------------------------------------------------------------------

# accounts: one JSON per user, with the defaults filled in; name is "firstName lastName", or the
# username when the file gives neither.
accounts() {
  yq -o json -I0 '.users // []' <"$ACCOUNTS_FILE" | jq -c '.[] | {username,
    firstName: (.firstName // ""), lastName: (.lastName // ""),
    email: (.email // (.username + "@sdlc.internal")), enabled: (.enabled != false)}
    | .name = ([.firstName, .lastName] | map(select(. != "")) | join(" ") | if . == "" then null else . end)
    | .name //= .username'
}

# Every problem of the file at once, before anything changes.
accounts_valid() {
  local problems lines
  problems=$(yq -o json -I0 '.users // []' <"$ACCOUNTS_FILE" | jq -r --args '
    (.[] | select((.username // "") | test("^[a-z0-9][a-z0-9_.-]*$") | not) | "not a username: \(.username)"),
    (.[] | select(.username | IN($ARGS.positional[])) | "the platform'"'"'s own account: \(.username)"),
    (group_by(.username)[] | select(length > 1) | "listed twice: \(.[0].username)"),
    (.[] | (keys - ["username", "email", "firstName", "lastName", "enabled"]) | select(length > 0) |
      "unknown keys: \(join(", "))")
  ' "${RESERVED[@]}") || problems="$ACCOUNTS_FILE is not YAML with a list under users"
  [[ -n $problems ]] || return 0
  mapfile -t lines <<<"$problems"
  printf '  FAIL  %s\n' "${lines[@]}" >&2
  return 1
}

want() { jq -r ".$1" <<<"$ACCOUNT"; }

# password_for <username>: the password this run gives the account, the same in both tools.
password_for() { [[ -n ${PASSWORDS[$1]-} ]] || PASSWORDS[$1]=$(random_secret 22); }

# --- Gitea ----------------------------------------------------------------------------------------

gitea_live() { gitea_api GET "/users/$USER_NAME" 2>/dev/null || true; } # as the admin: with e-mail and status

gitea_there() { [[ -n $(gitea_live) || $(want enabled) == false ]]; }

gitea_create() {
  password_for "$USER_NAME"
  printf '%s' "${PASSWORDS[$USER_NAME]}" | jq -Rc --argjson a "$ACCOUNT" '{username: $a.username,
    full_name: $a.name, email: $a.email, password: ., must_change_password: true, visibility: "private"}' |
    gitea_api_stdin POST /admin/users >/dev/null
}

gitea_current() {
  local live
  live=$(gitea_live)
  [[ -z $live ]] || jq -e --argjson a "$ACCOUNT" \
    '.full_name == $a.name and .email == $a.email and .prohibit_login == ($a.enabled | not)' <<<"$live" >/dev/null
}

# Gitea's edit takes source_id and login_name, which a local account has as 0 and its username.
gitea_edit() { # [json fields, from stdin]
  jq -c --arg user "$USER_NAME" '. + {source_id: 0, login_name: $user}' | gitea_api_stdin PATCH "/admin/users/$USER_NAME" >/dev/null
}

gitea_update() {
  jq -nc --argjson a "$ACCOUNT" '{full_name: $a.name, email: $a.email, prohibit_login: ($a.enabled | not)}' | gitea_edit
}

gitea_converge() {
  step "Gitea: $USER_NAME exists (created when missing)" gitea_there gitea_create
  step "Gitea: $USER_NAME has its name, e-mail address and status" gitea_current gitea_update
}

# --- Plane ----------------------------------------------------------------------------------------

# Plane has no API for users, so its Django shell does it, as Plane's setup Job does. The program
# arrives on stdin, with its input base64-encoded inside it; it prints OUT lines and ends with
# "OUT end", so a program that failed is told from one that had nothing to say.
plane_shell() {
  kubectl exec -i -n plane deploy/plane-api-wl -- python -W ignore manage.py shell --no-imports 2>/dev/null |
    sed -n 's/^OUT //p'
}

# plane_accounts <apply: true|false>: for each account, "<done|todo|run> <username> <what>";
# with apply, Plane is changed. A new user gets PASSWORDS[username], onboarding marked done
# (they have a workspace already), and the username as its display name, which mentions show.
plane_accounts() {
  local given user out
  # The passwords go through pipes only, never into an argument.
  given=$({
    accounts | jq -sc .
    for user in "${!PASSWORDS[@]}"; do printf '%s\t%s\n' "$user" "${PASSWORDS[$user]}"; done |
      jq -Rsc 'split("\n") | map(select(. != "") | split("\t") | {(.[0]): .[1]}) | add // {}'
  } | jq -sc --argjson apply "$1" '{apply: $apply, accounts: .[0], passwords: .[1]}' | base64 -w0)
  out=$(plane_shell <<PY
import base64, json
from plane.db.models import Profile, User
given = json.loads(base64.b64decode("$given"))
for account in given["accounts"]:
    name = account["username"]
    want = {"email": account["email"], "first_name": account["firstName"] or name,
            "last_name": account["lastName"], "is_active": account["enabled"]}
    user = User.objects.filter(username=name).first()
    if user is None and not account["enabled"]:
        print("OUT done", name, "is not there and is disabled")
    elif user is None:
        if given["apply"]:
            user = User.objects.create(username=name, display_name=name, is_email_verified=True,
                                       is_password_autoset=False, **want)
            user.set_password(given["passwords"][name])
            user.save()
            Profile.objects.update_or_create(user=user, defaults={"is_onboarded": True})
        print("OUT", "run" if given["apply"] else "todo", name, "is created")
    elif any(getattr(user, key) != value for key, value in want.items()):
        if given["apply"]:
            for key, value in want.items():
                setattr(user, key, value)
            user.save()
        print("OUT", "run" if given["apply"] else "todo", name, "gets its name, e-mail address and status")
    else:
        print("OUT done", name, "has its name, e-mail address and status")
print("OUT end")
PY
  )
  [[ $(tail -n 1 <<<"$out") == end ]] || { echo "error: Plane's shell did not finish" >&2; return 1; }
  sed '$d' <<<"$out"
}

# A new, enabled or disabled Plane user changes the projects' members and people.json: Plane's
# setup Job, a PostSync hook, makes them, so Argo CD syncs Plane once more.
resync_plane() {
  kubectl patch application plane -n argocd --type merge \
    -p '{"operation": {"initiatedBy": {"username": "platform/accounts.sh"}, "sync": {"revision": "main"}}}' >/dev/null
  log "        Argo CD syncs plane again; its setup Job adds the people to their projects"
}

plane_converge() {
  local plan state user what
  plan=$(plane_accounts false)
  if $DRY_RUN || ! grep -qv '^done ' <<<"$plan"; then
    while read -r state user what; do log "  $state  Plane: $user $what"; done <<<"$plan"
    return 0
  fi
  while read -r state user what; do
    if [[ $state == "done" ]]; then log "  done  Plane: $user $what"; fi
    if [[ $what == "is created" ]]; then password_for "$user"; fi
  done <<<"$plan"
  plan=$(plane_accounts true)
  while read -r state user what; do
    if [[ $state != "done" ]]; then log "  run   Plane: $user $what"; fi
  done <<<"$plan"
  resync_plane
}

# --- Passwords ------------------------------------------------------------------------------------

show_passwords() {
  local user
  ((${#PASSWORDS[@]} > 0)) || return 0
  log "== passwords, shown only now: Plane signs in with the e-mail address, Gitea with the"
  log "   username and asks for a new one at once; in Plane, Profile settings -> Security changes it"
  for user in "${!PASSWORDS[@]}"; do printf '  %-24s %s\n' "$user" "${PASSWORDS[$user]}"; done
}

reset_password() { # <username>
  USER_NAME=$1
  ACCOUNT=$(accounts | jq -c --arg u "$1" 'select(.username == $u and .enabled)')
  [[ -n $ACCOUNT ]] || { echo "error: $1 is no enabled user in $ACCOUNTS_FILE" >&2; return 1; }
  password_for "$1"
  printf '%s' "${PASSWORDS[$1]}" | jq -Rc '{password: ., must_change_password: true}' | gitea_edit
  log "  run   Gitea: $1 has a new password"
  local out
  out=$(plane_shell <<PY
import base64
from plane.db.models import User
user = User.objects.get(username="$1")
user.set_password(base64.b64decode("$(printf '%s' "${PASSWORDS[$1]}" | base64 -w0)").decode())
user.save()
print("OUT end")
PY
  )
  [[ $out == end ]] || { echo "error: Plane's shell did not finish" >&2; return 1; }
  log "  run   Plane: $1 has a new password"
}

# --- Main -----------------------------------------------------------------------------------------

accounts_main() {
  local reset=''
  case ${1-} in
    "") ;;
    --dry-run) DRY_RUN=true ;;
    --reset) reset=${2:?usage: platform/accounts.sh --reset <user>} ;;
    -h | --help) usage; return 0 ;;
    *) usage >&2; return 2 ;;
  esac
  [[ -f $ACCOUNTS_FILE ]] || { echo "error: no $ACCOUNTS_FILE; see platform/accounts.yaml.example" >&2; return 1; }
  log "== checks"
  accounts_valid || { echo "error: nothing was changed" >&2; return 1; }
  log "  ok    $ACCOUNTS_FILE"
  start_gitea_forward
  if [[ -n $reset ]]; then
    reset_password "$reset"
  else
    log "== accounts"
    while read -r ACCOUNT; do
      USER_NAME=$(want username)
      gitea_converge
    done < <(accounts)
    plane_converge
    if $DRY_RUN; then log "dry run: nothing was changed"; fi
  fi
  show_passwords
}

[[ ${BASH_SOURCE[0]} != "$0" ]] || accounts_main "$@"
