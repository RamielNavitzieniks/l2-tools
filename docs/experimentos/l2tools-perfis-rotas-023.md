# Experimento 023: perfis versionados, regras, catálogos e rotas (27/10/2026)

**Branch:** `experimento/l2tools-perfis-rotas-023`, empilhada sobre `experimento/l2tools-gui-021` (PR #15), que por sua vez está sobre a #14. **Sem merge.**

**Origem:** a seção *Próximos passos que fazem sentido sem integrar o cliente* do **Registro 022** da issue #1 (*Evidências estáticas sobre arquitetura do Adrenaline*, 27/10/2026). Esta entrega implementa **apenas** os itens do 022. Os próximos passos do 021 não fazem parte dela.

O registro pede para “aprofundar na PR #15”. Como a #15 ainda está em revisão, o trabalho foi para uma PR empilhada com base na branch da #15: se aceita, entra na #15 sem misturar a revisão das duas.

Tudo roda sobre **Replay/Mock**:

- nenhum binário, XML, mapa, GPS, ícone ou dado do Adrenaline foi usado, copiado ou consultado;
- as rotas e os perfis de teste são **sintéticos**;
- missões continuam manuais.

## Requisitos e onde foram atendidos

| Item do Registro 022 | Implementação | Teste |
| --- | --- | --- |
| `CharacterProfile` versionado, com migração | `profile.py`: formato `l2tools-profile/2`, `migrate()` com cadeia de migrações (`v1->v2`), recusa de versão futura ou desconhecida, sem alterar o dicionário de entrada. A GUI grava o perfil migrado e guarda o original como `profile.v1.json` | `ProfileV2Test`, `test_v1_profile_on_disk_is_migrated_with_backup` |
| Catálogo de habilidades | `SkillEntry(name, game_id, confirmed, note)` no perfil. É a **fonte única** de IDs: `Rule` não tem mais `game_id`, que é resolvido pelo nome. “Confirmado” exige ID. A migração v1 move os IDs das regras para o catálogo e acusa IDs conflitantes. Página **Catálogo de habilidades** na configuração; a coluna “ID (catálogo)” das regras mostra o ID, “não confirmado” ou “não catalogada” | `test_skill_catalog_rules`, `test_skill_catalog_page_feeds_rule_id_column` |
| Motor de regras com condições tipadas | `rules.RuleEngine`: prioridade, `delay_ms` (condição verdadeira por um tempo seguido), `interval_ms` (intervalo entre usos), sem ID confirmado = nunca pronta, condição desconhecida = bloqueia. **Só decide; não gera intenção.** O painel **Decisões de regras (sem execução)** marca a escolhida como “seria usada (sem execução)” | `RuleEngineTest`, `test_decision_panel_marks_chosen_rule_without_executing` |
| Catálogo de entidades separado do snapshot | `catalog.EntityCatalog`: primeiro e último ciclo, avistamentos e última posição; NPC identificado pelo `npc_id`, jogador pelo nome. Só em memória: nomes de terceiros não vão para o disco. Painel **Catálogo de entidades** | `EntityCatalogTest`, `test_route_example_feeds_catalog_observation_and_map` |
| Mapa em camadas | `MapView.layers`: grade, trilha, entidades, zona, rota e menor caminho, em **Visualizar → Camadas do mapa** | `test_layers_and_route_editor` |
| Editor de rotas com dados próprios | `routes.RouteGraph` (formato `l2tools-route/1`, campo `provenance` **obrigatório**, Dijkstra por distância XYZ). Menu **Rota**: nova, abrir, salvar como (grava `rota.route_file` no perfil), editar (cada clique no mapa adiciona um nó ligado ao anterior), desfazer o último nó, menor caminho entre nós | `RouteGraphTest`, `test_layers_and_route_editor` |
| Navegação visual sobre mock | `scripts/route_walker.py`: menor caminho do nó mais próximo até `goal`, `move_to` nó a nó com tolerância, ida e volta opcional, pausa na janela de missão, prazo por nó. Exemplo na GUI: **Caminhar por rota (simulado)** | `RouteWalkerTest` |
| Perfis fictícios Fight/Path/Mons/Drop/Party/Relax, serialização e migração | `fixtures/profiles/exemplo_v1.json` e `exemplo_v2.json` (nomes e IDs fictícios, marcados como tal) | `test_v2_fixture_roundtrip_and_sections`, `test_v1_migrates_ids_into_catalog_without_touching_input` |
| Conjunto mínimo de observações antes de um adaptador real | `observation.py` + `docs/contratos/observacao-minima.md`. Os adaptadores declaram `observes()`; o motor registra `latency_ms` e `observes`; painel **Observação mínima** | `ObservationContractTest` |
| `GameAdapter` por capacidades (observação e ação) | `observes()` complementa `capabilities()` no protocolo | `test_adapters_declare_and_engine_journals_latency` |

## Correspondência conceitual com as seções do Adrenaline

A correspondência abaixo se baseia **apenas nos nomes de seção** citados no Registro 022. Nenhum XML foi lido, e o formato do L2-Tools não é compatível nem pretende ser.

| Seção citada no 022 | L2-Tools (`profile.SECTIONS`) | Situação |
| --- | --- | --- |
| General | `geral` | configuração do motor |
| Fight | `ataque` (regras) e catálogo de habilidades | decisão sem execução |
| Path | `rota` + `RouteGraph` + `route_walker.py` | executa no **simulado** |
| Mons | `alvo` + `alvo.filtro` | só configuração |
| Drop | `drop` + `drop.filtro` | só configuração (nenhuma fonte observa drops) |
| Party | `grupo`, `grupo_cura` | seguir líder no simulado/replay (021) |
| Relax | `cura.relaxar` | só configuração |
| Death | `cura.morte` | só configuração |

## Segurança (Registro 022, riscos operacionais)

- Nada deste experimento depende de componente proprietário. Não foram reproduzidos injeção/manual mapping, driver de entrada, proteção/decifragem, roteamento de pacotes nem rotinas contra anticheat.
- **Não desabilite** o Microsoft Defender nem qualquer proteção do sistema para usar o L2-Tools. O L2-Tools não precisa de exclusões, drivers nem executáveis de terceiros.
- Sobre a verificação pedida no 022, que é tarefa da Founder no Windows e não foi executada aqui:
  - conferir a assinatura dos binários proprietários sem executá-los, por exemplo com `Get-AuthenticodeSignature <arquivo>` no PowerShell;
  - confirmar que a proteção em tempo real está ativa (`Get-MpComputerStatus`, campo `RealTimeProtectionEnabled`).
- Não versionar licenças, HWID, chaves, dumps, capturas ou nomes de terceiros. O catálogo de entidades fica só em memória, e os perfis recusam campos de credencial.
- Rotas exigem `provenance`, e o editor só grava pontos desenhados pelo operador. Não importe GPS, mapas ou tiles de terceiros.

## Evidências (executadas neste ambiente: Linux/WSL2, Python 3.14.7, PySide6 offscreen)

```text
$ cd native && python -m unittest discover -s tests -t .      # venv com numpy + OpenCV + PySide6
Ran 117 tests ... OK (skipped=1)        # 1 = teste exclusivo de Windows
$ python3 -m unittest discover -s tests -t .                   # python do sistema, sem numpy/OpenCV
Ran 117 tests ... OK (skipped=9)        # visao (8), Windows (1)
$ python -m pyflakes l2native scripts tests plugins             # sem avisos
$ for i in 1..10: python -m unittest tests.test_gui            # 10/10 sem falha
```

`python -m l2native run --script scripts/route_walker.py --adapter mock --scenario fixtures/route_world.json --param loop=true`:

- percorre o menor caminho 1 → 5 → 4 → 7 (1366 u);
- pausa no ciclo 12 pela janela de missão e retoma ao vê-la fechada;
- volta ao nó 1;
- encerra no ciclo 45 com `destino alcancado: no 1 (trechos=2)`.

## Resultado da CI

A CI da PR #16 rodou no [run 40482834828](https://github.com/LyannaValerie/l2-tools/actions/runs/40482834828) em três ambientes: Ubuntu/Python 3.10, Ubuntu/Python 3.13 e **Windows/Python 3.12**. Os três passaram, e em cada um rodaram 117 testes:

- **sem as dependências opcionais:** 27 pulados;
- **com numpy, OpenCV e PySide6 offscreen:** 1 pulado, o teste de outra plataforma.

A primeira execução ([run 40482646892](https://github.com/LyannaValerie/l2-tools/actions/runs/40482646892)) falhou só no Python 3.10. Nessa versão, `datetime.fromisoformat` não aceita fração de segundo com 1 dígito. A correção normaliza a fração em `state_time_ms`, e o teste agora cobre 1 e 6 dígitos e o timestamp inválido.

## Limitações

- O `RuleEngine` **não executa**. Nenhuma política (cura, buff, ataque) gera intenção. O painel mostra o que *seria* usado e simula o uso apenas para exibir o intervalo.
- A rota é um grafo no plano do mapa esquemático, sem geodata nem obstáculos. Um caminho válido no mock não prova que exista no jogo.
- O clique no mapa usa o Z da posição atual, ou 0 sem posição. Rotas com desníveis precisam de edição manual do arquivo.
- O catálogo de habilidades é preenchido à mão. Não existe fonte confirmada de IDs. Os IDs dos perfis de teste são fictícios (900001…).
- Nenhuma fonte atual cumpre o contrato de observação (ver o documento do contrato).
- A GUI continua sem teste manual num Windows real.

## Decisões da Founder

1. Fazer ou não o merge desta PR na branch da #15, e da #15/#14 na `main`.
2. Confirmar os limites do contrato de observação (90% de cobertura, p95 ≤ 250 ms, erros ≤ 5%) ou ajustá-los.
3. Escolher qual seção ganha execução **simulada** primeiro a partir do `RuleEngine` (por exemplo Relaxar), o que exige ampliar o mock com HP/MP.
4. Fazer a verificação de assinatura dos binários e da proteção do Windows descrita acima.
