"""Fontes de imagem para a percepcao visual. Frames ficam so em memoria.

- `WindowClientCapture`: area cliente de UMA janela indicada explicitamente
  pelo operador (HWND), opcionalmente recortada por fracoes da janela.
  Somente Windows; exige a janela em primeiro plano (como no experimento 018),
  para nunca capturar outra aplicacao. `grab_frame(regiao)` (exp. 028) captura
  so a regiao pedida e antes confere: janela existe, nao esta minimizada,
  pertence ao PID esperado e a regiao nao esta encoberta por outra janela.
  Nao ha alternativa silenciosa (area de trabalho, outra janela): falha vira
  `CaptureUnavailable` com o motivo.
- `SequenceCapture`: frames em memoria ou PNGs locais, para execucao offline e testes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol


class CaptureUnavailable(RuntimeError):
    """Captura impossivel agora (sem foco, janela invalida, plataforma...)."""


class CaptureExhausted(Exception):
    """Nao ha mais frames (somente fontes finitas)."""


class Capture(Protocol):
    def grab(self) -> Any: ...
    def close(self) -> None: ...


Region = tuple[float, float, float, float]  # (x, y, largura, altura) em fracoes 0..1


def _validate_region(region: Optional[Region]) -> None:
    if region is None:
        return
    x, y, w, h = region
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x and 0 < h <= 1 - y):
        raise ValueError("regiao deve estar contida na janela (fracoes 0..1)")


class SequenceCapture:
    def __init__(self, frames: Iterable[Any]) -> None:
        self._frames = iter(frames)

    def grab(self) -> Any:
        try:
            item = next(self._frames)
        except StopIteration:
            raise CaptureExhausted("sem mais frames") from None
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, (str, Path)):
            import cv2
            image = cv2.imread(str(item), cv2.IMREAD_COLOR)
            if image is None:
                raise CaptureUnavailable("IMAGEM_ILEGIVEL")
            return image
        return item

    def close(self) -> None:
        pass


class WindowClientCapture:
    def __init__(self, hwnd: int, region: Optional[Region] = None, require_foreground: bool = True,
                 expected_pid: Optional[int] = None) -> None:
        if sys.platform != "win32":
            raise CaptureUnavailable("PLATAFORMA_NAO_SUPORTADA: captura de janela exige Windows")
        if not hwnd:
            raise CaptureUnavailable("HWND_NECESSARIO: informe a janela autorizada")
        _validate_region(region)
        try:
            import mss
        except ImportError as exc:
            raise CaptureUnavailable("DEPENDENCIA_AUSENTE: pip install mss") from exc
        self._hwnd = hwnd
        self._region = region
        self._require_foreground = require_foreground
        self._expected_pid = expected_pid
        self._mss = mss.mss()

    def _client_rect(self) -> tuple[int, int, int, int]:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        hwnd = wintypes.HWND(self._hwnd)
        if not user32.IsWindow(hwnd):
            raise CaptureUnavailable("JANELA_INVALIDA: a janela foi fechada")
        if self._expected_pid is not None:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != self._expected_pid:
                raise CaptureUnavailable("JANELA_REUTILIZADA: o HWND agora pertence a outro processo")
        if user32.IsIconic(hwnd):
            raise CaptureUnavailable("JANELA_MINIMIZADA")
        if not user32.IsWindowVisible(hwnd):
            raise CaptureUnavailable("JANELA_OCULTA")
        if self._require_foreground and user32.GetForegroundWindow() != hwnd.value:
            raise CaptureUnavailable("JOGO_SEM_FOCO")
        rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise CaptureUnavailable("CLIENT_RECT")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 64 or height < 64:
            raise CaptureUnavailable("DIMENSOES_INVALIDAS")
        return origin.x, origin.y, width, height

    def grab(self) -> Any:
        import numpy as np

        left, top, width, height = self._client_rect()
        if self._region:
            fx, fy, fw, fh = self._region
            left, top = left + int(fx * width), top + int(fy * height)
            width, height = max(1, int(fw * width)), max(1, int(fh * height))
        shot = self._mss.grab({"left": left, "top": top, "width": width, "height": height})
        return np.asarray(shot)[:, :, :3].copy()

    def _region_visible(self, left: int, top: int, width: int, height: int) -> bool:
        """Centro e cantos da regiao pertencem a esta janela (nada a encobre)."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        GA_ROOT = 2
        points = [(left + width // 2, top + height // 2), (left + 1, top + 1), (left + width - 2, top + 1),
                  (left + 1, top + height - 2), (left + width - 2, top + height - 2)]
        for x, y in points:
            hit = user32.WindowFromPoint(wintypes.POINT(x, y))
            if not hit or user32.GetAncestor(hit, GA_ROOT) != self._hwnd:
                return False
        return True

    def grab_frame(self, region=None):
        """Frame da area cliente inteira ou so de `region` (RegionOfInterest)."""
        import time

        import numpy as np

        from .frames import Frame
        left, top, width, height = self._client_rect()
        x, y, w, h = (0, 0, width, height) if region is None else region.pixels((width, height))
        if not self._region_visible(left + x, top + y, w, h):
            raise CaptureUnavailable("REGIAO_ENCOBERTA: outra janela cobre a regiao (nada foi capturado)")
        shot = self._mss.grab({"left": left + x, "top": top + y, "width": w, "height": h})
        return Frame(np.asarray(shot)[:, :, :3].copy(), (width, height), time.time(), region,
                     f"janela:{self._hwnd}")

    def close(self) -> None:
        self._mss.close()
