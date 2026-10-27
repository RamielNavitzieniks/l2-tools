"""Experimento 028: identidade observada por leitura visual (OCR local).

Imagens sinteticas e OCR substituivel; nenhum nome real, nenhuma captura do jogo.
O Tesseract real so roda onde estiver instalado (o teste diz quando e pulado).
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = np = None

from l2native.discovery import (SUGGESTED_RULES, CandidateRule, DiscoveryConfig, LocalIdentity, SessionDiscovery,
                                StaticWindowSource, WindowInfo, suggestions_for)
from l2native.identity import (STALE, IdentityMatcher, IdentityReading, IdentityStatus, ReadingStatus,
                               VisualIdentityProbe)
from l2native.ocr import NameRules, OcrUnavailable, ScriptedOcr, TesseractOcr, name_key, normalize_text
from l2native.vision.capture import CaptureUnavailable
from l2native.vision.frames import RegionOfInterest, StaticFrameSource

CLIENT = (800, 600)
ROI = RegionOfInterest(0.05, 0.02, 0.3, 0.05, CLIENT)    # 240x30 px em 800x600


def render_client(text: str, *, scale=0.6, noise=0, blur=0.0, fg=225):
    """Area cliente sintetica 800x600 com o 'nome' desenhado dentro da ROI."""
    img = np.full((CLIENT[1], CLIENT[0], 3), (30, 25, 20), np.uint8)
    left, top, w, h = ROI.pixels(CLIENT)
    img[top + h:, :] = (70, 90, 60)                       # resto da "interface", fora da regiao
    cv2.putText(img, "ChatDeOutraPessoa", (40, 500), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    if text:
        cv2.putText(img, text, (left + 6, top + h - 8), cv2.FONT_HERSHEY_SIMPLEX, scale, (fg, fg, fg), 1, cv2.LINE_AA)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if noise:
        rng = np.random.default_rng(7)
        img = np.clip(img + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    return img


def frames(*images, size=CLIENT):
    return StaticFrameSource(images, client_size=size, clock=lambda: 1000.0)


def probe(ocr, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("clock", lambda: 1000.0)
    return VisualIdentityProbe(ocr, **kw)


def tesseract_or_none():
    try:
        return TesseractOcr()
    except OcrUnavailable:
        return None


@unittest.skipIf(np is None, "numpy/OpenCV ausentes: pip install numpy opencv-python-headless")
class RegionAndFrameTest(unittest.TestCase):
    def test_region_validation_pixels_and_layout(self):
        for bad in ((-0.1, 0, 0.2, 0.2), (0.9, 0, 0.2, 0.1), (0, 0, 0, 0.1), (0.5, 0.5, 0.6, 0.1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RegionOfInterest(*bad)
        self.assertEqual(ROI.pixels(CLIENT), (40, 12, 240, 30))
        self.assertIsNone(ROI.layout_problem(CLIENT))
        self.assertIn("LAYOUT_ALTERADO", ROI.layout_problem((1024, 768)))
        self.assertIn("REGIAO_PEQUENA", RegionOfInterest(0, 0, 0.005, 0.005).layout_problem(CLIENT))
        self.assertEqual(RegionOfInterest.from_dict(ROI.to_dict()), ROI)

    def test_only_the_region_is_handed_to_the_probe(self):
        src = frames(render_client("Seratonina"))
        frame = src.grab_frame(ROI)
        self.assertEqual(frame.image.shape[:2], (30, 240))     # so a regiao, nao a area cliente
        self.assertEqual(frame.client_size, CLIENT)
        self.assertEqual(src.requests, [ROI])


@unittest.skipIf(np is None, "numpy/OpenCV ausentes")
class RecognitionTest(unittest.TestCase):
    """Casos com OCR substituivel: o que o motor faz com cada tipo de resposta."""

    def read(self, *answers, images=None, **kw):
        ocr = ScriptedOcr(answers)
        n = kw.pop("samples", 3)
        imgs = images or [render_client("x")] * n
        return probe(ocr, samples=n, **kw).probe("s1", frames(*imgs), ROI)

    def test_perfectly_legible_name_needs_agreeing_samples(self):
        r = self.read([("Seratonina", 0.93)], [("Seratonina", 0.91)], [("seratonina", 0.95)])
        self.assertEqual((r.status, r.name, r.confidence, r.samples), (ReadingStatus.READ, "Seratonina", 0.91, 3))
        self.assertEqual((r.source, r.region, r.observed_at), ("visual:ocr", ROI, 1000.0))

    def test_partially_illegible_low_confidence_or_malformed(self):
        low = self.read([("Seratonlna", 0.57)])
        self.assertEqual(low.status, ReadingStatus.INCONCLUSIVE)
        self.assertIn("abaixo do limiar", low.reason)
        broken = self.read([("Sera?nina", 0.9)])
        self.assertIn("formato de nome", broken.reason)
        one_bad = self.read([("Seratonina", 0.93)], [("Seratonina", 0.62)], [("Seratonina", 0.93)])
        self.assertEqual(one_bad.status, ReadingStatus.INCONCLUSIVE)        # uma amostra fraca derruba

    def test_empty_and_ambiguous(self):
        self.assertIn("nenhum texto", self.read([]).reason)
        two = self.read([("Mira", 0.95), ("Kael", 0.94)])
        self.assertEqual(two.status, ReadingStatus.INCONCLUSIVE)
        self.assertIn("candidatos", two.reason)
        near = self.read([("Mira", 0.95), ("Kael", 0.40)])                    # o segundo nome fraco tambem conta
        self.assertIn("ambiguo", near.reason)
        noise = self.read([("Seratonina", 0.93), ("|", 0.3)], [("Seratonina", 0.93)], [("Seratonina", 0.93)])
        self.assertEqual(noise.status, ReadingStatus.READ)                    # simbolo solto nao e candidato
        diverging = self.read([("Lyanna", 0.95)], [("Lyana", 0.95)], [("Lyanna", 0.95)])
        self.assertIn("nomes diferentes", diverging.reason)

    def test_unicode_normalization_is_conservative(self):
        nfd = "Ária"                                                 # A + acento combinante
        r = self.read([(nfd, 0.9)], samples=1)
        self.assertEqual(r.name, "Ária")
        self.assertEqual(normalize_text("Ária​"), "Ária")
        self.assertEqual(name_key("ÁRIA"), name_key("ária"))
        self.assertNotEqual(name_key("Aria"), name_key("Ária"))              # acento nao e removido
        self.assertNotEqual(name_key("Ａria"), name_key("Aria"))             # largura total nao vira ASCII
        self.assertTrue(NameRules().valid("Ária2"))
        self.assertFalse(NameRules().valid("Ária Kael"))
        self.assertFalse(NameRules(max_len=5).valid("Seratonina"))

    def test_threshold_is_configurable(self):
        self.assertEqual(self.read([("Velina", 0.79)], samples=1).status, ReadingStatus.INCONCLUSIVE)
        self.assertEqual(self.read([("Velina", 0.80)], samples=1).status, ReadingStatus.READ)
        self.assertEqual(self.read([("Velina", 0.79)], samples=1, min_confidence=0.7).status, ReadingStatus.READ)
        self.assertIn("confianca", self.read([("Velina", None)], samples=1).reason)
        with self.assertRaises(ValueError):
            probe(ScriptedOcr([]), min_confidence=0)

    def test_unavailable_ocr_capture_layout_and_cancel(self):
        r = self.read(OcrUnavailable("TESSERACT_AUSENTE: x"))
        self.assertEqual((r.status, r.reason), (ReadingStatus.UNAVAILABLE, "TESSERACT_AUSENTE: x"))
        p = probe(ScriptedOcr([[("Seratonina", 0.95)]]), samples=2)
        minimized = p.probe("s1", frames(render_client("x"), CaptureUnavailable("JANELA_MINIMIZADA")), ROI)
        self.assertEqual(minimized.status, ReadingStatus.UNAVAILABLE)
        self.assertIn("JANELA_MINIMIZADA", minimized.reason)
        black = np.zeros((600, 800, 3), np.uint8)
        self.assertIn("CAPTURA_PRETA", self.read([("x", 1.0)], images=[black], samples=1).reason)
        resized = p.probe("s1", frames(render_client("x"), size=(1024, 768)), ROI)
        self.assertIn("LAYOUT_ALTERADO", resized.reason)
        self.assertIn("REGIAO_NAO_CONFIGURADA", p.probe("s1", frames(), None).reason)
        cancel = threading.Event()
        cancel.set()
        self.assertIn("CANCELADA", p.probe("s1", frames(render_client("x")), ROI, cancel).reason)

    def test_missing_tesseract_program_is_reported(self):
        with self.assertRaises(OcrUnavailable) as ctx:
            TesseractOcr(cmd=str(Path(tempfile.gettempdir()) / "nao-existe" / "tesseract"))
        self.assertRegex(str(ctx.exception), "OCR_AUSENTE|TESSERACT_AUSENTE")

    def test_reading_contract(self):
        with self.assertRaises(ValueError):
            IdentityReading("s", ReadingStatus.READ, 0.0)                   # lida sem nome
        with self.assertRaises(ValueError):
            IdentityReading("s", ReadingStatus.INCONCLUSIVE, 0.0, name="X")


TESS = tesseract_or_none() if np is not None else None


@unittest.skipIf(TESS is None, "Tesseract OCR local ausente (pip install pytesseract + programa tesseract)")
class TesseractSyntheticTest(unittest.TestCase):
    """OCR REAL sobre imagens sinteticas (fonte Hershey do OpenCV, nao a do jogo).
    Mostra o motor funcionando; nao mede precisao no Lineage II."""

    def read(self, image, samples=2):
        return probe(TESS, samples=samples).probe("s1", frames(*[image] * samples), ROI)

    def test_legible_names_are_read_and_matched(self):
        matcher = IdentityMatcher()
        for name, kw in (("Seratonina", {}), ("Arkhen42", {"noise": 25}), ("Lyrae", {"fg": 60})):
            with self.subTest(name=name):
                r = self.read(render_client(name, **kw))
                self.assertEqual((r.status, r.name), (ReadingStatus.READ, name), r.reason)
                self.assertGreaterEqual(r.confidence, 0.80)
                self.assertEqual(matcher.compare("s1", r, name, 1000.0).status, IdentityStatus.MATCH)
                self.assertEqual(matcher.compare("s1", r, "OutraPersonagem", 1000.0).status, IdentityStatus.MISMATCH)

    def test_degraded_images_never_confirm(self):
        for label, img in (("borrado", render_client("Seratonina", blur=1.2)),
                           ("pequeno", render_client("Velina", scale=0.35)),
                           ("muito ruido", render_client("Lyrae", noise=60)),
                           ("dois nomes", render_client("Mira Kael"))):
            with self.subTest(label):
                r = self.read(img)
                self.assertNotEqual(r.status, ReadingStatus.READ, (r.name, r.confidence))

    def test_region_without_text_is_not_sent_to_ocr(self):
        r = self.read(render_client(""))
        self.assertEqual(r.status, ReadingStatus.UNAVAILABLE)
        self.assertIn("CAPTURA_UNIFORME", r.reason)


def read_ok(session_id, name, at=1000.0, conf=0.9):
    return IdentityReading(session_id, ReadingStatus.READ, at, "ok", name, conf, 3, ROI)


A = WindowInfo(1001, 40, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin", started_at=500.0)
B = WindowInfo(1002, 41, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin", started_at=510.0)
RULE = CandidateRule("cliente", process="L2.bin", evidence="teste")


class AssociationTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.source = StaticWindowSource([A, B])
        self.d = SessionDiscovery(self.source, DiscoveryConfig([RULE]), clock=lambda: self.now)
        self.d.scan()
        self.a, self.b = self.d.sessions[A.key], self.d.sessions[B.key]

    def status(self, s):
        return self.d.identity(s.window.key).status

    def test_match_mismatch_unknown_and_unbound(self):
        self.assertEqual(self.status(self.a), IdentityStatus.UNKNOWN)          # ainda nao identificada
        self.d.apply_reading(read_ok(self.a.session_id, "Seratonina"))
        self.assertEqual(self.status(self.a), IdentityStatus.UNBOUND)
        self.d.bind(A.key, LocalIdentity("seratonina", "p"))
        self.assertEqual(self.status(self.a), IdentityStatus.MATCH)
        self.assertEqual(self.d.action_gate(A.key)[0], True)
        self.d.bind(A.key, LocalIdentity("Velina", "p"))                        # associacao alterada depois
        self.assertEqual(self.status(self.a), IdentityStatus.MISMATCH)
        allowed, why = self.d.action_gate(A.key)
        self.assertFalse(allowed)
        self.assertIn("BLOQUEADO", why)
        self.d.bind(A.key, LocalIdentity("Seratonin", "p"))                     # parecido nao confirma
        self.assertEqual(self.status(self.a), IdentityStatus.UNKNOWN)
        self.assertIn("parecido", self.d.identity(A.key).reason)
        self.assertFalse(self.d.action_gate(A.key)[0])

    def test_two_windows_same_title_keep_their_own_identity(self):
        self.assertNotEqual(self.a.session_id, self.b.session_id)
        self.d.bind(A.key, LocalIdentity("Seratonina"))
        self.d.bind(B.key, LocalIdentity("Velina"))
        self.d.apply_reading(read_ok(self.b.session_id, "Velina"))
        self.assertEqual(self.status(self.b), IdentityStatus.MATCH)
        self.assertEqual(self.status(self.a), IdentityStatus.UNKNOWN)           # nao contaminou a outra
        self.d.apply_reading(read_ok(self.a.session_id, "Seratonina"))
        self.assertEqual((self.status(self.a), self.status(self.b)), (IdentityStatus.MATCH, IdentityStatus.MATCH))
        wrong = self.d.sessions[A.key].identity(self.now, self.d.matcher)
        self.assertEqual(wrong.session_id, self.a.session_id)

    def test_closed_window_and_reused_ids_drop_old_readings(self):
        reading = read_ok(self.a.session_id, "Seratonina")
        self.source.windows.remove(A)
        r = self.d.scan()
        self.assertEqual(r.closed[0].session_id, self.a.session_id)
        self.assertIsNone(self.d.apply_reading(reading))                       # chegou depois: descartada
        reused = WindowInfo(1001, 40, "Lineage II", "L2UnrealWWindowsViewportWindow", "L2.bin", started_at=900.0)
        self.source.windows.append(reused)                                    # mesmo HWND e PID, outro processo
        self.d.scan()
        new = self.d.sessions[A.key]
        self.assertNotEqual(new.session_id, self.a.session_id)
        self.assertIsNone(new.reading)
        self.assertIsNone(self.d.apply_reading(reading))
        self.assertEqual(self.status(new), IdentityStatus.UNKNOWN)

    def test_same_process_keeps_session_and_reading(self):
        self.d.apply_reading(read_ok(self.a.session_id, "Seratonina"))
        self.now = 1001.0
        self.d.scan()
        self.assertIs(self.d.sessions[A.key], self.a)
        self.assertIsNotNone(self.a.reading)

    def test_stale_reading_needs_new_read(self):
        self.d.bind(A.key, LocalIdentity("Seratonina"))
        self.d.apply_reading(read_ok(self.a.session_id, "Seratonina", at=1000.0))
        self.now = 1000.0 + self.d.matcher.max_age_s + 1
        obs = self.d.identity(A.key)
        self.assertEqual((obs.status, obs.reason), (IdentityStatus.UNKNOWN, STALE))
        self.assertFalse(self.d.action_gate(A.key)[0])
        self.d.clear_reading(A.key)
        self.assertIsNone(self.a.reading)

    def test_reading_of_other_session_is_not_used(self):
        obs = IdentityMatcher().compare("outra", read_ok(self.a.session_id, "X"), "X", 1000.0)
        self.assertEqual(obs.status, IdentityStatus.UNKNOWN)

    def test_clients_opened_before_the_tool_are_found(self):
        tool_started = 2000.0
        d = SessionDiscovery(StaticWindowSource([A, B]), DiscoveryConfig([RULE]), clock=lambda: tool_started)
        r = d.scan()
        self.assertEqual(len(r.sessions), 2)
        self.assertTrue(all(s.window.started_at < tool_started for s in r.sessions))

    def test_unavailable_and_inconclusive_readings(self):
        self.d.apply_reading(IdentityReading(self.a.session_id, ReadingStatus.UNAVAILABLE, 1000.0,
                                             "JANELA_MINIMIZADA"))
        self.assertEqual(self.d.identity(A.key).status, IdentityStatus.UNAVAILABLE)
        self.assertIn("JANELA_MINIMIZADA", self.d.identity(A.key).describe())
        self.d.apply_reading(IdentityReading(self.a.session_id, ReadingStatus.INCONCLUSIVE, 1000.0, "ambiguo"))
        self.assertIn("OCR inconclusivo", self.d.identity(A.key).describe())


class SuggestedRuleAndPersistenceTest(unittest.TestCase):
    def test_e13_rule_is_only_a_suggestion(self):
        other = WindowInfo(5, 6, "Lineage II", "OutraClasse", "L2.bin")
        self.assertEqual(suggestions_for([other], []), [])
        sug = suggestions_for([A, other], [])
        self.assertEqual([s.rule.name for s in sug], ["Lineage II (sugerida E13)"])
        self.assertIn("E13", sug[0].source)
        self.assertEqual(sug[0].rule.origin, "suggested:E13")
        d = SessionDiscovery(StaticWindowSource([A]), DiscoveryConfig())
        r = d.scan()
        self.assertEqual(r.sessions, [])                                    # nao vale sem confirmacao
        self.assertIn("regra sugerida", r.message)
        d.config.rules.append(SUGGESTED_RULES[0].rule)
        self.assertEqual(len(d.scan().sessions), 1)
        self.assertEqual(suggestions_for([A], d.config.rules), [])

    def test_region_persists_without_volatile_ids(self):
        cfg = DiscoveryConfig([RULE], regions={"cliente": ROI})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            cfg.save(path)
            text = path.read_text(encoding="utf-8")
            loaded = DiscoveryConfig.load(path)
        self.assertEqual(loaded.regions, {"cliente": ROI})
        self.assertNotIn("1001", text)
        self.assertEqual(loaded.region_for(["outra", "cliente"]), ROI)


@unittest.skipUnless(sys.platform == "win32", "somente Windows")
class Win32CaptureTest(unittest.TestCase):
    """Janela REAL criada pelo teste (tkinter), com um nome ficticio desenhado.
    Falha de captura real aparece como falha ou como skip com motivo, nunca como sucesso."""

    def setUp(self):
        try:
            import importlib
            import tkinter
            importlib.import_module("mss")
            self.root = tkinter.Tk()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"tkinter/mss indisponivel: {exc}")
        self.root.title("L2Tools-teste-identidade-5b1c")
        self.root.geometry("480x200+60+60")
        self.root.configure(bg="#1e1914")
        tkinter.Label(self.root, text="Seratonina", fg="#e1e1e1", bg="#1e1914",
                      font=("Arial", 20)).place(x=10, y=10)
        self.root.update()
        self.addCleanup(self.root.destroy)
        from l2native.discovery import Win32WindowSource
        mine = [w for w in Win32WindowSource().list_windows() if w.title == "L2Tools-teste-identidade-5b1c"]
        self.assertEqual(len(mine), 1)
        self.window = mine[0]
        self.assertIsNotNone(self.window.started_at)                 # GetProcessTimes funcionou

    def capture(self):
        from l2native.vision.capture import WindowClientCapture
        return WindowClientCapture(self.window.hwnd, expected_pid=self.window.pid)

    def test_minimized_and_reused_window_are_explicit(self):
        from l2native.vision.capture import WindowClientCapture
        self.root.iconify()
        self.root.update()
        cap = self.capture()
        with self.assertRaisesRegex(CaptureUnavailable, "MINIMIZADA"):
            cap.grab_frame(RegionOfInterest(0, 0, 0.5, 0.3))
        cap.close()
        wrong = WindowClientCapture(self.window.hwnd, expected_pid=self.window.pid + 1)
        with self.assertRaisesRegex(CaptureUnavailable, "REUTILIZADA"):
            wrong.grab_frame(None)
        wrong.close()

    def test_region_capture_of_foreground_window(self):
        import ctypes
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", True)
        self.root.update()
        if ctypes.windll.user32.GetForegroundWindow() != self.window.hwnd:
            self.skipTest("o runner nao deu primeiro plano a janela de teste; captura exige foco")
        cap = self.capture()
        try:
            frame = cap.grab_frame(None)
            roi = RegionOfInterest(0.0, 0.0, 0.6, 0.35, frame.client_size)
            part = cap.grab_frame(roi)
        finally:
            cap.close()
        self.assertEqual(part.image.shape[1::-1], tuple(roi.pixels(frame.client_size)[2:]))
        self.assertIsNone(__import__("l2native.vision.frames", fromlist=["x"]).blank_reason(part.image))
        if TESS is None:
            self.skipTest("Tesseract ausente neste Windows: captura real verificada, OCR real nao")
        r = probe(TESS, samples=1).probe("w", StaticFrameSource([part]), roi)
        self.assertEqual((r.status, r.name), (ReadingStatus.READ, "Seratonina"), r.reason)


if __name__ == "__main__":
    unittest.main()
