# L2-Tools Native (experimento 020)

Motor **independente** de automação para Lineage II, com scripts escritos diretamente em Python. Não depende do Adrenaline nem do interpretador Pascal.

> **Estado:** primeira base executável, **somente offline**. O motor roda sobre replays JSONL, sobre um mundo simulado e, opcionalmente, sobre observação visual da janela de missão. **Não há integração com o cliente Lineage II Live.** O `LiveAdapter` existe apenas para declarar que essa integração não está implementada. Não há cliques, injeção, leitura de memória nem pacotes.

Evolui o protótipo `prototype_native/` (experimento 019, PR #13), que fica preservado sem alterações como registro histórico, e integra o detector visual do experimento 018 (PR #12).

## Instalação

Requer Python 3.10 ou superior. O núcleo usa somente a biblioteca padrão.

```powershell
cd native
# opcional: visão (detector por imagem-modelo) e captura de janela no Windows
python -m pip install -r requirements-vision.txt
```

Também pode ser instalado como pacote, com `pip install -e .[vision,capture]`, o que cria o comando `l2native`.

## Execução imediata, sem Lineage II

Na pasta `native/`:

```powershell
# A: observador de missões pausa ao ver a janela e retoma quando ela fecha
python -m l2native run --script scripts/quest_watch.py --adapter replay --events fixtures/quest_dialog.jsonl

# B: seguidor de Sarynne sobre posições gravadas (registra intenções; o replay não executa nada)
python -m l2native run --script scripts/follow_sarynne.py --adapter replay --events fixtures/follow_sarynne.jsonl

# B: seguidor no mundo simulado (a seguidora anda de fato no simulador)
python -m l2native run --script scripts/follow_sarynne.py --adapter mock --scenario fixtures/follow_world.json

# C: patrulha circular simulada, com pausa de missão no meio
python -m l2native run --script scripts/circular_patrol.py --adapter mock --scenario fixtures/patrol_world.json --param laps=2

# LiveAdapter: encerra com código 3 e a mensagem "não implementado"
python -m l2native run --script scripts/quest_watch.py --adapter live
```

Outras opções:

- `--journal saida.jsonl` grava o registro completo: observações, intenções, resultados, erros, pausas e encerramento.
- `--quiet` mostra só o resumo final.
- `--max-ticks N` limita o número de ciclos.
- `--interval S` define os segundos entre ciclos.
- `--param chave=valor` é repassado a `bot.params`. O valor é lido como JSON quando possível.

Códigos de saída: `0` encerramento controlado; `1` falha (script ou adaptador); `2` script inválido; `3` adaptador indisponível.

### Observação visual (opcional)

```powershell
# offline: PNGs locais, um por ciclo
python -m l2native run --script scripts/quest_watch.py --adapter visual --template assets/quest_box.png --image a.png --image b.png
# Windows: área cliente de UMA janela autorizada (HWND), que precisa estar em primeiro plano; duas amostras por ciclo
python -m l2native run --script scripts/quest_watch.py --adapter visual --template assets/quest_box.png --hwnd 123456 --interval 1
```

O modelo `quest_box.png` precisa ser recortado **localmente** do seu cliente, na mesma escala de interface (veja `scripts/assets/README.md` da PR #12). Use `--scale 1.25` (repetível) para aceitar outras escalas. O repositório não traz nenhum modelo real. Os frames ficam só em memória e nada é gravado.

## Testes

```powershell
cd native
python -m unittest discover -s tests -t . -v
```

São 60 testes. Sem `numpy`/`opencv`, os testes de visão são **pulados**, com a mensagem informando o motivo; instale `requirements-vision.txt` para rodar a suíte completa. Os testes não usam rede, tempo real nem o jogo. A CI (`.github/workflows/native-tests.yml`) executa a suíte em Ubuntu (Python 3.10 e 3.13) e em Windows (Python 3.12).

## Arquitetura

```
           +-------------------- Engine (l2native/engine.py) --------------------+
 observe() |  1 observar   2 on_tick(bot)   3 registrar     4 validar   5 executar  | execute()
Adapter -->|  GameState -> script decide -> intenções  -> parâmetros -> só se o   |--> Adapter
           |  (Facts)      (Perception)     (Journal)     + política   adaptador   |
           |                                                           suporta     |
           |  6 registrar resultado   7 pausa/parada/cancelamento   8 próximo ciclo |
           +-----------------------------------------------------------------------+
```

| Módulo | Responsabilidade |
| --- | --- |
| `model.py` | `Fact` (observado / inferido / desconhecido, com `source`, `confidence`, `note`), `Position`, `Entity` e `GameState`. |
| `perception.py` | Consultas de leitura para scripts; o desconhecido nunca vira "falso". |
| `actions.py` | `ActionKind`, `Intent`, `ActionResult`, `ResultStatus` e a validação de parâmetros. |
| `engine.py` | Ciclo, estados (`created`, `running`, `paused`, `stopped`, `failed`), pausa/retomada, cancelamento, política, limites de erro e o objeto `bot`. |
| `script.py` | Carregamento por `importlib` e validação do contrato. |
| `journal.py` | Registro estruturado, em memória e opcionalmente em JSONL. |
| `geometry.py` | Fórmulas portadas dos experimentos 015–017. |
| `adapters/` | `ReplayAdapter`, `MockAdapter`, `VisualAdapter` e `LiveAdapter` (não implementado). |
| `vision/` | `TemplateDetector` (OpenCV) e as capturas `SequenceCapture` / `WindowClientCapture`. |

### Garantias do motor

- Um erro em `on_tick` descarta as intenções daquele ciclo: elas são registradas como `intents_discarded` e nunca executadas. Por padrão, o motor encerra com `failed`; `max_consecutive_script_errors` controla a tolerância.
- Uma ação fora de `adapter.capabilities()` recebe `UNSUPPORTED` **sem chegar ao adaptador**.
- Uma exceção do adaptador, ou um retorno que não seja `ActionResult` da mesma intenção, vira `FAILED`.
- `pause`, `stop` e o cancelamento marcam como `INTERRUPTED` as intenções seguintes do mesmo ciclo.
- Falha de observação: até `max_consecutive_adapter_errors` (padrão 3) falhas seguidas são registradas e toleradas; acima disso, o motor encerra com `failed`. `AdapterUnavailable` encerra na hora.
- Política: `move_to` mais distante que `max_move_distance` (padrão 5000) da posição observada é `REJECTED`.
- `Ctrl+C` e `engine.cancel()` encerram de forma controlada. `on_stop` e `adapter.close()` são sempre chamados.

## API para scripts Python

```python
def on_start(bot): ...          # opcional
def on_tick(bot):               # obrigatório
    if bot.pause_if_quest_dialog():          # missões continuam manuais
        return
    me = bot.perception.position()           # Position ou None (desconhecido)
    leader = bot.perception.character("Sarynne")
    if me and leader and leader.position.known:
        bot.actions.move_to(leader.position.value.x, leader.position.value.y, me.z)
def on_resume(bot): ...         # opcional: ao sair de uma pausa
def on_stop(bot, reason): ...   # opcional
```

`bot` oferece:

- **Percepção.** `bot.perception` traz `quest_dialog_visible()`, `quest_dialog_unknown()`, `position()`, `characters_known()`, `character(nome)`, `fact(campo)` e `.state` (o `GameState` completo, com Facts).
- **Ações.** `bot.actions` traz `move_to`, `move_to_position`, `follow`, `select_target`, `attack`, `use_skill`, `teleport`, `pause` e `stop`. Cada uma só **enfileira** uma intenção.
- **Atalhos.** `bot.pause_for_manual_interaction(reason, resume_on="dialog_closed"|"operator")`, `bot.pause_if_quest_dialog()` e `bot.stop(reason)`.
- **Estado.** `bot.memory` (dicionário preservado entre ciclos e pausas), `bot.params`, `bot.tick`, `bot.last_results` (resultados do ciclo anterior), `bot.capabilities` e `bot.log(msg, **dados)`.

A retomada de uma pausa acontece quando a janela de missão é **observada fechada** em 2 ciclos seguidos; um estado desconhecido zera a contagem. Ela também pode vir do operador, com `engine.request_resume()`. Durante a pausa, `on_tick` não é chamado e `bot.memory` é preservado.

> **Segurança:** scripts são código Python confiável, executado **sem sandbox**, com as permissões do usuário. Carregue somente arquivos seus.

## Contrato GameAdapter

```python
class GameAdapter(Protocol):
    name: str
    def observe(self) -> GameState            # AdapterError (recuperável), SourceExhausted (fim normal), AdapterUnavailable (fatal)
    def capabilities(self) -> frozenset[ActionKind]
    def execute(self, intent: Intent) -> ActionResult   # só recebe intenções válidas e contidas em capabilities()
    def close(self) -> None
```

Valores de `ResultStatus`:

- `completed`: terminou, confirmado pelo adaptador.
- `dispatched`: comando enviado, efeito ainda não verificado.
- `rejected`: parâmetros inválidos ou política de segurança.
- `unsupported`: sem backend validado.
- `interrupted`: não enviada por pausa, parada ou cancelamento.
- `failed`: o adaptador falhou.

Só `completed` e `dispatched` contam como sucesso.

| Adaptador | Observa | Executa |
| --- | --- | --- |
| `ReplayAdapter` | JSONL; campo ausente ou `null` = desconhecido; aceita Facts explícitos (`{"status": "inferred", ...}`) | nada (`unsupported`) |
| `MockAdapter` | mundo simulado: posição própria, waypoints de outras personagens e fatos por ciclo; falhas injetáveis | `move_to` (`dispatched`); o restante, `unsupported` |
| `VisualAdapter` | apenas `quest_dialog`, com a confiança do casamento visual; falha de captura = desconhecido | nada |
| `LiveAdapter` | **não implementado** (`AdapterUnavailable`) | nada |

## Funcionalidades

**Funcionais e testadas offline:**

- motor completo;
- carregamento e validação de scripts;
- replay;
- mundo simulado;
- pausa e retomada de missão;
- autofollow simulado;
- patrulha circular simulada;
- detector visual com imagens sintéticas;
- registro em JSONL;
- CLI.

**Pendentes (não declarar funcionais):**

- qualquer leitura de estado do cliente Live: posição, HP/MP, NPCs, jogadores, inventário, quests;
- qualquer execução no jogo: movimento, alvo, ataque, skill, teleporte;
- detector visual com um modelo real do cliente Kain;
- captura de janela no Windows, implementada mas não executada;
- combate, áreas ocupadas, recuperação e coordenação de grupo.

## Limitações conhecidas

- O mundo simulado anda em linha reta, sem terreno, colisão, latência ou regras do servidor. Uma patrulha "funcional" no mock **não prova** a geometria nem a fluidez no jogo, que eram as questões abertas do experimento 016.
- O `VisualAdapter` só reconhece a janela para a qual existe modelo. Não lê texto (sem OCR) e pode capturar uma imagem preta em DirectX fullscreen; nesse caso, o resultado é desconhecido.
- `bot.tick` vem da fonte. Prazos dos scripts contam ciclos ativos, não segundos.
- Os scripts não são isolados.
- Lineage II proíbe programas de automação não autorizados. Veja `../docs/pesquisa/integracao-live-020.md`.

## Próximos componentes recomendados

1. Gravar replays a partir de observações autorizadas, como a visão ou exportações manuais, e evoluir o formato JSONL com campos de proveniência reais.
2. Validar o `VisualAdapter` no Windows da Founder com um modelo real da Quest Chat: janela aberta e fechada, escalas de interface, fullscreen e janela.
3. Ampliar a percepção visual (barras de HP/MP, alvo) apenas com modelos medidos e sempre com confiança e origem.
4. Fazer o planejador de tarefas e os objetivos (spot → caça → recuperação) consumir o mesmo contrato, ainda no mock.
5. Integração real: somente depois de uma decisão explícita da Founder sobre um mecanismo autorizado.
