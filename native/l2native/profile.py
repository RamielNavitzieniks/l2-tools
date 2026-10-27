"""Perfis por personagem/sessao: configuracao tipada das secoes da GUI.

As secoes seguem o inventario UX-001 (Diversos e Eventos ficam de fora).
Cada secao declara as acoes do contrato de que dependeria; a GUI cruza isso
com as capabilities do adaptador para mostrar Simulacao/Disponivel/Nao
suportada/Desconhecida. Configurar uma secao NAO a torna funcional.

Perfis nunca guardam credenciais: chaves com nomes sensiveis sao recusadas.

Versoes do formato (campo "format"):
- l2tools-profile/1: IDs de habilidade/item ficavam em cada regra (game_id).
- l2tools-profile/2: catalogo de habilidades (`skills`) e a fonte unica de IDs;
  regras referenciam entradas pelo nome. Nova secao "rota" (Path).
Perfis antigos sao migrados ao carregar (`MIGRATIONS`); versoes futuras sao recusadas.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .actions import ActionKind
from .model import Position
from .rules import FilterRule, Rule

FORMAT_PREFIX = "l2tools-profile/"
SCHEMA_VERSION = 2
FORMAT = f"{FORMAT_PREFIX}{SCHEMA_VERSION}"
_SENSITIVE = re.compile(r"senha|password|passwd|token|secret|credential|login|account|conta|cookie", re.I)


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    type: str                       # int | float | bool | choice | text | position
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    choices: tuple[str, ...] = ()
    locked: bool = False            # exibido, mas nao editavel (decisao vigente da Founder)
    help: str = ""

    def coerce(self, value: Any) -> Any:
        t = self.type
        if t == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{self.label}: esperado sim/nao")
        elif t in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{self.label}: esperado numero")
            if t == "int":
                if value != int(value):
                    raise ValueError(f"{self.label}: esperado inteiro")
                value = int(value)
            if self.min is not None and value < self.min or self.max is not None and value > self.max:
                raise ValueError(f"{self.label}: fora do intervalo {self.min}..{self.max}")
        elif t == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.label}: opcao invalida {value!r}")
        elif t == "text":
            if not isinstance(value, str):
                raise ValueError(f"{self.label}: esperado texto")
        elif t == "position":
            if value is not None:
                Position(*value)  # valida coordenadas
                value = [float(v) for v in value]
        else:  # pragma: no cover - erro de definicao
            raise ValueError(f"tipo desconhecido {t}")
        if self.locked and value != self.default:
            raise ValueError(f"{self.label}: valor fixado pela decisao vigente")
        return value


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    parent: Optional[str] = None
    settings: tuple[Setting, ...] = ()
    rules: bool = False                       # tabela de habilidades/itens/acoes
    filter_actions: tuple[str, ...] = ()      # tabela de filtros com estas acoes
    needs: tuple[ActionKind, ...] = ()        # acoes do contrato necessarias para funcionar
    note: str = ""

    def setting(self, key: str) -> Setting:
        for s in self.settings:
            if s.key == key:
                return s
        raise KeyError(f"{self.key}.{key}")


S = Setting
SECTIONS: tuple[Section, ...] = (
    Section("geral", "Geral", settings=(
        S("tick_interval_ms", "Intervalo entre ciclos (ms)", "int", 200, 0, 10000),
        S("max_move_distance", "Distancia maxima por movimento", "int", 5000, 100, 20000),
        S("max_script_errors", "Erros seguidos de script tolerados", "int", 1, 1, 100),
        S("autostart", "Iniciar script ao abrir", "bool", False, locked=True,
          help="Desativado: nada executa sem acao explicita do operador."),
    ), note="Execucao do motor. Afeta Replay e Simulado."),
    Section("cura", "Cura propria", rules=True, needs=(ActionKind.USE_SKILL,),
            note="Habilidades e itens para recuperar a propria personagem."),
    Section("cura.relaxar", "Relaxar", parent="cura", settings=(
        S("sit_hp_below", "Sentar com HP abaixo de (%)", "int", 40, 0, 100),
        S("sit_mp_below", "Sentar com MP abaixo de (%)", "int", 30, 0, 100),
        S("stand_hp_above", "Levantar com HP acima de (%)", "int", 95, 0, 100),
        S("stand_mp_above", "Levantar com MP acima de (%)", "int", 95, 0, 100),
        S("idle_without_target_s", "Descansar apos sem alvo (s)", "int", 0, 0, 3600),
        S("rest_point", "Ponto de descanso (x, y, z)", "position", None),
    ), needs=(ActionKind.REST,), note="Executado pela PolicyRunner (hunter.py) com a acao 'rest'; so o Simulado a implementa."),
    Section("cura.morte", "Personagem morreu", parent="cura", settings=(
        S("wait_respawn_s", "Aguardar ressurreicao (s)", "int", 0, 0, 3600),
        S("wait_party", "Aguardar grupo", "bool", False),
        S("fallback", "Se ninguem ressuscitar", "choice", "parar", choices=("parar", "ir_para_cidade")),
        S("pause_after_res_s", "Pausa apos ressuscitar (s)", "int", 5, 0, 600),
    ), needs=(ActionKind.TELEPORT,), note="PolicyRunner: 'parar' encerra o script; 'ir_para_cidade' pausa para o operador (teleporte sem backend)."),
    Section("buff", "Buff proprio", rules=True, needs=(ActionKind.USE_SKILL,)),
    Section("grupo_cura", "Cura-buff do grupo", rules=True, settings=(
        S("radius", "Raio de atuacao", "int", 900, 0, 5000),
        S("outside_party", "Atuar fora do grupo", "bool", False),
    ), needs=(ActionKind.SELECT_TARGET, ActionKind.USE_SKILL)),
    Section("grupo", "Configuracoes do grupo", settings=(
        S("accept_invites", "Aceitar convites", "choice", "nunca", choices=("nunca", "do_lider")),
        S("leader", "Lider", "text", "Sarynne"),
        S("follow_leader", "Seguir lider", "bool", True),
        S("follow_distance", "Distancia de seguimento", "int", 250, 50, 3000),
        S("assist_leader", "Assistir alvo do lider", "bool", False),
        S("quest_dialogs", "Dialogos de missao", "choice", "manual", choices=("manual",), locked=True,
          help="Decisao vigente: aceitar/entregar missoes e manual."),
    ), needs=(ActionKind.MOVE_TO,),
        note="Seguir lider alimenta follow_sarynne.py (leader, distance)."),
    Section("ataque", "Ataque", rules=True, settings=(
        S("attack_range", "Distancia de ataque", "int", 600, 0, 3000),
        S("aggressive_limit", "Maximo de agressivos proximos", "int", 3, 0, 50),
    ), needs=(ActionKind.SELECT_TARGET, ActionKind.ATTACK, ActionKind.USE_SKILL)),
    Section("alvo", "Busca de alvo", settings=(
        S("avoid_fighting_others", "Evitar mobs em combate com outros", "bool", True),
        S("avoid_near_players", "Evitar mobs perto de jogadores", "bool", True),
        S("players_radius", "Raio de jogadores", "int", 500, 0, 5000),
        S("hp_unchanged_s", "Trocar alvo se HP nao mudar (s)", "int", 10, 0, 600),
        S("max_level_diff", "Diferenca maxima de nivel", "int", 10, 0, 100),
    ), needs=(ActionKind.SELECT_TARGET, ActionKind.MOVE_TO),
        note="PolicyRunner: cada alvo descartado e registrado com o motivo (evento script_log)."),
    Section("alvo.filtro", "Filtro", parent="alvo", filter_actions=("atacar", "ignorar", "revidar"),
            needs=(ActionKind.SELECT_TARGET,)),
    Section("zona", "Zona de combate", settings=(
        S("mode", "Tipo de zona", "choice", "centro_raio", choices=("centro_raio", "ponto", "arbitraria")),
        S("center", "Centro (x, y, z)", "position", None),
        S("radius", "Raio", "int", 1500, 100, 20000),
        S("z_min", "Z minimo", "int", -20000, -30000, 30000),
        S("z_max", "Z maximo", "int", 20000, -30000, 30000),
        S("return_to_center", "Voltar ao centro sem alvo", "bool", True),
    ), needs=(ActionKind.MOVE_TO,), note="Desenhada no mapa XY quando o centro esta definido."),
    Section("rota", "Rota", settings=(
        S("route_file", "Arquivo de rota (l2tools-route/1)", "text", ""),
        S("loop", "Repetir rota", "bool", False),
        S("tolerance", "Tolerancia de chegada no no", "int", 50, 5, 1000),
    ), needs=(ActionKind.MOVE_TO,), note="Grafo de rota proprio, desenhado no mapa. Executado so no Simulado (route_walker.py)."),
    Section("drop", "Drop", settings=(
        S("radius", "Raio de coleta", "int", 400, 0, 3000),
        S("z_limit", "Limite Z", "int", 200, 0, 2000),
        S("only_mine", "Somente meus", "bool", True),
        S("inside_zone", "Somente dentro da zona", "bool", True),
        S("timeout_s", "Desistir apos (s)", "int", 5, 0, 120),
    ), note="Coletar item nao existe no contrato de acoes: nao suportado."),
    Section("drop.filtro", "Filtro", parent="drop", filter_actions=("coletar", "ignorar")),
)
del S
SECTION_BY_KEY = {s.key: s for s in SECTIONS}


@dataclass(frozen=True)
class SkillEntry:
    """Entrada do catalogo de habilidades/itens. `confirmed` so com ID visto no proprio cliente."""
    name: str
    game_id: Optional[int] = None
    confirmed: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("habilidade precisa de nome")
        if self.game_id is not None and (isinstance(self.game_id, bool) or not isinstance(self.game_id, int)
                                         or self.game_id <= 0):
            raise ValueError(f"{self.name}: ID deve ser inteiro positivo")
        if self.confirmed and self.game_id is None:
            raise ValueError(f"{self.name}: nao pode ser confirmada sem ID")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "game_id": self.game_id, "confirmed": self.confirmed, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillEntry":
        return cls(d["name"], d.get("game_id"), bool(d.get("confirmed", False)), d.get("note", ""))


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Move game_id das regras para o catalogo. Na v1 o campo so devia ser
    preenchido com ID confirmado, entao a entrada migra como confirmada."""
    skills: dict[str, dict[str, Any]] = {}
    for sec_key, rules in (data.get("rules") or {}).items():
        for rule in rules:
            gid = rule.pop("game_id", None)
            if gid is None:
                continue
            known = skills.get(rule["name"])
            if known and known["game_id"] != gid:
                raise ValueError(f"migracao v1->v2: '{rule['name']}' tem IDs diferentes ({known['game_id']} e {gid})")
            skills[rule["name"]] = {"name": rule["name"], "game_id": gid, "confirmed": True,
                                    "note": f"migrado da v1 ({sec_key})"}
    data["skills"] = list(skills.values())
    data["format"] = f"{FORMAT_PREFIX}2"
    return data


MIGRATIONS = {1: _v1_to_v2}  # versao de origem -> funcao que leva a versao seguinte


def migrate(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Leva um perfil serializado ate SCHEMA_VERSION. Devolve (dados, passos aplicados)."""
    fmt = str(data.get("format", ""))
    if not fmt.startswith(FORMAT_PREFIX) or not fmt[len(FORMAT_PREFIX):].isdigit():
        raise ValueError(f"formato de perfil nao reconhecido (esperado {FORMAT_PREFIX}N)")
    version = int(fmt[len(FORMAT_PREFIX):])
    if version > SCHEMA_VERSION:
        raise ValueError(f"perfil da versao {version}, mais nova que a suportada ({SCHEMA_VERSION})")
    applied = []
    data = json.loads(json.dumps(data))  # copia profunda: nao altera o dicionario do chamador
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        applied.append(f"v{version}->v{version + 1}")
        version += 1
    return data, applied


def _check_keys(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SENSITIVE.search(str(k)):
                raise ValueError(f"campo sensivel recusado em perfil: {path}{k}")
            _check_keys(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for v in obj:
            _check_keys(v, path)


@dataclass
class Profile:
    name: str
    character: str = ""
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: dict[str, list[Rule]] = field(default_factory=dict)
    filters: dict[str, list[FilterRule]] = field(default_factory=dict)
    skills: list[SkillEntry] = field(default_factory=list)
    migrated: list[str] = field(default_factory=list, compare=False)  # passos aplicados ao carregar

    @classmethod
    def default(cls, name: str = "padrao", character: str = "") -> "Profile":
        p = cls(name, character)
        for sec in SECTIONS:
            p.values[sec.key] = {s.key: s.default for s in sec.settings}
            if sec.rules:
                p.rules[sec.key] = []
            if sec.filter_actions:
                p.filters[sec.key] = []
        return p

    def get(self, section: str, key: str) -> Any:
        return self.values[section][key]

    def set(self, section: str, key: str, value: Any) -> None:
        self.values[section][key] = SECTION_BY_KEY[section].setting(key).coerce(value)

    def set_rules(self, section: str, rules: list[Rule]) -> None:
        if not SECTION_BY_KEY[section].rules:
            raise KeyError(f"secao sem regras: {section}")
        self.rules[section] = list(rules)

    def set_filters(self, section: str, filters: list[FilterRule]) -> None:
        sec = SECTION_BY_KEY[section]
        for f in filters:
            if f.action not in sec.filter_actions:
                raise ValueError(f"acao de filtro invalida em {section}: {f.action}")
        self.filters[section] = list(filters)

    def set_skills(self, skills: list[SkillEntry]) -> None:
        names = [s.name.casefold() for s in skills]
        if len(names) != len(set(names)):
            raise ValueError("catalogo com nomes repetidos")
        self.skills = list(skills)

    def skill(self, name: str) -> Optional[SkillEntry]:
        wanted = name.casefold()
        return next((s for s in self.skills if s.name.casefold() == wanted), None)

    def resolve_game_id(self, rule: Rule) -> Optional[int]:
        """ID utilizavel por uma regra: somente de entrada CONFIRMADA do catalogo."""
        entry = self.skill(rule.name)
        return entry.game_id if entry and entry.confirmed else None

    def script_params(self) -> dict[str, Any]:
        """Parametros derivados do perfil para os scripts de exemplo."""
        g = self.values["grupo"]
        params: dict[str, Any] = {}
        if g["follow_leader"] and g["leader"].strip():
            params.update(leader=g["leader"].strip(), distance=g["follow_distance"])
        r = self.values["rota"]
        if r["route_file"].strip():
            params.update(route=r["route_file"].strip(), loop=r["loop"], tolerance=r["tolerance"])
        return params

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT, "name": self.name, "character": self.character,
            "values": self.values,
            "rules": {k: [r.to_dict() for r in v] for k, v in self.rules.items()},
            "filters": {k: [f.to_dict() for f in v] for k, v in self.filters.items()},
            "skills": [s.to_dict() for s in self.skills],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        _check_keys(data)
        data, applied = migrate(data)
        p = cls.default(str(data.get("name") or "importado"), str(data.get("character") or ""))
        p.migrated = applied
        for sec_key, values in (data.get("values") or {}).items():
            if sec_key not in SECTION_BY_KEY:
                raise ValueError(f"secao desconhecida: {sec_key}")
            for key, value in values.items():
                p.set(sec_key, key, value)
        for sec_key, rules in (data.get("rules") or {}).items():
            p.set_rules(sec_key, [Rule.from_dict(r) for r in rules])
        for sec_key, filters in (data.get("filters") or {}).items():
            p.set_filters(sec_key, [FilterRule.from_dict(f) for f in filters])
        p.set_skills([SkillEntry.from_dict(s) for s in data.get("skills", [])])
        return p

    def save(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "Profile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def diff(self, other: "Profile") -> list[str]:
        """Mudancas de `other` em relacao a este perfil, legiveis para a GUI."""
        changes: list[str] = []
        for sec in SECTIONS:
            for s in sec.settings:
                a, b = self.values[sec.key][s.key], other.values[sec.key][s.key]
                if a != b:
                    changes.append(f"{sec.title} > {s.label}: {a!r} -> {b!r}")
            if sec.rules and self.rules[sec.key] != other.rules[sec.key]:
                changes.append(f"{sec.title}: regras {len(self.rules[sec.key])} -> {len(other.rules[sec.key])}")
            if sec.filter_actions and self.filters[sec.key] != other.filters[sec.key]:
                changes.append(f"{sec.title}: filtros {len(self.filters[sec.key])} -> {len(other.filters[sec.key])}")
        if self.skills != other.skills:
            changes.append(f"Catalogo de habilidades: {len(self.skills)} -> {len(other.skills)} entrada(s)")
        if (self.name, self.character) != (other.name, other.character):
            changes.append(f"Perfil: {self.name}/{self.character} -> {other.name}/{other.character}")
        return changes

    def copy(self) -> "Profile":
        return Profile.from_dict(json.loads(json.dumps(self.to_dict())))
