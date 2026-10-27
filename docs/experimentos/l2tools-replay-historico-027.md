# Experimento 027: dados históricos → replay (H1, 27/10/2026)

**Branch:** `experimento/l2tools-replay-historico-027`, empilhada sobre `experimento/l2tools-sessoes-025` (PR #18). **Sem merge.**

**Origem:** a hipótese H1 da investigação 026 (PR #19) e a revisão que encerrou a pesquisa. A revisão pediu um conversor CSV → replay com quatro resultados:

1. conversão sem perda silenciosa;
2. tratamento específico por formato;
3. relatório de perdas e ambiguidades;
4. validação no motor atual.

Pediu também que o conversor **não** vire dependência permanente do Adrenaline.

## Separação de responsabilidades

| Parte | Onde | Conhece o Adrenaline? |
| --- | --- | --- |
| Formato `l2tools-replay/1` (metadados, `spawn` das entidades, extras preservados) | `native/l2native/adapters/replay.py`, `model.py`; contrato em `docs/contratos/replay.md` | **Não** |
| Verificação de replay (`summarize`, `l2native replay-info`) | `native/l2native/adapters/replay.py`, `cli.py` | **Não** |
| Particularidades dos CSVs (cabeçalhos, colunas, regras por campo, pseudônimos) | `native/conversores/adrenaline_csv.py` | Sim, e só ele |

Um teste garante que nenhum arquivo de `l2native/` menciona `conversores` ou `adrenaline`. Sem o conversor, o L2-Tools funciona igual: ele é uma ferramenta de migração, executada uma vez sobre dados já exportados.

```sh
cd native
python -m conversores.adrenaline_csv GravadorQuest_AAAAMMDD_HHMMSS.csv -o gravador.jsonl
python -m conversores.adrenaline_csv LeituraSpots_AAAAMMDD_HHMMSS.csv -o spots.jsonl [--aceitar-regra spawn_trio_zero]
python -m l2native replay-info gravador.jsonl
python -m l2native run --script scripts/hunter.py --adapter replay --events gravador.jsonl
```

O conversor grava `SAIDA.jsonl` e `SAIDA.relatorio.json`. Ele não sobrescreve arquivos sem `--forcar`.

## 1. Conversão sem perda silenciosa

- Cada estado leva em `raw` os valores originais da linha (texto) e o número da linha.
- O teste reconstrói cada linha convertida a partir de `raw` e compara com o CSV. Toda linha de dados aparece **ou** em `raw` **ou** na lista de recusadas, nunca nas duas e nunca em nenhuma.
- `replay_meta` registra o arquivo, o SHA-256, a codificação (UTF-8 ou cp1252), o formato, o tipo (série ou fotografia), o conversor e as regras aceitas.
- `seq`, `ms`, `origem`, `evento` e `detalhe` do GravadorQuest são preservados como `seq`, `elapsed_ms` e `event`.
- Campo sem destino no `GameState` (`sp`, `npc_alvo_id`, `dlg_tamanho`, linhas `SUMMON`) fica em `raw` e é **contado** no relatório (`raw_only`). Não é descartado e não recebe interpretação.
- Valor vazio vira desconhecido, com `note` explicando o motivo, e é contado em `empty_values`.

## 2. Tratamento por formato

Cada coluna de cada formato declara **destino, regra e evidência** (`FORMATS` no conversor, copiado para `mapping` no relatório). Um teste garante que nenhuma coluna fica sem regra.

| Formato | Tipo | Mapeamento principal | Fica só em `raw` |
| --- | --- | --- | --- |
| QuestProbe | série (1 Hz) | `horario` → `timestamp`; `x,y,z` → `self.position`; `nivel`, `exp` → `self.level`, `self.exp`; `quest_id` + `estado_bruto` → `quest_info` | `dlg_tamanho` |
| GravadorQuest | série (amostras + eventos) | idem, mais `seq`, `elapsed_ms` e `event` (amostras não geram evento) | `sp`, `npc_alvo_id`, `dlg_tamanho` |
| LeituraSpots | **fotografia** → **um** estado | `NPC_ATACAVEL` → `npcs` (nome, `npc_id`, posição, `spawn`); `JOGADOR_PT`/`JOGADOR_EXTERNO` → `characters` (pseudônimo, posição) | `SUMMON`, `id` dos jogadores |

**Decisões por campo, todas registradas no relatório:**

- **Coordenada 0 isolada** continua sendo valor observado (`zero_isolado` em ambiguidades).
- **Trio de spawn `(0,0,0)`** com posição atual sem nenhum zero: por padrão, fica **observado**. Só vira desconhecido com `--aceitar-regra spawn_trio_zero`. O relatório informa em quantos casos a regra se aplicaria e em quantos foi aplicada. Isso segue a revisão: o trio só vira `UNKNOWN` se a regra for aceita.
- **`quest_status_bruto` / `estado_bruto`** ficam como `status_bruto`, sem interpretação de etapa.
- **`npc_alvo_id`** não vira `target`, porque o `GameState` guarda o nome do alvo, e `0` não ganha o significado de "sem alvo".
- **`dlg_tamanho`** não vira `quest_dialog`: o tamanho do texto não prova que havia janela de missão aberta.
- **`dead`** fica desconhecido em todas as entidades, porque nenhum CSV informa.
- **`object_id`** fica ausente, porque a LeituraSpots não identifica instâncias.
- **Horário:** é o relógio local do Windows, sem fuso, com resolução de 1 s. No GravadorQuest, `elapsed_ms` separa estados do mesmo segundo.
- **Nomes de jogadores** são substituídos por `jogador_pt_N` / `jogador_externo_N` antes de qualquer saída, inclusive em `raw`. O mesmo nome recebe o mesmo pseudônimo dentro do arquivo, e o mapeamento não é gravado.

## 3. Relatório de perdas e ambiguidades

O arquivo `SAIDA.relatorio.json` usa o formato `l2tools-conversion-report/1` e contém:

- `rows`: lidas, convertidas e recusadas;
- `rejected`: cada linha recusada, com o motivo (número de colunas, texto em coluna numérica, horário inválido, tipo desconhecido);
- `mapping`: a tabela de semântica por campo;
- `empty_values` e `raw_only`;
- `rules`: as regras opcionais, com casos encontrados, se foram aceitas e quantas vezes foram aplicadas;
- `ambiguities`: `zero_isolado`, `xyz_incompleto`, `seq_descontinuo`, `ms_regressivo`, `npc_alvo_id_zero`, `spawn_trio_zero`, `id_zero_jogador`, `spawn_em_jogador` e `sem_horario`;
- `privacy`: quantidade de nomes pseudonimizados.

O terminal mostra um resumo do mesmo relatório.

## 4. Validação no motor atual

| Verificação | Resultado |
| --- | --- |
| `ReplayAdapter` carrega os três replays e expõe `meta` e `record` | `QuestProbeTest`, `RecorderTest`, `SnapshotTest` |
| Observado × desconhecido no `GameState` | posição, nível, EXP e `quest_info` observados; HP, MP, alvo, combate, janela de missão, NPCs e jogadores **desconhecidos** nas séries; na fotografia, `npcs`/`characters` observados, `dead` desconhecido e posição própria desconhecida |
| `hunter.py` (políticas do exp. 024) sobre os replays | todas as decisões são "desconhecido": "NPCs desconhecidos" na série e "posição própria desconhecida" na fotografia. **Nenhuma intenção** é emitida (`test_hunter_never_decides_on_unknown_data`) |
| `ObservationMonitor` sobre o GravadorQuest | posição própria atendida; HP/MP e entidades próximas **não** atendidos; `ready()` falso |
| `l2native replay-info` | contagens por campo e por evento |
| Determinismo | a mesma entrada gera os mesmos bytes de replay e o mesmo relatório |
| Replays antigos (sem `replay_meta`) | continuam carregando |

Testes: `native/tests/test_replay_conversion.py` (21). Fixtures **sintéticas** em `native/fixtures/adrenaline_csv/`, com o cabeçalho, o `;` e o CRLF dos reais, nomes e IDs inventados e linhas inválidas de propósito. Os CSVs reais **não** entram no repositório.

## Execução local com os CSVs reais da Founder

Os replays e os relatórios ficaram só na máquina local; nada disso foi publicado. Contagens agregadas:

| CSV | Linhas | Convertidas | Recusadas | Estados | Observações do relatório |
| --- | --- | --- | --- | --- | --- |
| GravadorQuest (24/10, 23:52) | 311 | 311 | 0 | 311 | `sp`, `npc_alvo_id` e `dlg_tamanho` só em `raw`; `npc_alvo_id=0` em 5 linhas; eventos: 171 atributos, 20 mensagens de sistema, 5 inventário, 1 novo alvo, 1 alvo removido, 1 teleporte, 2 marcos |
| QuestProbe (24/10, 23:13) | 61 | 61 | 0 | 61 | `dlg_tamanho` só em `raw` |
| LeituraSpots (24/10, 22:22) | 66 | 66 | 0 | **1** | 35 NPCs, 21 jogadores (20 nomes distintos, todos pseudonimizados), 10 `SUMMON` só em `raw`; trio `(0,0,0)` em **22** NPCs, mantido observado por padrão; com `--aceitar-regra spawn_trio_zero`, `npcs.spawn` passa a 13 observados e 22 desconhecidos |

`replay-info` nas séries reais confirma a mesma separação: posição, nível, EXP e `quest_info` aparecem como observados em 100% dos estados; HP, MP, alvo, NPCs e jogadores, como desconhecidos em 100%.

## O que este experimento não faz

- Não reconhece personagens conectadas nem lê nada do cliente. A identificação real continua dependendo da validação da PR #18 e da identidade visual (028).
- Não aceita a regra do trio `(0,0,0)` por padrão; essa decisão é da Founder.
- Não interpreta `QuestStatus`, `dlg_tamanho`, `npc_alvo_id` nem o `detalhe` das mensagens de sistema.
- Não inicia engenharia reversa do Adrenaline. O conversor lê apenas CSVs gerados pelos scripts da própria Founder.

## Decisões da Founder

1. Fazer ou não o merge (#20 → #18 → … → `main`).
2. Aceitar ou não a regra `spawn_trio_zero` como padrão.
3. Confirmar a ordem seguinte: 028, identidade visual; 029, HP/MP visual.
