"""Builds docs/diagrams/<name>_ru.svg from the EN source by translating text nodes.

Usage, from anywhere:  python3 docs/diagrams/tools/translate_ru.py
The EN SVG is the source of truth: edit it, add the new strings to T (or KEEP_ALL), re-run.
Every text node must be translated or kept; an unknown one fails the run with its text.
example.svg is not built here: tools/example.py generates both of its languages.
"""
import pathlib
import re
import sys

DIAGRAMS = pathlib.Path(__file__).resolve().parent.parent

# Text that stays as is in every diagram: product names, statuses, identifiers, URLs.
KEEP_ALL = {
    "Plane", "Gitea", "Mattermost", " / Jira / Trello", " / Slack / Telegram", " / GitLab / GitHub",
    "GIT · CI", "Guardrails", "Executor", "Loki · Langfuse", "k8s Job · OpenCode", "Job",
    "k3s + Traefik", "Argo CD · app-of-apps", "Keycloak · OIDC", "Gitea act_runner · CI",
    "Product owner", "Delta spec", "PR", "k8s Job", "Dev", "CI · Argo CD", "GIT",
    "Plane · EWA-12", "example-webapp", "Argo CD", "→ @agent", "main", "development",
    "feat/EWA-12_*", "v1.5.0", "v1.6.0", "PR → development", "CI · image :sha", "→ dev", "→ prod",
    "proposal · specs · tasks", "ewa-12.qa.example-webapp.sdlc.internal",
    "dev.example-webapp.sdlc.internal", "example-webapp.sdlc.internal",
    "ApplicationSet, PR generator", "feat/EWA-12_add-filters",
    # statuses and states: names from the tools and the state machine
    "Drafting", "Spec review", "Approved", "Implementing", "Code review", "On dev", "Released",
    "Ticket open", "Triaged", "Awaiting answer", "Closed", "Spec approved", "Done",
    "approve · request_changes · answer · merge · release",
}

T = {
    "architecture": {
        "Architecture": "Архитектура",
        "Collaboration surfaces in, agent runs out; one orchestrator owns the state of every work item":
            "На входе — инструменты команды, на выходе — запуски агентов; состоянием каждого work item владеет один оркестратор",
        "People": "Люди", "Platform": "Платформа", "Ours": "Наше", "Target projects": "Целевые проекты",
        "PEOPLE": "ЛЮДИ", "COLLABORATION SURFACES": "ИНСТРУМЕНТЫ КОМАНДЫ",
        "SDLC CONTROL PLANE · OURS": "SDLC CONTROL PLANE · НАШЕ",
        "EXECUTION &amp; RUNTIME": "ИСПОЛНЕНИЕ И РАНТАЙМ",
        "intent · approvals": "намерение · апрувы", "Engineer": "Инженер",
        "specs · code review": "спеки · код-ревью",
        "TRACKER": "ТРЕКЕР", "Tickets, boards, statuses;": "Тикеты, доски, статусы;",
        "clarifications in comments": "уточнения в комментариях",
        "CHAT": "ЧАТ", "Summaries, quick approvals;": "Сводки, быстрые апрувы;",
        "one thread per work item": "тред на каждый work item",
        "Spec PRs and code PRs;": "PR со спеками и кодом;", "branch protection, Actions": "защита веток, Actions",
        "Channel adapters": "Адаптеры каналов",
        "webhooks in · API out · bot echoes dropped": "вебхуки на входе · API на выходе · без эха бота",
        "Event normalizer": "Нормализатор событий",
        "@agent mention → intent → policy (who · role · phase)": "@agent → намерение → policy (кто, роль, фаза)",
        "CORE": "ЯДРО", "Orchestrator": "Оркестратор",
        "LangGraph graph per work item": "LangGraph-граф на каждый work item",
        "Postgres checkpointer · one step per event": "Postgres-чекпоинтер · шаг на событие",
        "injection classifiers · PII · provenance tags": "классификаторы инъекций · PII · метки источника",
        "swappable harness interface · k8s API": "заменяемый интерфейс харнесса · k8s API",
        "TARGET PROJECTS": "ЦЕЛЕВЫЕ ПРОЕКТЫ", "Company products": "Продукты компании",
        "example-webapp, agent app, …": "example-webapp, агент, …",
        "own repo + tracker project each": "у каждого свой репо и проект",
        "OBSERVABILITY": "НАБЛЮДАЕМОСТЬ", "logs with Grafana;": "логи с Grafana;",
        "LLM traces of apps and agents": "LLM-трейсы приложений и агентов",
        "AGENT RUN": "ЗАПУСК АГЕНТА", "spec agent or coding agent;": "агент спеки или кода;",
        "scoped token, egress policy": "узкий токен, egress-политика",
        "logs · traces": "логи · трейсы", "triage": "для", "context": "триажа", "create": "создать",
        "push branch · open PR": "пуш ветки · открыть PR", "deploys via GitOps": "деплой через GitOps",
        "PLATFORM FOUNDATION": "ФУНДАМЕНТ ПЛАТФОРМЫ",
        "k3s on a single 32 GB laptop · everything reconciled from git by Argo CD (app-of-apps)":
            "k3s на одном ноутбуке с 32 ГБ · всё синхронизирует из git Argo CD (app-of-apps)",
        "CloudNativePG · shared Postgres": "CloudNativePG · общий Postgres",
        "cert-manager · local CA": "cert-manager · свой CA",
    },
    "environments": {
        "Branches and environments": "Ветки и окружения",
        "One ticket from request to release: what the ticket says, what happens in git, where to click":
            "Один тикет от запроса до релиза: что видно в тикете, что происходит в git, куда кликнуть",
        "Agent works": "Работает агент", "Human decides": "Решает человек",
        "TICKET": "ТИКЕТ", "DEPLOY": "ДЕПЛОЙ",
        "other tickets": "другие тикеты", "spec commit": "коммит спеки",
        "spec approved": "спека одобрена", "via @agent or a status": "через @agent или статус",
        "code + archive": "код + архив", "apply in a k8s Job": "apply в k8s Job",
        "→ preview": "→ превью", "preview is up": "превью поднято",
        "link posted to the ticket": "ссылка ушла в тикет",
        "merge": "мёрж", "engineer, or @agent on request": "инженер или @agent по просьбе",
        "release": "релиз", "PR by @agent, merged by a person": "PR от @agent, мёржит человек",
        "Preview": "Превью", "one per PR, gone when it closes": "живёт, пока жив PR",
        "tracks": "ветка", "the PR head of feat/EWA-12_*": "head PR из feat/EWA-12_*",
        "deploys": "деплой", "code is in the PR and CI is green": "код в PR и CI зелёный",
        "via": "через", "always on": "работает всегда", "every merge": "на каждый мёрж",
        "image": "образ", "SHA of the commit Argo CD synced": "SHA коммита, который синкнул Argo CD",
        "Production": "Продакшен", "a person merges the release PR": "человек мёржит релизный PR",
        "version": "версия", "tag v1.6.0: minor bump per release": "тег v1.6.0: minor на каждый релиз",
        "ONE ID ACROSS SYSTEMS": "ОДИН ID ВО ВСЕХ СИСТЕМАХ",
        "release notes of v1.6.0": "release notes v1.6.0",
        "The image tag is the commit SHA everywhere, and the version is only a label · agent Jobs cannot merge, and only people merge into main · closing the PR removes its preview":
            "Тег образа — везде SHA коммита, версия — лишь ярлык · агентские Job’ы не мёржат, в main мёржат только люди · закрыли PR — превью удалилось",
    },
    "lifecycle": {
        "Work item lifecycle": "Жизненный цикл work item",
        "The orchestrator's state machine: each event moves a work item one step from its saved phase":
            "Машина состояний оркестратора: каждое событие сдвигает work item на шаг от сохранённой фазы",
        "Agent works": "Работает агент", "Waits for a human": "Ждёт человека", "System": "Система",
        "Terminal": "Финал", "SPEC PHASE": "ФАЗА СПЕКИ", "BUILD &amp; RELEASE": "СБОРКА И РЕЛИЗ",
        "ticket created": "тикет создан", "Plane webhook": "вебхук Plane",
        "logs + traces read": "прочитаны логи и трейсы", "spec agent in a Job": "агент спеки в Job",
        "asked in ticket + chat": "вопрос в тикете и чате", "the PR holds the spec": "в PR пока только спека",
        "rejected": "отклонён", "spec revision locked": "ревизия спеки закреплена",
        "spec PR": "PR спеки", "approve": "апрув", "needs info": "нужна инфа", "answer": "ответ",
        "reject": "отказ", "request changes": "доработать", "dispatch": "запуск",
        "coding agent in a Job": "кодинг-агент в Job", "same PR · preview up": "тот же PR · превью",
        "development deployed": "development задеплоен", "main tagged · prod synced": "тег в main · прод обновлён",
        "ticket closed": "тикет закрыт", "code pushed": "код запушен", "human merge": "мёрж",
        "release": "релиз", "changes requested": "нужны правки",
        "HUMAN EVENTS · ONLY WHAT MENTIONS @agent": "СОБЫТИЯ ОТ ЛЮДЕЙ · ТОЛЬКО С УПОМИНАНИЕМ @agent",
        "Plane: @agent · status": "Plane: @agent · статус", "Gitea: @agent · review": "Gitea: @agent · ревью",
        "Mattermost: @agent in thread": "Mattermost: @agent в треде",
        "A model reads the intent, code decides whether it is allowed (who, role, phase) · the agent's own events are dropped · only people merge into main":
            "Модель понимает намерение, код решает, можно ли (кто, роль, фаза) · события самого агента отбрасываются · в main мёржат только люди",
    },
    "loop": {
        "The AI-native delivery loop": "AI-native цикл поставки",
        "From ticket to deployed change: agents do the work, humans own the decisions":
            "От тикета до задеплоенного изменения: работу делают агенты, решения принимают люди",
        "Trigger": "Триггер", "Agent": "Агент", "Human gate": "Гейт человека", "Platform": "Платформа",
        "TRIGGER": "ТРИГГЕР", "Signal": "Сигнал", "A human report, or an": "Сообщение человека или",
        "anomaly in logs and traces": "аномалия в логах и трейсах",
        "PLATFORM": "ПЛАТФОРМА", "Ticket": "Тикет", "Work item in Plane,": "Work item в Plane,",
        "linked across all systems": "связан во всех системах",
        "AGENT": "АГЕНТ", "Triage": "Триаж", "Reads Loki logs and": "Читает логи Loki",
        "Langfuse traces": "и трейсы Langfuse",
        "OpenSpec change, opened": "change в OpenSpec,", "as a spec PR in Gitea": "PR со спекой в Gitea",
        "HUMAN GATE": "ГЕЙТ ЧЕЛОВЕКА", "Spec approval": "Апрув спеки",
        "Approve, change or answer": "Апрув, правки или ответ",
        "via Plane, Gitea, Mattermost": "в Plane, Gitea, Mattermost",
        "Implementation": "Реализация", "OpenCode, models via a router": "OpenCode, модели через роутер",
        "in an isolated k8s Job": "в изолированном k8s Job",
        "Code review": "Код-ревью", "Regular PR review; only": "Обычное ревью PR; в main",
        "people merge into main": "мёржат только люди",
        "Deploy": "Деплой", "Argo CD syncs the cluster": "Argo CD синхронизирует",
        "from git": "кластер из git",
        "spec PR": "PR спеки", "clarify · request changes": "уточнить · доработать", "approve": "апрув",
        "merge": "мёрж", "changes requested": "нужны правки", "observe": "наблюдение",
        "logs · traces": "логи · трейсы",
        "One work item = one OpenSpec change": "Один work item = один change в OpenSpec",
        "specs are the contract between people and agents": "спеки — это контракт между людьми и агентами",
    },
}

DESC = {
    "architecture": "Люди работают в Plane, Mattermost и Gitea или через те же адаптеры в Jira, Trello, Slack, Telegram, GitLab или GitHub. Адаптеры каналов передают события оркестратору на LangGraph, который проходит guardrails и запускает агентов как Kubernetes Jobs. Целевые проекты — продукты компании, сколько угодно, каждый в своём репозитории и проекте трекера; они пишут логи и трейсы для триажа. Всё работает на k3s и синхронизируется Argo CD.",
    "environments": "Один тикет от запроса до релиза в трёх дорожках. Статусы тикета в Plane: назначен на @agent, drafting, spec review, approved, implementing, code review, on dev, released. Git: агент ответвляет feat/EWA-12 от development, коммитит спеку, открывает PR, человек апрувит спеку, агент коммитит код, CI собирает образ, и у PR появляется превью; инженер или агент по его просьбе мёржит в development, который деплоится в dev; агент открывает релизный PR, а человек мёржит его в main с тегом v1.6.0, который деплоится в продакшен. Окружения: превью на каждый PR, dev из development, продакшен из main.",
    "lifecycle": "Машина состояний, которой владеет оркестратор. Фаза спеки: ticket open, triaged, drafting, awaiting answer, spec review, closed или spec approved. Сборка и релиз: implementing, code review в том же PR с превью, on dev после мёржа в development, released, когда релиз доходит до main, done. События от людей учитываются, только если упоминают @agent; модель понимает намерение, а код решает, можно ли его выполнить.",
    "loop": "Сигнал, тикет, триаж, delta spec, апрув спеки, реализация, код-ревью, деплой и снова сигнал. Два человеческих гейта с обратной связью.",
}

# Layout tweaks that the longer Russian text needs, as exact (old, new) replacements.
TWEAKS = {
    "architecture": [
        ('''    <circle class="a-plat" cx="82" cy="0" r="6"/><text class="lg" x="94" y="4">Платформа</text>
    <circle class="a-agent" cx="174" cy="0" r="6"/><text class="lg" x="186" y="4">Наше</text>
    <circle class="a-app" cx="240" cy="0" r="6"/><text class="lg" x="252" y="4">Целевые проекты</text>''',
         '''    <circle class="a-plat" cx="62" cy="0" r="6"/><text class="lg" x="74" y="4">Платформа</text>
    <circle class="a-agent" cx="164" cy="0" r="6"/><text class="lg" x="176" y="4">Наше</text>
    <circle class="a-app" cx="230" cy="0" r="6"/><text class="lg" x="242" y="4">Целевые проекты</text>'''),
    ],
    "lifecycle": [
        ('''    <circle class="a-human" cx="104" cy="0" r="6"/><text class="lg" x="116" y="4">Ждёт человека</text>
    <circle class="a-sys" cx="244" cy="0" r="6"/><text class="lg" x="256" y="4">Система</text>
    <circle class="a-term" cx="322" cy="0" r="6"/><text class="lg" x="334" y="4">Финал</text>''',
         '''    <circle class="a-human" cx="114" cy="0" r="6"/><text class="lg" x="126" y="4">Ждёт человека</text>
    <circle class="a-sys" cx="226" cy="0" r="6"/><text class="lg" x="238" y="4">Система</text>
    <circle class="a-term" cx="302" cy="0" r="6"/><text class="lg" x="314" y="4">Финал</text>'''),
        ('<text class="lbl" x="712" y="438" text-anchor="middle">мёрж</text>',
         '<text class="lbl" x="712" y="438" text-anchor="middle">мёрж</text>\n'
         '  <text class="lbl" x="712" y="468" text-anchor="middle">человеком</text>'),
    ],
}


def build(name):
    src = open(DIAGRAMS / f"{name}.svg", encoding="utf-8").read()
    table = T[name]
    missing = []

    def sub(m):
        text = m.group(1)
        if text in table:
            return ">" + table[text] + "<"
        if not re.search(r"[A-Za-z]", text) or text in KEEP_ALL:
            return m.group(0)
        missing.append(text)
        return m.group(0)

    head, sep, body = src.partition("</style>")
    out = head + sep + re.sub(r">([^<>]+)<", sub, body)
    out = re.sub(r'(<desc id="desc">)(.*?)(</desc>)', lambda m: m.group(1) + DESC[name] + m.group(3), out, flags=re.S)
    out = re.sub(r"(<title id=\"title\">)(.*?)(</title>)",
                 lambda m: m.group(1) + table.get(m.group(2), m.group(2)) + m.group(3), out)
    out = out.replace('<svg xmlns="http://www.w3.org/2000/svg" ', '<svg xmlns="http://www.w3.org/2000/svg" lang="ru" ', 1)
    for old, new in TWEAKS.get(name, []):
        assert out.count(old) == 1, (name, old)
        out = out.replace(old, new)
    if missing:
        sys.exit(f"{name}: untranslated: {sorted(set(missing))}")
    open(DIAGRAMS / f"{name}_ru.svg", "w", encoding="utf-8").write(out)
    print(f"{name}_ru.svg ok")


for n in T:
    build(n)
