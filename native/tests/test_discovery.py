"""Bloqueio LIVE-IDENT-001 (experimento 025): descoberta de janelas somente
leitura, regras do operador, vinculo manual e os quatro estados separados."""
import sys
import tempfile
import unittest
from pathlib import Path

from l2native import cli
from l2native.discovery import (IDENTITY_UNOBSERVED, TELEMETRY_UNAVAILABLE, CandidateRule, DiscoveryConfig,
                                DiscoveryStatus, DiscoveryUnavailable, LocalIdentity, SessionDiscovery,
                                StaticWindowSource, Win32WindowSource, WindowInfo)

CLIENT = WindowInfo(hwnd=1001, pid=40, title="Cliente A", class_name="ClasseCliente", process="cliente.exe")
CLIENT2 = WindowInfo(hwnd=1002, pid=41, title="Cliente B", class_name="ClasseCliente", process="cliente.exe")
EDITOR = WindowInfo(hwnd=2001, pid=50, title="notas.txt - Editor", class_name="Notepad", process="notepad.exe")
ELEVATED = WindowInfo(hwnd=3001, pid=60, title="Cliente C", class_name="ClasseCliente", process="",
                      process_note="processo nao consultavel (erro 5; pode estar elevado)")

RULE = CandidateRule("cliente", process="cliente.exe", class_name="ClasseCliente", evidence="teste")


class RuleTest(unittest.TestCase):
    def test_matching_requires_every_criterion(self):
        self.assertTrue(RULE.matches(CLIENT))
        self.assertFalse(RULE.matches(EDITOR))
        self.assertFalse(RULE.matches(ELEVATED))                       # executavel desconhecido nao casa
        by_class = CandidateRule("classe", class_name="classecliente")
        self.assertTrue(by_class.matches(ELEVATED))                    # sem diferenciar maiusculas
        titled = CandidateRule("t", title_contains="cliente b")
        self.assertEqual([titled.matches(w) for w in (CLIENT, CLIENT2)], [False, True])
        with self.assertRaisesRegex(ValueError, "criterio"):
            CandidateRule("vazia")
        rule = CandidateRule.from_window(CLIENT, "do cliente", "vi na minha tela")
        self.assertEqual((rule.process, rule.class_name, rule.title_contains, rule.origin),
                         ("cliente.exe", "ClasseCliente", "", "operator"))

    def test_config_roundtrip_rejects_sensitive_and_volatile_ids(self):
        cfg = DiscoveryConfig([RULE], [LocalIdentity("Seguidora", "padrao")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            cfg.save(path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(DiscoveryConfig.load(path).to_dict(), cfg.to_dict())
            self.assertEqual(DiscoveryConfig.load(Path(tmp) / "nao_existe.json").rules, [])
        self.assertNotIn("hwnd", text)
        self.assertNotIn("1001", text)
        data = cfg.to_dict()
        for bad in ({"password": "x"}, {"user_login": "x"}, {"hwnd": 1}, {"pid": 2}, {"HWID": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                DiscoveryConfig.from_dict({**data, "identities": [{"label": "S", **bad}]})
        with self.assertRaisesRegex(ValueError, "formato"):
            DiscoveryConfig.from_dict({"format": "outro"})


class SessionDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.source = StaticWindowSource([CLIENT, EDITOR, CLIENT2])
        self.d = SessionDiscovery(self.source, DiscoveryConfig([RULE]), clock=lambda: self.now)

    def test_candidates_states_and_show_all(self):
        r = self.d.scan()
        self.assertEqual(r.status, DiscoveryStatus.OK)
        self.assertEqual([s.window.hwnd for s in r.sessions], [1001, 1002])
        self.assertIn("2 janela(s) candidata(s)", r.message)
        st = r.sessions[0].states()
        self.assertEqual(st, {"janela": "localizada (regra: cliente)", "perfil": "nao associado",
                              "identidade": IDENTITY_UNOBSERVED, "telemetria": TELEMETRY_UNAVAILABLE})
        everything = self.d.scan(include_all=True).sessions
        self.assertEqual(len(everything), 3)
        self.assertEqual(everything[-1].states()["janela"], "visivel, sem regra")

    def test_bind_unbind_and_window_closing(self):
        self.d.scan()
        s = self.d.bind(CLIENT.key, LocalIdentity("Seguidora", "padrao"))
        self.assertIn("Seguidora / perfil padrao (associado pelo operador)", s.states()["perfil"])
        self.assertEqual(s.states()["identidade"], IDENTITY_UNOBSERVED)          # associar nao e observar
        self.assertEqual([i.label for i in self.d.config.identities], ["Seguidora"])
        with self.assertRaisesRegex(ValueError, "outra janela"):
            self.d.bind(CLIENT2.key, LocalIdentity("seguidora"))
        self.now = 105.0
        again = self.d.scan().sessions[0]
        self.assertEqual((again.binding.label, again.first_seen, again.last_seen), ("Seguidora", 100.0, 105.0))
        self.source.windows.remove(CLIENT)                                       # processo saiu
        r = self.d.scan()
        self.assertEqual([s.window.hwnd for s in r.sessions], [1002])
        self.assertEqual(r.closed[0].binding.label, "Seguidora")
        with self.assertRaises(KeyError):
            self.d.bind(CLIENT.key, LocalIdentity("Seguidora"))
        self.d.bind(CLIENT2.key, LocalIdentity("Seguidora"))                     # o rotulo ficou livre
        self.d.unbind(CLIENT2.key)
        self.assertIsNone(self.d.sessions[CLIENT2.key].binding)

    def test_reused_hwnd_with_other_process_is_a_new_session(self):
        self.d.scan()
        self.d.bind(CLIENT.key, LocalIdentity("Seguidora"))
        self.source.windows[0] = WindowInfo(1001, 40, "outra", "ClasseCliente", "outro.exe")
        self.assertIsNone(self.d.scan(include_all=True).sessions[-1].binding)

    def test_messages_without_rules_clients_or_platform(self):
        empty = SessionDiscovery(StaticWindowSource([EDITOR]), DiscoveryConfig()).scan()
        self.assertEqual(empty.sessions, [])
        self.assertIn("Nenhuma regra", empty.message)
        closed = SessionDiscovery(StaticWindowSource([EDITOR]), DiscoveryConfig([RULE])).scan()
        self.assertIn("Nenhuma janela candidata", closed.message)

        class Broken:
            def list_windows(self):
                raise OSError("acesso negado")
        self.assertEqual(SessionDiscovery(Broken(), DiscoveryConfig()).scan().status, DiscoveryStatus.ERROR)
        none = SessionDiscovery(None, DiscoveryConfig(), unavailable_reason="so Windows").scan()
        self.assertEqual((none.status, none.sessions), (DiscoveryStatus.UNAVAILABLE, []))

    @unittest.skipIf(sys.platform == "win32", "teste do comportamento fora do Windows")
    def test_outside_windows_is_unavailable_and_cli_exits_3(self):
        with self.assertRaises(DiscoveryUnavailable):
            Win32WindowSource()
        self.assertEqual(SessionDiscovery.for_platform(DiscoveryConfig()).scan().status, DiscoveryStatus.UNAVAILABLE)
        self.assertEqual(cli.main(["windows"]), cli.EXIT_UNAVAILABLE)


@unittest.skipUnless(sys.platform == "win32", "somente Windows")
class Win32DiscoveryTest(unittest.TestCase):
    """Janela REAL criada pelo proprio teste (tkinter); nenhuma outra janela e tocada."""

    def test_enumerates_own_window_with_process_name(self):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception as exc:  # pragma: no cover - runner sem Tk/area de trabalho
            self.skipTest(f"tkinter indisponivel: {exc}")
        title = "L2Tools-teste-descoberta-7f3a"
        root.title(title)
        root.update()
        try:
            windows = Win32WindowSource().list_windows()
            mine = [w for w in windows if w.title == title]
            self.assertEqual(len(mine), 1, [w.title for w in windows][:20])
            w = mine[0]
            self.assertTrue(w.process.lower().startswith("python"), w)
            rule = CandidateRule.from_window(w, "teste", "janela criada pelo teste", use_title=title)
            r = SessionDiscovery(Win32WindowSource(), DiscoveryConfig([rule])).scan()
            self.assertEqual([s.window.hwnd for s in r.sessions], [w.hwnd])
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "d.json"
                DiscoveryConfig([rule]).save(path)
                self.assertEqual(cli.main(["windows", "--config", str(path)]), cli.EXIT_OK)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
