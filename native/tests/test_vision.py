"""Visao com imagens ARTIFICIAIS geradas em memoria; nada e lido do jogo nem gravado."""
import sys
import unittest

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None

from l2native.adapters.base import SourceExhausted
from l2native.engine import Engine, EngineStatus
from l2native.script import load_script

from .helpers import SCRIPTS, results


def synthetic(with_dialog: bool, at=(220, 90), size=(420, 700), seed=42):
    rng = np.random.default_rng(seed)
    frame = rng.integers(30, 120, (*size, 3), dtype=np.uint8)
    # "Janela" artificial: painel com borda, barra de titulo, texto e dois botoes.
    template = np.full((80, 160, 3), 25, np.uint8)
    cv2.rectangle(template, (0, 0), (159, 79), (200, 180, 120), 2)
    cv2.rectangle(template, (2, 2), (157, 16), (90, 70, 40), -1)
    cv2.putText(template, "QUEST", (8, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1)
    cv2.putText(template, "New Path", (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
    cv2.rectangle(template, (14, 54), (70, 72), (60, 140, 60), -1)
    cv2.rectangle(template, (90, 54), (146, 72), (60, 60, 140), -1)
    if with_dialog:
        x, y = at
        frame[y:y + 80, x:x + 160] = template
    return frame, template


@unittest.skipIf(cv2 is None, "numpy/opencv ausentes: pip install numpy opencv-python-headless")
class TemplateDetectorTest(unittest.TestCase):
    def setUp(self):
        from l2native.vision import DetectionStatus, TemplateDetector
        self.S = DetectionStatus
        self.TD = TemplateDetector

    def test_detects_position_and_confidence(self):
        frame, tpl = synthetic(True)
        d = self.TD(tpl).detect(frame)
        self.assertEqual(d.status, self.S.DETECTED)
        self.assertEqual((d.x, d.y), (220, 90))
        self.assertGreater(d.confidence, 0.99)
        self.assertAlmostEqual(d.rel_x, 220 / 700, places=3)

    def test_absent_dialog(self):
        frame, tpl = synthetic(False)
        d = self.TD(tpl).detect(frame)
        self.assertEqual(d.status, self.S.NOT_DETECTED)
        self.assertLess(d.confidence, 0.5)

    def test_capture_problems_are_unavailable_not_absent(self):
        _, tpl = synthetic(False)
        det = self.TD(tpl)
        self.assertEqual(det.detect(np.zeros((300, 400, 3), np.uint8)).reason, "CAPTURA_PRETA")
        self.assertEqual(det.detect(np.zeros((0, 0, 3), np.uint8)).reason, "IMAGEM_VAZIA")
        small = np.random.default_rng(1).integers(0, 255, (40, 60, 3), dtype=np.uint8)
        d = det.detect(small)
        self.assertEqual((d.status, d.reason), (self.S.UNAVAILABLE, "MODELO_MAIOR_QUE_JANELA"))

    def test_rejects_bad_templates(self):
        with self.assertRaisesRegex(ValueError, "MODELO_PEQUENO"):
            self.TD(np.full((8, 8), 100, np.uint8))
        with self.assertRaisesRegex(ValueError, "SEM_DETALHES"):
            self.TD(np.full((40, 40), 100, np.uint8))

    def test_other_ui_scale(self):
        frame, tpl = synthetic(True)
        scaled = cv2.resize(frame, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_LINEAR)
        self.assertNotEqual(self.TD(tpl).detect(scaled).status, self.S.DETECTED)
        d = self.TD(tpl, scales=(1.0, 1.25)).detect(scaled)
        self.assertEqual((d.status, d.scale), (self.S.DETECTED, 1.25))
        self.assertAlmostEqual(d.rel_x, 220 / 700, delta=0.01)  # posicao relativa preservada


@unittest.skipIf(cv2 is None, "numpy/opencv ausentes")
class VisualAdapterTest(unittest.TestCase):
    def adapter(self, frames, **kw):
        from l2native.adapters.visual import VisualAdapter
        from l2native.vision import SequenceCapture, TemplateDetector
        _, tpl = synthetic(False)
        return VisualAdapter(SequenceCapture(frames), TemplateDetector(tpl), sleep=lambda s: None, **kw)

    def test_states_from_frames(self):
        from l2native.vision import CaptureUnavailable
        on, _ = synthetic(True)
        off, _ = synthetic(False)
        a = self.adapter([on, off, CaptureUnavailable("JOGO_SEM_FOCO")])
        s1, s2, s3 = a.observe(), a.observe(), a.observe()
        self.assertTrue(s1.quest_dialog.is_true())
        self.assertEqual(s1.quest_dialog.source, "visual:template")
        self.assertGreater(s1.quest_dialog.confidence, 0.99)
        self.assertTrue(s2.quest_dialog.is_false())
        self.assertFalse(s3.quest_dialog.known)  # captura impossivel = desconhecido, nao "fechado"
        self.assertEqual(s3.quest_dialog.note, "JOGO_SEM_FOCO")
        self.assertFalse(s1.self_position.known)  # visao nao inventa posicao
        with self.assertRaises(SourceExhausted):
            a.observe()

    def test_two_samples_require_stable_position(self):
        a, _ = synthetic(True, at=(220, 90))
        b, _ = synthetic(True, at=(400, 250))
        state = self.adapter([a, b], samples=2).observe()
        self.assertTrue(state.quest_dialog.is_false())
        self.assertEqual(state.quest_dialog.note, "POSICAO_INSTAVEL")
        state = self.adapter([a, a], samples=2).observe()
        self.assertTrue(state.quest_dialog.is_true())

    def test_engine_pauses_from_visual_detection_and_never_clicks(self):
        on, _ = synthetic(True)
        off, _ = synthetic(False)
        adapter = self.adapter([off, on, on, off, off, off])
        engine = Engine(adapter, load_script(SCRIPTS / "quest_watch.py"))
        report = engine.run()
        self.assertEqual(report.status, EngineStatus.STOPPED)
        self.assertEqual(engine.journal.events("paused")[0].tick, 2)
        self.assertEqual(engine.journal.events("resumed")[0].tick, 5)
        self.assertEqual(adapter.capabilities(), frozenset())
        self.assertEqual([r["intent"]["kind"] for r in results(engine)], ["pause"])


class WindowCaptureTest(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "comportamento fora do Windows")
    def test_reports_unavailable_outside_windows(self):
        from l2native.vision import CaptureUnavailable, WindowClientCapture
        with self.assertRaisesRegex(CaptureUnavailable, "PLATAFORMA_NAO_SUPORTADA"):
            WindowClientCapture(hwnd=1234)

    @unittest.skipUnless(sys.platform == "win32", "somente Windows")
    def test_requires_hwnd_on_windows(self):  # pragma: no cover
        from l2native.vision import CaptureUnavailable, WindowClientCapture
        with self.assertRaisesRegex(CaptureUnavailable, "HWND_NECESSARIO"):
            WindowClientCapture(hwnd=0)


if __name__ == "__main__":
    unittest.main()
