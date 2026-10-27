"""PluginManager: extensoes locais e confiaveis, separadas dos scripts (UX-002 B).

- Script = estrategia executada pelo motor (on_tick).
- Plugin = extensao que observa/estende o aplicativo por hooks explicitos.

Contrato (api_version 1): uma pasta com `plugin.json`
    {"id": "...", "name": "...", "version": "...", "api_version": 1,
     "description": "...", "entry": "plugin.py", "permissions": ["journal:read"]}
e um modulo `entry` com `activate(api)` e, opcionalmente, `deactivate()` e
`on_record(record)` (exige a permissao journal:read).

Plugins sao codigo Python local executado SEM sandbox. Instalar, ativar e
remover exigem confirmacao explicita; nada e ativado automaticamente ao abrir,
e nada e baixado. Nenhuma permissao da API 1 da acesso ao jogo.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

API_VERSION = 1
KNOWN_PERMISSIONS = {"journal:read": "ler eventos do motor (somente leitura)"}
_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class PluginState(str, Enum):
    DISABLED = "desativado"
    ENABLED = "ativo"
    ERROR = "erro"
    INCOMPATIBLE = "incompativel"


class ConfirmationRequired(PermissionError):
    pass


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    api_version: int
    entry: str
    description: str = ""
    permissions: tuple[str, ...] = ()

    @classmethod
    def load(cls, folder: Path) -> "Manifest":
        data = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
        for key in ("id", "name", "version", "api_version", "entry"):
            if key not in data:
                raise ValueError(f"manifesto sem '{key}'")
        if not _ID.match(str(data["id"])):
            raise ValueError("id invalido (use minusculas, digitos e _)")
        entry = str(data["entry"])
        if Path(entry).is_absolute() or ".." in Path(entry).parts or not entry.endswith(".py"):
            raise ValueError("entry deve ser um .py dentro da pasta do plugin")
        return cls(str(data["id"]), str(data["name"]), str(data["version"]), int(data["api_version"]), entry,
                   str(data.get("description", "")), tuple(data.get("permissions", ())))

    def problems(self) -> list[str]:
        out = []
        if self.api_version != API_VERSION:
            out.append(f"api_version {self.api_version} nao suportada (esperado {API_VERSION})")
        unknown = [p for p in self.permissions if p not in KNOWN_PERMISSIONS]
        if unknown:
            out.append(f"permissoes desconhecidas: {unknown}")
        return out


@dataclass
class PluginInfo:
    folder: Path
    manifest: Optional[Manifest]
    state: PluginState
    error: str = ""
    module: Optional[ModuleType] = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.manifest.id if self.manifest else self.folder.name


class PluginAPI:
    """Objeto entregue a activate(api). Expoe apenas o que a API 1 permite."""

    def __init__(self, manager: "PluginManager", plugin_id: str) -> None:
        self.api_version = API_VERSION
        self.plugin_id = plugin_id
        self._manager = manager

    def log(self, message: str) -> None:
        self._manager._notify("plugin_log", {"plugin": self.plugin_id, "message": str(message)})


class PluginManager:
    def __init__(self, root: Path | str, notify: Optional[Callable[[str, dict[str, Any]], None]] = None) -> None:
        self.root = Path(root)
        self.plugins: dict[str, PluginInfo] = {}
        self._notify_cb = notify

    def _notify(self, event: str, data: dict[str, Any]) -> None:
        if self._notify_cb:
            self._notify_cb(event, data)

    def discover(self) -> list[PluginInfo]:
        """Le manifestos das subpastas de `root`. Nao importa nenhum codigo."""
        found: dict[str, PluginInfo] = {}
        if self.root.is_dir():
            for folder in sorted(p for p in self.root.iterdir() if (p / "plugin.json").is_file()):
                info = self._inspect(folder)
                previous = self.plugins.get(info.id)
                found[info.id] = previous if previous and previous.state is PluginState.ENABLED else info
        self.plugins = found
        return list(found.values())

    @staticmethod
    def _inspect(folder: Path) -> PluginInfo:
        try:
            manifest = Manifest.load(folder)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return PluginInfo(folder, None, PluginState.ERROR, f"manifesto invalido: {exc}")
        problems = manifest.problems()
        if problems:
            return PluginInfo(folder, manifest, PluginState.INCOMPATIBLE, "; ".join(problems))
        return PluginInfo(folder, manifest, PluginState.DISABLED)

    def install(self, source: Path | str, *, confirmed: bool = False) -> PluginInfo:
        source = Path(source)
        info = self._inspect(source)
        if info.manifest is None:
            raise ValueError(info.error)
        if not confirmed:
            raise ConfirmationRequired("instalacao exige confirmacao explicita")
        target = self.root / info.manifest.id
        if target.exists():
            raise FileExistsError(f"plugin '{info.manifest.id}' ja instalado")
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        self.discover()
        self._notify("plugin_installed", {"plugin": info.manifest.id})
        return self.plugins[info.manifest.id]

    def enable(self, plugin_id: str, *, confirmed: bool = False) -> PluginInfo:
        info = self.plugins[plugin_id]
        if info.state is PluginState.ENABLED:
            return info
        if info.state is PluginState.INCOMPATIBLE or info.manifest is None:
            raise ValueError(f"plugin nao pode ser ativado: {info.error}")
        if not confirmed:
            raise ConfirmationRequired("ativar executa codigo local sem sandbox: confirmacao necessaria")
        name = f"l2native_plugin_{plugin_id}"
        try:
            spec = importlib.util.spec_from_file_location(name, info.folder / info.manifest.entry)
            if spec is None or spec.loader is None:
                raise ImportError("modulo de entrada ilegivel")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            if not callable(getattr(module, "activate", None)):
                raise AttributeError("plugin precisa definir activate(api)")
            module.activate(PluginAPI(self, plugin_id))
        except Exception as exc:
            sys.modules.pop(name, None)
            info.state, info.error, info.module = PluginState.ERROR, f"{type(exc).__name__}: {exc}", None
            self._notify("plugin_error", {"plugin": plugin_id, "error": info.error})
            return info
        info.state, info.error, info.module = PluginState.ENABLED, "", module
        self._notify("plugin_enabled", {"plugin": plugin_id})
        return info

    def disable(self, plugin_id: str) -> PluginInfo:
        info = self.plugins[plugin_id]
        if info.state is PluginState.ENABLED and info.module is not None:
            deactivate = getattr(info.module, "deactivate", None)
            try:
                if callable(deactivate):
                    deactivate()
            except Exception as exc:
                info.error = f"erro em deactivate: {type(exc).__name__}: {exc}"
            sys.modules.pop(f"l2native_plugin_{plugin_id}", None)
            info.module = None
            info.state = PluginState.DISABLED
            self._notify("plugin_disabled", {"plugin": plugin_id})
        return info

    def remove(self, plugin_id: str, *, confirmed: bool = False) -> None:
        info = self.plugins[plugin_id]
        if not confirmed:
            raise ConfirmationRequired("remocao apaga a pasta do plugin: confirmacao necessaria")
        if info.folder.resolve().parent != self.root.resolve():
            raise PermissionError("so plugins instalados na pasta gerenciada podem ser removidos")
        self.disable(plugin_id)
        shutil.rmtree(info.folder)
        del self.plugins[plugin_id]
        self._notify("plugin_removed", {"plugin": plugin_id})

    def dispatch(self, record: Any) -> None:
        """Entrega um registro do journal aos plugins ativos com journal:read."""
        for info in list(self.plugins.values()):
            if info.state is not PluginState.ENABLED or info.module is None or info.manifest is None:
                continue
            hook = getattr(info.module, "on_record", None)
            if not callable(hook) or "journal:read" not in info.manifest.permissions:
                continue
            try:
                hook(record)
            except Exception as exc:
                self.disable(info.id)
                info.state, info.error = PluginState.ERROR, f"on_record: {type(exc).__name__}: {exc}"
                self._notify("plugin_error", {"plugin": info.id, "error": info.error})
