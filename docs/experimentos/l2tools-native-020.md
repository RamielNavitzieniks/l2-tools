# Experimento 020: primeira base executável do L2-Tools Native (27/10/2026)

**Branch:** `experimento/l2tools-native-020`, criada a partir de `experimento/bot-python-nativo-019` (PR #13). Os commits do 019 estão incluídos e o merge desta branch substituiria a PR #13. **Sem merge na `main`.**

## Pergunta

É possível transformar o protótipo da PR #13 em um motor Python nativo com contrato de adaptador, percepção com proveniência, validação de ações, pausa e retomada para missões manuais, além de três scripts reais, tudo verificável sem o jogo?

## O que foi construído

- O código fica em `native/` (pacote `l2native`). Veja a arquitetura e a API em `native/README.md`.
- `prototype_native/` foi **preservado intacto**; seus 3 testes continuam passando.

Origem de cada parte reaproveitada:

| Origem | Destino | O que mudou |
| --- | --- | --- |
| `prototype_native/runner.py` (019) | `l2native/script.py`, `engine.py` | `ctx.propose` passou a ser `bot.actions.*`, com tipos, validação, resultados e pausa; os módulos são isolados por nome único |
| `prototype_native/scripts/quest_dialog_watch.py` (019) | `native/scripts/quest_watch.py` | Pede uma pausa real ao motor; o estado desconhecido continua sem ser tratado como ausência |
| `scripts/python/dialogo_detector.py` (018) | `l2native/vision/` | Vira biblioteca, sem arquivo de resposta nem processo externo; mantém as guardas e o limiar 0.91; adiciona escalas e posição relativa |
| `Teste017_AutofollowSarynne.pas` | `native/scripts/follow_sarynne.py` | Mesmos parâmetros (250/420/270, 3000, Δz 400) e a mesma fórmula do ponto de acompanhamento |
| `Teste015/016` | `native/scripts/circular_patrol.py` | Raio 300, 24 pontos, antecipação de 35 e volta contada no vértice 0 |

## Evidências (executadas neste ambiente: Linux/WSL2, Python 3.14.7)

```text
$ cd native && python -m unittest discover -s tests -t . -v      # com numpy 2.5.4 + opencv-python-headless 5.0.0
Ran 60 tests in 0.241s
OK (skipped=1)        # pulado: test_requires_hwnd_on_windows (somente Windows)

$ python -m unittest discover -s tests -t .                      # sem numpy/opencv
Ran 60 tests in 0.026s
OK (skipped=9)        # 8 testes de visao pulados por falta de dependencia + 1 somente Windows

$ cd prototype_native && python -m unittest discover -s tests
Ran 3 tests ... OK
```

Demonstrações (saídas resumidas):

```text
$ python -m l2native run --script scripts/quest_watch.py --adapter replay --events fixtures/quest_dialog.jsonl
[t0003] intencao  #1 pause {...}
[t0003] resultado #1 pause -> COMPLETED
[t0003] paused    {...'resume_on': 'dialog_closed'}
[t0007] resumed   {'by': 'dialogo observado fechado', 'paused_since': 3}
[t0008] script    visibilidade da janela de missao desconhecida: nada presumido
FIM: stopped (fonte encerrada: fim do arquivo de replay); ciclos=8; resultados={'completed': 1}

$ python -m l2native run --script scripts/follow_sarynne.py --adapter replay --events fixtures/follow_sarynne.jsonl
[t0002] intencao  #1 move_to {'x': 250, 'y': 0, 'z': -3100}
[t0002] resultado #1 move_to -> UNSUPPORTED (adaptador 'replay' nao tem backend validado para move_to)
...
[t0009] intencao  #4 stop {'reason': 'diferenca de altura grande: rota desconhecida'}
FIM: stopped (...); ciclos=9; resultados={'unsupported': 3, 'completed': 1}

$ python -m l2native run --script scripts/circular_patrol.py --adapter mock --scenario fixtures/patrol_world.json --param laps=2
[t0030] paused    {'reason': 'janela de missao visivel: aceitar/entregar manualmente', ...}
[t0035] resumed   {'by': 'dialogo observado fechado', 'paused_since': 30}
[t0035] script    retomando patrulha no ponto 16 (voltas=0)
[t0047] script    voltas completadas: 1
[t0083] script    voltas completadas: 2
FIM: stopped (script solicitou parada: concluido: 2 voltas); ciclos=83; resultados={'dispatched': 50, 'completed': 2}

$ python -m l2native run --script scripts/quest_watch.py --adapter live ; echo $?
FIM: failed (adaptador indisponivel: LiveAdapter nao implementado: ...); ciclos=0; resultados={}
3
```

## Critérios de conclusão da missão

| # | Critério | Evidência |
| --- | --- | --- |
| 1 | Iniciar o motor Python | CLI `python -m l2native run` |
| 2 | Carregar um script externo | `test_script_loading`, demonstrações |
| 3 | Receber uma sequência simulada de estados | Replay JSONL e mock |
| 4 | Produzir decisões coerentes | `test_replay_decisions`, `test_simulated_follow_keeps_distance`, `test_two_laps_in_simulation_with_quest_pause` |
| 5 | Registrar intenções de movimento e pausa | Eventos `intent`/`result`/`paused` no journal |
| 6 | Rejeitar ações não implementadas | `test_unsupported_action_never_reaches_adapter`; LiveAdapter |
| 7 | Processar falhas com rastreabilidade | `FailureTest` (script, adaptador, retorno inválido, `on_stop`) |
| 8 | Executar os testes | 60 testes, acima |
| 9 | Comandos reproduzíveis | `native/README.md` |

## Não comprovado / pendente

- Nenhuma execução contra o cliente Lineage II. O `LiveAdapter` está **não implementado** de propósito; veja `docs/pesquisa/integracao-live-020.md`.
- `WindowClientCapture` (Windows/mss) não foi executado; a CI em Windows cobre apenas a recusa sem HWND.
- Não houve teste com Python 3.10–3.13 neste ambiente; isso fica a cargo da CI (matriz 3.10/3.13 Ubuntu e 3.12 Windows), ainda sem execução registrada aqui.
- Não há modelo real da Quest Chat; a detecção foi validada só com imagens artificiais.
- O mundo simulado não modela terreno nem latência, então a patrulha e o seguimento simulados não provam o comportamento em jogo.

## Decisões que dependem da Founder

1. Fazer ou não o merge (esta PR inclui os commits da #13).
2. Se e como buscar alguma integração real com o Live, dados os riscos de política descritos na pesquisa.
3. Captura de um modelo local da Quest Chat e teste do `VisualAdapter` no Windows.
4. Fonte autorizada de replays reais.
