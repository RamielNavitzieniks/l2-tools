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

## Resultado

Registrar na issue #1, sem dados sensíveis:

- versão do Windows;
- versão do Python;
- escala de exibição;
- os itens ❌ com a descrição do que aconteceu.
