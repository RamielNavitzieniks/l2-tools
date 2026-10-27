"""Percepcao visual opcional. Importar este pacote nao exige numpy/OpenCV;
as dependencias so sao carregadas ao detectar ou capturar."""
from .capture import Capture, CaptureExhausted, CaptureUnavailable, SequenceCapture, WindowClientCapture
from .detector import Detection, DetectionStatus, TemplateDetector, VisionUnavailable
from .frames import Frame, FrameSource, RegionOfInterest, StaticFrameSource, VisualObserver, blank_reason

__all__ = [
    "Capture", "CaptureExhausted", "CaptureUnavailable", "SequenceCapture", "WindowClientCapture",
    "Detection", "DetectionStatus", "TemplateDetector", "VisionUnavailable",
    "Frame", "FrameSource", "RegionOfInterest", "StaticFrameSource", "VisualObserver", "blank_reason",
]
