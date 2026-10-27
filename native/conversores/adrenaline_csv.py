"""Conversor offline: CSVs exportados por scripts proprios da Founder no Adrenaline
(GravadorQuest, QuestProbe, LeituraSpots) -> replay `l2tools-replay/1`.

    python -m conversores.adrenaline_csv ENTRADA.csv -o SAIDA.jsonl [--aceitar-regra spawn_trio_zero]

Ferramenta de migracao de dados historicos (Experimento 027). O L2-Tools nao
importa este modulo nem depende dele; tudo o que e particular desses CSVs fica aqui.

- Nada se perde em silencio: cada estado leva os valores originais da linha em
  `raw`; linhas recusadas, valores vazios, campos sem destino no GameState e
  regras aplicadas vao para o relatorio `SAIDA.relatorio.json`.
- Semantica por campo: cada coluna declara destino, regra e evidencia. Coluna
  sem regra comprovada passa como valor bruto observado, nunca como desconhecido presumido.
- Interpretacao sem prova e opcional (`--aceitar-regra`) e fica desligada por padrao.
- LeituraSpots e uma fotografia: vira UM estado, nao uma sequencia de aparecimentos.
- Nomes de jogadores sao pseudonimizados antes de qualquer saida, inclusive em `raw`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

REPLAY_FORMAT = "l2tools-replay/1"          # contrato do L2-Tools (docs/contratos/replay.md)
REPORT_FORMAT = "l2tools-conversion-report/1"
CONVERTER = "conversores.adrenaline_csv"
CONVERTER_VERSION = 1

SERIES, SNAPSHOT = "serie", "fotografia"

E_API = "documentacao publica da API do Adrenaline (relatorio 026, E8)"
E_HEADER = "cabecalho do CSV gerado pelo script da Founder"


@dataclass(frozen=True)
class FieldRule:
    column: str
    destination: str    # caminho no estado do replay; "raw" = so preservado no valor bruto
    rule: str
    evidence: str


@dataclass(frozen=True)
class OptionalRule:
    id: str
    effect: str
    evidence: str
    why_optional: str


@dataclass(frozen=True)
class SourceFormat:
    name: str
    kind: str
    columns: tuple[str, ...]
    fields: tuple[FieldRule, ...]


_TIME = FieldRule("horario", "timestamp", "AAAA-MM-DD HH:MM:SS -> AAAA-MM-DDTHH:MM:SS, sem fuso",
                  f"{E_HEADER}; mesmo padrao em todas as linhas. Relogio local do Windows; o fuso nao foi gravado")
_XYZ = tuple(FieldRule(c, f"self.position.{c}", "inteiro observado; 0 e coordenada valida",
                       f"`{c.upper()}: integer` da personagem, {E_API}") for c in ("x", "y", "z"))
_LEVEL = FieldRule("nivel", "self.level", "inteiro observado", f"`Level` da personagem, {E_API}")
_EXP = FieldRule("exp", "self.exp", "valor absoluto observado, sem conversao para porcentagem",
                 f"`Exp` da personagem, {E_API}")
_QUEST = FieldRule("quest_id", "quest_info.quest_id", "inteiro observado", "ID da missao consultada pelo script (Registro 013)")
_DLG = FieldRule("dlg_tamanho", "raw", "sem destino: tamanho do texto do dialogo nao prova janela de missao aberta",
                 E_HEADER)


def _status(col: str) -> FieldRule:
    return FieldRule(col, "quest_info.status_bruto", "valor bruto observado, sem interpretacao de etapa",
                     "semantica de QuestStatus nao documentada (relatorio 026, H1)")


FORMATS: dict[str, SourceFormat] = {f.name: f for f in (
    SourceFormat("QuestProbe", SERIES,
                 ("horario", "quest_id", "estado_bruto", "nivel", "exp", "x", "y", "z", "dlg_tamanho"),
                 (_TIME, _QUEST, _status("estado_bruto"), _LEVEL, _EXP, *_XYZ, _DLG)),
    SourceFormat("GravadorQuest", SERIES,
                 ("seq", "ms", "horario", "origem", "evento", "detalhe", "quest_id", "quest_status_bruto", "nivel",
                  "exp", "sp", "x", "y", "z", "npc_alvo_id", "dlg_tamanho"),
                 (FieldRule("seq", "seq", "inteiro; ordem da fonte", E_HEADER),
                  FieldRule("ms", "elapsed_ms", "inteiro; ms desde o marco 'inicio'", f"{E_HEADER}; o marco inicio tem ms=0"),
                  _TIME,
                  FieldRule("origem", "event.origin", "texto; 'amostra' nao gera evento", E_HEADER),
                  FieldRule("evento", "event.kind", "texto", E_HEADER),
                  FieldRule("detalhe", "event.detail", "texto bruto, sem interpretacao", E_HEADER),
                  _QUEST, _status("quest_status_bruto"), _LEVEL, _EXP,
                  FieldRule("sp", "raw", "sem destino: o GameState nao tem SP", E_HEADER),
                  *_XYZ,
                  FieldRule("npc_alvo_id", "raw", "sem destino: o GameState guarda o nome do alvo, nao o ID; "
                            "0 nao tem semantica comprovada", E_HEADER),
                  _DLG)),
    SourceFormat("LeituraSpots", SNAPSHOT,
                 ("tipo", "nome", "id", "x", "y", "z", "spawn_x", "spawn_y", "spawn_z"),
                 (FieldRule("tipo", "npcs | characters | raw", "NPC_ATACAVEL -> npcs; JOGADOR_PT e JOGADOR_EXTERNO -> "
                            "characters; SUMMON -> so raw (nao e candidato a alvo); outro valor -> linha recusada",
                            E_HEADER),
                  FieldRule("nome", "*.name", "NPC: texto observado; jogador: pseudonimo (privacidade)", E_HEADER),
                  FieldRule("id", "npcs.npc_id", "ID de tipo do NPC; em jogadores fica so em raw", f"`ID`, {E_API}"),
                  *(FieldRule(c, f"*.position.{c}", "inteiro observado; 0 e coordenada valida", f"`{c.upper()}`, {E_API}")
                    for c in ("x", "y", "z")),
                  *(FieldRule(f"spawn_{c}", f"npcs.spawn.{c}", "inteiro observado; vazio = nao informado",
                              f"`Start{c.upper()}`, {E_API}") for c in ("x", "y", "z")))),
)}

OPTIONAL_RULES: dict[str, OptionalRule] = {r.id: r for r in (
    OptionalRule("spawn_trio_zero", "spawn (0,0,0) com posicao atual sem nenhum zero passa a desconhecido",
                 "22 de 45 NPCs da LeituraSpots (Registro 004, relatorio 026 secao 4): sugere spawn nao informado",
                 "a semantica de StartX..Z nao e documentada; por padrao o trio fica como valor observado"),
)}

_FILE_TIME = re.compile(r"_(\d{8})_(\d{6})(?:\.csv)?$", re.IGNORECASE)
_NPC, _SUMMON, _PLAYERS = "NPC_ATACAVEL", "SUMMON", ("JOGADOR_PT", "JOGADOR_EXTERNO")


class FormatError(ValueError):
    """Arquivo que nao e um dos CSVs conhecidos (nada e convertido)."""


class RowError(ValueError):
    """Linha recusada; o motivo vai para o relatorio."""


@dataclass
class Report:
    source: dict[str, Any]
    fmt: SourceFormat
    accepted_rules: tuple[str, ...]
    read: int = 0
    states: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)
    empty: dict[str, int] = field(default_factory=dict)
    raw_only: dict[str, int] = field(default_factory=dict)
    applied: dict[str, int] = field(default_factory=dict)
    matches: dict[str, int] = field(default_factory=dict)
    ambiguities: dict[str, dict[str, Any]] = field(default_factory=dict)
    pseudonymized: int = 0

    def ambiguity(self, key: str, detail: str, n: int = 1) -> None:
        a = self.ambiguities.setdefault(key, {"id": key, "count": 0, "detail": detail})
        a["count"] += n

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": REPORT_FORMAT,
            "source": self.source,
            "rows": {"read": self.read, "converted": self.read - len(self.rejected), "rejected": len(self.rejected)},
            "states": self.states,
            "mapping": [vars(r) for r in self.fmt.fields],
            "rejected": self.rejected,
            "empty_values": self.empty,
            "raw_only": self.raw_only,
            "rules": [{**vars(r), "accepted": r.id in self.accepted_rules, "matches": self.matches.get(r.id, 0),
                       "applied": self.applied.get(r.id, 0)}
                      for r in OPTIONAL_RULES.values() if r.id in self.matches or r.id in self.accepted_rules],
            "ambiguities": list(self.ambiguities.values()),
            "privacy": {"pseudonymized_player_names": self.pseudonymized},
        }


@dataclass
class Conversion:
    records: list[dict[str, Any]]   # primeiro item: replay_meta
    report: Report

    def jsonl(self) -> str:
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self.records)


# --- leitura -----------------------------------------------------------------------------
def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise FormatError("codificacao nao reconhecida (nem UTF-8 nem cp1252)")


def detect(header: Sequence[str]) -> SourceFormat:
    cols = tuple(h.strip() for h in header)
    for f in FORMATS.values():
        if cols == f.columns:
            return f
    raise FormatError(f"cabecalho nao reconhecido: {';'.join(cols)!r}. Formatos: {', '.join(FORMATS)}")


def _int(row: dict[str, str], col: str) -> Optional[int]:
    """Inteiro da coluna; None se vazia. Texto nao numerico recusa a linha."""
    text = row[col].strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise RowError(f"coluna '{col}' nao e inteiro: {text!r}") from None


def _time(text: str) -> str:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError:
        raise RowError(f"horario invalido: {text!r}") from None


def _unknown(note: str) -> dict[str, Any]:
    return {"status": "unknown", "note": note}


def _position(row: dict[str, str], cols: Sequence[str], report: Report) -> Any:
    values = [_int(row, c) for c in cols]
    if all(v is not None for v in values):
        if 0 < values.count(0) < 3:
            report.ambiguity("zero_isolado", "coordenada 0 isolada mantida como valor observado")
        return values
    for c, v in zip(cols, values):
        if v is None:
            report.empty[c] = report.empty.get(c, 0) + 1
    if any(v is not None for v in values):
        report.ambiguity("xyz_incompleto", "posicao com parte das coordenadas vazia: desconhecida")
    return _unknown(f"vazio na fonte ({','.join(c for c, v in zip(cols, values) if v is None)})")


def _count_empty(row: dict[str, str], cols: Sequence[str], report: Report) -> None:
    for c in cols:
        if not row[c].strip():
            report.empty[c] = report.empty.get(c, 0) + 1


def _count_raw_only(row: dict[str, str], fmt: SourceFormat, report: Report) -> None:
    for r in fmt.fields:
        if r.destination == "raw" and row[r.column].strip():
            report.raw_only[r.column] = report.raw_only.get(r.column, 0) + 1


# --- formatos ---------------------------------------------------------------------------------
def _series_record(line: int, row: dict[str, str], fmt: SourceFormat, report: Report) -> dict[str, Any]:
    rec: dict[str, Any] = {}
    if fmt.name == "GravadorQuest":
        rec["seq"], rec["elapsed_ms"] = _int(row, "seq"), _int(row, "ms")
        if rec["seq"] is None or rec["elapsed_ms"] is None:
            raise RowError("seq/ms vazios")
    rec["timestamp"] = _time(row["horario"])
    me: dict[str, Any] = {"position": _position(row, ("x", "y", "z"), report)}
    for col, key in (("nivel", "level"), ("exp", "exp")):
        v = _int(row, col)
        me[key] = v if v is not None else _unknown("vazio na fonte")
    rec["self"] = me
    status_col = "estado_bruto" if fmt.name == "QuestProbe" else "quest_status_bruto"
    quest = {k: v for k, v in (("quest_id", _int(row, "quest_id")), ("status_bruto", _int(row, status_col)))
             if v is not None}
    if quest:
        rec["quest_info"] = quest
    if fmt.name == "GravadorQuest":
        for col in ("sp", "npc_alvo_id", "dlg_tamanho"):
            _int(row, col)   # valida; o valor fica em raw
        if _int(row, "npc_alvo_id") == 0:
            report.ambiguity("npc_alvo_id_zero", "npc_alvo_id=0 mantido so em raw; 'sem alvo' nao foi comprovado")
        if row["origem"].strip() != "amostra":
            rec["event"] = {"origin": row["origem"].strip(), "kind": row["evento"].strip(),
                            "detail": row["detalhe"].strip()}
    else:
        _int(row, "dlg_tamanho")
    _count_empty(row, ("horario", "quest_id", status_col, "nivel", "exp"), report)
    _count_raw_only(row, fmt, report)
    rec["raw"] = {"line": line, "row": row}
    return rec


class _Pseudonyms:
    def __init__(self) -> None:
        self.names: dict[tuple[str, str], str] = {}

    def get(self, kind: str, name: str) -> str:
        key = (kind, name)
        if key not in self.names:
            n = sum(1 for k in self.names if k[0] == kind) + 1
            self.names[key] = f"{kind.lower()}_{n}"
        return self.names[key]


def _snapshot_entity(row: dict[str, str], report: Report, accepted: tuple[str, ...]) -> Optional[tuple[str, dict]]:
    kind = row["tipo"].strip()
    if kind not in (_NPC, _SUMMON, *_PLAYERS):
        raise RowError(f"tipo desconhecido: {kind!r}")
    npc_id = _int(row, "id")
    pos = _position(row, ("x", "y", "z"), report)
    spawn_cols = ("spawn_x", "spawn_y", "spawn_z")
    spawn_raw = [_int(row, c) for c in spawn_cols]
    if kind in _PLAYERS:
        if any(v is not None for v in spawn_raw):
            report.ambiguity("spawn_em_jogador", "jogador com spawn preenchido: mantido so em raw")
        if npc_id == 0:
            report.ambiguity("id_zero_jogador", "id=0 em jogador mantido so em raw, sem interpretacao")
        return "characters", {"name": row["nome"], "position": pos}
    if kind == _SUMMON:
        report.raw_only["tipo=SUMMON"] = report.raw_only.get("tipo=SUMMON", 0) + 1
        return None
    entity: dict[str, Any] = {"name": row["nome"].strip(), "position": pos}
    if npc_id is not None:
        entity["npc_id"] = npc_id
    if all(v is None for v in spawn_raw):
        _count_empty(row, spawn_cols, report)
        entity["spawn"] = _unknown("vazio na fonte")
    else:
        spawn = _position(row, spawn_cols, report)
        trio = spawn == [0, 0, 0] and isinstance(pos, list) and 0 not in pos
        if trio:
            report.matches["spawn_trio_zero"] = report.matches.get("spawn_trio_zero", 0) + 1
            if "spawn_trio_zero" in accepted:
                report.applied["spawn_trio_zero"] = report.applied.get("spawn_trio_zero", 0) + 1
                spawn = _unknown("regra spawn_trio_zero: (0,0,0) com posicao atual nao nula")
            else:
                report.ambiguity("spawn_trio_zero", "spawn (0,0,0) mantido como valor observado; "
                                 "use --aceitar-regra spawn_trio_zero para trata-lo como desconhecido")
        entity["spawn"] = spawn
    return "npcs", entity


def _snapshot_time(name: str, report: Report) -> Optional[str]:
    m = _FILE_TIME.search(name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            pass
    report.ambiguity("sem_horario", "nome do arquivo sem _AAAAMMDD_HHMMSS: estado sem timestamp")
    return None


# --- conversao -----------------------------------------------------------------------------------
def convert_bytes(data: bytes, name: str, accept: Sequence[str] = ()) -> Conversion:
    unknown_rules = sorted(set(accept) - set(OPTIONAL_RULES))
    if unknown_rules:
        raise FormatError(f"regra opcional desconhecida: {', '.join(unknown_rules)}; existentes: {', '.join(OPTIONAL_RULES)}")
    text, encoding = _decode(data)
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    if not rows:
        raise FormatError("arquivo vazio")
    fmt = detect(rows[0])
    accepted = tuple(sorted(set(accept)))
    origin = {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "format": fmt.name, "kind": fmt.kind,
              "encoding": encoding, "rows": len(rows) - 1}
    report = Report(source=origin, fmt=fmt, accepted_rules=accepted)
    meta = {"format": REPLAY_FORMAT, "origin": origin,
            "converter": {"name": CONVERTER, "version": CONVERTER_VERSION, "accepted_rules": list(accepted)},
            "note": "valores observados pela ferramenta de origem (scripts da Founder no Adrenaline), nao pelo "
                    "L2-Tools; horario local sem fuso; nomes de jogadores pseudonimizados"}
    records: list[dict[str, Any]] = [{"replay_meta": meta}]
    names = _Pseudonyms()
    snapshot: dict[str, Any] = {"npcs": [], "characters": []}
    raw_rows: list[dict[str, Any]] = []
    last_seq: Optional[int] = None
    last_ms: Optional[int] = None
    for line, values in enumerate(rows[1:], 2):
        if not any(v.strip() for v in values):
            continue
        report.read += 1
        if len(values) != len(fmt.columns):
            report.rejected.append({"line": line, "reason": f"{len(values)} colunas, esperadas {len(fmt.columns)}"})
            continue
        row = dict(zip(fmt.columns, values))
        if fmt.kind == SNAPSHOT and row["tipo"].strip() in _PLAYERS:
            row["nome"] = names.get(row["tipo"].strip(), row["nome"])
        try:
            if fmt.kind == SERIES:
                rec = _series_record(line, row, fmt, report)
                seq, ms = rec.get("seq"), rec.get("elapsed_ms")
                if seq is not None and last_seq is not None and seq != last_seq + 1:
                    report.ambiguity("seq_descontinuo", "seq fora de sequencia (lacuna ou repeticao); ordem do arquivo mantida")
                if ms is not None and last_ms is not None and ms < last_ms:
                    report.ambiguity("ms_regressivo", "ms menor que o da linha anterior; ordem do arquivo mantida")
                last_seq, last_ms = seq, ms
                records.append(rec)
            else:
                got = _snapshot_entity(row, report, accepted)
                if got:
                    snapshot[got[0]].append(got[1])
                raw_rows.append({"line": line, "row": row})
        except RowError as exc:
            report.rejected.append({"line": line, "reason": str(exc)})
    if fmt.kind == SNAPSHOT:
        state: dict[str, Any] = {}
        ts = _snapshot_time(name, report)
        if ts:
            state["timestamp"] = ts
        state.update(snapshot)
        state["raw"] = {"rows": raw_rows}
        records.append(state)
    report.pseudonymized = len(names.names)
    report.states = len(records) - 1
    return Conversion(records, report)


def convert_file(path: Path | str, accept: Sequence[str] = ()) -> Conversion:
    path = Path(path)
    return convert_bytes(path.read_bytes(), path.name, accept)


def report_path(out: Path) -> Path:
    return out.with_name(out.name.removesuffix(".jsonl") + ".relatorio.json")


def write(conv: Conversion, out: Path) -> Path:
    out.write_text(conv.jsonl(), encoding="utf-8", newline="\n")
    rp = report_path(out)
    rp.write_text(json.dumps(conv.report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return rp


def summary(report: dict[str, Any]) -> str:
    rows = report["rows"]
    lines = [f"{report['source']['format']} ({report['source']['kind']}): {rows['read']} linhas lidas, "
             f"{rows['converted']} convertidas, {rows['rejected']} recusadas -> {report['states']} estado(s)"]
    lines += [f"  recusada linha {r['line']}: {r['reason']}" for r in report["rejected"][:20]]
    if len(report["rejected"]) > 20:
        lines.append(f"  ... mais {len(report['rejected']) - 20} (ver relatorio)")
    if report["empty_values"]:
        lines.append("  vazios (viram desconhecido): " + ", ".join(f"{k}={v}" for k, v in report["empty_values"].items()))
    if report["raw_only"]:
        lines.append("  so em raw (sem campo no GameState): " + ", ".join(f"{k}={v}" for k, v in report["raw_only"].items()))
    for r in report["rules"]:
        lines.append(f"  regra opcional {r['id']}: {r['matches']} casos, "
                     + (f"aplicada em {r['applied']}" if r["accepted"] else "NAO aplicada"))
    for a in report["ambiguities"]:
        lines.append(f"  ambiguidade {a['id']} ({a['count']}x): {a['detail']}")
    if report["privacy"]["pseudonymized_player_names"]:
        lines.append(f"  nomes de jogadores pseudonimizados: {report['privacy']['pseudonymized_player_names']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m conversores.adrenaline_csv",
                                     description="CSV historico do Adrenaline -> replay l2tools-replay/1 (offline)")
    parser.add_argument("entrada", type=Path, help="GravadorQuest_*.csv, QuestProbe_*.csv ou LeituraSpots_*.csv")
    parser.add_argument("-o", "--saida", type=Path, required=True, help="replay .jsonl a gerar")
    parser.add_argument("--aceitar-regra", action="append", default=[], choices=sorted(OPTIONAL_RULES),
                        help="aplicar uma regra de interpretacao opcional (repetivel)")
    parser.add_argument("--forcar", action="store_true", help="sobrescrever a saida existente")
    args = parser.parse_args(argv)
    if not args.saida.name.endswith(".jsonl"):
        print("ERRO: a saida deve terminar em .jsonl", file=sys.stderr)
        return 2
    if not args.forcar and (args.saida.exists() or report_path(args.saida).exists()):
        print(f"ERRO: {args.saida} ou seu relatorio ja existe (use --forcar)", file=sys.stderr)
        return 2
    try:
        conv = convert_file(args.entrada, args.aceitar_regra)
    except (OSError, FormatError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    rp = write(conv, args.saida)
    print(summary(conv.report.to_dict()))
    print(f"replay: {args.saida}\nrelatorio: {rp}")
    return 0 if conv.report.states else 1


if __name__ == "__main__":
    sys.exit(main())
