# Checklist: validação manual da GUI no Windows

**Origem:** Registro 021, *Decisões da Founder*, item “validação visual no Windows”. A CI roda os testes de GUI no Windows apenas em modo offscreen. Esta lista cobre o que só uma pessoa diante da tela pode confirmar.

**Pré-requisitos:**

- Python 3.10 ou superior;
- `pip install -e native[gui]`, ou os pacotes de `requirements-gui.txt`;
- **Defender e proteção em tempo real ligados.** Nada desta lista exige desligá-los, nem rodar binário ou driver de terceiros;
- o Lineage II **não precisa** estar aberto: tudo usa Replay e Simulado.

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

## Resultado

Registrar na issue #1, sem dados sensíveis:

- versão do Windows;
- versão do Python;
- escala de exibição;
- os itens ❌ com a descrição do que aconteceu.
