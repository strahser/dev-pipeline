# -*- coding: utf-8 -*-
"""Тесты git-активности (pipeline/activity.py + /api/activity, карточка 5.1).

Запуск: python -X utf8 tests/test_activity.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("PIPELINE_DB",
                      os.path.join(tempfile.mkdtemp(prefix="pact_"), "t.db"))
import server.app as app_mod  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True, env=env)


def make_repo(tmp: Path, name: str, commits: list[str]) -> Path:
    repo = tmp / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for i, subj in enumerate(commits):
        f = repo / f"f{i}.txt"
        f.write_text(subj, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", subj)
    return repo


class ParseSubjectTest(unittest.TestCase):
    def test_plan_card_prefix(self):
        from pipeline.activity import parse_subject
        info = parse_subject("plan/U1.2: выполнено — выбор комнат")
        self.assertEqual(info["prefix"], "plan")
        self.assertEqual(info["card"], "U1.2")

    def test_known_and_legacy_prefixes(self):
        from pipeline.activity import parse_subject
        self.assertEqual(parse_subject("pipeline: фикс X")["prefix"], "pipeline")
        self.assertEqual(parse_subject("feat: старое")["prefix"], "feat")
        pr = parse_subject("project/heatlossrevit2: baseline")
        self.assertEqual(pr["prefix"], "project")
        self.assertEqual(pr["scope"], "heatlossrevit2")

    def test_no_prefix(self):
        from pipeline.activity import parse_subject
        info = parse_subject("совсем без префикса")
        self.assertEqual(info["prefix"], "")
        self.assertIsNone(info["card"])

    def test_card_from_refs_trailer(self):
        from pipeline.activity import parse_subject
        info = parse_subject("pipeline: вердикт по отчёту",
                             body="Подробности\nRefs: U1.2, TIT-9\n")
        self.assertEqual(info["card"], "U1.2")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pact_repo_"))
        self.repo = make_repo(self.tmp, "proj-repo", [
            "plan/U1.2: выполнено — выбор комнат",
            "pipeline: хотфикс отчёта",
            "мусорный коммит без префикса",
        ])

    def test_collect_fields_and_order(self):
        from pipeline.activity import collect
        out = collect([self.repo], days=30, limit=10)
        self.assertGreaterEqual(len(out), 3)
        by_subj = {c["subject"]: c for c in out}
        u12 = by_subj["plan/U1.2: выполнено — выбор комнат"]
        self.assertEqual(u12["prefix"], "plan")
        self.assertEqual(u12["card"], "U1.2")
        self.assertEqual(u12["repo"], "proj-repo")
        self.assertTrue(all(len(c["hash"]) == 8 for c in out))
        dates = [c["date"] for c in out]
        self.assertEqual(dates, sorted(dates, reverse=True), "новые сверху")

    def test_days_window(self):
        from pipeline.activity import collect
        out = collect([self.repo], days=7, limit=10)
        self.assertEqual({c["subject"] for c in out},
                         {"plan/U1.2: выполнено — выбор комнат",
                          "pipeline: хотфикс отчёта",
                          "мусорный коммит без префикса"})


class ApiActivityTest(unittest.TestCase):
    def test_endpoint_groups_by_convention(self):
        from fastapi.testclient import TestClient
        tmp = Path(tempfile.mkdtemp(prefix="pact_api_"))
        repo = make_repo(tmp, "PlansPalns", [
            "plan/3.2: конфиги в дело или долой — done"])

        class FakeCfg:
            name = "proj"
            root = tmp / "proj-root"          # не существует — пропускается
            plan_repo = [repo]

        with mock.patch.object(app_mod, "load_config", lambda n: FakeCfg()):
            client = TestClient(app_mod.app)
            r = client.get("/api/activity", params={"project": "proj",
                                                    "days": 30, "limit": 20})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["project"], "proj")
        self.assertEqual(len(data["commits"]), 1)
        c = data["commits"][0]
        self.assertEqual(c["prefix"], "plan")
        self.assertEqual(c["card"], "3.2")
        self.assertEqual(c["hash"], subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True).stdout.strip())


class GroupStagesTest(unittest.TestCase):
    """Карточка 5.1: агрегат /api/activity по этапам (group=stages)."""

    def test_group_stages(self):
        from pipeline.activity import group_stages
        commits = [
            {"subject": "plan/A-1.1: a", "card": "A-1.1", "prefix": "plan",
             "date": "2026-08-24T10:00:00+00:00"},
            {"subject": "plan/A-1.2: b", "card": "A-1.2", "prefix": "plan",
             "date": "2026-08-24T11:00:00+00:00"},
            {"subject": "fix: баг", "card": None, "prefix": "fix",
             "date": "2026-08-24T12:00:00+00:00"},
            {"subject": "совсем без префикса", "card": None, "prefix": "",
             "date": "2026-08-24T13:00:00+00:00"},
        ]
        out = group_stages(commits)
        by = {g["stage"]: g for g in out}
        self.assertEqual(set(by.keys()), {"A-1", "—"})
        a = by["A-1"]
        self.assertEqual(a["commits"], 2)
        self.assertEqual(a["last_subject"], "plan/A-1.2: b")
        self.assertEqual(a["feat_n"], 0)
        self.assertEqual(a["fix_n"], 0)
        dash = by["—"]
        self.assertEqual(dash["commits"], 2)
        self.assertEqual(dash["fix_n"], 1)
        self.assertEqual(dash["last_subject"], "совсем без префикса")
        # свежий этап сверху
        self.assertEqual(out[0]["stage"], "—")

    def test_api_group_stages_endpoint(self):
        from fastapi.testclient import TestClient
        tmp = Path(tempfile.mkdtemp(prefix="pact_gs_"))
        repo = make_repo(tmp, "PlansPalns", [
            "plan/A-1.1: выбор комнат — done",
            "plan/A-1.2: конфиги — done",
            "fix: правка",
            "без префикса"])

        class FakeCfg:
            name = "proj"
            root = tmp / "proj-root"
            plan_repo = [repo]

        with mock.patch.object(app_mod, "load_config", lambda n: FakeCfg()):
            client = TestClient(app_mod.app)
            r = client.get("/api/activity", params={
                "project": "proj", "days": 30, "limit": 50, "group": "stages"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["group"], "stages")
        by = {g["stage"]: g for g in data["stages"]}
        self.assertEqual(set(by.keys()), {"A-1", "—"})
        self.assertEqual(by["A-1"]["commits"], 2)
        self.assertEqual(by["—"]["commits"], 2)
        self.assertEqual(by["—"]["fix_n"], 1)
        self.assertEqual(by["A-1"]["fix_n"], 0)
        for g in data["stages"]:
            for k in ("stage", "commits", "last_subject", "last_date",
                      "feat_n", "fix_n"):
                self.assertIn(k, g)


if __name__ == "__main__":
    unittest.main(verbosity=2)
