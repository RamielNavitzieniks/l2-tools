# Experimento 021: interface gráfica do L2-Tools Native (27/10/2026)

**Branch:** `experimento/l2tools-gui-021`, empilhada sobre `experimento/l2tools-native-020` (PR #14). **Sem merge.**

**Origem:** comentários da Founder na issue #1 em 27/10/2026:

- *Registro 020 — Diretriz de produto: interface gráfica desde a primeira entrega* (08:37).
- *UX-001 — Inventário de 14 capturas* (08:42).
- *UX-002 — Editor de scripts integrado e painel de plugins* (08:52).

A diretriz pedia o próximo número livre caso o 020 já existisse; o 020 já existia como implementação, então esta entrega é o **021**. As capturas do Adrenaline não foram vistas nem versionadas; o trabalho partiu só do inventário textual da issue. Não foi copiada arte, ícone nem layout exato do Adrenaline.

## Requisitos e onde foram atendidos

| Requisito (issue) | Implementação | Teste |
| --- | --- | --- |
| GUI desktop PySide6, Core independente, CLI auxiliar | `l2native/gui/`, com `QMainWindow` e `QDockWidget`. O Core não importa Qt | `test_gui.MainWindowTest` |
| Estado "Replay / Simulado / Desconectado", sem fingir conexão | Seletor de fonte e barra de estado; Live = "Desconectado", que falha com "não implementado" | `test_live_source_is_disconnected_and_fails_explicitly` |
| Painel de personagem com campos só quando presentes | `CharacterPanel` (Fact: valor, origem e confiança, ou "desconhecido") | `test_layout_and_honest_initial_state` |
| Mapa XY esquemático, sem geodata, com transformações testáveis | `MapView` + `mapping.MapTransform` (zoom ancorado, enquadramento, clique → ponto → centro da zona) | `MappingTest`, `test_map_pick_sets_zone_center_in_profile` |
| Editor `.py` integrado (Mapa ⇄ Script sem perder sessão/log) | `ScriptEditor`: numeração de linhas, realce de sintaxe, alterações não salvas, abrir/salvar/salvar como/executar, linha do erro, aviso de "sem sandbox" | `test_editor_save_run_and_error_line`, `test_dirty_script_requires_confirmation_before_run` |
| Iniciar/Pausar/Retomar/Parar reais, fora da thread Qt | `Session` (thread de trabalho) + `Engine.request_pause`; eventos por sinais Qt | `SessionTest`, `test_pause_resume_stop_buttons_with_simulation` |
| Intervenção manual da Quest Chat | Faixa de aviso + botão **Intervenção manual concluída** (retomada pelo operador) | `test_manual_intervention_button` |
| Eventos/logs com filtro | `EventLog`: categorias e texto; `print`/stderr do script viram `script_output` | `test_failures_are_visible` |
| Capacidades: Simulação/Disponível/Não suportada/Desconhecida | `CapabilityPanel`; cada página de configuração mostra o backend das ações de que depende | `CapabilityStatusTest`, `ConfigDialogTest` |
| Configurações por seção (UX-001), sem Diversos/Eventos | `profile.SECTIONS` + `ConfigDialog` (árvore + páginas tipadas) | `ProfileTest.test_sections_follow_inventory_without_diversos_eventos` |
| Componente reutilizável de regras tipadas | `rules.Condition/Rule/FilterRule` + `RuleTable`/`FilterTable` (coluna "Agora": verdadeira/falsa/desconhecida) | `RulesTest`, `test_rule_now_column_uses_last_state` |
| Perfis com salvamento, comparação e importação/exportação sem autenticação | `Profile` (`l2tools-profile/1`), recusa campos sensíveis | `ProfileTest` |
| Aceitar diálogos de quest segue manual | Campo fixado "manual" (🔒) e `autostart` desligado e travado | `test_typed_settings_locked_and_roundtrip` |
| Salvar/reabrir layout e caminhos, sem executar | `QSettings` (`gui.ini`), reabre o script e a fonte **sem iniciar** | `test_layout_and_recent_paths_persist_without_autorun` |
| PluginManager separado de scripts | `plugins.py` (manifesto, API 1, permissão `journal:read`, confirmação) + `PluginPanel` + `plugins/event_counter` | `PluginTest`, `PluginPanelTest` |
| Abrir sem console | `L2ToolsNative.pyw`, `l2native-gui` (gui-scripts) | Não testado em Windows real |

## Evidências (executadas neste ambiente: Linux/WSL2, Python 3.14.7, PySide6 6.12.0, plataforma Qt `offscreen`)

```text
$ cd native && python -m unittest discover -s tests -t .      # numpy + OpenCV + PySide6
Ran 94 tests ... OK (skipped=1)        # 1 = teste exclusivo de Windows
$ python -m unittest discover -s tests -t .                   # sem dependencias opcionais
Ran 94 tests ... OK (skipped=22)       # visao (8), GUI (13), Windows (1)
```

CI da PR #15 ([run 40479359526](https://github.com/LyannaValerie/l2-tools/actions/runs/40479359526)): Ubuntu/Python 3.10, Ubuntu/Python 3.13 e **Windows/Python 3.12**. Em cada um, 94 testes passaram: 22 pulados sem as dependências opcionais; com numpy, OpenCV e PySide6 offscreen, só 1 pulado (o teste de outra plataforma). Ou seja, os testes de GUI rodaram também no Windows.

A suíte completa foi repetida 25 vezes seguidas sem falha, depois de corrigir um teste de GUI que dependia de tempo: a patrulha de 5 ms por ciclo podia terminar antes do clique em Pausar.

## Limitações

- A GUI não foi aberta por uma pessoa num Windows real. A CI executou os 13 testes de GUI no Windows, mas apenas offscreen.
- Nenhuma política configurada (cura, buff, ataque, busca de alvo, drop, morte, relaxar) é executada; o editor apenas salva. As páginas dizem isso.
- O painel de Drops não tem fonte de dados em nenhum adaptador.
- O editor não tem depuração, breakpoints nem várias abas.
- Plugins rodam na thread Qt e sem sandbox; um plugin lento atrasa a interface.
- O mapa é um plano XY sintético, sem relevo, obstáculos ou geodata.

## Decisões da Founder

1. Fazer ou não o merge da #14 (020) e desta PR, que é empilhada sobre ela.
2. Validar a GUI no Windows: abrir sem console, alternar Mapa/Script, executar um exemplo e conferir a legibilidade.
3. Escolher quais seções de política ganham execução simulada primeiro (sugestão: Relaxar e Busca de alvo no mock).
4. A numeração: existe um comentário "Registro 020 — Diretriz" e o registro técnico 020; esta entrega usa 021.
