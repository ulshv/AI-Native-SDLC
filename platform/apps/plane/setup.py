"""Plane's setup for the delivery loop (design decision 14).

Run by the plane-setup Job after every sync of Plane, in Django's shell of Plane's own API image.
Every step checks before it changes, so a second run changes nothing:

- the service account `agent`, which people assign tickets to and mention, with e-mail
  notifications off, and its API key in the Secret sdlc/plane-agent for the orchestrator;
- for each project file in /projects: the workspace, the project with the loop's statuses, and
  `agent` and the file's people as members;
- per workspace, one webhook to the orchestrator for work items and comments, signed with
  PLANE_WEBHOOK_SECRET.

Users, API keys and webhooks have no public API, so the Django ORM makes them. Projects,
statuses and members go through Plane's public API in-process, with a temporary admin key, the
same calls the orchestrator makes, so Plane's own rules (default statuses, identifiers, members'
settings) apply.
"""

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

import yaml
from django.test import Client
from django.utils import timezone
from plane.app.serializers import WebhookSerializer
from plane.db.models import APIToken, User, UserNotificationPreference, Webhook, Workspace, WorkspaceMember
from plane.license.models import InstanceAdmin

# name, group, colour; Backlog, Plane's default status, stays: new tickets land there.
LOOP_STATES = [
    ("Drafting", "started", "#60a5fa"),
    ("Needs info", "started", "#f59e0b"),
    ("Spec review", "started", "#a78bfa"),
    ("Approved", "started", "#22c55e"),
    ("Implementing", "started", "#3b82f6"),
    ("Deploying to QA", "started", "#06b6d4"),
    ("Code review", "started", "#8b5cf6"),
    ("On dev", "completed", "#10b981"),
    ("Released", "completed", "#059669"),
    ("Closed", "cancelled", "#6b7280"),
]
# Plane's other default statuses, removed once, right after the project is created; statuses
# people add later are theirs and stay.
PLANE_DEFAULTS = {"Todo", "In Progress", "Done", "Cancelled"}
ADMIN, MEMBER = 20, 15  # Plane's workspace and project roles
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
AGENT_SECRET = ("sdlc", "plane-agent")  # namespace, name


def log(message):
    print(f"plane-setup: {message}", flush=True)


# --- Kubernetes: the agent's key goes to the orchestrator's namespace --------------------------

SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
K8S = "https://kubernetes.default.svc"


def k8s(method, path, body=None, content_type="application/json"):
    request = urllib.request.Request(
        K8S + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {(SA / 'token').read_text()}", "Content-Type": content_type})
    context = ssl.create_default_context(cafile=str(SA / "ca.crt"))
    try:
        with urllib.request.urlopen(request, context=context, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def stored(key):
    namespace, name = AGENT_SECRET
    secret = k8s("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    value = (secret or {}).get("data", {}).get(key)
    return base64.b64decode(value).decode() if value else None


def store(values):
    """Sets keys of the Secret sdlc/plane-agent, leaving the others."""
    namespace, name = AGENT_SECRET
    k8s("PATCH", f"/api/v1/namespaces/{namespace}/secrets/{name}?fieldManager=plane-setup&force=true", {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace,
                     "labels": {"app.kubernetes.io/managed-by": "plane-setup"}},
        "stringData": {"username": "agent", **values},
    }, content_type="application/apply-patch+yaml")


def store_people(users):
    """The Plane user id of every person in the project files, and the agent's. Plane's public API
    names members by display name only, which anyone can change, so the orchestrator matches the
    people by these ids instead (key people.json: username -> id)."""
    people = json.dumps({user.username: str(user.id) for user in users}, sort_keys=True)
    if stored("people.json") != people:
        store({"people.json": people})
        log("stored the Plane ids of the people in sdlc/plane-agent")


# --- The agent -----------------------------------------------------------------------------------

def ensure_agent():
    agent, created = User.objects.get_or_create(username="agent", defaults={
        "email": "agent@sdlc.internal", "display_name": "agent", "first_name": "agent",
        "is_password_autoset": True})
    if created:
        agent.set_unusable_password()
        agent.save()
        log("created user agent")
    # Assignments and mentions would e-mail the agent; nobody reads that mailbox.
    if UserNotificationPreference.objects.filter(user=agent).exclude(
            property_change=False, state_change=False, comment=False, mention=False,
            issue_completed=False).update(property_change=False, state_change=False, comment=False,
                                          mention=False, issue_completed=False):
        log("turned off agent's e-mail notifications")
    return agent


def ensure_agent_key(agent, workspace):
    current = stored("api-key")
    if current and APIToken.objects.filter(token=current, user=agent, is_active=True).exists():
        return
    # all_objects: Plane's models delete softly, and an old key must not stay usable.
    APIToken.all_objects.filter(user=agent, label="orchestrator").delete()
    token = APIToken.objects.create(user=agent, workspace=workspace, label="orchestrator",
                                    description="the orchestrator's key, kept in the Secret sdlc/plane-agent")
    store({"api-key": token.token})
    log("stored a new API key for agent in sdlc/plane-agent")


# --- Workspaces, projects, members ---------------------------------------------------------------

def plane_user(person):
    """The person's Plane account, or None (with a note) when the file names none or it is missing."""
    username = person.get("plane")
    if not username:
        return None
    user = User.objects.filter(username=username, is_active=True).first()
    if user is None:
        log(f"no Plane user {username} for {person.get('name')}: skipped, create it with platform/accounts.sh")
    return user


def ensure_workspace_member(workspace, user, role):
    member, created = WorkspaceMember.objects.get_or_create(workspace=workspace, member=user, defaults={"role": role})
    if created:
        log(f"{user.username} joined workspace {workspace.slug}")
    elif not member.is_active:
        member.is_active = True
        member.save(update_fields=["is_active"])


class Api:
    """Plane's public API, in-process, with a temporary key of the instance admin.

    Plane allows an API key 60 calls a minute (API_KEY_RATE_LIMIT), and a new project takes about
    20, so a 429 waits for the next window instead of failing the Job."""

    LABEL = "plane-setup"

    def __init__(self, admin, workspace):
        self.slug = workspace.slug
        # Keys a crashed run left behind go first; all_objects, because Plane deletes softly.
        APIToken.all_objects.filter(user=admin, label=self.LABEL).delete()
        self.token = APIToken.objects.create(user=admin, workspace=workspace, label=self.LABEL,
                                             description="temporary, deleted when the setup Job ends",
                                             expired_at=timezone.now() + timedelta(minutes=30))
        self.client = Client(HTTP_X_API_KEY=self.token.token)

    def __call__(self, method, path, body=None):
        url = f"/api/v1/workspaces/{self.slug}{path}"
        call = getattr(self.client, method.lower())
        for _ in range(10):
            response = call(url, json.dumps(body), content_type="application/json") if body is not None else call(url)
            if response.status_code != 429:
                break
            log("Plane's rate limit reached, waiting a minute")
            time.sleep(61)
        if response.status_code >= 400:
            sys.exit(f"{method} {url}: {response.status_code} {response.content[:300]!r}")
        return response.json() if response.content else None

    def close(self):
        APIToken.all_objects.filter(pk=self.token.pk).delete()


def results(page):
    return page["results"] if isinstance(page, dict) and "results" in page else page


def ensure_project(api, tracker, agent, people):
    projects = {p["identifier"]: p for p in results(api("GET", "/projects/"))}
    project = projects.get(tracker["identifier"])
    created = project is None
    if created:
        project = api("POST", "/projects/", {"name": tracker["name"], "identifier": tracker["identifier"]})
        log(f"created project {tracker['identifier']}")
    path = f"/projects/{project['id']}"

    states = {s["name"]: s for s in results(api("GET", f"{path}/states/"))}
    for name, group, color in LOOP_STATES:
        if name not in states:
            api("POST", f"{path}/states/", {"name": name, "group": group, "color": color})
            log(f"{tracker['identifier']}: added status {name}")
    if created:
        for name, state in states.items():
            if name in PLANE_DEFAULTS and not state.get("default"):
                api("DELETE", f"{path}/states/{state['id']}/")
                log(f"{tracker['identifier']}: removed Plane's default status {name}")

    members = {m["id"] for m in results(api("GET", f"{path}/members/"))}
    for user in [agent, *people]:
        if str(user.id) not in members:
            api("POST", f"{path}/members/", {"member": str(user.id), "role": MEMBER})
            log(f"{tracker['identifier']}: {user.username} became a member")


def ensure_webhook(workspace):
    """Work items and comments are all the orchestrator reads. A new webhook goes through Plane's
    serializer, which checks the URL against WEBHOOK_ALLOWED_IPS; its generated secret is then
    replaced with PLANE_WEBHOOK_SECRET, which the orchestrator checks deliveries against."""
    hook = Webhook.objects.filter(workspace=workspace, url=WEBHOOK_URL).first()
    if hook and hook.is_active and hook.issue and hook.issue_comment and hook.secret_key == WEBHOOK_SECRET:
        return
    if hook is None:
        serializer = WebhookSerializer(data={"url": WEBHOOK_URL, "issue": True, "issue_comment": True},
                                       context={"request": None})
        if not serializer.is_valid():
            sys.exit(f"webhook to {WEBHOOK_URL}: {serializer.errors}")
        hook = serializer.save(workspace_id=workspace.id)
        log(f"{workspace.slug}: created the webhook to {WEBHOOK_URL}")
    else:
        log(f"{workspace.slug}: updated the webhook to {WEBHOOK_URL}")
    hook.is_active, hook.issue, hook.issue_comment, hook.secret_key = True, True, True, WEBHOOK_SECRET
    hook.save()


def main():
    admin_record = InstanceAdmin.objects.order_by("created_at").first()
    if admin_record is None:
        sys.exit("no instance admin yet; the plane-create-admin Job creates it")
    admin = admin_record.user
    agent = ensure_agent()

    projects = [yaml.safe_load(path.read_text()) for path in sorted(Path("/projects").glob("*.yaml"))]
    by_workspace = {}
    for project in projects:
        by_workspace.setdefault(project["tracker"]["workspace"], []).append(project)

    workspaces, everyone = [], {agent}
    for slug, workspace_projects in by_workspace.items():
        workspace, created = Workspace.objects.get_or_create(slug=slug, defaults={"name": slug, "owner": admin})
        if created:
            log(f"created workspace {slug}")
        workspaces.append(workspace)
        ensure_workspace_member(workspace, admin, ADMIN)
        ensure_workspace_member(workspace, agent, MEMBER)
        api = Api(admin, workspace)
        try:
            for project in workspace_projects:
                people = [user for person in project.get("people", []) if (user := plane_user(person)) is not None]
                everyone.update(people)
                for user in people:
                    if user != admin:
                        ensure_workspace_member(workspace, user, MEMBER)
                ensure_project(api, project["tracker"], agent, people)
        finally:
            api.close()
        ensure_webhook(workspace)

    # One key for the orchestrator. Plane ties a key to a workspace; one workspace is all the
    # loop has today.
    if workspaces:
        ensure_agent_key(agent, workspaces[0])
        store_people(everyone)
    else:
        log("no project files: no workspace, so no API key for agent yet")
    log("done")


main()
