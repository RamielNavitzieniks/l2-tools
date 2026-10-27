"""Descoberta de sessoes locais (bloqueio LIVE-IDENT-001, primeiro incremento).

SOMENTE LEITURA. Enumera janelas top-level visiveis do Windows (titulo, classe,
PID e executavel) e marca como candidatas as que casam com regras configuradas
pelo operador. Nao envia mensagens, nao injeta, nao le memoria de outro processo
e nao captura imagem. Uma janela localizada NAO prova personagem logado.

Nenhuma regra vem pronta: classe, titulo ou executavel do cliente nao sao
presumidos. O operador cria a regra a partir de uma janela que ele mesmo ve
(evidencia registrada na regra).

HWND e PID valem so enquanto a janela existe; o que persiste sao as regras e as
identidades locais (rotulo de personagem + perfil), nunca HWND/PID.

Quatro estados, sempre separados (registro LIVE-IDENT-001, item 5):
  janela localizada / perfil associado manualmente / identidade observada /
  conexao-telemetria real indisponivel.

Experimento 028: a identidade observada vem de uma leitura visual (identity.py)
guardada na propria sessao. Cada sessao tem um `session_id` proprio, nunca
persistido; janela fechada, HWND reaproveitado ou PID reaproveitado com outro
instante de inicio viram sessao nova, sem a leitura antiga. A regra sugerida
E13 (PR #19) so passa a valer depois de confirmada pelo operador.
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

from .identity import NOT_IDENTIFIED, IdentityMatcher, IdentityObservation, IdentityReading, IdentityStatus
from .vision.frames import RegionOfInterest

FORMAT = "l2tools-discovery/1"
# por palavra da chave ("title_contains" nao e "conta")
_SENSITIVE = re.compile(r"(^|_)(senha|password|passwd|token|secret|credentials?|login|account|conta|cookie|hwid|"
                        r"licen[cs]e|licenca)($|_)", re.I)

IDENTITY_UNOBSERVED = f"desconhecida: {NOT_IDENTIFIED}"
TELEMETRY_UNAVAILABLE = "indisponivel: LiveAdapter nao implementado (sem NPCs, HP/MP, XYZ ou acoes)"


class DiscoveryUnavailable(RuntimeError):
    """Enumeracao de janelas impossivel neste sistema (ex.: fora do Windows)."""


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    process: str          # nome do executavel; "" quando o Windows nega a consulta
    process_note: str = ""
    started_at: Optional[float] = None   # inicio do processo (epoch), distingue PID reaproveitado

    @property
    def key(self) -> tuple[int, int]:
        return (self.hwnd, self.pid)


class WindowSource(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...


class StaticWindowSource:
    """Fonte deterministica para testes e demonstracao (lista mutavel)."""

    def __init__(self, windows: Iterable[WindowInfo] = ()) -> None:
        self.windows = list(windows)

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)


class Win32WindowSource:
    """EnumWindows + consultas de leitura (user32/kernel32 via ctypes)."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DiscoveryUnavailable("descoberta de janelas so existe no Windows")
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        k = self._kernel32
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        u = self._user32
        u.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        u.IsWindowVisible.argtypes = (wintypes.HWND,)
        u.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
        u.GetWindow.restype = wintypes.HWND
        u.EnumWindows.argtypes = (self._enum_proc, wintypes.LPARAM)

    def _started_at(self, handle) -> Optional[float]:
        ft = [self._wt.FILETIME() for _ in range(4)]
        if not self._kernel32.GetProcessTimes(handle, *(self._ct.byref(f) for f in ft)):
            return None
        ticks = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime   # 100 ns desde 1601
        return round((ticks - 116444736000000000) / 1e7, 3)

    def _process(self, pid: int) -> tuple[str, str, Optional[float]]:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return "", f"processo nao consultavel (erro {self._ct.get_last_error()}; pode estar elevado)", None
        try:
            started = self._started_at(handle)
            size = self._wt.DWORD(32768)
            buf = self._ct.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, buf, self._ct.byref(size)):
                return "", f"nome do executavel indisponivel (erro {self._ct.get_last_error()})", started
            return Path(buf.value).name, "", started
        finally:
            self._kernel32.CloseHandle(handle)

    def list_windows(self) -> list[WindowInfo]:
        ct, wt, u = self._ct, self._wt, self._user32
        GW_OWNER = 4
        found: list[WindowInfo] = []

        def visit(hwnd, _lparam):
            if not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, GW_OWNER):
                return True
            length = u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ct.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, title, length + 1)
            cls = ct.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ct.byref(pid))
            process, note, started = self._process(pid.value)
            found.append(WindowInfo(int(hwnd), int(pid.value), title.value, cls.value, process, note, started))
            return True

        if not u.EnumWindows(self._enum_proc(visit), 0):
            raise DiscoveryUnavailable(f"EnumWindows falhou (erro {ct.get_last_error()})")
        return found


# --- regras e identidades (persistidas) -------------------------------------------
@dataclass(frozen=True)
class CandidateRule:
    """Janela candidata se TODOS os criterios preenchidos casarem (sem diferenciar maiusculas)."""
    name: str
    process: str = ""            # nome exato do executavel
    title_contains: str = ""
    class_name: str = ""         # nome exato da classe
    evidence: str = ""           # de onde veio (ex.: "janela vista pela Founder em 27/10")
    origin: str = "operator"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("regra precisa de nome")
        if not (self.process.strip() or self.title_contains.strip() or self.class_name.strip()):
            raise ValueError("regra precisa de ao menos um criterio (executavel, titulo ou classe)")

    def matches(self, w: WindowInfo) -> bool:
        cf = str.casefold
        if self.process.strip() and cf(w.process) != cf(self.process.strip()):
            return False
        if self.title_contains.strip() and cf(self.title_contains.strip()) not in cf(w.title):
            return False
        if self.class_name.strip() and cf(w.class_name) != cf(self.class_name.strip()):
            return False
        return True

    @classmethod
    def from_window(cls, w: WindowInfo, name: str, evidence: str, *, use_title: str = "") -> "CandidateRule":
        """Regra a partir de uma janela observada: executavel e classe; titulo so se o operador escolher."""
        return cls(name, process=w.process, class_name=w.class_name, title_contains=use_title, evidence=evidence)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "process": self.process, "title_contains": self.title_contains,
                "class_name": self.class_name, "evidence": self.evidence, "origin": self.origin}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CandidateRule":
        return cls(d["name"], d.get("process", ""), d.get("title_contains", ""), d.get("class_name", ""),
                   d.get("evidence", ""), d.get("origin", "operator"))


@dataclass(frozen=True)
class LocalIdentity:
    """Personagem local informado pelo operador (origem 'operator', nunca 'game')."""
    label: str
    profile: str = ""
    origin: str = "operator"

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("identidade local precisa de rotulo")

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "profile": self.profile, "origin": self.origin}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LocalIdentity":
        return cls(d["label"], d.get("profile", ""), d.get("origin", "operator"))


@dataclass(frozen=True)
class SuggestedRule:
    """Regra com evidencia documental, oferecida ao operador. Nao vale ate ser confirmada."""
    rule: CandidateRule
    source: str
    caveat: str


SUGGESTED_RULES: tuple[SuggestedRule, ...] = (
    SuggestedRule(
        CandidateRule("Lineage II (sugerida E13)", process="L2.bin", title_contains="Lineage II",
                      class_name="L2UnrealWWindowsViewportWindow",
                      evidence="PR #19, adendo E13: executavel, classe e titulo observados em cinco clientes no "
                               "Windows da Founder em 27/10/2026", origin="suggested:E13"),
        "PR #19, adendo E13 (observacao passiva de 27/10/2026)",
        "Vale para a versao do cliente observada naquele dia; outra versao pode usar outra classe ou executavel. "
        "Janela localizada nao prova personagem logada."),
)


def suggestions_for(windows: Iterable[WindowInfo], rules: Iterable[CandidateRule]) -> list[SuggestedRule]:
    """Sugestoes ainda nao adotadas que casam com alguma janela aberta."""
    taken = {r.name.casefold() for r in rules}
    ws = list(windows)
    return [s for s in SUGGESTED_RULES if s.rule.name.casefold() not in taken and any(s.rule.matches(w) for w in ws)]


def _check_keys(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SENSITIVE.search(str(k)):
                raise ValueError(f"campo sensivel recusado: {path}{k}")
            if str(k).casefold() in ("hwnd", "pid"):
                raise ValueError(f"{path}{k}: HWND/PID mudam a cada execucao e nao sao persistidos")
            _check_keys(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for v in obj:
            _check_keys(v, path)


@dataclass
class DiscoveryConfig:
    rules: list[CandidateRule] = field(default_factory=list)
    identities: list[LocalIdentity] = field(default_factory=list)
    regions: dict[str, RegionOfInterest] = field(default_factory=dict)   # regiao do nome, por regra

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"format": FORMAT, "rules": [r.to_dict() for r in self.rules],
                               "identities": [i.to_dict() for i in self.identities]}
        if self.regions:
            out["identity_regions"] = [{"rule": k, **v.to_dict()} for k, v in self.regions.items()]
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryConfig":
        _check_keys(data)
        if data.get("format") != FORMAT:
            raise ValueError(f"formato nao reconhecido (esperado {FORMAT})")
        regions = {r["rule"]: RegionOfInterest.from_dict(r) for r in data.get("identity_regions", [])}
        return cls([CandidateRule.from_dict(r) for r in data.get("rules", [])],
                   [LocalIdentity.from_dict(i) for i in data.get("identities", [])], regions)

    def region_for(self, rule_names: Iterable[str]) -> Optional[RegionOfInterest]:
        return next((self.regions[n] for n in rule_names if n in self.regions), None)

    def save(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "DiscoveryConfig":
        p = Path(path)
        if not p.is_file():
            return cls()
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def add_identity(self, identity: LocalIdentity) -> None:
        self.identities = [i for i in self.identities if i.label.casefold() != identity.label.casefold()] + [identity]


# --- descoberta e vinculo ---------------------------------------------------------------
class DiscoveryStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "indisponivel"
    ERROR = "erro"


def _new_session_id() -> str:
    return uuid.uuid4().hex[:12]


_DEFAULT_MATCHER = IdentityMatcher()


@dataclass
class DiscoveredSession:
    window: WindowInfo
    rules: list[str]
    first_seen: float
    last_seen: float
    binding: Optional[LocalIdentity] = None
    session_id: str = field(default_factory=_new_session_id)   # so nesta execucao; nunca persistido
    reading: Optional[IdentityReading] = None                   # ultima leitura visual desta sessao

    @property
    def candidate(self) -> bool:
        return bool(self.rules)

    def identity(self, now: Optional[float] = None, matcher: Optional[IdentityMatcher] = None) -> IdentityObservation:
        """Identidade observada comparada com a associacao manual ATUAL."""
        m = matcher or _DEFAULT_MATCHER
        return m.compare(self.session_id, self.reading, self.binding.label if self.binding else None,
                         time.time() if now is None else now)

    def states(self, now: Optional[float] = None, matcher: Optional[IdentityMatcher] = None) -> dict[str, str]:
        """Os quatro estados, cada um com sua propria evidencia."""
        return {
            "janela": f"localizada (regra: {', '.join(self.rules)})" if self.rules else "visivel, sem regra",
            "perfil": (f"{self.binding.label} / perfil {self.binding.profile or '-'} (associado pelo operador)"
                       if self.binding else "nao associado"),
            "identidade": self.identity(now, matcher).describe(),
            "telemetria": TELEMETRY_UNAVAILABLE,
        }


@dataclass
class ScanResult:
    status: DiscoveryStatus
    sessions: list[DiscoveredSession]
    message: str
    closed: list[DiscoveredSession] = field(default_factory=list)   # sumiram desde a ultima varredura


class SessionDiscovery:
    def __init__(self, source: Optional[WindowSource], config: DiscoveryConfig,
                 clock: Callable[[], float] = time.time, unavailable_reason: str = "",
                 matcher: Optional[IdentityMatcher] = None) -> None:
        self.source = source
        self.config = config
        self.clock = clock
        self.unavailable_reason = unavailable_reason
        self.matcher = matcher or IdentityMatcher()
        self.sessions: dict[tuple[int, int], DiscoveredSession] = {}
        self.suggestions: list[SuggestedRule] = []

    @classmethod
    def for_platform(cls, config: DiscoveryConfig) -> "SessionDiscovery":
        try:
            return cls(Win32WindowSource(), config)
        except (DiscoveryUnavailable, OSError, AttributeError) as exc:
            return cls(None, config, unavailable_reason=str(exc))

    def scan(self, include_all: bool = False) -> ScanResult:
        if self.source is None:
            return ScanResult(DiscoveryStatus.UNAVAILABLE, [], f"Descoberta indisponivel: {self.unavailable_reason}")
        try:
            windows = self.source.list_windows()
        except (DiscoveryUnavailable, OSError) as exc:
            return ScanResult(DiscoveryStatus.ERROR, [], f"Falha ao enumerar janelas: {exc}")
        now = self.clock()
        seen: dict[tuple[int, int], DiscoveredSession] = {}
        for w in windows:
            rules = [r.name for r in self.config.rules if r.matches(w)]
            old = self.sessions.get(w.key)
            if old is not None and old.window.process == w.process and old.window.started_at == w.started_at:
                old.window, old.rules, old.last_seen = w, rules, now
                seen[w.key] = old
            else:
                seen[w.key] = DiscoveredSession(w, rules, now, now)
        closed = [s for k, s in self.sessions.items() if k not in seen]
        self.sessions = seen
        listed = [s for s in seen.values() if include_all or s.candidate]
        listed.sort(key=lambda s: (not s.candidate, s.window.process.casefold(), s.window.title.casefold()))
        candidates = sum(s.candidate for s in seen.values())
        self.suggestions = suggestions_for(windows, self.config.rules)
        hint = (f" Ha uma regra sugerida ({self.suggestions[0].rule.name}) que casa com uma janela aberta: "
                "revise em 'Regras sugeridas...'." if self.suggestions else "")
        if not self.config.rules:
            msg = ("Nenhuma regra de janela candidata configurada. Abra o cliente, marque 'Mostrar todas as "
                   "janelas' e crie uma regra a partir da janela dele." + hint)
        elif not candidates:
            msg = "Nenhuma janela candidata aberta (cliente fechado ou regras nao casam)."
        else:
            msg = (f"{candidates} janela(s) candidata(s). Janela encontrada nao e personagem identificada; "
                   "telemetria do jogo continua indisponivel.")
        return ScanResult(DiscoveryStatus.OK, listed, msg, closed)

    def by_id(self, session_id: str) -> Optional[DiscoveredSession]:
        return next((s for s in self.sessions.values() if s.session_id == session_id), None)

    def region_for(self, session: DiscoveredSession) -> Optional[RegionOfInterest]:
        return self.config.region_for(session.rules)

    def apply_reading(self, reading: IdentityReading) -> Optional[DiscoveredSession]:
        """Guarda a leitura na sessao de origem. Se ela fechou ou virou outra
        (HWND/PID reaproveitado), a leitura e descartada (None)."""
        session = self.by_id(reading.session_id)
        if session is not None:
            session.reading = reading
        return session

    def clear_reading(self, key: tuple[int, int]) -> None:
        session = self.sessions.get(key)
        if session is not None:
            session.reading = None

    def identity(self, key: tuple[int, int]) -> IdentityObservation:
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("janela nao esta mais aberta")
        return session.identity(self.clock(), self.matcher)

    def action_gate(self, key: tuple[int, int]) -> tuple[bool, str]:
        """Acoes que dependam da identidade (nenhuma existe no Live hoje) so com
        identidade compativel e recente. Divergencia bloqueia explicitamente."""
        obs = self.identity(key)
        if obs.status is IdentityStatus.MATCH:
            return True, "identidade observada compativel"
        if obs.status is IdentityStatus.MISMATCH:
            return False, "BLOQUEADO: identidade observada diverge do perfil associado"
        return False, f"bloqueado: identidade nao confirmada ({obs.describe()})"

    def bind(self, key: tuple[int, int], identity: LocalIdentity) -> DiscoveredSession:
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("janela nao esta mais aberta")
        for other in self.sessions.values():
            if other is not session and other.binding and other.binding.label.casefold() == identity.label.casefold():
                raise ValueError(f"'{identity.label}' ja esta associado a outra janela")
        session.binding = identity
        self.config.add_identity(identity)
        return session

    def unbind(self, key: tuple[int, int]) -> None:
        session = self.sessions.get(key)
        if session is not None:
            session.binding = None
