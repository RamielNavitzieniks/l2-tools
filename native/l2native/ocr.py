"""OCR local e substituivel (experimento 028).

`OcrBackend.recognize(imagem)` devolve palavras com confianca 0..1 (ou None,
quando o mecanismo nao informa). Nada sai da maquina: nenhum servico remoto e
nenhum download de modelo. A unica implementacao concreta, `TesseractOcr`,
usa o Tesseract instalado explicitamente pelo operador (opcional):

    pip install pytesseract           # + Tesseract OCR 5 instalado no sistema

Sem ele, `OcrUnavailable` traz o motivo; a identidade fica indisponivel, nunca presumida.
"""
from __future__ import annotations

import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

_TESS_LOCK = threading.Lock()   # pytesseract guarda o caminho do programa numa variavel global

# caracteres invisiveis que o OCR as vezes devolve e que nao fazem parte de um nome
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


class OcrUnavailable(RuntimeError):
    """Mecanismo de OCR ausente, incompativel ou falhou ao executar."""


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: Optional[float] = None   # 0..1; None = o mecanismo nao informa


@dataclass(frozen=True)
class OcrResult:
    words: tuple[OcrWord, ...]
    engine: str


class OcrBackend(Protocol):
    name: str

    def recognize(self, image: Any) -> OcrResult: ...


def normalize_text(text: str) -> str:
    """NFC, sem caracteres invisiveis e sem espacos nas pontas. Nao troca letras
    parecidas nem remove acentos: isso poderia juntar dois nomes diferentes."""
    return unicodedata.normalize("NFC", text).translate(_INVISIBLE).strip()


def name_key(text: str) -> str:
    """Chave de comparacao: texto normalizado sem diferenciar maiusculas."""
    return normalize_text(text).casefold()


@dataclass(frozen=True)
class NameRules:
    """O que pode ser um nome de personagem. Padrao: 2 a 16 letras ou digitos
    (Unicode), sem espacos nem simbolos. Ajustavel por servidor."""
    min_len: int = 2
    max_len: int = 16
    allowed: Callable[[str], bool] = str.isalnum

    def valid(self, text: str) -> bool:
        return self.min_len <= len(text) <= self.max_len and all(self.allowed(c) for c in text)


def preprocess(image: Any, upscale: int = 3) -> Any:
    """Cinza, ampliado, binarizado (Otsu), texto escuro em fundo claro, com margem."""
    import cv2
    import numpy as np
    arr = np.asarray(image)
    gray = arr if arr.ndim == 2 else cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY if arr.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.mean() < 128:     # texto claro sobre fundo escuro, como na interface do jogo
        binary = 255 - binary
    return cv2.copyMakeBorder(binary, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)


class TesseractOcr:
    """Tesseract local via pytesseract, uma linha de texto por regiao (--psm 7)."""
    name = "tesseract"

    def __init__(self, lang: str = "eng", psm: int = 7, cmd: Optional[str] = None) -> None:
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrUnavailable("OCR_AUSENTE: pip install pytesseract e instale o Tesseract OCR 5") from exc
        self._tess = pytesseract
        self._cmd = cmd
        try:
            with self._program():
                self.version = str(pytesseract.get_tesseract_version())
        except Exception as exc:   # TesseractNotFoundError e erros de execucao
            raise OcrUnavailable(f"TESSERACT_AUSENTE: programa tesseract nao encontrado ({exc})") from exc
        try:
            with self._program():
                langs = pytesseract.get_languages(config="")
        except Exception as exc:
            raise OcrUnavailable(f"TESSERACT_INCOMPATIVEL: {exc}") from exc
        if lang not in langs:
            raise OcrUnavailable(f"IDIOMA_AUSENTE: idioma '{lang}' nao instalado no Tesseract ({', '.join(langs)})")
        self._lang = lang
        self._config = f"--psm {int(psm)}"

    @contextmanager
    def _program(self):
        """Usa `cmd` so durante a chamada, sem mudar o padrao de outras instancias."""
        with _TESS_LOCK:
            module = self._tess.pytesseract
            previous = module.tesseract_cmd
            if self._cmd:
                module.tesseract_cmd = self._cmd
            try:
                yield
            finally:
                module.tesseract_cmd = previous

    def recognize(self, image: Any) -> OcrResult:
        try:
            with self._program():
                data = self._tess.image_to_data(preprocess(image), lang=self._lang, config=self._config,
                                                output_type=self._tess.Output.DICT)
        except Exception as exc:
            raise OcrUnavailable(f"OCR_FALHOU: {exc}") from exc
        words = []
        for text, conf in zip(data["text"], data["conf"]):
            t = normalize_text(str(text))
            c = float(conf)
            if t and c >= 0:
                words.append(OcrWord(t, round(c / 100.0, 4)))
        return OcrResult(tuple(words), f"tesseract {self.version}")


class ScriptedOcr:
    """Respostas pre-definidas, para testes e para demonstrar estados sem o jogo.
    Cada item: OcrResult, lista de (texto, confianca) ou excecao."""
    name = "roteiro"

    def __init__(self, answers) -> None:
        self._answers = list(answers)
        self.calls = 0

    def recognize(self, image: Any) -> OcrResult:
        self.calls += 1
        item = self._answers[min(self.calls, len(self._answers)) - 1]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, OcrResult):
            return item
        return OcrResult(tuple(OcrWord(normalize_text(t), c) for t, c in item), "roteiro")
