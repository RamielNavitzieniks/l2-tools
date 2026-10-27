# Contrato: conjunto mínimo de observações antes de um adaptador real

**Origem:** Registro 022 da issue #1, item “Próximos passos”. Ele pede definir explicitamente o que um adaptador precisa observar antes de ser conectado ao cliente: posição XYZ, entidades próximas, HP/MP, estado do alvo, erros/latência, origem e confiança.

**Implementação:** `native/l2native/observation.py` (`ObservationMonitor`). A GUI mostra o resultado no painel **Observação mínima**.

Este documento **não autoriza** nenhuma integração. Ele define o critério de entrada: um adaptador que não cumpre este contrato não alimenta políticas de movimento, combate ou recuperação, mesmo que exista.

## Requisitos

| Requisito | Campos do `GameState` | Por que é necessário |
| --- | --- | --- |
| `posicao_propria` | `self_position` | Base de movimento, rotas, zona e seguimento. |
| `entidades_proximas` | `characters` **e** `npcs` | Alvo e áreas ocupadas. Uma lista vazia observada é diferente de uma lista desconhecida. |
| `hp_mp` | `hp` **e** `mp` | Recuperação, morte e condições das regras. |
| `alvo` | `target` | Combate e confirmação de `select_target`. |
| `proveniencia` | todos os Facts conhecidos | Todo valor conhecido precisa de `source`; `confidence` é obrigatória quando a fonte é probabilística (visão). |
| `saude_do_adaptador` | erros de `observe()` e `latency_ms` | Decisões tomadas sobre dados obsoletos ou intermitentes são piores do que nenhuma decisão. |

## Como é medido

Uma janela das últimas **50** observações é avaliada. Abaixo de **10** amostras, o resultado é **dados insuficientes**.

| Critério | Limite padrão |
| --- | --- |
| Cobertura de cada requisito de campo: fração das observações em que todos os campos do requisito são *conhecidos* (observado ou inferido) | ≥ 90% |
| Proveniência | nenhum valor conhecido sem `source` |
| Taxa de erro de `observe()` (`adapter_error`, exceto `close()`) | ≤ 5% |
| Latência de `observe()`, p95 | ≤ 250 ms |

Cada requisito recebe **atendido**, **não atendido** ou **dados insuficientes**. A prontidão geral (`ready()`) exige todos atendidos.

## Declaração do adaptador × evidência

Cada adaptador declara em `observes()` o que se compromete a observar:

| Adaptador | `observes()` |
| --- | --- |
| `ReplayAdapter` | `None`: não declara; depende do arquivo e é medido pelos dados |
| `MockAdapter` | campos configurados no cenário (`self_position` se houver posição, `characters`, `quest_dialog` e os fatos de `facts_at`) |
| `VisualAdapter` | `{quest_dialog}` |
| `LiveAdapter` | conjunto vazio: não implementado |

A declaração **não basta**. Um requisito só é atendido com evidência na janela. Se o adaptador **não declara** um campo, o requisito fica **não atendido**, mesmo que um valor apareça por acaso.

O motor registra a declaração em `engine_start.observes` e a latência em cada `observation.latency_ms`.

## Situação atual (medida nos testes)

- **Mundo simulado** (`fixtures/route_world.json`): atende `posicao_propria`, `proveniencia` e `saude_do_adaptador`. **Não atende** `hp_mp`, `alvo` e `entidades_proximas` (`test_mock_world_is_not_ready_for_live`).
- **VisualAdapter:** só observa a janela de missão e não atende nenhum requisito de campo.
- **LiveAdapter:** não observa nada.

- **Identidade visual (exp. 028):** o nome da personagem lido por OCR **não é campo do `GameState`** e não conta para nenhum requisito. Ele fica na sessão descoberta (`DiscoveredSession.reading`), com fonte `visual:ocr`, confiança, instante e região. É uma fonte parcial de identidade, não de estado do jogo.

Conclusão: hoje **nenhuma fonte cumpre o contrato**. Isso é coerente com a decisão de não conectar um adaptador real.

## Fora do escopo

Este contrato não diz **como** obter os campos. Ele não autoriza:

- leitura de memória;
- injeção;
- pacotes;
- drivers;
- qualquer mecanismo proprietário descrito no Registro 022.

A escolha de um mecanismo é uma decisão separada da Founder (ver `docs/pesquisa/integracao-live-020.md`).
