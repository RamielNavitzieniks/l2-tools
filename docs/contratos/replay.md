# Contrato: replay `l2tools-replay/1`

O formato de replay **pertence ao L2-Tools Native** (`native/l2native/adapters/replay.py`). Conversores de fontes externas (por exemplo, `native/conversores/adrenaline_csv.py`) produzem este formato. O núcleo não conhece nenhuma fonte externa e não depende de nenhum conversor.

## Arquivo

- JSONL em UTF-8: um objeto por linha.
- Linhas vazias e linhas iniciadas por `#` são ignoradas.
- Cada linha que não for de metadados vira **um** `GameState`, na ordem do arquivo.

## Metadados (opcional, só na primeira linha)

```json
{"replay_meta": {"format": "l2tools-replay/1",
                 "origin": {"file": "...", "sha256": "...", "format": "...", "kind": "serie | fotografia", "rows": 311},
                 "converter": {"name": "...", "version": 1, "accepted_rules": []},
                 "note": "..."}}
```

- `format` é obrigatório e precisa ser `l2tools-replay/1`. Um formato diferente é recusado, com o número da linha.
- Uma linha `replay_meta` depois de qualquer estado também é recusada.
- O restante é proveniência livre: arquivo de origem, hash, conversor e regras aplicadas. O `ReplayAdapter` expõe esses dados em `adapter.meta`, e `python -m l2native replay-info` os mostra.
- Replays sem `replay_meta` continuam válidos, como as fixtures dos experimentos 020–024.

## Estado

```json
{"timestamp": "2026-10-24T23:52:28",
 "self": {"position": [x, y, z], "hp": 0.9, "mp": 0.5, "level": 1, "exp": 0},
 "target": "...", "in_combat": false, "quest_dialog": false,
 "characters": [{"name": "...", "position": [x, y, z]}],
 "npcs": [{"name": "...", "npc_id": 1, "object_id": 7, "position": [x, y, z], "dead": false, "spawn": [x, y, z]}],
 "inventory": {}, "quest_info": {}}
```

- **Campo ausente ou `null` = desconhecido.** O conversor nunca troca um valor ausente por um padrão plausível (0, `false`, lista vazia).
- **Lista vazia ≠ desconhecida.** `"npcs": []` é uma observação: não havia ninguém à vista. Sem `"npcs"`, a fonte não enxerga a lista.
- **Fato explícito.** Qualquer valor pode ser escrito como `{"status": "observed" | "inferred" | "unknown", "value": ..., "source": "...", "confidence": 0.7, "note": "..."}`. Um `unknown` com `note` registra **por que** o valor é desconhecido, por exemplo `"vazio na fonte (x)"` ou `"regra spawn_trio_zero: ..."`.
- **`spawn`** é opcional e indica o ponto de origem da entidade, quando a fonte o informa (desde o exp. 027).

## Extras preservados, fora do `GameState`

Ficam no arquivo e aparecem em `ReplayAdapter.record` a cada leitura. O motor não decide nada a partir deles.

| Campo | Conteúdo |
| --- | --- |
| `seq` | Sequência da fonte. Lacunas são mantidas como vieram |
| `elapsed_ms` | Milissegundos desde o início da gravação da fonte |
| `event` | `{"origin", "kind", "detail"}`: evento ou marco registrado pela fonte, sem interpretação |
| `raw` | Valores originais da fonte, como texto, com o número da linha (`{"line", "row"}`; numa fotografia, `{"rows": [...]}`) |

## Série × fotografia

- **Série:** uma linha por amostra ou evento da fonte, na ordem da fonte.
- **Fotografia:** uma leitura única, como uma lista de NPCs num instante, vira **um** estado. Uma fotografia nunca é transformada em sequência temporal de aparecimentos.

## Verificação

`python -m l2native replay-info ARQUIVO.jsonl` lê o arquivo pelo mesmo caminho do motor (`ReplayAdapter`) e conta, por campo, os estados com valor observado, inferido ou desconhecido. Conta também as entidades (`npcs.position`, `npcs.dead`, `npcs.spawn`...) e os eventos.
