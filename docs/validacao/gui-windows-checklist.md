# Checklist: validação manual da GUI no Windows

**Origem:** Registro 021, *Decisões da Founder*, item “validação visual no Windows”. A CI roda os testes de GUI no Windows apenas em modo offscreen. Esta lista cobre o que só uma pessoa diante da tela pode confirmar.

**Pré-requisitos:**

- Python 3.10 ou superior;
- `pip install -e native[gui]`, ou os pacotes de `requirements-gui.txt`;
- **Defender e proteção em tempo real ligados.** Nada desta lista exige desligá-los, nem rodar binário ou driver de terceiros;
- o Lineage II **não precisa** estar aberto nas seções 1 a 4; as seções 5 e 6 usam o cliente real.

Anote cada item como ✅, ❌ (com o que aconteceu) ou ➖ (não testado). Não anexe capturas que mostrem nomes de terceiros, licenças ou HWID.

## 1. Abertura

| # | Passo | Esperado |
| --- | --- | --- |
| 1.1 | Duplo clique em `native\L2ToolsNative.pyw` | A janela abre **sem console** |
| 1.2 | Ler a barra de estado | “Fonte: Replay · Motor: Ocioso”; nenhum script em execução |
| 1.3 | Fechar e reabrir | O layout volta como estava; o último script reabre **sem executar** |
| 1.4 | Escala de exibição de 125% e 150% (Configurações do Windows) | Textos e tabelas legíveis, sem cortes |

## 2. Exemplos (Arquivo → Exemplos, depois ▶ Iniciar / F5)

| # | Exemplo | Esperado |
| --- | --- | --- |
| 2.1 | Missão manual (replay) | Faixa amarela de intervenção manual; retoma quando a janela é observada fechada |
| 2.2 | Seguir Sarynne (simulado) | A trajetória aparece no mapa; a aba Jogadores mostra Sarynne |
| 2.3 | Patrulha circular (simulado), com Pausar/Retomar/Parar durante a volta | Os botões respondem na hora; a interface não congela |
| 2.4 | Caminhar por rota (simulado) | A rota aparece no mapa; a personagem segue o caminho 1 → 5 → 4 → 7 |
| 2.5 | Caçador com políticas (simulado) | Pede confirmação antes de trocar o perfil. Nos Eventos aparecem “relaxar: sentar”, “cura: Cura Leve”, “ataque: Golpe Forte” e “busca de alvo: descartados”. Encerra com “3 alvos derrotados” |
| 2.6 | Fonte “Desconectado” + Iniciar | Falha com “LiveAdapter não implementado”, sem travar |

## 3. Editor, mapa e configurações

| # | Passo | Esperado |
| --- | --- | --- |
| 3.1 | Alternar Mapa ⇄ Script durante a execução | A sessão e os eventos continuam |
| 3.2 | Editar o script, salvar com Ctrl+S e executar um script com erro proposital | A linha do erro fica destacada |
| 3.3 | Mapa: roda do mouse, arrastar, clique direito → centro da zona | Zoom e movimento suaves; a zona aparece |
| 3.4 | Rota → Nova, cliques no mapa, Desfazer, Salvar como… | Os nós aparecem ligados; o arquivo é salvo; o perfil grava o caminho |
| 3.5 | Visualizar → Camadas do mapa | Cada camada some e volta |
| 3.6 | Configurações → Perfil e políticas | Páginas legíveis; “Ver alterações” lista o que mudou; o catálogo mostra os IDs |

## 4. Plugins

| # | Passo | Esperado |
| --- | --- | --- |
| 4.1 | Instalar `native\plugins\event_counter`, ativar e rodar um exemplo | Pede confirmação; o resumo aparece nos eventos |
| 4.2 | Desativar e remover | Pede confirmação; o plugin some da lista |

## 5. Sessões locais descobertas (LIVE-IDENT-001)

| # | Passo | Esperado |
| --- | --- | --- |
| 5.1 | Sem nenhum cliente aberto, aba **Sessões locais descobertas** | Mensagem “Nenhuma regra…” e lista vazia |
| 5.2 | Abrir **um** cliente do Lineage II, marcar “Mostrar todas as janelas visíveis” | A janela do cliente aparece com executável, PID, HWND, título e classe. Se o executável aparecer como “?” (processo elevado), anotar |
| 5.3 | Selecionar a janela do cliente → **Criar regra desta janela…** e descrever a evidência | Ao desmarcar “Mostrar todas”, só a janela do cliente fica, com “localizada (regra: …)” |
| 5.4 | **Associar…** com o rótulo do personagem | “Perfil associado” mostra o rótulo “(associado pelo operador)”; “Identidade observada” continua **desconhecida**; “Telemetria real” continua **indisponível** |
| 5.5 | Abrir um **segundo** cliente | Duas linhas candidatas; associar o segundo rótulo; o mesmo rótulo não pode ir para as duas |
| 5.6 | Fechar um cliente com “Atualizar a cada 2 s” ligado | A linha some e o evento “janela fechada: associação removida” aparece |
| 5.7 | Fechar e reabrir o L2-Tools | A regra continua; os vínculos **não** voltam sozinhos |
| 5.8 | `python -m l2native windows --all` no terminal | Mesma lista, sem gravar nada |

Para cada item, anote a regra que funcionou (executável/classe), sem títulos com nomes de personagens.

## 6. Identificação visual da personagem (experimento 028)

**Pré-requisitos extras:**

- `pip install -r requirements-ocr.txt`;
- o programa **Tesseract OCR 5** instalado, com o idioma `eng` e o `tesseract.exe` no `PATH`. Sem ele, o item 6.6 deve mostrar "indisponível: TESSERACT_AUSENTE", e isso também é um resultado válido para registrar.

**Privacidade:** não publique capturas, o nome real das personagens nem o título das janelas. No registro, escreva só ✅/❌/➖, o estado mostrado ("compatível", "divergente"…) e o motivo.

A coluna **Quem verificou** separa o que uma pessoa fez diante do cliente real do que só os testes automatizados comprovaram, com imagens sintéticas e janela tkinter. Preencha "pessoa" só no que você executou.

| # | Passo | Esperado | Automatizado (CI) | Quem verificou |
| --- | --- | --- | --- | --- |
| 6.1 | Abrir o cliente **antes** do L2-Tools e logar uma personagem | — | — | |
| 6.2 | Abrir o L2-Tools, aba **Sessões locais descobertas**, “Mostrar todas as janelas” | A janela do cliente aparece, mesmo aberta antes | `test_clients_opened_before_the_tool_are_found` (lista sintética) | |
| 6.3 | **Regras sugeridas…** (ou a regra manual da seção 5) | A sugestão E13 mostra origem e limite e só é adotada depois de “Sim”. Anote se `L2.bin` / `L2UnrealWWindowsViewportWindow` / “Lineage II” casaram | `test_suggested_rule_needs_confirmation` | |
| 6.4 | **Associar…** a um rótulo de personagem | “Perfil associado” preenchido; “Identidade observada” = “ainda não identificada” | `test_button_reads_and_result_lands_on_the_right_row` | |
| 6.5 | **Configurar região do nome…** e marcar o nome da **sua** personagem, não o do chat nem o do alvo | Mensagem “Região do nome salva…”. Anote em que lugar da interface o nome estava e se havia um lugar estável | `test_region_configuration_and_dialog` | |
| 6.6 | Com o jogo em **primeiro plano**, **Identificar personagem** | “identificando…” e depois **compatível**, com confiança e “Lida há”. A janela não congela | `IdentityGuiTest` + `TesseractSyntheticTest` (OCR real, imagens sintéticas) | |
| 6.7 | Comparar com o perfil | Estado coerente com o rótulo associado | idem | |
| 6.8 | Segundo cliente: associar outro rótulo, configurar ou reaproveitar a região e identificar | Cada linha com a sua identidade; uma não altera a outra | `test_two_windows_same_title_keep_their_own_identity` | |
| 6.9 | Divergência intencional: **Associar…** um rótulo errado na janela já identificada | **DIVERGENTE** em vermelho, sem nova leitura | `test_match_mismatch_unknown_and_unbound` | |
| 6.10 | Fechar um cliente (com “Atualizar a cada 2 s”) | A linha some; o evento diz que a identidade observada foi removida | `test_closed_window_and_reused_ids_drop_old_readings` | |
| 6.11 | Fechar e reabrir o L2-Tools com o jogo aberto | As janelas voltam; a identidade observada volta a “ainda não identificada” e exige nova leitura | `test_clients_opened_before_the_tool_are_found` | |
| 6.12 | Identificar com o jogo **minimizado**, **atrás** de outra janela, em segundo plano e, se usar, em **tela cheia** | “indisponível” com `JANELA_MINIMIZADA`, `REGIAO_ENCOBERTA`, `JOGO_SEM_FOCO` ou `CAPTURA_PRETA`; nunca um nome | `Win32CaptureTest` (janela tkinter: minimizada e HWND reutilizado) | |
| 6.13 | Mudar a resolução ou o tamanho da janela do jogo e identificar | “indisponível: LAYOUT_ALTERADO… revise a região” | `test_unavailable_ocr_capture_layout_and_cancel` | |
| 6.14 | Mudar a **escala da interface** do jogo sem mudar a resolução | Anote o resultado. Esta mudança **não** é detectada automaticamente (ver limitações do 028) | — | |
| 6.15 | Durante uma identificação, **Cancelar identificação** | “indisponível: CANCELADA…” em menos de 2 s | `test_worker_keeps_gui_responsive_and_cancel_works` | |
| 6.16 | Depois de “compatível”, conferir a barra de estado e os scripts | Nenhum script iniciou; “Telemetria real” continua indisponível | idem 6.4 | |

**Se não houver** um lugar da interface que mostre sempre o nome da própria personagem, registre isso como limitação. Não use o nome do chat, do alvo nem da lista de jogadores.

## Resultado

Registrar na issue #1, sem dados sensíveis:

- versão do Windows;
- versão do Python;
- escala de exibição;
- os itens ❌ com a descrição do que aconteceu.
