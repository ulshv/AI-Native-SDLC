"""The Plane and Gitea clients against fakes (task 7.6): a retried action is not repeated."""

import json
import re

import httpx

from orchestrator.adapters.gitea import GiteaCodeHost
from orchestrator.adapters.plane import PlaneTracker


class FakePlane:
    def __init__(self, throttle_first=0):
        self.state = "s-backlog"
        self.comments, self.calls, self.throttle = [], [], throttle_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.throttle:
            self.throttle -= 1
            return httpx.Response(429, headers={"Retry-After": "1"})
        path = request.url.path.removeprefix("/api/v1/workspaces/sdlc")
        if path == "/projects/":
            return httpx.Response(200, json={"results": [{"id": "p-1", "identifier": "EWA"}]})
        if path == "/projects/p-1/states/":
            return httpx.Response(200, json={"results": [{"id": "s-backlog", "name": "Backlog"}, {"id": "s-draft", "name": "Drafting"}]})
        if path == "/projects/p-1/issues/i-1/":
            if request.method == "PATCH":
                self.state = json.loads(request.content)["state"]
            return httpx.Response(200, json={"id": "i-1", "state": self.state})
        if path == "/projects/p-1/issues/i-1/comments/":
            if request.method == "POST":
                self.comments.append(json.loads(request.content)["comment_html"])
                return httpx.Response(201, json={})
            return httpx.Response(200, json={"results": [{"comment_html": c} for c in self.comments]})
        return httpx.Response(404)


def plane(fake):
    return PlaneTracker("http://plane", "key", {"agent": "u-agent", "admin": "u-admin"},
                        transport=httpx.MockTransport(fake), sleep=lambda s: None)


def test_plane_status_and_comment_are_not_repeated_by_a_retry():
    fake = FakePlane()
    tracker = plane(fake)
    pid = tracker.project_id("sdlc", "EWA")
    for _ in range(2):
        tracker.set_status("sdlc", pid, "i-1", "Drafting")
        tracker.comment("sdlc", pid, "i-1", "Drafting the spec.\nPR soon.", marker="evt-1")
    assert fake.state == "s-draft"
    assert [m for m, _ in fake.calls].count("PATCH") == 1
    assert len(fake.comments) == 1 and "<p>Drafting the spec.</p><p>PR soon.</p>" in fake.comments[0]
    assert "<!-- agent:evt-1 -->" in fake.comments[0]


def test_plane_people_are_matched_by_id_and_the_project_by_its_identifier():
    tracker = plane(FakePlane())
    assert tracker.agent_id() == "u-agent" and tracker.username("u-admin") == "admin" and tracker.username("u-x") == ""
    assert tracker.identifier("sdlc", "p-1") == "EWA" and tracker.identifier("sdlc", "p-9") is None


def test_plane_waits_out_its_rate_limit():
    fake = FakePlane(throttle_first=2)
    assert plane(fake).project_id("sdlc", "EWA") == "p-1"
    assert len(fake.calls) == 3


class FakeGitea:
    def __init__(self):
        self.prs, self.comments, self.labels, self.issue_labels, self.merges = {}, [], {}, set(), 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path.removeprefix("/api/v1/repos/sdlc/example-webapp"), request.method
        body = json.loads(request.content) if request.content else {}
        if m := re.fullmatch(r"/pulls/(\w+)/(.+)", path):
            if method == "GET" and not m.group(1).isdigit():
                pr = next((p for p in self.prs.values() if p["base"] == m.group(1) and p["head"] == m.group(2)), None)
                return httpx.Response(200, json=pr) if pr else httpx.Response(404)
        if path == "/pulls" and method == "GET":
            return httpx.Response(200, json=[{"number": p["number"], "head": {"ref": p["head"]}, "base": {"ref": p["base"]}}
                                             for p in self.prs.values() if p["state"] == "open"])
        if path == "/pulls" and method == "POST":
            n = len(self.prs) + 1
            self.prs[n] = {"number": n, "head": body["head"], "base": body["base"], "state": "open", "merged": False}
            return httpx.Response(201, json=self.prs[n])
        if m := re.fullmatch(r"/pulls/(\d+)", path):
            pr = self.prs[int(m.group(1))]
            if method == "PATCH":
                pr["state"] = body["state"]
            return httpx.Response(200, json=pr)
        if m := re.fullmatch(r"/pulls/(\d+)/merge", path):
            self.merges += 1
            self.prs[int(m.group(1))].update(merged=True, state="closed")
            return httpx.Response(200)
        if re.fullmatch(r"/issues/\d+/comments", path):
            if method == "POST":
                self.comments.append(body["body"])
                return httpx.Response(201, json={})
            return httpx.Response(200, json=[{"body": c} for c in self.comments])
        if path == "/labels":
            if method == "POST":
                self.labels[body["name"]] = len(self.labels) + 1
                return httpx.Response(201, json={"id": self.labels[body["name"]]})
            return httpx.Response(200, json=[{"name": n, "id": i} for n, i in self.labels.items()])
        if re.fullmatch(r"/issues/\d+/labels", path):
            self.issue_labels.update(body["labels"])
            return httpx.Response(200, json=[])
        if re.fullmatch(r"/pulls/\d+/reviews", path):
            return httpx.Response(200, json=[{"id": 7, "user": {"login": "eng"}}, {"id": 9, "user": {"login": "eng"}},
                                             {"id": 8, "user": {"login": "po"}}])
        if re.fullmatch(r"/pulls/\d+/reviews/9/comments", path):
            return httpx.Response(200, json=[{"path": "src/orders.ts", "position": 4, "body": "rename this"}])
        return httpx.Response(404)


def gitea(fake):
    return GiteaCodeHost("http://gitea", "token", transport=httpx.MockTransport(fake))


def test_gitea_actions_are_not_repeated_by_a_retry():
    fake = FakeGitea()
    host = gitea(fake)
    repo = "sdlc/example-webapp"
    twice = lambda action: [action() for _ in range(2)][0]  # noqa: E731 - each action as a retry would run it
    n = twice(lambda: host.find_or_open_pr(repo, "feat/EWA-12_x", "development", "EWA-12: x", "spec"))
    twice(lambda: host.comment(repo, n, "Spec ready.", marker="evt-1"))
    twice(lambda: host.label(repo, n, "preview"))
    twice(lambda: host.merge(repo, n))
    twice(lambda: host.close(repo, n))
    assert list(fake.prs) == [1] and len(fake.comments) == 1 and fake.merges == 1
    assert fake.labels == {"preview": 1} and fake.issue_labels == {1}


def test_gitea_reads_the_latest_reviews_inline_comments():
    assert gitea(FakeGitea()).review_comments("sdlc/example-webapp", 3, "eng") == ["src/orders.ts:4: rename this"]


def test_a_closed_pull_request_of_the_branch_does_not_count():
    fake = FakeGitea()
    host = gitea(fake)
    first = host.find_or_open_pr("sdlc/example-webapp", "feat/EWA-12_x", "development", "t", "b")
    host.close("sdlc/example-webapp", first)
    assert host.find_or_open_pr("sdlc/example-webapp", "feat/EWA-12_x", "development", "t", "b") == first + 1
