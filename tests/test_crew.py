# -*- coding: utf-8 -*-
"""Тесты crew-супервизора (pipeline/crew.py, карточка 6.2).

Запуск: python -X utf8 tests/test_crew.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import ProjectConfig  # noqa: E402


def make_cfg(tmp: Path, **kw) -> ProjectConfig:
    for sub in ("Tasks/Конвейер/handoff", ".opencode"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    base = dict(name="proj", root=tmp, msbuild="none", sln="",
                test_runner="none", checkpoint_stages=False,
                crew_roles=["executor"], crew_model="",
                crew_permissions="write", restart_max=2, restart_cooldown_sec=0)
    base.update(kw)
    return ProjectConfig(**base)


class FakeClient:
    def __init__(self, sessions):
        self.sessions = sessions
        self.created = []
        self.created_agents = []
        self.notified = []

    def list_sessions(self, project=""):
        return [s for s in self.sessions
                if not project or s.get("project") == project]

    def get_session(self, sid):
        return next((s for s in self.sessions if s["id"] == sid), None)

    def create_agent(self, role, project="", model=""):
        sid = "A-" + str(100 + len(self.created_agents))
        s = {"id": sid, "role": role, "project": project, "model": model}
        self.created_agents.append(s)
        return s

    def create_session(self, **kw):
        nid = "S-" + str(100 + len(self.created))
        s = {"id": nid, "status": "created", "note": "", "project": kw.get("project")}
        s.update(kw)
        self.created.append(s)
        self.sessions.append(s)
        return s

    def notify(self, type_, to="", task="", payload=None):
        self.notified.append((type_, task, dict(payload or {})))


class CrewConfigTest(unittest.TestCase):
    def test_load_crew_normalizes(self):
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_"))
        cfg = make_cfg(tmp)
        c = __import__("pipeline.crew", fromlist=["load_crew"]).load_crew(cfg)
        self.assertEqual(c["roles"], ["executor"])
        self.assertEqual(c["permissions"], "write")
        self.assertEqual(c["policy"], {"max_restarts": 2, "cooldown_sec": 0})

    def test_ensure_permissions_write_profile(self):
        from pipeline import crew
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_p_"))
        cfg = make_cfg(tmp)
        p = crew.ensure_permissions(cfg)
        self.assertIsNotNone(p)
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"]["edit"], "allow")
        self.assertIsNone(crew.ensure_permissions(cfg),
                          "существующий профиль не перезаписывается")


class SetPermissionsTest(unittest.TestCase):
    """Карточка 4.2: динамическая выдача прав записи (API + CLI)."""

    def _cfg(self):
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_perms_"))
        (tmp / ".opencode").mkdir(parents=True, exist_ok=True)
        return make_cfg(tmp)

    def test_set_write_then_read_rewrites_file(self):
        from pipeline import crew
        cfg = self._cfg()
        w = crew.set_permissions(cfg, "write")
        data = json.loads(Path(w).read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"]["edit"], "allow")
        self.assertEqual(crew.read_permissions(cfg), "write")
        r = crew.set_permissions(cfg, "read")
        data = json.loads(Path(r).read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"]["edit"], "deny")
        self.assertEqual(crew.read_permissions(cfg), "read")

    def test_invalid_mode_raises_valueerror(self):
        from pipeline import crew
        cfg = self._cfg()
        with self.assertRaises(ValueError):
            crew.set_permissions(cfg, "admin")

    def test_read_permissions_none_when_missing(self):
        from pipeline import crew
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_noperm_"))
        cfg = make_cfg(tmp)
        self.assertIsNone(crew.read_permissions(cfg))


class UpProjectTest(unittest.TestCase):
    """Карточка 6.1: up_project — поднятие crew по ролям через create_agent.

    - обе роли подняты -> список из двух записей, обе ok=True;
    - отказ одной роли (create_agent вернул пусто) -> ok=False у этой роли,
      cmd_up возвращает rc≠0;
    - client=None -> [] и сообщение «сервер недоступен»."""

    def _cfg(self, roles=("executor",)):
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_up_"))
        (tmp / ".opencode").mkdir(parents=True, exist_ok=True)
        return make_cfg(tmp, crew_roles=list(roles))

    def test_both_roles_raised(self):
        from pipeline.crew import up_project
        cfg = self._cfg(("executor", "browser"))
        client = FakeClient([])
        out = up_project(cfg, client)
        self.assertEqual([r["role"] for r in out], ["executor", "browser"])
        self.assertTrue(all(r["ok"] for r in out))
        self.assertEqual([a["role"] for a in client.created_agents],
                         ["executor", "browser"])
        self.assertEqual(client.created_agents[0]["project"], "proj")

    def test_one_role_refusal_marks_ok_false(self):
        from pipeline.crew import up_project

        class RefusingClient(FakeClient):
            def create_agent(self, role, project="", model=""):
                if role == "executor":
                    return {"id": "A-1", "role": role}
                return None  # отказ второй роли

        cfg = self._cfg(("executor", "browser"))
        client = RefusingClient([])
        out = up_project(cfg, client)
        self.assertEqual([r["ok"] for r in out], [True, False])
        # cmd_up возвращает ненулевой rc, если хоть одна роль не поднялась
        from pipeline import cli
        with mock.patch.object(cli, "_crew_client", return_value=client):
            rc = cli.cmd_up(cfg, mock.Mock())
        self.assertNotEqual(rc, 0)

    def test_client_none_returns_empty_and_prints(self):
        from pipeline.crew import up_project
        cfg = self._cfg()
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = up_project(cfg, None)
        self.assertEqual(out, [])
        self.assertIn("сервер недоступен", buf.getvalue())


class PlanRestartsTest(unittest.TestCase):
    P = {"max_restarts": 2, "cooldown_sec": 300}

    def test_done_without_handoff_skipped(self):
        from pipeline.crew import plan_restarts
        s = [{"id": "S1", "status": "done", "note": "отчёт готов", "task": "A-1"}]
        self.assertEqual(plan_restarts(s, self.P, {}, now=1000.0), [])

    def test_running_skipped_failed_restarted(self):
        from pipeline.crew import plan_restarts
        s = [{"id": "S1", "status": "running", "note": "", "task": "A-1"},
             {"id": "S2", "status": "failed", "note": "", "task": "A-1"}]
        out = plan_restarts(s, self.P, {}, now=1000.0)
        self.assertEqual([d["action"] for d in out], ["restart"])
        self.assertEqual(out[0]["sid"], "S2")

    def test_handoff_note_restarts_with_path(self):
        from pipeline.crew import HANDOFF_MARK, plan_restarts
        s = [{"id": "S9", "status": "done",
              "note": HANDOFF_MARK + "Tasks/Конвейер/handoff/S9.md",
              "task": "A-2"}]
        out = plan_restarts(s, self.P, {}, now=1000.0)
        self.assertEqual(out[0]["action"], "restart")
        self.assertEqual(out[0]["handoff"], "Tasks/Конвейер/handoff/S9.md")

    def test_cooldown_then_exhausted(self):
        from pipeline.crew import plan_restarts
        s = [{"id": "S1", "status": "failed", "note": "", "task": "A-1"}]
        counters = {"S1": {"count": 1, "last_ts": 1000.0}}
        out = plan_restarts(s, self.P, counters, now=1100.0)
        self.assertEqual(out[0]["action"], "cooldown")
        out = plan_restarts(s, self.P, counters, now=2000.0)
        self.assertEqual(out[0]["action"], "restart")
        counters["S1"]["count"] = 2
        out = plan_restarts(s, self.P, counters, now=9999.0)
        self.assertEqual(out[0]["action"], "exhausted")


class RestartChainTest(unittest.TestCase):
    """Карточка 1.2: бюджет рестартов по РОДОСЛОВНОЙ цепочки, а не по sid.

    Каждый рестарт создаёт новый sid со свежим счётчиком — лимит был
    недостижим и супервизор штормил сессиями. Теперь счётчик и время
    последнего рестарта едут с сессией (instruction._restarts/_restart_ts)."""

    P = {"max_restarts": 2, "cooldown_sec": 300}

    def test_counter_travels_with_instruction(self):
        from pipeline import crew
        from pipeline.crew import plan_restarts
        # новое поколение S2 при ПУСТОМ локальном кэше counters
        s = [{"id": "S2", "status": "failed", "note": "", "task": "A-1",
              "instruction": {"_restarts": 1, "_restart_ts": 1000.0}}]
        out = plan_restarts(s, self.P, {}, now=1100.0)
        self.assertEqual(out[0]["action"], "cooldown",
                         "cooldown от последнего рестарта ЦЕПОЧКИ")
        out = plan_restarts(s, self.P, {}, now=1400.0)
        self.assertEqual(out[0]["action"], "restart")
        s[0]["instruction"][crew.RESTARTS_KEY] = 2
        out = plan_restarts(s, self.P, {}, now=99999.0)
        self.assertEqual(out[0]["action"], "exhausted",
                         "exhausted по родословной без локального кэша")

    def test_chain_S1_to_S2_to_exhausted(self):
        """S1 failed -> restart S2 -> S2 failed -> exhausted; дальше тишина,
        crew_exhausted уходит ровно один раз на цепочку."""
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_chain_"))
        cfg = make_cfg(tmp)                    # max_restarts=2, cooldown=300
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "failed", "note": "",
             "task": "A-1", "role": "executor", "model": "m1",
             "instruction": {"prompt": "P"}}])
        counters: dict = {}
        # тик 1: S1 -> S2, бюджет наследуется (_restarts=1)
        out = supervise_once(cfg, client, counters, now=1000.0)
        self.assertEqual([d["action"] for d in out], ["restart"])
        s2 = client.created[0]
        self.assertEqual(s2["instruction"]["_restarts"], 1)
        self.assertEqual(s2["instruction"]["prompt"], "P")
        # S1 больше не рассматривается (преемник уже есть)
        self.assertTrue(counters["S1"].get("handled"))
        # тик 2: S2 упало -> restart S3 c _restarts=2; S1 молчит
        s2["status"] = "failed"
        out = supervise_once(cfg, client, counters, now=5000.0)
        self.assertEqual([(d["sid"], d["action"]) for d in out],
                         [(s2["id"], "restart")])
        s3 = client.created[1]
        self.assertEqual(s3["instruction"]["_restarts"], 2)
        # тик 3: S3 упало — бюджет цепочки (2) исчерпан -> exhausted + событие
        s3["status"] = "failed"
        out = supervise_once(cfg, client, counters, now=90000.0)
        self.assertEqual([(d["sid"], d["action"]) for d in out],
                         [(s3["id"], "exhausted")])
        self.assertEqual(len(client.created), 2, "новых сессий больше нет")
        self.assertEqual(len(client.notified), 1, "событие один раз на цепочку")
        self.assertEqual(client.notified[0][0], "crew_exhausted")
        # тик 4: полная тишина — ни решений, ни повторного события
        out = supervise_once(cfg, client, counters, now=130000.0)
        self.assertEqual(out, [])
        self.assertEqual(len(client.notified), 1)

    def test_handoff_continuation_inherits_budget(self):
        """Продолжение по handoff — тот же бюджет цепочки (_restarts=1)."""
        from pipeline.crew import HANDOFF_MARK, supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_hchain_"))
        cfg = make_cfg(tmp)
        rel = "Tasks/Конвейер/handoff/S1.md"
        hf = tmp / rel
        hf.write_text("ХВОСТ-РАБОТЫ", encoding="utf-8")
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "done",
             "note": HANDOFF_MARK + rel, "task": "A-1", "role": "executor",
             "model": "", "instruction": {}}])
        out = supervise_once(cfg, client, {}, now=1000.0)
        self.assertEqual([d["action"] for d in out], ["restart"])
        created = client.created[0]
        self.assertEqual(created["instruction"]["_restarts"], 1)
        self.assertIn("ХВОСТ-РАБОТЫ", created["instruction"]["prompt"])

    def test_cooldown_from_last_chain_restart(self):
        """Cooldown считается от момента рестарта цепочки (_restart_ts),
        даже если локальный кэш counters потерян."""
        from pipeline import crew
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_cd_"))
        cfg = make_cfg(tmp, restart_cooldown_sec=300)
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "failed", "note": "",
             "task": "A-1", "role": "", "model": "", "instruction": {}}])
        counters: dict = {}
        supervise_once(cfg, client, counters, now=1000.0)
        s2 = client.created[0]
        self.assertEqual(s2["instruction"][crew.RESTART_TS_KEY], 1000.0)
        s2["status"] = "failed"
        # cooldown от последнего рестарта цепочки (1000 + 300)
        out = supervise_once(cfg, client, counters, now=1200.0)
        self.assertEqual(out[0]["action"], "cooldown")
        out = supervise_once(cfg, client, counters, now=1300.0)
        self.assertEqual(out[0]["action"], "restart")


class UpHandoffLoopTest(unittest.TestCase):
    """Карточка 1.3: сессии, поднятые `up`/«▶ Поднять проект»
    (POST /api/agents, instruction.continues=True), продолжаются супервизором
    по handoff — критерий приёмки 6.2.3 для основного пути.

    Сценарий: воркер-заглушка завершает ролевую сессию как session_worker
    при флаге continues (done + заметка «handoff:<путь>», session_worker.py);
    supervise_once планирует рестарт с промптом «исходная инструкция + хвост
    handoff»."""

    def test_up_session_done_with_handoff_restarts(self):
        from pipeline.crew import HANDOFF_MARK, supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_up_"))
        cfg = make_cfg(tmp)
        hf_abs = tmp / "Tasks" / "Конвейер" / "handoff" / "S1.md"
        hf_abs.parent.mkdir(parents=True, exist_ok=True)
        hf_abs.write_text("ХВОСТ-ПОРЦИИ", encoding="utf-8")
        client = FakeClient([
            {"id": "S1", "project": "proj",
             # воркер-заглушка: done + маркер handoff — ровно то, что пишет
             # session_worker при rc=0 и instruction.continues=True
             "status": "done", "note": HANDOFF_MARK + str(hf_abs),
             "task": "", "role": "executor", "model": "",
             "instruction": {"prompt": "РОЛЬ+СКИЛЛ+АВТОЗАДАНИЕ",
                             "skill": "pipeline-executor", "role": "executor",
                             "task": "", "continues": True}}])
        out = supervise_once(cfg, client, {}, now=1000.0)
        self.assertEqual([d["action"] for d in out], ["restart"])
        s2 = client.created[0]
        self.assertIn("РОЛЬ+СКИЛЛ+АВТОЗАДАНИЕ", s2["instruction"]["prompt"],
                      "промпт наследуется из исходной инструкции")
        self.assertIn("ХВОСТ-ПОРЦИИ", s2["instruction"]["prompt"],
                      "к промпту добавлено содержимое handoff")
        self.assertTrue(s2["instruction"].get("continues"),
                        "цепочка продолжается и дальше")

    def test_up_session_done_without_handoff_not_restarted(self):
        """Контроль: done БЕЗ марки handoff супервизор не трогает."""
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_up2_"))
        cfg = make_cfg(tmp)
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "done",
             "note": "отчёт не требуется", "task": "", "role": "executor",
             "model": "",
             "instruction": {"prompt": "P", "continues": True}}])
        out = supervise_once(cfg, client, {}, now=1000.0)
        self.assertEqual(out, [], "done без handoff-марки — продолжения нет")


class SuperviseOnceTest(unittest.TestCase):
    def test_restart_spawns_and_counts(self):
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_s_"))
        cfg = make_cfg(tmp)
        spawned = []
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "failed", "note": "",
             "task": "A-1", "role": "executor", "model": "m1",
             "instruction": {"prompt": "P"}}])
        counters: dict = {}
        out = supervise_once(cfg, client, counters, now=1000.0,
                             spawn=lambda s: spawned.append(s))
        self.assertEqual(len(spawned), 1)
        self.assertEqual(counters["S1"]["count"], 1)
        self.assertTrue(out[0]["new_sid"].startswith("S-"))
        self.assertEqual(client.created[0]["instruction"]["prompt"], "P")

    def test_exhausted_notifies_crew_exhausted(self):
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_e_"))
        cfg = make_cfg(tmp)
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "failed", "note": "",
             "task": "A-1", "role": "executor", "model": "", "instruction": {}}])
        counters = {"S1": {"count": 2, "last_ts": 0.0}}
        out = supervise_once(cfg, client, counters, now=1000.0,
                             spawn=lambda s: None)
        self.assertEqual(out[0]["action"], "exhausted")
        self.assertIn(("crew_exhausted", "A-1",
                       {"session_id": "S1", "reason": "max_restarts"}),
                      client.notified)


class WorkerHandoffTest(unittest.TestCase):
    def test_write_handoff_sections(self):
        from agents.session_worker import write_handoff
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_w_"))
        p = write_handoff(str(tmp), "S-77", task_id="U1.2",
                          report="", rc=1, error_tail="boom")
        self.assertIsNotNone(p)
        txt = Path(p).read_text(encoding="utf-8")
        for sec in ("## Репозитории и коммиты", "## Контекст", "## ГОТОВО",
                    "## ЗАДАЧА", "## Цикл работы", "## Грабли"):
            self.assertIn(sec, txt)
        self.assertIn("U1.2", txt)
        self.assertIn("boom", txt)


class TuiCycleTest(unittest.TestCase):
    """Карточка 2.1: автономный терминал — цикл порций, каждая следующая
    сессия получает свежий контекст + handoff предыдущей (/new-эквивалент)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcrew_tui_"))
        self.cfg = make_cfg(self.tmp)

    @staticmethod
    def _fake_runner(handoff_markers, prompts, rc=0):
        state = {"n": 0}

        def _run(cfg, prompt):
            state["n"] += 1
            prompts.append(prompt)
            if len(handoff_markers) >= state["n"]:
                d = cfg.conveyor_dir() / "handoff"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"S-{state['n']}.md").write_text(
                    f"ПОРЦИЯ-{handoff_markers[state['n'] - 1]}", encoding="utf-8")
            time.sleep(0.01)   # mtime handoff строго больше старта итерации
            return rc
        return _run

    def test_cycle_feeds_handforward_and_stops_on_limit(self):
        from agents import tui_cycle
        prompts = []
        # порции 1 и 2 пишут handoff, порция 3 — нет; лимит restart_max+1 = 3
        runner = self._fake_runner(["один", "два"], prompts)
        n = tui_cycle.run_cycle(self.cfg, role="executor", user_prompt="ДЕЛО-X",
                                runner=runner)
        self.assertEqual(n, 3, f"лимит {self.cfg.restart_max + 1} порций")
        self.assertEqual(len(prompts), 3)
        self.assertIn("ДЕЛО-X", prompts[0])
        self.assertIn("ПОРЦИЯ-один", prompts[1],
                      "вторая сессия получает handoff первой")
        self.assertIn("ПОРЦИЯ-два", prompts[2],
                      "третья сессия получает handoff второй")

    def test_no_handoff_retries_same_prompt_then_stops(self):
        """«Ответил без handoff» — не смерть цикла: та же порция повторяется
        restart_max раз, затем цикл останавливается."""
        from agents import tui_cycle
        prompts = []
        calls = {"n": 0}

        def runner(cfg, prompt):
            calls["n"] += 1
            prompts.append(prompt)
            return 0                       # отчёта/handoff нет ни разу

        with mock.patch("time.sleep", lambda s: None):
            n = tui_cycle.run_cycle(self.cfg, runner=runner,
                                    log=lambda *a, **k: None)
        self.assertEqual(calls["n"], self.cfg.restart_max + 1,
                         "порция без handoff дёргается повторно")
        self.assertEqual(len(set(prompts)), 1,
                         "повторы идут с одним и тем же промптом")
        self.assertEqual(n, calls["n"])

    def test_persistent_failure_retries_then_gives_up(self):
        """Перегрузка провайдера: сбойную порцию ДЁРГАЕМ повторно
        (restart_max раз), затем цикл завершается."""
        from agents import tui_cycle
        calls = []

        def runner(cfg, prompt):
            calls.append(prompt)
            return 3

        with mock.patch("time.sleep", lambda s: None):
            n = tui_cycle.run_cycle(self.cfg, runner=runner,
                                    log=lambda *a, **k: None)
        self.assertEqual(n, 0, "успешных порций нет")
        self.assertEqual(len(calls), self.cfg.restart_max + 1,
                         f"1 первая + {self.cfg.restart_max} повторов")

    def test_transient_failure_then_success_continues(self):
        """Одиночный сбой (перегрузка) — порция повторяется с тем же промптом,
        затем цикл продолжается по handoff."""
        from agents import tui_cycle
        prompts = []
        calls = {"n": 0}

        def runner(cfg, prompt):
            calls["n"] += 1
            prompts.append(prompt)
            d = cfg.conveyor_dir() / "handoff"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"S-{calls['n']}.md").write_text(f"ПОРЦИЯ-{calls['n']}",
                                                  encoding="utf-8")
            time.sleep(0.01)
            return 1 if calls["n"] == 1 else 0   # первый запуск — перегрузка

        with mock.patch("time.sleep", lambda s: None):
            n = tui_cycle.run_cycle(self.cfg, runner=runner,
                                    log=lambda *a, **k: None)
        self.assertEqual(calls["n"], 3, "сбой -> повтор той же порции -> далее")
        self.assertEqual(n, 2, "успешных порций: повторная первая + вторая")
        self.assertEqual(prompts[1], prompts[0],
                         "повтор идёт с тем же промптом (без handoff)")
        self.assertEqual(prompts[1], prompts[0],
                         "повтор идёт с тем же промптом (без handoff)")
        self.assertEqual(n, 2, "успешных порций: повторная первая + вторая")

    def test_base_prompt_contains_role_and_user_text(self):
        from agents import tui_cycle
        p = tui_cycle.build_base_prompt(self.cfg, "executor", "почини фильтр")
        self.assertIn("ИСПОЛНИТЕЛ", p)
        self.assertIn("proj", p)
        self.assertIn("почини фильтр", p)

    def test_auto_task_empty_project(self):
        from agents import tui_cycle
        txt = tui_cycle.auto_task(self.cfg)
        self.assertIn("ТЕКУЩЕЕ СОСТОЯНИЕ", txt)
        self.assertIn("файла плана нет", txt)
        self.assertIn("ЗАДАНИЕ:", txt)

    def test_auto_task_existing_plan_names_next_card(self):
        from agents import tui_cycle
        cur = self.tmp / "proj" / "_current"     # plan_repo=[tmp] + name=proj
        cur.mkdir(parents=True, exist_ok=True)
        (cur / "p.md").write_text(
            "# План\n\n| СДР | Наименование | Тип | Статус |\n|---|---|---|---|\n"
            "| `1.1` | Первая | execution | Открыто |\n\n"
            "### Карточка 1.1 — Первая\n\n- **Статус**: `open`\n- **Цель**: дело.\n"
            "- **Зависимости**: нет.\n", encoding="utf-8")
        cfg2 = make_cfg(self.tmp, plan_repo=[self.tmp], plan_subdir="proj")
        txt = tui_cycle.auto_task(cfg2)
        self.assertIn("выполнено 0/1", txt)
        self.assertIn("следующая карточка: 1.1", txt)
        prompt = tui_cycle.build_base_prompt(cfg2, "executor", "")
        self.assertIn("следующая карточка", prompt,
                      "без явного задания подставляется автозадание")

    def test_auto_task_warns_when_runner_lock_held(self):
        from agents import tui_cycle
        (self.cfg.conveyor_dir() / "runner.lock").write_text("{}", encoding="utf-8")
        txt = tui_cycle.auto_task(self.cfg)
        self.assertIn("НЕ трогать", txt)


class ManagerTest(unittest.TestCase):
    """Общий менеджер проекта: восстановление сессий + приёмка работы."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcrew_mgr_"))
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив",
                    "Tasks/Конвейер"):
            (self.tmp / sub).mkdir(parents=True, exist_ok=True)
        self.cfg = make_cfg(self.tmp)

    def test_accepts_done_report_without_verdict(self):
        from agents import project_manager as pm
        (self.tmp / "Tasks" / "Активные" / "A-01_дело.md").write_text(
            "---\nid: A-01\nстатус: done_report\n---\n# ЗАДАЧА A-01\n",
            encoding="utf-8")
        (self.tmp / "Tasks" / "Отчёты" / "A-01_Отчёт_2026-08-23.md").write_text(
            "# ОТЧЁТ A-01\n## Что сделано\nработа\n## Доказательства\nтесты\n",
            encoding="utf-8")
        seen = []

        def fake_verify(cfg_, args):
            seen.append(args.task)
            vd = cfg_.abs_tasks_dir("reports") / \
                f"{args.task}_Вердикт_менеджера_test.md"
            vd.write_text(f"Вердикт: **PASS** ({args.task})\n", encoding="utf-8")
            return 0

        with mock.patch("pipeline.cli.cmd_verify", fake_verify):
            accepted = pm.accept_pending_work(self.cfg, log=lambda *a, **k: None)
        self.assertEqual(accepted, ["A-01"])
        self.assertEqual(seen, ["A-01"])

    def test_skips_verdicted_and_open_tasks(self):
        from agents import project_manager as pm
        reports = self.tmp / "Tasks" / "Отчёты"
        # A-02 уже имеет вердикт; A-03 ещё open; A-04 без отчёта
        (self.tmp / "Tasks" / "Активные" / "A-02_готово.md").write_text(
            "---\nid: A-02\nстатус: done_report\n---\nx", encoding="utf-8")
        (self.tmp / "Tasks" / "Активные" / "A-03_открыта.md").write_text(
            "---\nid: A-03\nстатус: open\n---\nx", encoding="utf-8")
        (self.tmp / "Tasks" / "Активные" / "A-04_без_отчёта.md").write_text(
            "---\nid: A-04\nстатус: done_report\n---\nx", encoding="utf-8")
        (reports / "A-02_Вердикт_контролёра.md").write_text("PASS", encoding="utf-8")

        def fail_if_called(cfg_, args):
            raise AssertionError("verify не должен вызываться")

        with mock.patch("pipeline.cli.cmd_verify", fail_if_called):
            accepted = pm.accept_pending_work(self.cfg, log=lambda *a, **k: None)
        self.assertEqual(accepted, [])

    def test_manage_once_restores_failed_sessions(self):
        from agents import project_manager as pm
        client = FakeClient([
            {"id": "S9", "project": "proj", "status": "failed", "note": "",
             "task": "U2.2", "role": "worker", "model": "", "instruction": {}}])
        spawned = []
        restored, _ = pm.manage_once(self.cfg, client, {},
                                     spawner=lambda s: spawned.append(s),
                                     log=lambda *a, **k: None)
        self.assertEqual(restored, ["U2.2"])
        self.assertEqual(len(spawned), 1)

    def test_run_manager_covers_all_projects(self):
        from agents import project_manager as pm
        tmp2 = Path(tempfile.mkdtemp(prefix="pcrew_mgr2_"))
        for sub in ("Tasks/Активные", "Tasks/Отчёты"):
            (tmp2 / sub).mkdir(parents=True, exist_ok=True)
        cfg2 = make_cfg(tmp2)
        # по одной приёмке в каждом проекте
        for c in (self.cfg, cfg2):
            (c.abs_tasks_dir("active") / "A-01_дело.md").write_text(
                "---\nid: A-01\nстатус: done_report\n---\nx", encoding="utf-8")
            (c.abs_tasks_dir("reports") / "A-01_Отчёт_2026-08-23.md").write_text(
                "# ОТЧЁТ\n", encoding="utf-8")

        def fake_verify(cfg_, args):
            vd = cfg_.abs_tasks_dir("reports") / \
                f"{args.task}_Вердикт_менеджера_test.md"
            vd.write_text("PASS\n", encoding="utf-8")
            return 0

        with mock.patch("pipeline.cli.cmd_verify", fake_verify):
            out = pm.run_manager([self.cfg, cfg2], None, {},
                                 log=lambda *a, **k: None)
        self.assertEqual(sorted(name for name, _, _ in out),
                         ["proj", "proj"], "оба проекта обслужены")


if __name__ == "__main__":
    unittest.main(verbosity=2)
