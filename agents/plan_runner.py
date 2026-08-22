# -*- coding: utf-8 -*-
"""План-раннер: исполняет план ProjectsPalns карточка за карточкой.

Цикл (по умолчанию — последовательный, одна карточка за раз):
  1. Прочитать план (pipeline/plans.py) и выбрать готовую карточку:
     статус open, все Зависимости закрыты.
  2. GRILL-фаза: субагент сначала изучает код/вики, затем задаёт пользователю
     БЛОКИРУЮЩИЕ вопросы через Tasks\\Вопросы\\*.md (agents/wait_answer.py,
     таймаут -> работа по допущениям).
  3. Исполнение карточки субагентом (явная сессия на сервере или legacy opencode run).
  4. Механическая верификация: сборка + тесты + checks из pipeline.yaml -> Вердикт
     PASS/FAIL в Tasks\\Отчёты\\<CARD>_Вердикт_*.md.
  5. PASS -> отметить done прямо в файле плана + коммит в репозиторий планов;
     FAIL -> ретрай с хвостом ошибки (<= --retries), затем стоп и событие card_failed.
  6. Чекпоинты: после PASS карточки с меткой «Чекпоинт: да» или при закрытии целого
     этапа раннер встаёт на паузу до одобрения из панели (/api/checkpoints).

Запуск:
    python -X utf8 -m agents.plan_runner --project <p> [--plan <файл>] [--once]
           [--retries N] [--model <модель>] [--dry-run]

Состояние: Tasks\\Конвейер\\runner_state.json; чекпоинты: Tasks\\Конвейер\\checkpoints\\.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config                      # noqa: E402
from pipeline.plans import load as load_plan, render_card, set_card_status  # noqa: E402
from pipeline.proc import no_window_flags                    # noqa: E402
from pipeline.templates import now as _today                 # noqa: E402
from agents.agent_manager import run_subagent, slug          # noqa: E402

CHECKPOINTS_POLL_SEC = 15


# ---------------------------------------------------------------------------
# Промпт исполнителя карточки (grill-фаза встроена)
# ---------------------------------------------------------------------------

CARD_PROMPT = """Ты исполняешь КАРТОЧКУ ПЛАНА проекта {project}. Постановка прикреплена файлом: {task_file}

{card_text}

ПОРЯДОК РАБОТЫ (строго):

ЭТАП A. GRILL — пойми задачу ДО правок:
A1. Открой вики проекта (.opencode/wiki/index.md) и файлы, названные в карточке.
    Всё, что можно узнать из кода — узнай САМ. Не спрашивай того, что видно в коде.
A2. Если остались БЛОКИРУЮЩИЕ неясности (выбор подхода/интерфейса/формата данных,
    противоречие в карточке) — задай вопрос пользователю (МАКСИМУМ 2 на карточку):
    создай файл  Tasks\\Вопросы\\{task_id}_<ГГГГММДД-ЧЧММСС>.md  вида:
      ---
      карточка: {task_id}
      ---
      # ВОПРОС {task_id}: <кратко>
      ## Контекст
      <что уже проверил сам>
      ## Варианты
      - A) ... (рекомендую, потому что ...)
      - B) ...
      ## Ответы

    Затем ЗАБЛОКИРУЙСЯ в ожидании ответа одной командой (не больше 20 минут):
      python -X utf8 "{dp}\\agents\\wait_answer.py" "<путь к файлу вопроса>" --timeout 1200
    rc=0  — ответ появился, продолжай строго по нему;
    rc=1  — таймаут: продолжай по наиболее обоснованному варианту, а каждое
            допущение пометь в отчёте строкой «ASSUMPTION: <что предположил>».
A3. Не задавай вопросов «как делать» по мелочам, требованиям сборки и путям — это есть
    в конфиге проекта и критериях карточки.

ЭТАП B. ИСПОЛНЕНИЕ:
B1. СРАЗУ применяй изменения: edit/write файлов. Не пиши планы и намерения — редактируй.
B2. Выполни проверки из критериев приёмки (команды rg/grep/скрипты) — каждая должна
    дать результат, указанный в критерии.
B3. Собери проект и прогони тесты командами из pipeline.yaml проекта (build/tests);
    убедись, что состояние не хуже базового. Тесты — ТОЛЬКО с фильтром из секции tests
    (если задан): полный прогон без фильтра запрещён — идёт дольше 10 минут.

ЭТАП C. ОТЧЁТ И ФИНАЛ:
C1. Отчёт ПО-РУССКИ в {report}: секции «Что было не так», «Что сделано» (пути файлов),
    «Доказательства» (выводы команд из критериев + сборка/тесты), «ASSUMPTION» (если были),
    «Открытые вопросы», «Как проверить». Без доказательств «сделано» не считается!
C2. Коммит: git add -A && git commit -m "plan/{task_id}: <суть>".

Правила:
- Временные файлы — в Tasks\\Конвейер\\logs\\ проекта (НЕ в %TEMP%).
- Не трогай файлы вне задачи; не создавай субагентов.
- ФАЙЛ ОТЧЁТА {report} ОБЯЗАТЕЛЕН: не завершай сессию без него.
- Что-то не получается — честно blocked в отчёте, но сначала сделай максимум изменений.
"""


def _now_ts() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def _grill_skill_path() -> str:
    """Путь к скиллу pipeline-grill (revit-skills или локальный skills/)."""
    here = Path(__file__).resolve().parent.parent
    for cand in (here / "skills" / "pipeline-grill" / "SKILL.md",
                 here.parent / "revit-skills" / ".opencode" / "skills" /
                 "pipeline-grill" / "SKILL.md",
                 Path(r"D:\Projects\revit-skills\.opencode\skills") /
                 "pipeline-grill" / "SKILL.md",
                 Path(r"E:\ПлагиныРевит\agent-skills\.opencode\skills") /
                 "pipeline-grill" / "SKILL.md"):
        if cand.exists():
            return str(cand)
    return ""


class PlanRunner:
    def __init__(self, cfg, plan_path: Path | None = None, model: str = "",
                 skill: str = "", retries: int | None = None, once: bool = False,
                 dry_run: bool = False, client=None):
        self.cfg = cfg
        self.plan_path = plan_path
        self.model = model or cfg.runner_model
        self.skill = skill
        self.retries = cfg.runner_retries if retries is None else retries
        self.once = once
        self.dry_run = dry_run
        self.client = client
        self.cp_dir = cfg.conveyor_dir() / "checkpoints"
        self.state_file = cfg.conveyor_dir() / "runner_state.json"

    # --- служебные ---------------------------------------------------------

    def _state(self, **kw):
        self.cfg.conveyor_dir().mkdir(parents=True, exist_ok=True)
        data = {"updated": _now_ts(), "plan": str(self._current_plan_path() or ""), **kw}
        self.state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        return data

    def _notify(self, ev_type: str, task: str = "", payload: dict | None = None):
        if self.client is not None:
            try:
                self.client.notify(ev_type, to="feed", task=task, payload=payload or {})
            except Exception:
                pass
        print(f"[runner] {ev_type} {task} {payload or ''}".rstrip())

    def _current_plan_path(self):
        if self.plan_path:
            p = Path(self.plan_path)
            return p if p.exists() else None
        return self.cfg.find_plan_file()

    def _git_commit(self, root: Path, message: str) -> str:
        try:
            subprocess.run(["git", "-C", str(root), "add", "-A"],
                           capture_output=True, timeout=30, creationflags=no_window_flags())
            r = subprocess.run(["git", "-C", str(root), "commit", "-m", message],
                               capture_output=True, text=True, timeout=30,
                               creationflags=no_window_flags())
            return (r.stdout or "").strip().splitlines()[-1][:12] if r.returncode == 0 else ""
        except Exception:
            return ""

    # --- постановка карточки -------------------------------------------------

    def _dispatch_md(self, card) -> Path:
        """MD-постановка карточки в Активные (переиспользуем существующую)."""
        active = self.cfg.abs_tasks_dir("active")
        active.mkdir(parents=True, exist_ok=True)
        existing = sorted(active.glob(f"{card.id}_*.md"))
        if existing:
            return existing[0]
        dst = active / f"{card.id}_{slug(card.title)[:40]}.md"
        body = render_card(card)
        deps = ", ".join(card.deps) if card.deps else "нет"
        content = f"""---
id: {card.id}
приоритет: высокий
статус: open
постановщик: план-раннер
исполнитель: subagent
дата: {_now_ts()}
зависимости: {deps}
источник_запроса: план {self._current_plan_path().name if self._current_plan_path() else ''}
---

{body}

## Границы (что НЕ делать)
- Не менять то, что не относится к карточке; не трогать чужие отчёты/планы.
- Не коммитить: .idea\\, .opencode\\, bin\\obj, TestResults\\, node_modules\\.
- Не создавать субагентов; статус done ставит контролёр (план-раннер).

## Результат (куда положить артефакты)
Отчёт — Tasks\\Отчёты\\{card.id}_Отчёт_<дата>.md; коммит plan/{card.id}.
"""
        dst.write_text(content, encoding="utf-8")
        return dst

    def _error_tail(self, card) -> str:
        parts = []
        # хвост лога субагента
        log = self.cfg.root / "Tasks" / "Конвейер" / "logs" / f"{card.id}_run.log"
        if log.exists():
            try:
                lines = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
                         if l.strip()]
                parts.append("\n".join(lines[-20:]))
            except Exception:
                pass
        # механические провалы из последнего вердикта
        vf = sorted(glob.glob(str(self.cfg.abs_tasks_dir("reports") /
                                   f"{card.id}_Вердикт_*")))
        if vf:
            try:
                vlines = [l for l in Path(vf[-1]).read_text(encoding="utf-8",
                                                             errors="replace").splitlines()
                          if ("FAIL" in l or "НЕТ" in l) and "|" in l]
                if vlines:
                    parts.append("ПРОВАЛЫ ВЕРДИКТА:\n" + "\n".join(vlines[:8]))
            except Exception:
                pass
        return "\n".join(parts)

    # --- чекпоинты ------------------------------------------------------------

    def _checkpoint_pending(self, card, reason: str):
        self.cp_dir.mkdir(parents=True, exist_ok=True)
        pend = self.cp_dir / f"{card.id}.pending.json"
        pend.write_text(json.dumps({
            "card": card.id, "title": card.title, "reason": reason,
            "created": _now_ts(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self._notify("checkpoint_pending", task=card.id,
                     payload={"reason": reason, "checkpoint": card.id})

    def _wait_decision(self, card) -> str:
        """Ждёт <id>.decision.json; 'approved' | 'retry'."""
        dec = self.cp_dir / f"{card.id}.decision.json"
        while not dec.exists():
            time.sleep(CHECKPOINTS_POLL_SEC)
        try:
            data = json.loads(dec.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        action = "retry" if data.get("decision") == "retry" else "approve"
        comment = data.get("comment", "")
        dec.unlink(missing_ok=True)
        self._notify("checkpoint_decided", task=card.id,
                     payload={"action": action, "comment": comment[:200]})
        return action

    def _stage_complete(self, plan, card) -> bool:
        """Карточка закрыла весь свой этап? (для числовых СДР: 3.5 -> этап 3)."""
        core = card.id.split("-", 1)[-1]
        if "." not in core:
            return False
        stage = core.rsplit(".", 1)[0]
        kids = [c for c in plan.execution_cards()
                if c.id.split("-", 1)[-1].rsplit(".", 1)[0] == stage]
        return bool(kids) and all(c.status == "done" for c in kids)

    def _stage_id(self, card) -> str:
        core = card.id.split("-", 1)[-1]
        return card.id.rsplit(".", 1)[0] if "." in core else ""

    # --- основной цикл ----------------------------------------------------------

    # --- блокировка второго конвейера (инцидент 2026-08-22) ------------------

    LOCK_STALE_SEC = 6 * 3600

    def _lock_acquire(self) -> bool:
        """Единственный конвейер на проект: Tasks\\Конвейер\\runner.lock."""
        from agents.agent_manager import _pid_alive
        d = self.cfg.conveyor_dir()
        d.mkdir(parents=True, exist_ok=True)
        lp = d / "runner.lock"
        if lp.exists():
            pid, ts, plan = 0, 0.0, "?"
            try:
                data = json.loads(lp.read_text(encoding="utf-8"))
                pid = int(data.get("pid", 0))
                ts = float(data.get("ts", 0))
                plan = str(data.get("plan", "?"))
            except Exception:
                pass
            age = time.time() - ts
            if pid and _pid_alive(pid):
                print(f"[runner] ЗАПРЕЩЕНО: на проекте уже работает конвейер "
                      f"(pid={pid}, план {plan}, {int(age // 60)} мин). "
                      f"Два конвейера на один проект смешивают артефакты "
                      f"(инцидент с ложным PASS 2026-08-22).")
                return False
            if age < self.LOCK_STALE_SEC and not pid:
                return False  # непонятный свежий lock без пидa — не рискуем
            print("[runner] устаревший runner.lock — перехватываю")
        try:
            lp.write_text(json.dumps({
                "pid": os.getpid(), "ts": time.time(),
                "started": _now_ts(),
                "plan": str(self._current_plan_path() or ""),
            }, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            print(f"[runner] не смог создать runner.lock: {e}")
            return False
        self._lock_path = lp
        return True

    def _lock_release(self):
        try:
            getattr(self, "_lock_path", None)
            if getattr(self, "_lock_path", None):
                self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    def run(self) -> int:
        if not self._lock_acquire():
            self._state(phase="locked")
            return 5
        try:
            return self._run_locked()
        finally:
            self._lock_release()

    def _run_locked(self) -> int:
        pp = self._current_plan_path()
        if pp is None:
            print("[runner] план не найден: задайте plan.repo/subdir/file в pipeline.yaml "
                  "или --plan <файл>")
            self._state(phase="no_plan")
            return 1
        print(f"[runner] план: {pp}")
        self._state(phase="running")

        while True:
            plan = load_plan(pp)

            # 1) следующая готовая карточка
            ready = plan.ready_cards()
            if not ready:
                left = [c for c in plan.execution_cards() if c.status == "open"]
                if not left:
                    prog = plan.progress()
                    print(f"[runner] ПЛАН ВЫПОЛНЕН: {prog['done']}/{prog['total']} карточек")
                    self._state(phase="done", progress=prog)
                    self._notify("runner_done", payload=prog)
                    return 0
                stuck = [c for c in left if c.deps]
                msg = ("нет готовых карточек; открытые с зависимостями: "
                       + ", ".join(f"{c.id} (ждёт {','.join(c.deps)})" for c in stuck[:5])) \
                    if stuck else "нет готовых карточек"
                print(f"[runner] СТОП: {msg}")
                self._state(phase="blocked", note=msg)
                self._notify("runner_blocked", payload={"note": msg})
                return 2

            card = ready[0]
            attempt = 0

            while True:  # цикл попыток одной карточки
                attempt += 1
                self._state(phase="executing", card=card.id, attempt=attempt,
                            title=card.title)
                if self.dry_run:
                    print(f"[dry-run] выполнил бы карточку {card.id} — {card.title}")
                    return 0

                md = self._dispatch_md(card)
                # Уникальное имя отчёта: иначе вчерашний/приёмочный отчёт с тем же
                # именем «1.4_Отчёт_<дата>.md» маскирует отсутствие работы субагента.
                import time as _t
                report = self.cfg.abs_tasks_dir("reports") / \
                    f"{card.id}_Отчёт_{_today()}_{_t.strftime('%H%M%S')}.md"
                log = self.cfg.conveyor_dir() / "logs" / f"{card.id}_run.log"

                extra_error = f"\n\nХВОСТ ОШИБКИ ПРОШЛОЙ ПОПЫТКИ:\n{self._error_tail(card)}\n" \
                    if attempt > 1 else ""
                dp_dir = str(Path(__file__).resolve().parent.parent)
                prompt = (CARD_PROMPT
                          .replace("{card_text}", render_card(card) + extra_error)
                          .replace("{dp}", dp_dir))
                grill = _grill_skill_path()
                if grill:
                    prompt = prompt.replace(
                        "ЭТАП A. GRILL — пойми задачу ДО правок:",
                        f"ЭТАП A. GRILL — пойми задачу ДО правок "
                        f"(методика: скилл {grill}, прочитай его первым):")
                self._notify("card_started", task=card.id,
                             payload={"title": card.title[:120], "attempt": attempt})
                rc = run_subagent(self.cfg, card.id, report, log,
                                  model=self.model, skill=self.skill,
                                  client=self.client, prompt_override=prompt)

                ok_report = report.exists() and report.stat().st_size > 200
                verdict = "FAIL"
                if ok_report:
                    verdict = self._verify(card)

                if verdict == "PASS":
                    break

                # провал: ретраи исчерпаны?
                if attempt > self.retries:
                    self._state(phase="failed", card=card.id, attempt=attempt)
                    self._notify("card_failed", task=card.id,
                                 payload={"attempts": attempt, "rc": rc,
                                          "note": "ретраи исчерпаны — нужно вмешательство"})
                    print(f"[runner] КАРТОЧКА ПРОВАЛЕНА {card.id} после {attempt} попыток — стоп")
                    return 3
                print(f"[runner] {card.id}: попытка {attempt} не прошла "
                      f"(rc={rc}, вердикт={verdict}) — ретрай")

            # карточка прошла верификацию
            need_cp = card.checkpoint or \
                (self.cfg.checkpoint_stages and self._stage_complete_after(plan, card))
            if need_cp:
                reason = "marked" if card.checkpoint else \
                    f"этап завершён ({self._stage_id(card) or '—'})"
                self._checkpoint_pending(card, reason)
                self._state(phase="checkpoint", card=card.id, note=reason)
                action = self._wait_decision(card)
                if action == "retry":
                    set_card_status(pp, card.id, "open")
                    print(f"[runner] {card.id}: перезапуск по решению пользователя")
                    continue

            set_card_status(pp, card.id, "done")
            commit = self._git_commit(
                pp.parent, f"plan/{card.id}: выполнено — {card.title[:60]}")
            self._notify("card_done", task=card.id,
                         payload={"title": card.title[:120], "commit": commit})
            print(f"[runner] {card.id} ГОТОВО ({commit})")

            if self.once:
                return 0

    # --- верификация -------------------------------------------------------------

    def _verify(self, card, report_path=None) -> str:
        """Механическая проверка: cmd_verify пишет Вердикт; возвращаем PASS/FAIL/PARTIAL.
        Привязка к СВОЕМУ отчёту через PIPELINE_EXPECT_REPORT — защита от чужих
        отчётов при параллельных конвейерах (инцидент 2026-08-22)."""
        from pipeline.cli import cmd_verify
        import argparse as _ap
        old = os.environ.get("PIPELINE_EXPECT_REPORT")
        if report_path is not None:
            os.environ["PIPELINE_EXPECT_REPORT"] = str(report_path)
        try:
            rc = cmd_verify(self.cfg, _ap.Namespace(task=card.id))
        except SystemExit:
            rc = 2
        except Exception as e:
            print(f"[runner] verify {card.id} ошибка: {e}")
            return "FAIL"
        finally:
            if report_path is not None:
                if old is None:
                    os.environ.pop("PIPELINE_EXPECT_REPORT", None)
                else:
                    os.environ["PIPELINE_EXPECT_REPORT"] = old
        vf = sorted(glob.glob(str(self.cfg.abs_tasks_dir("reports") /
                                   f"{card.id}_Вердикт_*")))
        vtxt = Path(vf[-1]).read_text(encoding="utf-8", errors="replace") if vf else ""
        m = re.search(r"\*\*(PASS|FAIL|PARTIAL|NEED_DATA)\*\*", vtxt)
        return m.group(1) if m else ("PASS" if rc == 0 else "FAIL")

    def _stage_complete_after(self, plan, card) -> bool:
        """Этап будет закрыт этой карточкой (все сиблинги уже done)."""
        sid = self._stage_id(card)
        if not sid:
            return False
        kids = [c for c in plan.execution_cards()
                if c.id.split("-", 1)[-1].rsplit(".", 1)[0] == sid]
        return bool(kids) and all(c.status == "done" for c in kids if c.id != card.id)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agents.plan_runner",
                                 description="Исполнитель планов ProjectsPalns")
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", default="", help="файл плана (иначе — из plan.repo конфига)")
    ap.add_argument("--once", action="store_true", help="одна карточка и выход")
    ap.add_argument("--retries", type=int, default=None,
                    help="ретраев карточки после падения верификации (default из конфига)")
    ap.add_argument("--model", default="", help="модель opencode для субагентов")
    ap.add_argument("--skill", default="", help="скилл, обязательный к загрузке субагентом")
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--legacy", action="store_true",
                    help="без серверных сессий: opencode run напрямую")
    ap.add_argument("--dry-run", action="store_true", help="показать выбранную карточку и выйти")
    a = ap.parse_args(argv)

    cfg = load_config(a.project)
    client = None
    if not a.legacy:
        try:
            from pipeline.client import Client
            client = Client("plan-runner", project=cfg.name, base_url=a.url,
                            notif_dir=str(cfg.resolve(cfg.notif)))
            if not client.server_alive():
                client = None
        except Exception:
            client = None

    runner = PlanRunner(cfg, plan_path=Path(a.plan) if a.plan else None,
                        model=a.model, skill=a.skill, retries=a.retries,
                        once=a.once, dry_run=a.dry_run, client=client)
    try:
        return runner.run()
    except KeyboardInterrupt:
        print("\n[runner] прерван пользователем")
        return 130


if __name__ == "__main__":
    sys.exit(main())
