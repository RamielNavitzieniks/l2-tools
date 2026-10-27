# L2-Tools Native (experimentos 020, 021, 023 e 024)

Motor **independente** de automação para Lineage II, com scripts escritos diretamente em Python. Não depende do Adrenaline nem do interpretador Pascal.

> **Estado:** primeira base executável com **interface gráfica desktop** (exp. 021), **somente offline**. O motor roda sobre replays JSONL, sobre um mundo simulado e, opcionalmente, sobre observação visual da janela de missão. **Não há integração com o cliente Lineage II Live.** O `LiveAdapter` existe apenas para declarar que essa integração não está implementada. Não há cliques, injeção, leitura de memória nem pacotes.

Evolui o protótipo `prototype_native/` (experimento 019, PR #13), que fica preservado sem alterações como registro histórico, e integra o detector visual do experimento 018 (PR #12).

## Instalação

Requer Python 3.10 ou superior. O núcleo usa somente a biblioteca padrão.

```powershell
cd native
# opcional: visão (detector por imagem-modelo) e captura de janela no Windows
python -m pip install -r requirements-vision.txt
```

Para a interface gráfica:

```powershell
python -m pip install -r requirements-gui.txt   # PySide6-Essentials
```

Também pode ser instalado como pacote, com `pip install -e .[gui,vision,capture]`. Isso cria os comandos `l2native` (CLI) e `l2native-gui`; este último abre a interface sem console no Windows.

## Interface gráfica (principal)

```powershell
python -m l2native.gui          # ou: l2native-gui, ou: python -m l2native gui
```

No Windows, um duplo clique em `L2ToolsNative.pyw` abre a interface sem console.

Para ver funcionando sem o jogo: escolha **Arquivo → Exemplos** e depois **▶ Iniciar** (F5). Carregar um exemplo só abre o script e a fonte de dados; nada executa sem o clique.

| Área | Conteúdo |
| --- | --- |
| Barra superior | Fonte (**Replay**, **Simulado** ou **Desconectado**; "Desconectado" é o Live, não implementado), Iniciar, Pausar, Retomar, Parar e **Intervenção manual concluída** (habilitada só em pausa de missão). |
| Centro, aba **Mapa** | Plano XY esquemático: trajetória, personagem, outras personagens, NPCs, destino pedido e zona de combate. Roda do mouse dá zoom; arrastar move; um clique escolhe um ponto; o menu de contexto define o centro da zona. Sem geodata e sem mapa raster. |
| Centro, aba **Script** | Editor `.py` com numeração de linhas, realce de sintaxe e marcação de alterações não salvas. Abrir, salvar, salvar como e executar. A linha do erro fica destacada e o estado de execução é mostrado. |
| Esquerda | **Sessão/Personagem**: nível, HP, MP, EXP, XYZ, alvo, combate e janela de missão, com origem e confiança, ou "desconhecido". **Plugins**. |
| Direita | **Alvo**, **Jogadores**, **NPCs** e **Drops** (a lista vazia observada é diferente da lista desconhecida) e **Capacidades do adaptador** (Simulação / Disponível / Não suportada / Desconhecida). |
| Inferior | **Eventos**: hora, ciclo, evento e detalhe, com filtros por categoria e por texto. |
| Configurações → Perfil e políticas | Árvore de seções do inventário UX-001: Geral, Cura própria (Relaxar, Personagem morreu), Buff próprio, Cura-buff do grupo, Configurações do grupo, Ataque, Busca de alvo (Filtro), Zona de combate, Rota e Drop (Filtro), mais o **Catálogo de habilidades** (fonte única dos IDs, casados pelo nome da regra). Usa um editor de regras reutilizável com condições tipadas e colunas "ID (catálogo)" e "Agora" (verdadeira, falsa ou desconhecida). Também importa e exporta o perfil e mostra as alterações. Perfis `v1` são migrados ao carregar. |
| Inferior, outras abas (exp. 023) | **Decisões de regras (sem execução)**: o que o `RuleEngine` escolheria agora, por seção ("seria usada (sem execução)", aguardando atraso, em intervalo, sem ID confirmado, desconhecida). **Catálogo de entidades**: jogadores e NPCs já vistos na sessão (só em memória). |
| Direita, aba **Observação mínima** (exp. 023) | Prontidão da fonte em relação ao contrato de `../docs/contratos/observacao-minima.md`: atendido, não atendido ou dados insuficientes, com o que o adaptador declara observar. |
| Menu **Rota** (exp. 023) | Nova, abrir, salvar como, **Editar rota** (cada clique no mapa adiciona um nó ligado ao anterior), desfazer o último nó, menor caminho entre nós. Visualizar → **Camadas do mapa** liga e desliga grade, trilha, entidades, zona, rota e caminho. |

Regras da interface:

- O motor roda numa thread de trabalho e a interface recebe os eventos por sinais Qt.
- Os controles chamam o **mesmo Core** do CLI.
- A pausa do operador congela o replay, sem consumir linhas.
- Layout, último script e última fonte são salvos em `%APPDATA%\l2tools\` (ou `~/.config/l2tools/`) e reabertos **sem executar**.
- O perfil (`profile.json`) nunca aceita campos de credencial.
- Cada página de configuração mostra se existe backend. Hoje, quase tudo aparece como **Não suportada**, e é verdade: configurar não torna a função executável.

### Plugins

Plugins são diferentes de scripts: um script é a estratégia executada pelo motor; um plugin estende o aplicativo por hooks.

- **Contrato (API 1):** uma pasta com `plugin.json` (`id`, `name`, `version`, `api_version`, `entry`, `permissions`) e um módulo com `activate(api)`, mais `deactivate()` e `on_record(record)` opcionais.
- **Permissões:** a única permissão existente é `journal:read`, que dá leitura dos eventos. Nenhuma permissão dá acesso ao jogo, e manifestos com permissões desconhecidas aparecem como **incompatíveis**.
- **Confirmação:** instalar (copiar para a pasta gerenciada), ativar e remover pedem confirmação explícita. Nada é ativado ao abrir o programa e nada é baixado.
- **Exemplo:** `plugins/event_counter/` conta os eventos e registra um resumo ao final.

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

# D: caminhar pelo menor caminho de uma rota própria (fixtures/route_demo.json), ida e volta
python -m l2native run --script scripts/route_walker.py --adapter mock --scenario fixtures/route_world.json --param goal=7 --param loop=true

# E: caçador com as políticas do perfil (Relaxar, Cura, Ataque, Busca de alvo, Zona, Morte) em combate SIMULADO
python -m l2native run --script scripts/hunter.py --adapter mock --scenario fixtures/hunt_world.json

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

São 127 testes. Sem `numpy`/`opencv` ou sem `PySide6`, os testes de visão e de GUI são **pulados**, com a mensagem informando o motivo. Para a suíte completa, instale `requirements-vision.txt` e `requirements-gui.txt`. A GUI é testada fora da tela, com `QT_QPA_PLATFORM=offscreen`, que o próprio teste define. Os testes não usam rede nem o jogo. A CI (`.github/workflows/native-tests.yml`) executa a suíte em Ubuntu (Python 3.10 e 3.13) e em Windows (Python 3.12).

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
| `adapters/` | `ReplayAdapter`, `MockAdapter` (com combate simulado opcional em `simcombat.py`), `VisualAdapter` e `LiveAdapter` (não implementado). |
| `vision/` | `TemplateDetector` (OpenCV) e as capturas `SequenceCapture` / `WindowClientCapture`. |
| `session.py` | Motor numa thread de trabalho, com estados (ocioso, executando, pausado, parando, parado, falhou) e eventos para a GUI. |
| `rules.py` | `Condition` e `Rule` tipadas, com avaliação em três valores, `FilterRule` e `RuleEngine` (prioridade, atraso, intervalo; só decide, não executa). |
| `profile.py` | Seções tipadas (UX-001), perfil por personagem versionado (`l2tools-profile/2`, migração da v1), catálogo de habilidades, importação e exportação sem credenciais, e comparação de alterações. |
| `catalog.py` | Catálogo de entidades vistas na sessão, separado do `GameState` do ciclo. |
| `routes.py` | `RouteGraph` (`l2tools-route/1`, `provenance` obrigatória) e menor caminho. |
| `policies.py` | `PolicyRunner`: executa as seções do perfil como intenções (exp. 024); só o Simulado tem backend. |
| `observation.py` | Conjunto mínimo de observações e `ObservationMonitor` (cobertura, proveniência, erros e latência). |
| `plugins.py` | `PluginManager`: manifesto, API versionada, permissões e ciclo de vida com confirmação. |
| `mapping.py` | Transformação mundo ↔ tela do mapa XY (zoom ancorado, enquadramento, grade). |
| `gui/` | PySide6: `MainWindow`, `MapView`, `ScriptEditor`, painéis e `ConfigDialog`. |

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
- **Ações.** `bot.actions` traz `move_to`, `move_to_position`, `follow`, `select_target(name|npc_id|object_id)`, `attack`, `use_skill`, `teleport`, `rest(sit)`, `pause` e `stop`. Cada uma só **enfileira** uma intenção.
- **Atalhos.** `bot.pause_for_manual_interaction(reason, resume_on="dialog_closed"|"operator")`, `bot.pause_if_quest_dialog()` e `bot.stop(reason)`.
- **Estado.** `bot.memory` (dicionário preservado entre ciclos e pausas), `bot.params`, `bot.tick`, `bot.last_results` (resultados do ciclo anterior), `bot.capabilities` e `bot.log(msg, **dados)`.

A retomada de uma pausa acontece quando a janela de missão é **observada fechada** em 2 ciclos seguidos; um estado desconhecido zera a contagem. Ela também pode vir do operador, com `engine.request_resume()`. Durante a pausa, `on_tick` não é chamado e `bot.memory` é preservado.

> **Segurança:** scripts são código Python confiável, executado **sem sandbox**, com as permissões do usuário. Carregue somente arquivos seus.

## Contrato GameAdapter

```python
class GameAdapter(Protocol):
    name: str
    def observe(self) -> GameState            # AdapterError (recuperável), SourceExhausted (fim normal), AdapterUnavailable (fatal)
    def observes(self) -> frozenset[str] | None   # campos que se compromete a observar; None = não declara
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
| `MockAdapter` | mundo simulado: posição própria, waypoints de outras personagens e fatos por ciclo; falhas injetáveis. Com o bloco `combat`: HP/MP, NPCs (com `object_id`), alvo e combate | `move_to` (`dispatched`). Com `combat`: `select_target`, `attack`, `use_skill` e `rest`, com fórmulas inventadas. O restante fica `unsupported` |
| `VisualAdapter` | apenas `quest_dialog`, com a confiança do casamento visual; falha de captura = desconhecido | nada |
| `LiveAdapter` | **não implementado** (`AdapterUnavailable`) | nada |

## Funcionalidades

**Funcionais e testadas offline:**

- interface gráfica, com fontes Replay e Simulado;
- editor de scripts integrado à interface;
- perfis versionados com migração, regras, filtros e catálogo de habilidades (configuração; o `RuleEngine` só mostra a decisão);
- catálogo de entidades, contrato de observação mínima e mapa em camadas;
- editor de rotas e caminhada pelo menor caminho no simulado;
- execução simulada das políticas Relaxar, Cura própria, Buff, Ataque, Busca de alvo (com Filtro), Zona e Personagem morreu (`hunter.py`);
- desativação automática de plugins lentos (> 50 ms em 3 chamadas seguidas);
- gerenciador de plugins, com plugin de exemplo;
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
- combate, áreas ocupadas, recuperação e coordenação de grupo;
- execução das políticas **fora do Simulado**, e de Drop e Cura-buff do grupo mesmo no Simulado;
- qualquer fonte que cumpra o contrato de observação mínima: nenhuma cumpre hoje;
- GUI aberta em Windows real por uma pessoa: a CI só testa offscreen. Ver `../docs/validacao/gui-windows-checklist.md`;
- depuração passo a passo, breakpoints e várias abas de arquivo no editor.

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
