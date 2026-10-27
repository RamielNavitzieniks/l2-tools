# Experimento 024: execução simulada das políticas do perfil (27/10/2026)

**Branch:** `experimento/l2tools-politicas-024`, empilhada sobre `experimento/l2tools-perfis-rotas-023` (PR #16), que está sobre a #15 e a #14. **Sem merge.**

**Origem:** o **Registro 021** da issue #1 (*Interface gráfica do L2-Tools Native*). Ele lista limitações e decisões que tinham ficado sem implementação:

- “nenhuma política configurada (cura, buff, ataque, alvo, drop, morte, relaxar) é executada”;
- “quais políticas ganham execução simulada primeiro” (sugestão do doc 021: Relaxar e Busca de alvo no mock);
- “plugins rodam sem sandbox na thread da interface”;
- “validação visual no Windows”.

Tudo é **simulado**. Nenhuma ação chega ao Lineage II. Missões continuam manuais.

## Requisitos e onde foram atendidos

| Ponto do Registro 021 | Implementação | Teste |
| --- | --- | --- |
| Políticas não executam | `policies.PolicyRunner` + `scripts/hunter.py`. Executam, **só no Simulado**: Relaxar (sentar/levantar), Cura própria e Buff (regras do `RuleEngine` → `use_skill`), Ataque (`attack` + regras de ataque), Busca de alvo com Filtro (ignorar, lista “atacar”, só revidar), zona, limites de Z, jogadores próximos e Personagem morreu (“parar” encerra; “ir_para_cidade” pausa para o operador, porque teleporte não tem backend). Valor desconhecido nunca vira decisão | `HunterTest` |
| Explicar por que um alvo foi descartado (nota da seção) | Evento `busca de alvo: descartados`, com o motivo por NPC (filtro, zona, jogador por perto, morto, desconhecido…) | `test_full_hunt_uses_every_policy_and_pauses_for_quest` |
| Mock sem combate | `adapters/simcombat.py`, ligado por um bloco `combat` no cenário: HP/MP com regeneração (maior sentada), NPCs com HP, agressividade, renascimento, alcance, habilidades por ID com custo, cura e dano, morte. Fórmulas **inventadas** e documentadas como tais | `SimCombatTest` |
| Ação “sentar” não existia | `ActionKind.REST` (`sit: bool`) no contrato. Só o Mock a implementa. Replay e Live respondem `unsupported`, e a política não finge ter descansado | `test_rest_without_backend_is_reported_not_faked` |
| Vários NPCs com o mesmo nome e o mesmo ID | `Entity.object_id` opcional (instância, quando a fonte a identifica) e `select_target(object_id=…)` | `SimCombatTest`, `HunterTest` |
| Plugins na thread da interface | Cada `on_record` é cronometrado. Acima de 50 ms gera `plugin_slow`; 3 chamadas lentas seguidas desativam o plugin (estado erro, “travaria a interface”) | `SlowPluginTest` |
| Validação visual no Windows | `docs/validacao/gui-windows-checklist.md`, para a Founder executar | manual |
| GUI | Exemplo **Caçador com políticas (simulado)**: confirma antes de trocar o perfil e guarda o anterior em `profile.anterior.json`. O perfil inteiro vai para o script como `params["profile"]`. NPCs mortos aparecem em cinza no mapa | `test_hunter_example_runs_policies_in_simulation` |

Fixtures sintéticas:

- `fixtures/hunt_world.json`;
- `fixtures/profiles/cacador_simulado.json` (IDs 9000xx e 9200xx fictícios).

## Ordem de decisão (um pedido por ciclo)

1. Personagem morreu.
2. Relaxar: senta abaixo dos limites, fora de combate; levanta acima deles ou ao entrar em combate.
3. Cura própria, depois Buff.
4. Ataque: com alvo vivo, regra de ataque ou `attack`; aproxima se estiver fora do alcance.
5. Busca de alvo: o NPC válido mais próximo.
6. Voltar ao centro da zona.

O tempo das regras é simulado a 500 ms por ciclo (`SIM_TICK_MS`). Na corrida do cenário de exemplo, aparecem em sequência:

- seleção, ataque, Golpe Forte, aproximação e Cura Leve em combate;
- sentar com MP 27% e levantar com HP 100% / MP 82%;
- a pausa de missão no ciclo 40;
- 3 abates no ciclo 63.

## Evidências (Linux/WSL2, Python 3.14.7, PySide6 offscreen)

```text
$ cd native && python -m unittest discover -s tests -t .      # venv com numpy + OpenCV + PySide6
Ran 127 tests ... OK (skipped=1)
$ python3 -m unittest discover -s tests -t .                   # python do sistema, sem numpy/OpenCV
Ran 127 tests ... OK (skipped=9)
$ python -m pyflakes l2native scripts tests plugins             # sem avisos
$ 10x python -m unittest tests.test_gui                         # 10/10 sem falha
$ python -m l2native run --script scripts/hunter.py --adapter mock --scenario fixtures/hunt_world.json --quiet
FIM: stopped (script solicitou parada: concluido: 3 alvos derrotados); ciclos=63
```

## Resultado da CI

A CI da PR #17 rodou no [run 40499702440](https://github.com/LyannaValerie/l2-tools/actions/runs/40499702440) em três ambientes: Ubuntu/Python 3.10, Ubuntu/Python 3.13 e **Windows/Python 3.12**. Os três passaram na primeira execução, e em cada um rodaram 127 testes:

- **sem as dependências opcionais:** 28 pulados;
- **com numpy, OpenCV e PySide6 offscreen:** 1 pulado, o teste de outra plataforma.

## Fora do escopo (continua pendente)

- **Drop:** nenhuma fonte observa itens no chão e não existe ação de coleta no contrato.
- **Cura-buff do grupo:** não há membros do grupo com HP no mock.
- **Ataque:** `aggressive_limit` não é aplicado.
- **Busca de alvo:** `hp_unchanged_s`, `max_level_diff` e `avoid_fighting_others` não são aplicados, porque o mock não fornece nível dos NPCs nem quem combate com quem.
- **Editor:** sem depuração nem breakpoints.
- **Plugins:** continuam sem sandbox; agora só não travam a interface indefinidamente.
- **Windows real:** a GUI ainda não foi validada por uma pessoa (existe o checklist).
- **Formato do combate:** as fórmulas do simulador não representam o jogo. Um caçador “funcional” no mock não prova nada sobre o cliente.

## Decisões da Founder

1. Fazer ou não o merge (#17 → #16 → #15 → #14 → `main`).
2. Executar o checklist do Windows e registrar o resultado.
3. Decidir se a próxima política simulada é Cura-buff do grupo (exige um grupo simulado) ou os critérios restantes da Busca de alvo.
