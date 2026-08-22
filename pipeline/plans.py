# -*- coding: utf-8 -*-
"""Парсер планов ProjectsPalns — источник истины для план-раннера.

Поддерживает два формата карточек:

1. «Карточки СДР» (MepBimServer):
   ### Карточка 1.1 — Название
   - **Статус**: `open`
   - **Цель**: ...
   - **Критерии приёмки**: 1. ... 2. ...
   - **Зависимости**: нет | 1.1 (...)

2. «Именованные карточки» (HeatLossRevit2 GEO):
   ## GEO-1 — Название ⬜            (⬜ open / 🔄 в работе / ✅ done / ❌ снята)
   **DoD (машинный):**
   - [ ] проверка

Плюс сводная таблица СДР (если есть): | СДР | Наименование | Тип | Статус |.

Статусы нормализуются: open / in_progress / done / cancelled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CARD_RE = re.compile(
    r"^(#{1,3})\s+(?:Карточка(?:\s+задачи)?\s+)?"
    r"([A-Za-zА-Яа-яЁё]{0,12}-?\d+(?:\.\d+)*)\.?"
    r"(?:\s*[—–-]+\s*(.+?))??"
    r"\s*([⬜✅🔄❌])?\s*$",
    re.M,
)
STATUS_BULLET_RE = re.compile(r"-\s*\*\*Статус\*\*\s*:\s*`([^`]+)`")
DEP_ID_RE = re.compile(r"\d+(?:\.\d+)*|[A-Za-zА-Яа-яЁё]+-\d+")
CHECKBOX_RE = re.compile(r"^\s*-\s\[( |x|X)\]\s+(.+?)\s*$", re.M)
SDR_ROW_RE = re.compile(r"^\|\s*`?([\w.\-]+)`?\s*\|(.*?)$")
STATUS_LINE_RE = re.compile(r"Статус:\s*(Открыто|В работе|Выполнено|Отменено)")
NAME_SECTION_RE = re.compile(r"^#+\s*Наименование\s*\n(.+?)$", re.M)

EMOJI_BY_STATUS = {"open": "⬜", "in_progress": "🔄", "done": "✅", "cancelled": "❌"}
EMOJI_TO_STATUS = {v: k for k, v in EMOJI_BY_STATUS.items()}
STATUS_WORDS = {
    "открыто": "open", "open": "open", "новый": "open", "⬜": "open",
    "в работе": "in_progress", "in_progress": "in_progress", "активно": "in_progress",
    "готово к исполнению": "open", "🔄": "in_progress",
    "выполнено": "done", "done": "done", "закрыто": "done", "✅": "done",
    "снято": "cancelled", "отменено": "cancelled", "не актуален": "cancelled", "❌": "cancelled",
}

BULLETS = ["Цель", "Описание", "Входные данные", "Что проверяем", "Зависимости",
           "Сроки", "Задание", "Гиперссылки", "Ожидаемые доказательства",
           "Слой", "Модуль", "Чекпоинт", "Статус"]


def norm_status(raw: str) -> str:
    s = str(raw or "").strip().lower()
    for k, v in STATUS_WORDS.items():
        if s.startswith(k) or k in s:
            return v
    return "open"


def status_word(status: str) -> str:
    return {"open": "Открыто", "in_progress": "В работе",
            "done": "Выполнено", "cancelled": "Снято"}.get(status, status)


@dataclass
class Card:
    id: str                      # "1.1" или "GEO-1"
    title: str
    level: int = 2               # уровень заголовка (2/3)
    heading_line: int = 0        # 0-based строка заголовка
    end_line: int = 0            # граница секции (exclusive)
    status: str = "open"
    goal: str = ""
    description: str = ""
    inputs: str = ""
    checks_text: str = ""        # «Что проверяем»
    criteria: list = field(default_factory=list)
    deps: list = field(default_factory=list)
    dates: str = ""
    task_text: str = ""
    links: str = ""
    evidence: str = ""
    layer: str = ""
    module: str = ""
    checkpoint: bool = False     # явная метка чекпоинта на карточке
    body: str = ""

    @property
    def is_stage(self) -> bool:
        return "." not in self.id.replace("-", "").strip() and self.id.isdigit()


@dataclass
class Plan:
    path: Path
    title: str = ""
    mission: str = ""
    cards: list = field(default_factory=list)
    sdr_rows: dict = field(default_factory=dict)   # id -> {"name","kind","status_raw","line"}

    def card(self, cid: str):
        for c in self.cards:
            if c.id == str(cid):
                return c
        return None

    def execution_cards(self) -> list:
        return sorted((c for c in self.cards if not c.is_stage), key=lambda c: _sort_key(c.id))

    def done_ids(self) -> set:
        return {c.id for c in self.cards if c.status == "done"}

    def ready_cards(self) -> list:
        """Открытые листовые карточки с закрытыми зависимостями."""
        done = self.done_ids()
        out = []
        for c in self.execution_cards():
            if c.status != "open":
                continue
            if all(d in done for d in c.deps):
                out.append(c)
        return out

    def progress(self) -> dict:
        ex = self.execution_cards()
        done = sum(1 for c in ex if c.status == "done")
        return {"total": len(ex), "done": done, "left": len(ex) - done}


def _sort_key(cid: str):
    parts = re.split(r"(\d+)", str(cid))
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


COMBO_KEYS = ("Слой", "Модуль", "Статус", "Чекпоинт")
PROSE_KEYS = tuple(k for k in BULLETS if k not in COMBO_KEYS)


def _extract_bullets(text: str) -> dict:
    """'- **Поле**: значение'. Двухпроходно:
    1) прозаичные поля — значение до следующего буллета (многострочно);
    2) комбостроки '- **Слой**: x · **Модуль**: y · **Статус**: z' — по сегментам '·'."""
    out: dict = {}

    # 1) прозаичные (многострочные)
    pat_prose = re.compile(
        r"^-\s*\*\*(?P<k>" + "|".join(PROSE_KEYS) + r")\*\*\s*:\s*", re.M)
    ms = list(pat_prose.finditer(text))
    for i, m in enumerate(ms):
        vstart = m.end()
        vend = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        val = text[vstart:vend].strip()
        if val:
            out.setdefault(m.group("k"), val)

    # 2) комбостроки: сегменты '·' внутри строк, начинающихся с '- '
    pat_seg = re.compile(r"\*\*(?P<k>" + "|".join(COMBO_KEYS) + r")\*\*\s*:\s*")
    trim_edges = re.compile(r"^[`\s·]+|[`\s·]+$")
    for line in text.splitlines():
        if not line.lstrip().startswith("- "):
            continue
        kms = list(pat_seg.finditer(line))
        for i, m in enumerate(kms):
            vstart = m.end()
            vend = kms[i + 1].start() if i + 1 < len(kms) else len(line)
            val = trim_edges.sub("", line[vstart:vend])
            if val:
                out.setdefault(m.group("k"), val)
    return out


def _criteria_from(text: str) -> list:
    crit = []
    m = re.search(r"(?:\*\*)?Критерии приёмки(?:\*\*)?\s*:?\s*\n((?:\s*\d+\..+\n?)+)", text)
    if m:
        crit += [re.sub(r"^\s*\d+\.\s*", "", ln).strip()
                 for ln in m.group(1).strip().splitlines() if ln.strip()]
    for _, item in CHECKBOX_RE.findall(text):
        if item.strip() and item.strip() not in crit:
            crit.append(item.strip())
    return crit


def _sections(text: str) -> dict:
    """Секции '## Имя' -> содержимое (для формата карточек-секций)."""
    out: dict = {}
    pat = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.M)
    ms = list(pat.finditer(text))
    for i, m in enumerate(ms):
        name = m.group(1).strip().lower()
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.setdefault(name, text[m.end():end].strip())
    return out


def _deps_from(raw: str, own_id: str) -> list:
    if not raw or raw.strip().lower() in ("нет", "—", "-", "нет зависимостей"):
        return []
    deps = []
    for tok in DEP_ID_RE.findall(raw):
        if tok == own_id:
            continue
        if re.fullmatch(r"[A-Za-zА-Яа-яЁё]+-\d+", tok) or re.fullmatch(r"\d+(\.\d+)*", tok):
            if tok not in deps:
                deps.append(tok)
    return deps


def parse_plan(path) -> Plan:
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    plan = Plan(path=path)
    mtitle = None
    # первый H1, который НЕ является заголовком карточки (секционный формат)
    for m in re.finditer(r"^#\s+(.+)$", text, re.M):
        if not CARD_RE.match(m.group(0)):
            mtitle = m
            break
    plan.title = mtitle.group(1).strip() if mtitle else path.stem
    mm = re.search(r"##\s*Миссия\s*\n(.*?)(?=\n##\s|\Z)", text, re.S)
    plan.mission = mm.group(1).strip()[:4000] if mm else ""

    # Сводная таблица СДР (опционально)
    for i, ln in enumerate(text.splitlines()):
        rm = SDR_ROW_RE.match(ln.strip())
        if not rm or rm.group(1).lower() in ("сдр", "---", "id"):
            continue
        cells = [c.strip().strip("`") for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[\w.\-]+", cells[0]):
            plan.sdr_rows[cells[0]] = {
                "name": cells[1], "kind": cells[2], "status_raw": cells[3], "line": i,
            }

    # Карточки по заголовкам
    headings = []
    for m in CARD_RE.finditer(text):
        start_line = text.count("\n", 0, m.start())
        headings.append({
            "id": m.group(2).rstrip("."),
            "title": m.group(3) or "",
            "emoji": m.group(4) or "",
            "level": len(m.group(1)),
            "line": start_line,
            "span": m.span(),
        })
    for i, h in enumerate(headings):
        h["end_line"] = (headings[i + 1]["line"] if i + 1 < len(headings)
                         else text.count("\n") + 1)

    lines = text.splitlines()
    for h in headings:
        body = "\n".join(lines[h["line"]:h["end_line"]])
        bullets = _extract_bullets(body)
        secs = _sections(body)

        def pick(*keys, default=""):
            for k in keys:
                v = bullets.get(k)
                if v and v.strip():
                    return v.strip()
            for k in keys:
                sk = next((v for name, v in secs.items()
                           if name == k.lower() or name.startswith(k.lower())), "")
                if sk:
                    return sk
            return default

        raw_title = re.sub(r"\s*[⬜✅🔄❌]\s*$", "", h["title"] or "").strip()
        if not raw_title:
            nm = NAME_SECTION_RE.search(body)
            if nm:
                raw_title = nm.group(1).strip()
            elif secs.get("наименование"):
                raw_title = secs["наименование"].splitlines()[0].strip()

        status = ""
        if bullets.get("Статус"):
            status = norm_status(bullets["Статус"])
        if h.get("emoji"):
            status = EMOJI_TO_STATUS[h["emoji"]]
        if not status:
            sl = STATUS_LINE_RE.search(body)
            if sl:
                status = norm_status(sl.group(1))
        row = plan.sdr_rows.get(h["id"])
        if not status and row:
            status = norm_status(row["status_raw"])
        status = status or "open"

        cp_raw = (bullets.get("Чекпоинт") or "").strip().lower()
        deps_raw = pick("Зависимости")
        plan.cards.append(Card(
            id=h["id"], title=raw_title, level=h["level"], heading_line=h["line"],
            end_line=h["end_line"], status=status,
            goal=" ".join(pick("Цель").split()),
            description=pick("Описание"),
            inputs=pick("Входные данные"),
            checks_text=pick("Что проверяем"),
            criteria=_criteria_from(body),
            deps=_deps_from(deps_raw, h["id"]),
            dates=pick("Сроки"),
            task_text=pick("Задание", "Задание на выполнение"),
            links=pick("Гиперссылки"),
            evidence=pick("Ожидаемые доказательства", "Доказательства выполнения"),
            layer=bullets.get("Слой", ""), module=bullets.get("Модуль", ""),
            checkpoint=cp_raw in ("да", "true", "yes", "1"),
            body=body,
        ))
    return plan


def load(path) -> Plan:
    return parse_plan(path)


def set_card_status(path, card_id: str, status: str) -> bool:
    """Обновить статус карточки прямо в файле плана (буллет, эмодзи в заголовке,
    ячейка таблицы СДР). Возвращает True, если файл изменён."""
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    plan = parse_plan(path)
    card = plan.card(card_id)
    changed = False
    lines = text.splitlines()

    if card:
        i = card.heading_line
        # 1) буллет статуса внутри секции
        bullet_done = False
        for j in range(i, min(card.end_line, len(lines))):
            nm = STATUS_BULLET_RE.search(lines[j])
            if nm:
                lines[j] = STATUS_BULLET_RE.sub(f"- **Статус**: `{status}`", lines[j])
                changed = True
                bullet_done = True
                break
        # 1а) строка «Статус: Открыто» в «Основных данных» (секционный формат)
        if not bullet_done:
            for j in range(i, min(card.end_line, len(lines))):
                sl = STATUS_LINE_RE.search(lines[j])
                if sl:
                    lines[j] = STATUS_LINE_RE.sub(f"Статус: {status_word(status)}", lines[j])
                    changed = True
                    break
        # 2) эмодзи в конце заголовка
        head = lines[i]
        for emoji in EMOJI_BY_STATUS.values():
            if head.rstrip().endswith(emoji):
                lines[i] = head.rstrip()[:-len(emoji)].rstrip() + " " + EMOJI_BY_STATUS[status]
                changed = True
                break
        else:
            # без буллета и без эмодзи: помечаем в заголовке только если есть
            # тире-титул (иначе заголовок вида «# Карточка задачи 1.1» оставляем
            # как есть — статус ляжет в таблицу СДР и не сломает CARD_RE)
            if "—" in lines[i] or "–" in lines[i] or re.search(r"-+\s*\S", lines[i].split("]", 1)[-1]):
                lines[i] = head.rstrip() + " " + EMOJI_BY_STATUS[status]
                changed = True
    # 3) строка в таблице СДР
    for j, ln in enumerate(lines):
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0].strip("`") == str(card_id):
            cells[3] = status_word(status)
            lines[j] = "| " + " | ".join(cells) + " |"
            changed = True
            break
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def render_card(card: Card) -> str:
    """Текст карточки для постановки субагенту."""
    parts = [f"# КАРТОЧКА {card.id} — {card.title}"]
    if card.goal:
        parts.append("\n## Цель\n" + card.goal)
    if card.description:
        parts.append("\n## Описание (факт по коду)\n" + card.description)
    if card.inputs:
        parts.append("\n## Входные данные\n" + card.inputs)
    if card.task_text:
        parts.append("\n## Задание\n" + card.task_text)
    if card.criteria:
        parts.append("\n## Критерии приёмки (проверяются механически)\n" +
                     "\n".join(f"{i}. {c}" for i, c in enumerate(card.criteria, 1)))
    if card.checks_text:
        parts.append("\n## Что проверяем\n" + card.checks_text)
    if card.evidence:
        parts.append("\n## Ожидаемые доказательства\n" + card.evidence)
    if card.links:
        parts.append("\n## Гиперссылки\n" + card.links)
    if card.deps:
        parts.append("\n## Зависимости (уже выполнены)\n" + ", ".join(card.deps))
    return "\n".join(parts)

