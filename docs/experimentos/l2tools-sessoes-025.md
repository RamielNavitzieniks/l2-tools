# Experimento 025: sessões locais descobertas (bloqueio LIVE-IDENT-001, 27/10/2026)

**Branch:** `experimento/l2tools-sessoes-025`, empilhada sobre `experimento/l2tools-politicas-024` (PR #17). **Sem merge.**

**Origem:** o comentário *Bloqueio LIVE-IDENT-001 — GUI funcional, personagens reais ausentes* da issue #1. A Founder relatou, num Windows 11 real, que a aplicação abre mas não reconhece os personagens do Lineage II. O diagnóstico do comentário está correto: o modo Live não observa nada. Esta entrega implementa o **primeiro incremento** proposto, dentro dos critérios de aceite do próprio comentário.

## O que existe agora

| Item do LIVE-IDENT-001 | Implementação | Teste |
| --- | --- | --- |
| 1. `WindowDiscovery` somente leitura | `discovery.Win32WindowSource`: `EnumWindows`, `IsWindowVisible`, `GetWindowTextW`, `GetClassNameW`, `GetWindowThreadProcessId` e `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `QueryFullProcessImageNameW`. Não envia mensagens, não lê memória, não captura imagem. Quando o Windows nega a consulta do processo (por exemplo, cliente elevado), o executável fica desconhecido, com o motivo | `Win32DiscoveryTest` (janela tkinter real criada pelo teste, só no Windows) |
| Regras configuráveis, sem presumir classe ou processo | `CandidateRule` (executável, trecho de título, classe; todos os critérios preenchidos precisam casar). **Nenhuma regra vem pronta.** O operador cria a regra a partir de uma janela que ele mesmo vê (“Mostrar todas as janelas” → “Criar regra desta janela…”) e precisa descrever a evidência | `RuleTest`, `test_rule_from_observed_window_bind_persist_and_close` |
| 2. Painel “Sessões locais descobertas”, separado dos personagens identificados | Dock próprio, com processo, PID, HWND, título, os quatro estados e “visto há”. Atualização manual ou a cada 2 s | `LiveIdentSessionsGuiTest` |
| 3. `SessionBinding` manual | “Associar…” liga uma janela **candidata** a um rótulo de personagem + perfil local, com origem `operator`. O mesmo rótulo não fica em duas janelas. “Desassociar” desfaz | `test_bind_unbind_and_window_closing` |
| 5. Quatro estados distintos | Janela (“localizada (regra: …)”), Perfil associado (“… (associado pelo operador)”), Identidade observada (sempre “desconhecida: nenhuma sonda de identidade implementada”), Telemetria real (“indisponível: LiveAdapter não implementado”) | `test_candidates_states_and_show_all` |
| Persistir só identificadores não sensíveis | `discovery.json` (`l2tools-discovery/1`) guarda regras e identidades locais. HWND e PID **não** são gravados e são recusados ao carregar, assim como chaves de credencial, licença ou HWID. Ao reabrir, a regra volta e o vínculo não | `test_config_roundtrip_rejects_sensitive_and_volatile_ids` |
| Atualizar quando o processo sai | Janelas que somem saem da lista. O vínculo é removido, com o evento `session_closed`. HWND reaproveitado por outro executável vira uma sessão nova | `test_reused_hwnd_with_other_process_is_a_new_session` |
| Sem cliente: lista vazia e orientação | Mensagens específicas para sem regras, sem janela candidata, falha de enumeração e fora do Windows. `python -m l2native windows [--all] [--config discovery.json]` lista as janelas no terminal; fora do Windows sai com código 3 | `test_messages_without_rules_clients_or_platform` |
| 6. Testes determinísticos | `StaticWindowSource` para enumeração simulada | `tests/test_discovery.py` |

## O que continua indisponível (e a GUI diz isso)

- **Item 4 (`VisualIdentityProbe`, OCR):** não implementado neste incremento. O comentário o trata como opcional e não o inclui nos critérios de aceite. A coluna “Identidade observada” fica sempre desconhecida, e associar uma janela **não** a torna observada.
- **Item 7:** NPCs, jogadores, HP/MP, XYZ e qualquer ação no jogo continuam sem fonte. Nenhuma informação foi simulada como real.
- Uma janela localizada **não** prova personagem logado nem conexão.

## Segurança

O incremento usa apenas consultas de leitura documentadas do Windows sobre janelas visíveis. Não há:

- injeção, hook, leitura de memória remota, driver ou captura de tela;
- envio de mensagens ou cliques;
- nada que exija desligar o Defender.

Os títulos de outras janelas aparecem só localmente, na GUI ou no terminal, e só são gravados se o operador os escolher como trecho de uma regra.

## Validação que cabe à Founder (Windows real)

Os passos estão na seção 5 de `docs/validacao/gui-windows-checklist.md`:

1. Abrir 1 cliente e depois 2.
2. Em “Mostrar todas as janelas”, conferir o executável e a classe da janela do cliente.
3. Criar a regra, descrevendo a evidência.
4. Associar cada janela a um personagem.
5. Fechar um cliente e ver a linha sumir.

Também vale registrar se o executável aparece como desconhecido por elevação.

## Evidências (Linux/WSL2, Python 3.14.7, PySide6 offscreen)

```text
$ cd native && python -m unittest discover -s tests -t .      # venv com numpy + OpenCV + PySide6
Ran 137 tests ... OK (skipped=2)        # Windows: teste de janela real e captura
$ python3 -m unittest discover -s tests -t .                   # sem numpy/OpenCV
Ran 137 tests ... OK (skipped=10)
$ python -m pyflakes l2native scripts tests plugins             # sem avisos
$ 10x python -m unittest tests.test_gui                         # 10/10 sem falha
$ python -m l2native windows                                    # Linux
Descoberta indisponivel: descoberta de janelas so existe no Windows   (codigo 3)
```

O código Win32 **não roda neste ambiente Linux**. A evidência dele vem da CI no Windows (abaixo).

## Resultado da CI

A CI da PR #18 rodou no [run 40504358186](https://github.com/LyannaValerie/l2-tools/actions/runs/40504358186) em três ambientes: Ubuntu/Python 3.10, Ubuntu/Python 3.13 e **Windows/Python 3.12**. Os três passaram na primeira execução, e em cada um rodaram 137 testes:

- **sem as dependências opcionais:** 31 pulados;
- **com numpy, OpenCV e PySide6 offscreen:** 2 pulados (os testes da outra plataforma).

No Windows, `Win32DiscoveryTest.test_enumerates_own_window_with_process_name` **passou** nas duas suítes. O teste cria uma janela tkinter real e confirma quatro coisas:

- a enumeração a encontra pelo título;
- o executável é lido como `python*`;
- uma regra criada a partir dela a seleciona;
- `python -m l2native windows --config` sai com código 0.

Os testes de GUI também abriram a descoberta Win32 real no runner. Isso não substitui a validação com o cliente do Lineage II, que cabe à Founder.

## Decisões da Founder

1. Fazer ou não o merge (#18 → #17 → … → `main`).
2. Executar a seção 5 do checklist com 1 e 2 clientes e informar a regra que funcionou (executável/classe), sem capturas.
3. Decidir se o próximo incremento é o `VisualIdentityProbe` (OCR do nome na área cliente da janela associada, com confiança e estado desconhecido quando a interface estiver oculta).

## Correção após o primeiro teste no Windows (27/10/2026)

Ao abrir a GUI no Windows, a Founder recebeu dois erros:

- `'str' object has no attribute 'label'`, ao trocar a fonte no combo;
- `'str' object has no attribute 'type'`, ao abrir "Adicionar regra".

**Causa:** o PySide6 6.12 devolve como `str` puro os enums que herdam de `str` (`SourceKind`, `ConditionField`) guardados como dado de um `QComboBox`. Os testes não pegaram porque chamavam `set_source` diretamente.

**Correção:** os combos guardam o `.value` e convertem de volta para o enum. Os outros usos de dado de item do Qt na GUI guardam `str` comum e não foram afetados.

**Teste:** `ComboEnumTest` passa pelo próprio combo, como o operador. Sem a correção, reproduz os dois erros.
