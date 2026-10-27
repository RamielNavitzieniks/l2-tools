"""Experimento 027: CSVs historicos (formato dos scripts da Founder) -> replay.

Todos os CSVs daqui sao sinteticos (fixtures/adrenaline_csv); os reais nao entram no repositorio.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from conversores import adrenaline_csv as conv
from l2native.adapters.base import AdapterError
from l2native.adapters.replay import ReplayAdapter, summarize
from l2native.cli import main as cli_main
from l2native.engine import Engine, EngineConfig
from l2native.model import FactStatus, Position
from l2native.observation import ObservationMonitor, ReadinessStatus, Requirement
from l2native.script import load_script

from .helpers import FIXTURES, ROOT, SCRIPTS, make_script

CSV_DIR = FIXTURES / "adrenaline_csv"
PROBE = CSV_DIR / "QuestProbe_Teste_20260101_110000.csv"
RECORDER = CSV_DIR / "GravadorQuest_20260101_120000.csv"
SPOTS = CSV_DIR / "LeituraSpots_20260101_100000.csv"


def source_rows(path: Path) -> dict[int, list[str]]:
    """Linhas de dados do CSV original, pelo numero da linha no arquivo."""
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline=""), delimiter=";"))
    return {n: r for n, r in enumerate(rows[1:], 2)}


class Converted:
    """Converte para um diretorio temporario e abre o replay pelo ReplayAdapter."""

    def __init__(self, path: Path, accept=()):
        self.tmp = tempfile.TemporaryDirectory()
        self.conversion = conv.convert_file(path, accept)
        self.out = Path(self.tmp.name) / (path.stem + ".jsonl")
        conv.write(self.conversion, self.out)
        self.report = json.loads(conv.report_path(self.out).read_text(encoding="utf-8"))

    def states(self):
        adapter = ReplayAdapter.from_file(self.out)
        out = []
        try:
            for _ in self.conversion.records[1:]:
                out.append((adapter.observe(), dict(adapter.record)))
            return out, adapter.meta
        finally:
            adapter.close()

    def close(self):
        self.tmp.cleanup()


class FormatDetectionTest(unittest.TestCase):
    def test_known_headers_and_refusals(self):
        self.assertEqual(conv.detect(conv.FORMATS["QuestProbe"].columns).name, "QuestProbe")
        with self.assertRaisesRegex(conv.FormatError, "cabecalho nao reconhecido"):
            conv.convert_bytes(b"a;b;c\r\n1;2;3\r\n", "x.csv")
        with self.assertRaisesRegex(conv.FormatError, "regra opcional desconhecida"):
            conv.convert_file(SPOTS, ["zero_e_desconhecido"])
        with self.assertRaisesRegex(conv.FormatError, "vazio"):
            conv.convert_bytes(b"", "x.csv")

    def test_every_column_has_a_declared_rule_and_evidence(self):
        for fmt in conv.FORMATS.values():
            self.assertEqual({r.column for r in fmt.fields}, set(fmt.columns), fmt.name)
            for r in fmt.fields:
                self.assertTrue(r.rule and r.evidence and r.destination, r)

    def test_cp1252_input_is_accepted(self):
        data = SPOTS.read_bytes().replace(b"Aranha Ficticia", "Aranha Fictícia".encode("cp1252"))
        c = conv.convert_bytes(data, SPOTS.name)
        self.assertEqual(c.report.source["encoding"], "cp1252")
        self.assertIn("Aranha Fictícia", c.jsonl())


class QuestProbeTest(unittest.TestCase):
    def setUp(self):
        self.c = Converted(PROBE)
        self.addCleanup(self.c.close)

    def test_losses_are_reported_not_silent(self):
        r = self.c.report
        self.assertEqual(r["rows"], {"read": 7, "converted": 4, "rejected": 3})
        reasons = {x["line"]: x["reason"] for x in r["rejected"]}
        self.assertIn("estado_bruto", reasons[5])
        self.assertIn("4 colunas, esperadas 9", reasons[6])
        self.assertIn("horario invalido", reasons[7])
        self.assertEqual(r["empty_values"], {"x": 1})
        self.assertEqual(r["raw_only"], {"dlg_tamanho": 4})
        self.assertEqual({a["id"] for a in r["ambiguities"]}, {"zero_isolado", "xyz_incompleto"})

    def test_every_source_line_is_converted_or_rejected_with_raw_values_intact(self):
        states, _ = self.c.states()
        converted = {rec["raw"]["line"]: rec["raw"]["row"] for _, rec in states}
        rejected = {x["line"] for x in self.c.report["rejected"]}
        src = source_rows(PROBE)
        self.assertEqual(set(converted) | rejected, set(src))
        self.assertFalse(set(converted) & rejected)
        cols = conv.FORMATS["QuestProbe"].columns
        for line, row in converted.items():
            self.assertEqual([row[c] for c in cols], src[line])

    def test_observed_versus_unknown_in_the_engine_state(self):
        states, meta = self.c.states()
        self.assertEqual(meta["origin"]["format"], "QuestProbe")
        self.assertEqual(meta["origin"]["sha256"], self.c.conversion.report.source["sha256"])
        first, zero_x, empty_x, last = (s for s, _ in states)
        self.assertEqual(first.timestamp, "2026-01-01T11:00:00")
        self.assertEqual(first.self_position.value, Position(-14254, 123780, -3104))
        # zero isolado continua coordenada observada
        self.assertEqual(zero_x.self_position.status, FactStatus.OBSERVED)
        self.assertEqual(zero_x.self_position.value.x, 0)
        # vazio vira desconhecido com o motivo
        self.assertEqual(empty_x.self_position.status, FactStatus.UNKNOWN)
        self.assertIn("vazio na fonte (x)", empty_x.self_position.note)
        self.assertEqual(empty_x.exp.value, 150)
        # status bruto sem interpretacao de etapa; dlg_tamanho nao vira janela de missao
        self.assertEqual(last.quest_info.value, {"quest_id": 10001, "status_bruto": 2})
        for s, _ in states:
            for name in ("hp", "mp", "target", "in_combat", "quest_dialog", "characters", "npcs", "inventory"):
                self.assertEqual(getattr(s, name).status, FactStatus.UNKNOWN, name)


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.c = Converted(RECORDER)
        self.addCleanup(self.c.close)

    def test_sequence_time_and_events_preserved(self):
        states, _ = self.c.states()
        records = [rec for _, rec in states]
        self.assertEqual([r["seq"] for r in records], [1, 2, 3, 5, 6, 7])
        self.assertEqual([r["elapsed_ms"] for r in records], [0, 1000, 1203, 2000, 3000, 4000])
        self.assertNotIn("event", records[1])   # amostra
        self.assertEqual(records[2]["event"], {"origin": "evento", "kind": "mensagem_sistema", "detail": "2261"})
        self.assertEqual(records[0]["event"]["kind"], "inicio")
        # dois estados no mesmo segundo: o horario fica com resolucao de 1 s, ms separa
        self.assertEqual(states[1][0].timestamp, states[2][0].timestamp)

    def test_ambiguities_and_raw_only_fields(self):
        r = self.c.report
        self.assertEqual(r["rows"]["rejected"], 0)
        ids = {a["id"]: a["count"] for a in r["ambiguities"]}
        self.assertEqual(ids, {"seq_descontinuo": 1, "npc_alvo_id_zero": 3})
        self.assertEqual(r["raw_only"], {"sp": 6, "npc_alvo_id": 6, "dlg_tamanho": 6})
        states, _ = self.c.states()
        for s, rec in states:
            self.assertEqual(s.target.status, FactStatus.UNKNOWN)   # ID nao e nome do alvo
            self.assertIn("npc_alvo_id", rec["raw"]["row"])

    def test_replay_info_counts_events(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli_main(["replay-info", str(self.c.out)]), 0)
        text = out.getvalue()
        self.assertIn("formato: l2tools-replay/1", text)
        self.assertRegex(text, r"self_position\s+observado=6\s+inferido=0\s+desconhecido=0")
        self.assertRegex(text, r"hp\s+observado=0\s+inferido=0\s+desconhecido=6")
        self.assertIn("evento:teleporte=1", text)
        self.assertIn("marco:inicio=1", text)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.c = Converted(SPOTS)
        self.addCleanup(self.c.close)

    def test_snapshot_is_one_state_not_a_timeline(self):
        states, meta = self.c.states()
        self.assertEqual(len(states), 1)
        self.assertEqual(meta["origin"]["kind"], "fotografia")
        state, rec = states[0]
        self.assertEqual(state.timestamp, "2026-01-01T10:00:00")   # do nome do arquivo
        self.assertEqual(len(rec["raw"]["rows"]), 7)

    def test_entities_kinds_and_unknowns(self):
        (state, rec), = self.c.states()[0]
        npcs = state.npcs.value
        self.assertEqual([n.name for n in npcs], ["Lobo Cinzento", "Lobo Cinzento", "Aranha Ficticia"])
        self.assertEqual({n.npc_id for n in npcs}, {90001, 90002})
        self.assertTrue(all(n.dead.status is FactStatus.UNKNOWN for n in npcs))
        self.assertTrue(all(n.object_id is None for n in npcs))   # a fonte nao identifica instancias
        # SUMMON: so raw, nunca candidato a alvo
        self.assertNotIn("Espirito Ficticio", [n.name for n in npcs])
        self.assertEqual(self.c.report["raw_only"], {"tipo=SUMMON": 1})
        # spawn: observado, (0,0,0) observado por padrao, vazio desconhecido
        self.assertEqual(npcs[0].spawn.value, Position(-8592, 252799, -3048))
        self.assertEqual(npcs[1].spawn.value, Position(0, 0, 0))
        self.assertEqual(npcs[2].spawn.status, FactStatus.UNKNOWN)
        self.assertEqual(state.self_position.status, FactStatus.UNKNOWN)
        self.assertEqual(self.c.report["rejected"], [{"line": 9, "reason": "tipo desconhecido: 'DRAGAO'"}])

    def test_player_names_are_pseudonymized_everywhere(self):
        (state, rec), = self.c.states()[0]
        self.assertEqual([p.name for p in state.characters.value],
                         ["jogador_pt_1", "jogador_externo_1", "jogador_externo_1"])
        files = self.c.out.read_text(encoding="utf-8") + conv.report_path(self.c.out).read_text(encoding="utf-8")
        for real in ("NomeDeTesteAlfa", "NomeDeTesteBeta"):
            self.assertNotIn(real, files)
        self.assertEqual(self.c.report["privacy"], {"pseudonymized_player_names": 2})

    def test_optional_trio_rule_off_by_default_and_explicit_when_on(self):
        rule = self.c.report["rules"][0]
        self.assertEqual((rule["id"], rule["matches"], rule["accepted"], rule["applied"]),
                         ("spawn_trio_zero", 1, False, 0))
        on = Converted(SPOTS, ["spawn_trio_zero"])
        self.addCleanup(on.close)
        (state, _), = on.states()[0]
        spawn = state.npcs.value[1].spawn
        self.assertEqual(spawn.status, FactStatus.UNKNOWN)
        self.assertIn("regra spawn_trio_zero", spawn.note)
        self.assertEqual(on.report["rules"][0]["applied"], 1)
        self.assertEqual(state.npcs.value[0].spawn.status, FactStatus.OBSERVED)   # so o trio muda
        summary = summarize(on.out)
        self.assertEqual(summary.entities["npcs.spawn"], {"observed": 1, "unknown": 2})


class DeterminismAndCliTest(unittest.TestCase):
    def test_same_input_same_bytes(self):
        for path in (PROBE, RECORDER, SPOTS):
            a, b = conv.convert_file(path), conv.convert_file(path)
            self.assertEqual(a.jsonl(), b.jsonl())
            self.assertEqual(a.report.to_dict(), b.report.to_dict())

    def test_cli_writes_replay_and_report_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "spots.jsonl"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(conv.main([str(SPOTS), "-o", str(out)]), 0)
            self.assertIn("1 recusadas", buf.getvalue())
            self.assertTrue(conv.report_path(out).exists())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(conv.main([str(SPOTS), "-o", str(out)]), 2)
                self.assertEqual(conv.main([str(SPOTS), "-o", str(Path(tmp) / "x.json")]), 2)
            self.assertIn("--forcar", err.getvalue())

    def test_core_package_does_not_depend_on_the_converter(self):
        for py in (ROOT / "l2native").rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            self.assertNotIn("conversores", text, py)
            self.assertNotIn("adrenaline", text.lower(), py)


class EngineValidationTest(unittest.TestCase):
    def test_hunter_never_decides_on_unknown_data(self):
        for path in (RECORDER, SPOTS):
            c = Converted(path)
            self.addCleanup(c.close)
            adapter = ReplayAdapter.from_file(c.out)
            engine = Engine(adapter, load_script(SCRIPTS / "hunter.py"), config=EngineConfig(tick_interval=0),
                            params={"max_idle": 1000})
            engine.run()
            log = [r.data["message"] for r in engine.journal.events("script_log")
                   if r.data["message"].startswith("politica: ")]
            self.assertTrue(log and all("desconhecid" in m for m in log), (path.name, log))
            self.assertFalse(engine.journal.events("intent"), path.name)

    def test_observation_monitor_on_historical_data(self):
        c = Converted(RECORDER)
        self.addCleanup(c.close)
        monitor = ObservationMonitor(min_samples=3)
        for state, _ in c.states()[0]:
            monitor.feed(state, latency_ms=1.0)
        status = {r.requirement: r.status for r in monitor.report()}
        self.assertEqual(status[Requirement.SELF_POSITION], ReadinessStatus.MET)
        self.assertEqual(status[Requirement.VITALS], ReadinessStatus.NOT_MET)
        self.assertEqual(status[Requirement.NEARBY_ENTITIES], ReadinessStatus.NOT_MET)
        self.assertFalse(monitor.ready())

    def test_script_reads_facts_and_extras(self):
        c = Converted(RECORDER)
        self.addCleanup(c.close)
        seen = []

        def on_tick(bot):
            s = bot.perception.state
            seen.append((s.level.status, s.hp.status, s.timestamp))

        engine = Engine(ReplayAdapter.from_file(c.out), make_script(on_tick), config=EngineConfig(tick_interval=0))
        engine.run()
        self.assertEqual(len(seen), 6)
        self.assertTrue(all(lv is FactStatus.OBSERVED and hp is FactStatus.UNKNOWN for lv, hp, _ in seen))


class ReplayFormatTest(unittest.TestCase):
    def test_meta_must_be_first_and_known(self):
        meta = json.dumps({"replay_meta": {"format": "l2tools-replay/1"}})
        state = json.dumps({"self": {"level": 3}})
        a = ReplayAdapter([meta, state])
        self.assertEqual(a.observe().level.value, 3)
        self.assertEqual(a.meta["format"], "l2tools-replay/1")
        with self.assertRaisesRegex(AdapterError, "primeira linha"):
            b = ReplayAdapter([state, meta])
            b.observe()
            b.observe()
        with self.assertRaisesRegex(AdapterError, "nao suportado"):
            ReplayAdapter([json.dumps({"replay_meta": {"format": "outro/9"}}), state]).observe()

    def test_old_replays_without_meta_still_load(self):
        summary = summarize(FIXTURES / "quest_dialog.jsonl")
        self.assertEqual(summary.meta, {})
        self.assertEqual(summary.fields["quest_dialog"]["observed"], 6)


if __name__ == "__main__":
    unittest.main()
