"""Generates docs/diagrams/example.svg and example_ru.svg: one ticket as a conversation.

Usage, from anywhere:  python3 docs/diagrams/tools/example.py
Edit the EN rows and strings first, then bring RU_ROWS and LANG["ru"] in line with them.
EN text widths are tuned by hand; RU widths are measured with Pillow, using the fonts that
`fc-match` resolves for "Noto Sans" and "JetBrains Mono" (needs python3-pil and fontconfig).
"""
import pathlib
import subprocess
import sys

from PIL import ImageFont

DIAGRAMS = pathlib.Path(__file__).resolve().parent.parent


def _font(pattern):
    return subprocess.run(["fc-match", "-f", "%{file}", pattern], check=True,
                          capture_output=True, text=True).stdout

_FONTS = {
    "msg": ("Noto Sans", 12.5),
    "msg-m": ("Noto Sans:bold", 12),
    "act": ("Noto Sans:italic", 12.5),
    "msg mono": ("JetBrains Mono", 12.5),
    "eff": ("Noto Sans", 12),
    "ok": ("Noto Sans:bold", 12),
}


def measure(cls, text):
    pattern, size = _FONTS[cls]
    return round(ImageFont.truetype(_font(pattern), size).getlength(text) + 1)

URL = "ewa-12.qa.example-webapp.sdlc.internal"
CH_W = {"PLANE": 44, "GITEA": 44, "MATTERMOST": 86}

# (who, channel, parts, status_chip, chip_class, node, effect)
# parts: list of (css_class, text, width_estimate); who: po | en | ag | en-act
ROWS = [
    ("po", "PLANE", [("msg", "New ticket EWA-12: filter orders by status ·", 237), ("msg-m", "assignee: @agent", 104)],
     "Drafting", "c-agent", "agent", [("eff", "the agent takes it")]),
    ("ag", "PLANE", [("msg", "Should the filter survive a page reload?", 240)],
     "Needs info", "c-human", "wait", [("eff", "stops and waits")]),
    ("po", "PLANE", [("msg-m", "@agent", 44), ("msg", "no, reset it on reload", 128)],
     "Drafting", "c-agent", "agent", [("eff", "a Job writes the spec")]),
    ("ag", "PLANE", [("msg", "The spec is ready: PR #34, please review", 238)],
     "Spec review", "c-human", "wait", [("eff", "PR #34, only the spec")]),
    ("po", "PLANE", [("msg-m", "@agent", 44), ("msg", "looks good, go ahead", 120)],
     "Implementing", "c-agent", "gate", [("eff", "approve:"), ("ok", "PO may approve ✓")]),
    "sep",
    ("ag", "PLANE", [("msg", "Try it:", 32), ("msg mono", URL, 292)],
     "Code review", "c-human", "sys", [("eff", "code in PR #34, preview up")]),
    ("en", "GITEA", [("msg-m", "@agent", 44), ("msg", "add an empty state when nothing matches", 248)],
     "Implementing", "c-agent", "agent", [("eff", "changes:"), ("ok", "engineer may ✓")]),
    ("ag", "GITEA", [("msg", "Implementing a fix", 112)],
     None, None, "agent", [("eff", "a Job pushes the fix")]),
    ("ag", "GITEA", [("msg", "Fix is ready, have a look:", 140), ("msg mono", URL, 292)],
     "Code review", "c-human", "sys", [("eff", "preview updated")]),
    ("en", "GITEA", [("msg-m", "@agent", 44), ("msg", "ok, merge it into dev", 126)],
     "On dev", "c-sys", "gate", [("eff", "merge:"), ("ok", "engineer may ✓")]),
    "sep",
    ("po", "MATTERMOST", [("msg-m", "@agent", 44), ("msg", "let's prepare a release", 138)],
     None, None, "agent", [("eff", "prepare_release:"), ("ok", "PO may ✓")]),
    ("ag", "MATTERMOST", [("msg", "Release 1.6.0 is ready: PR #35", 174)],
     None, None, "wait", [("eff", "PR #35 → main, notes: EWA-9, EWA-12")]),
    ("en-act", "GITEA", [("act", "approves and merges PR #35 by hand", 214)],
     "Released", "c-sys", "gate", [("eff", "v1.6.0 tagged, prod updated")]),
]

RU_ROWS = [
    ("po", "PLANE", [("msg", "Новый тикет EWA-12: фильтр заказов по статусу ·", None), ("msg-m", "исполнитель: @agent", None)],
     "Drafting", "c-agent", "agent", [("eff", "агент берёт задачу")]),
    ("ag", "PLANE", [("msg", "Фильтр должен переживать перезагрузку страницы?", None)],
     "Needs info", "c-human", "wait", [("eff", "останавливается и ждёт")]),
    ("po", "PLANE", [("msg-m", "@agent", None), ("msg", "нет, пусть сбрасывается", None)],
     "Drafting", "c-agent", "agent", [("eff", "Job пишет спеку")]),
    ("ag", "PLANE", [("msg", "Спека готова: PR #34, жду ревью", None)],
     "Spec review", "c-human", "wait", [("eff", "PR #34, пока только спека")]),
    ("po", "PLANE", [("msg-m", "@agent", None), ("msg", "выглядит хорошо, поехали", None)],
     "Implementing", "c-agent", "gate", [("eff", "approve:"), ("ok", "PO может апрувить ✓")]),
    "sep",
    ("ag", "PLANE", [("msg", "Попробуйте:", None), ("msg mono", URL, 292)],
     "Code review", "c-human", "sys", [("eff", "код в PR #34, превью есть")]),
    ("en", "GITEA", [("msg-m", "@agent", None), ("msg", "добавь пустое состояние, когда ничего не нашлось", None)],
     "Implementing", "c-agent", "agent", [("eff", "changes:"), ("ok", "инженер может ✓")]),
    ("ag", "GITEA", [("msg", "Делаю фикс", None)],
     None, None, "agent", [("eff", "Job пушит фикс")]),
    ("ag", "GITEA", [("msg", "Фикс готов, смотрите:", None), ("msg mono", URL, 292)],
     "Code review", "c-human", "sys", [("eff", "превью обновлено")]),
    ("en", "GITEA", [("msg-m", "@agent", None), ("msg", "ок, мёржи в dev", None)],
     "On dev", "c-sys", "gate", [("eff", "merge:"), ("ok", "инженер может ✓")]),
    "sep",
    ("po", "MATTERMOST", [("msg-m", "@agent", None), ("msg", "давай готовить релиз", None)],
     None, None, "agent", [("eff", "prepare_release:"), ("ok", "PO может ✓")]),
    ("ag", "MATTERMOST", [("msg", "Релиз 1.6.0 готов: PR #35", None)],
     None, None, "wait", [("eff", "PR #35 → main, заметки: EWA-9, EWA-12")]),
    ("en-act", "GITEA", [("act", "апрувит и мёржит PR #35 руками", None)],
     "Released", "c-sys", "gate", [("eff", "тег v1.6.0, прод обновлён")]),
]

LANG = {
    "en": {
        "lang": "en", "rows": ROWS, "en_av": "EN",
        "title": "How it works, by example",
        "desc": 'One ticket, EWA-12, from request to release, as a conversation. Any message can come from Plane, Mattermost or a Gitea comment. The product owner files the ticket in Plane and assigns it to @agent. The agent asks whether the filter should survive a reload and waits; the product owner answers. The agent opens PR 34 with the spec; the product owner approves with a comment, and the policy checks that a product owner may approve. The agent posts a preview link. The engineer asks in a Gitea review for an empty state; the agent says it is implementing a fix, then posts the updated preview. The engineer tells the agent to merge into development. In the chat, the product owner asks for a release; the agent opens release PR 35. The engineer approves and merges it, CI tags v1.6.0 and production updates.',
        "subtitle": 'EWA-12, "filter orders by status": people talk to @agent in their usual tools, and it does the work in between',
        "engineer": "Engineer", "gate": "Gate",
        "who": "WHO SAYS WHAT, AND WHERE", "any": "any message works from", "any_attr": 'x="398"',
        "gitea_comment": "GITEA COMMENT", "what": "WHAT HAPPENS",
        "rules_kick": "THE RULES BEHIND IT",
        "rules": "@agent reads only what mentions it and answers where it was asked · a model reads the intent, code checks who may do what · only people merge into main",
    },
    "ru": {
        "lang": "ru", "rows": RU_ROWS, "en_av": "ИН",
        "title": "Как это работает, на примере",
        "desc": "Один тикет, EWA-12, от запроса до релиза в виде переписки. Любое сообщение может прийти из Plane, Mattermost или комментом в Gitea. Product owner заводит тикет в Plane и назначает его на @agent. Агент спрашивает, должен ли фильтр переживать перезагрузку, и ждёт; product owner отвечает. Агент открывает PR 34 со спекой; product owner апрувит комментом, а policy проверяет, что product owner может апрувить. Агент присылает ссылку на превью. Инженер в ревью в Gitea просит пустое состояние; агент пишет, что делает фикс, потом присылает обновлённое превью. Инженер просит агента смёржить в development. В чате product owner просит подготовить релиз; агент открывает релизный PR 35. Инженер апрувит и мёржит его, CI ставит тег v1.6.0, продакшен обновляется.",
        "subtitle": "EWA-12, «фильтр заказов по статусу»: люди пишут @agent в привычных инструментах, а он делает работу между репликами",
        "engineer": "Инженер", "gate": "Гейт",
        "who": "КТО, ЧТО И ГДЕ ПИШЕТ", "any": "любое сообщение — из", "any_attr": 'x="532" text-anchor="end"',
        "gitea_comment": "КОММЕНТ В GITEA", "what": "ЧТО ПРОИСХОДИТ",
        "rules_kick": "ПРАВИЛА ЗА ЭТИМ",
        "rules": "@agent читает только то, где его упомянули, и отвечает там же · модель понимает намерение, код проверяет, кто что может · в main мёржат только люди",
    },
}

EFF_W = {"approve:": 47, "changes:": 50, "merge:": 39, "prepare_release:": 92}

TOP = 172
STEP = 47
SEP_GAP = 29
TL_X = 868


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def row_svg(r, y, T):
    who, ch, parts, chip, chip_cls, node, eff = r
    out = []
    pill_w = CH_W[ch]
    text_w = sum((w or measure(c, t)) for c, t, w in parts) + 6 * (len(parts) - 1)
    bw = 10 + pill_w + 10 + text_w + 16
    if who == "ag":
        out.append(f'  <circle class="av-ag" cx="790" cy="{y}" r="14"/><text class="av" x="790" y="{y+4}" text-anchor="middle">@</text>')
        bx = 766 - bw
        bcls = "b-a"
    else:
        av = "po" if who == "po" else "en"
        label = "PO" if who == "po" else T["en_av"]
        out.append(f'  <circle class="av-{av}" cx="58" cy="{y}" r="14"/><text class="av" x="58" y="{y+4}" text-anchor="middle">{label}</text>')
        bx = 82
        bcls = {"po": "b-po", "en": "b-en", "en-act": "b-act"}[who]
    out.append(f'  <rect class="{bcls}" x="{bx}" y="{y-17}" width="{bw}" height="34" rx="12"/>')
    px = bx + 10
    out.append(f'  <rect class="chp" x="{px}" y="{y-9}" width="{pill_w}" height="18" rx="9"/>'
               f'<text class="ch" x="{px + pill_w/2:g}" y="{y+3.5}" text-anchor="middle">{ch}</text>')
    tx = px + pill_w + 10
    for cls, t, w in parts:
        out.append(f'  <text class="{cls}" x="{tx}" y="{y+4.5}">{esc(t)}</text>')
        tx += (w or measure(cls, t)) + 6
    # timeline node
    if node == "agent":
        out.append(f'  <circle class="n-agent" cx="{TL_X}" cy="{y}" r="7"/>')
    elif node == "wait":
        out.append(f'  <circle class="n-wait" cx="{TL_X}" cy="{y}" r="6"/>')
    elif node == "sys":
        out.append(f'  <circle class="n-sys" cx="{TL_X}" cy="{y}" r="7"/>')
    else:
        out.append(f'  <path class="gate" d="M{TL_X},{y-10} L{TL_X+10},{y} L{TL_X},{y+10} L{TL_X-10},{y} Z"/>')
    ex = 884
    if chip:
        out.append(f'  <rect class="{chip_cls}" x="884" y="{y-12}" width="104" height="24" rx="12"/>'
                   f'<text class="cst" x="936" y="{y+4}" text-anchor="middle">{chip}</text>')
        ex = 1000
    for cls, t in eff:
        out.append(f'  <text class="{cls}" x="{ex}" y="{y+4}">{esc(t)}</text>')
        ex += EFF_W.get(t) or measure(cls, t)
        ex += 6
    return "\n".join(out)


def main(path, lang):
    ROWS = LANG[lang]["rows"]
    T = LANG[lang]
    ys, seps = [], []
    y = TOP
    for r in ROWS:
        if r == "sep":
            seps.append(y - STEP + (STEP + SEP_GAP) // 2)
            y += SEP_GAP
            continue
        ys.append(y)
        y += STEP
    last = ys[-1]
    band_h = last + 24 - 104
    foot_y = 104 + band_h + 14
    H = foot_y + 66 + 22

    rows = [r for r in ROWS if r != "sep"]
    body = "\n\n".join(row_svg(r, yy, T) for r, yy in zip(rows, ys))
    sep_svg = "\n".join(f'  <line class="sep" x1="44" y1="{s}" x2="804" y2="{s}"/>' for s in seps)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" lang="{T["lang"]}" viewBox="0 0 1240 {H}" width="1240" height="{H}" role="img" aria-labelledby="title desc">
  <title id="title">{T["title"]}</title>
  <desc id="desc">{T["desc"]}</desc>
  <style>
    .bg {{ fill: #ffffff; stroke: #e5e7eb; }}
    text {{ font-family: Inter, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    .mono {{ font-family: "JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace; }}
    .h1 {{ font-size: 24px; font-weight: 700; fill: #0f172a; }}
    .h2 {{ font-size: 14px; fill: #64748b; }}
    .phase {{ font-size: 11px; font-weight: 700; letter-spacing: 1.4px; fill: #94a3b8; }}
    .msg {{ font-size: 12.5px; fill: #0f172a; }}
    .msg-m {{ font-size: 12px; font-weight: 600; fill: #6d28d9; }}
    .act {{ font-size: 12.5px; font-style: italic; fill: #475569; }}
    .ch {{ font-size: 10px; font-weight: 700; letter-spacing: 0.4px; fill: #475569; }}
    .av {{ font-size: 11px; font-weight: 700; fill: #ffffff; }}
    .cst {{ font-size: 12px; font-weight: 700; fill: #0f172a; }}
    .eff {{ font-size: 12px; fill: #475569; }}
    .ok {{ font-size: 12px; font-weight: 700; fill: #15803d; }}
    .lg {{ font-size: 12px; fill: #475569; }}
    .txt {{ font-size: 12.5px; fill: #475569; }}
    .kick {{ font-size: 10.5px; font-weight: 700; letter-spacing: 1.2px; fill: #6d28d9; }}

    .band {{ fill: #f8fafc; }}
    .panel {{ fill: #faf5ff; stroke: #ddd6fe; }}
    .chp {{ fill: #ffffff; stroke: #cbd5e1; }}
    .b-po {{ fill: #f0fdf4; stroke: #86efac; stroke-width: 1.2; }}
    .b-en {{ fill: #ecfeff; stroke: #67e8f9; stroke-width: 1.2; }}
    .b-act {{ fill: #ffffff; stroke: #06b6d4; stroke-width: 1.2; stroke-dasharray: 4 3; }}
    .b-a  {{ fill: #f5f3ff; stroke: #c4b5fd; stroke-width: 1.2; }}
    .av-po {{ fill: #16a34a; }}
    .av-en {{ fill: #0891b2; }}
    .av-ag {{ fill: #7c3aed; }}
    .c-agent {{ fill: #f5f3ff; stroke: #a78bfa; stroke-width: 1.3; }}
    .c-human {{ fill: #fffbeb; stroke: #f59e0b; stroke-width: 1.3; }}
    .c-sys   {{ fill: #f0fdfa; stroke: #5eead4; stroke-width: 1.3; }}
    .tl {{ stroke: #cbd5e1; stroke-width: 2; }}
    .n-agent {{ fill: #7c3aed; stroke: #ffffff; stroke-width: 2; }}
    .n-wait  {{ fill: #ffffff; stroke: #d97706; stroke-width: 2.5; }}
    .n-sys   {{ fill: #0f766e; stroke: #ffffff; stroke-width: 2; }}
    .gate    {{ fill: #f59e0b; stroke: #ffffff; stroke-width: 2; }}
    .sep {{ stroke: #e2e8f0; stroke-width: 1; stroke-dasharray: 3 4; }}

    @media (prefers-color-scheme: dark) {{
      .bg {{ fill: #0d1117; stroke: #30363d; }}
      .h1, .msg, .cst {{ fill: #f0f6fc; }}
      .h2 {{ fill: #8b949e; }}
      .phase {{ fill: #6e7681; }}
      .msg-m {{ fill: #c4b5fd; }}
      .act, .eff, .lg, .txt, .ch {{ fill: #b1bac4; }}
      .ok {{ fill: #4ade80; }}
      .kick {{ fill: #c4b5fd; }}
      .band {{ fill: #11161d; }}
      .panel {{ fill: #140f22; stroke: #3b2a63; }}
      .chp {{ fill: #0d1117; stroke: #30363d; }}
      .b-po {{ fill: #0c2216; stroke: #166534; }}
      .b-en {{ fill: #082a33; stroke: #0e7490; }}
      .b-act {{ fill: #0d1117; stroke: #06b6d4; }}
      .b-a  {{ fill: #1e1633; stroke: #5b21b6; }}
      .av-po {{ fill: #22c55e; }}
      .av-en {{ fill: #06b6d4; }}
      .av-ag {{ fill: #8b5cf6; }}
      .c-agent {{ fill: #1e1633; stroke: #7c3aed; }}
      .c-human {{ fill: #261c0c; stroke: #d97706; }}
      .c-sys   {{ fill: #0b2420; stroke: #0f766e; }}
      .tl {{ stroke: #30363d; }}
      .n-agent {{ fill: #8b5cf6; stroke: #11161d; }}
      .n-wait  {{ fill: #11161d; stroke: #f59e0b; }}
      .n-sys   {{ fill: #14b8a6; stroke: #11161d; }}
      .gate    {{ stroke: #11161d; }}
      .sep {{ stroke: #21262d; }}
    }}
  </style>

  <rect class="bg" x="0.5" y="0.5" width="1239" height="{H-1}" rx="18"/>

  <!-- header -->
  <text class="h1" x="40" y="56">{T["title"]}</text>
  <text class="h2" x="40" y="82">{T["subtitle"]}</text>
  <g transform="translate(866,52)">
    <circle class="av-po" cx="0" cy="0" r="9"/><text class="lg" x="15" y="4">Product owner</text>
    <circle class="av-en" cx="118" cy="0" r="9"/><text class="lg" x="133" y="4">{T["engineer"]}</text>
    <circle class="av-ag" cx="208" cy="0" r="9"/><text class="lg" x="223" y="4">@agent</text>
    <path class="gate" d="M286,-8 L294,0 L286,8 L278,0 Z"/><text class="lg" x="301" y="4">{T["gate"]}</text>
  </g>

  <!-- bands -->
  <rect class="band" x="24" y="104" width="800" height="{band_h}" rx="14"/>
  <rect class="band" x="836" y="104" width="380" height="{band_h}" rx="14"/>
  <text class="phase" x="44" y="130">{T["who"]}</text>
  <text class="eff" {T["any_attr"]} y="130">{T["any"]}</text>
  <rect class="chp" x="540" y="117" width="44" height="18" rx="9"/><text class="ch" x="562" y="129.5" text-anchor="middle">PLANE</text>
  <rect class="chp" x="590" y="117" width="86" height="18" rx="9"/><text class="ch" x="633" y="129.5" text-anchor="middle">MATTERMOST</text>
  <rect class="chp" x="682" y="117" width="122" height="18" rx="9"/><text class="ch" x="743" y="129.5" text-anchor="middle">{T["gitea_comment"]}</text>
  <text class="phase" x="856" y="130">{T["what"]}</text>

{sep_svg}

  <!-- timeline -->
  <line class="tl" x1="{TL_X}" y1="{ys[0]-2}" x2="{TL_X}" y2="{last}"/>

{body}

  <!-- rules -->
  <rect class="panel" x="24" y="{foot_y}" width="1192" height="66" rx="14"/>
  <text class="kick" x="44" y="{foot_y+25}">{T["rules_kick"]}</text>
  <text class="txt" x="44" y="{foot_y+48}">{T["rules"]}</text>
</svg>
'''
    open(path, "w", encoding="utf-8").write(svg)


if __name__ == "__main__":
    main(DIAGRAMS / "example.svg", "en")
    main(DIAGRAMS / "example_ru.svg", "ru")
    print("example.svg, example_ru.svg ok")
