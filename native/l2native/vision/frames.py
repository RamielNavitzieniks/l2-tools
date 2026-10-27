"""Frames e regioes para observadores visuais (experimento 028).

Contrato comum a qualquer leitura visual (identidade agora; HP/MP depois):

- `Frame`: imagem capturada de UMA janela autorizada, com o tamanho da area
  cliente no momento da captura e o instante. So existe em memoria.
- `RegionOfInterest`: retangulo em fracoes da area cliente (independe da
  resolucao), com o tamanho de referencia em que foi definido. Se a janela mudar
  de tamanho ou proporcao, a regiao exige revisao em vez de ler outro lugar.
- `FrameSource`: quem captura (janela real ou frames de teste). Recebe a regiao
  e devolve so ela; nunca outra janela nem a area de trabalho.
- `VisualObserver`: quem interpreta um frame (OCR de identidade, barras...).

Coordenadas daqui sao de imagem/tela, nunca coordenadas do mundo do jogo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol

from .capture import CaptureExhausted, CaptureUnavailable

MIN_REGION_PX = 8   # altura/largura minima, em pixels da janela, de uma regiao legivel


@dataclass(frozen=True)
class RegionOfInterest:
    x: float
    y: float
    w: float
    h: float
    reference_size: Optional[tuple[int, int]] = None   # area cliente (largura, altura) quando foi definida

    def __post_init__(self) -> None:
        for v in (self.x, self.y, self.w, self.h):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("regiao: valores devem ser fracoes numericas")
        if not (0 <= self.x < 1 and 0 <= self.y < 1 and 0 < self.w <= 1 - self.x + 1e-9
                and 0 < self.h <= 1 - self.y + 1e-9):
            raise ValueError("regiao deve estar contida na area cliente (fracoes 0..1)")
        if self.reference_size is not None:
            rw, rh = self.reference_size
            if rw <= 0 or rh <= 0:
                raise ValueError("tamanho de referencia invalido")

    @property
    def fractions(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)

    def layout_problem(self, client_size: tuple[int, int]) -> Optional[str]:
        """Motivo para revisar a regiao neste tamanho de janela, ou None."""
        cw, ch = client_size
        if self.w * cw < MIN_REGION_PX or self.h * ch < MIN_REGION_PX:
            return f"REGIAO_PEQUENA: menos de {MIN_REGION_PX} px nesta janela"
        if self.reference_size and tuple(self.reference_size) != (cw, ch):
            rw, rh = self.reference_size
            return (f"LAYOUT_ALTERADO: regiao definida em {rw}x{rh}, janela agora {cw}x{ch}; "
                    "revise a regiao do nome")
        return None

    def pixels(self, client_size: tuple[int, int]) -> tuple[int, int, int, int]:
        """(esquerda, topo, largura, altura) em pixels da area cliente."""
        cw, ch = client_size
        left, top = int(self.x * cw), int(self.y * ch)
        return left, top, max(1, min(cw - left, round(self.w * cw))), max(1, min(ch - top, round(self.h * ch)))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"x": self.x, "y": self.y, "w": self.w, "h": self.h}
        if self.reference_size:
            out["reference_width"], out["reference_height"] = self.reference_size
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegionOfInterest":
        ref = (int(d["reference_width"]), int(d["reference_height"])) if "reference_width" in d else None
        return cls(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]), ref)


@dataclass(frozen=True)
class Frame:
    image: Any                          # numpy (altura, largura, 3) BGR, so em memoria
    client_size: tuple[int, int]        # area cliente inteira no momento da captura
    captured_at: float
    region: Optional[RegionOfInterest] = None   # None = area cliente inteira
    source: str = ""


class FrameSource(Protocol):
    def grab_frame(self, region: Optional[RegionOfInterest] = None) -> Frame: ...
    def close(self) -> None: ...


class VisualObserver(Protocol):
    """Interpreta frames de uma regiao. Implementacoes: identidade (028); HP/MP (futuro)."""
    name: str

    def observe(self, frame: Frame) -> Any: ...


def blank_reason(image: Any) -> Optional[str]:
    """Motivo se a imagem nao tem conteudo utilizavel (vazia, preta ou uniforme)."""
    import numpy as np
    arr = None if image is None else np.asarray(image)
    if arr is None or arr.size == 0:
        return "IMAGEM_VAZIA"
    # variacao entre pixels, por canal (um fundo liso colorido nao e "conteudo")
    pixels = arr.reshape(-1, arr.shape[2]) if arr.ndim == 3 else arr.reshape(-1, 1)
    mean, std = float(arr.mean()), float(pixels.std(axis=0).max())
    if mean < 4.0 and std < 3.0:
        return "CAPTURA_PRETA: o jogo pode estar em tela cheia exclusiva (DirectX) ou a janela nao desenhou"
    if std < 2.0:
        return "CAPTURA_UNIFORME: regiao sem texto ou sem desenho"
    return None


class StaticFrameSource:
    """Frames prontos (testes e demonstracao). Cada item: Frame, imagem ou excecao.

    `client_size` vale para imagens soltas; a regiao pedida e recortada da imagem,
    como a captura real faria."""

    def __init__(self, items: Iterable[Any], client_size: Optional[tuple[int, int]] = None,
                 clock=time.time) -> None:
        self._items = iter(items)
        self._client_size = client_size
        self._clock = clock
        self.requests: list[Optional[RegionOfInterest]] = []
        self.closed = False

    def grab_frame(self, region: Optional[RegionOfInterest] = None) -> Frame:
        self.requests.append(region)
        try:
            item = next(self._items)
        except StopIteration:
            raise CaptureExhausted("sem mais frames") from None
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, Frame):
            return item
        h, w = item.shape[:2]
        size = self._client_size or (w, h)
        image = item
        if region is not None and (w, h) == size:
            left, top, rw, rh = region.pixels(size)
            image = item[top:top + rh, left:left + rw]
        return Frame(image, size, self._clock(), region, "teste")

    def close(self) -> None:
        self.closed = True


__all__ = ["CaptureUnavailable", "Frame", "FrameSource", "RegionOfInterest", "StaticFrameSource",
           "VisualObserver", "blank_reason", "MIN_REGION_PX"]
