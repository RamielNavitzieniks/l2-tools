# Pesquisa limitada: integração do L2-Tools Native com o Lineage II Live (27/10/2026)

**Natureza:** pesquisa documental. **Nenhuma ação foi executada em servidor, cliente ou conta.** Nada aqui autoriza a automação no Live.

## Legenda

- **Demonstrado:** existe evidência de execução neste repositório.
- **Hipótese:** plausível, mas sem teste.
- **Inexistente:** não há componente nem evidência.

## 1. Observação visual da interface

| Item | Classificação |
| --- | --- |
| Detector por imagem-modelo (OpenCV `TM_CCOEFF_NORMED`, limiar 0.91), com imagens sintéticas | **Demonstrado offline**: `native/tests/test_vision.py` e exp. 018 |
| Captura da área cliente de uma janela autorizada (`mss`, Windows) | **Hipótese**: código pronto, nunca executado com o cliente; pode capturar imagem preta em DirectX fullscreen |
| Reconhecer a Quest Chat do Kain com um modelo real | **Hipótese**: falta o recorte local e o teste com a janela aberta e fechada |
| HP/MP, alvo, NPCs e posição por visão | **Inexistente**: exigiria modelos ou OCR calibrados; a posição XYZ não aparece na interface de forma confiável |

A observação visual é o único caminho **não invasivo** já implementado: só lê pixels de uma janela escolhida pelo operador. O estado que ela fornece é parcial e cada valor carrega confiança.

## 2. APIs oficiais

Nenhuma API pública oficial da NCSoft/PlayNC para ler estado ou enviar comandos ao Lineage II Live foi encontrada nesta pesquisa (**inexistente até prova em contrário**). Um representante da NCSoft, em post de fórum de cerca de cinco anos atrás, afirma que automação **além do auto-hunting e da pesca embutidos no jogo** é proibida, incluindo macros de teclado ([devtrackers](https://devtrackers.gg/lineageii/p/fc373641-some-info-about-dragon-shirt-life-stones)).

O auto-hunting **nativo** do cliente (segundo cobertura jornalística, introduzido no update Fafurion; fonte primária não conferida) é a forma de automação que esse post reconhece como permitida. A disponibilidade e o alcance desse recurso no Live NA de 2026 **não foram verificados**.

## 3. Projetos independentes (somente referência arquitetural)

| Projeto | Arquitetura | Alvo | Situação |
| --- | --- | --- | --- |
| [L2Net](https://github.com/L2-Net/L2Net) | C#; modo OOG (cliente próprio de protocolo) e IG (túnel/proxy entre cliente e servidor); linguagem de script própria | O README restringe a servidores **L2J**; changelog até High Five/GoD; último commit em 2018 | Incompatível comprovadamente com o Live atual (fora de escopo pelo próprio projeto) |
| [xarkes/L2Bot](https://github.com/xarkes/L2Bot) | C++/Qt + DLL injetada no processo do cliente | L2 Classic 1.5/3.0; "proof of concept" | Usa injeção: **fora do escopo** desta missão |
| [Ariakan79/L2Bot](https://github.com/Ariakan79/L2Bot) | Node.js | High Five | Protocolo antigo; não presumir compatibilidade |

Lição arquitetural aproveitada: todos separam **fonte de estado** de **lógica de decisão**. No L2-Tools Native, isso corresponde ao `GameAdapter` × script. Nenhum protocolo, opcode ou estrutura desses projetos foi copiado ou presumido.

## 4. Estado estruturado

O Adrenaline fornecia `User`, `NpcList`, `CharList`, `QuestStatus` e eventos `OnAction` (registros 008–010 da issue #1). Fora dele, as únicas formas de obter esse estado seriam:

1. Ler a memória do processo.
2. Interceptar ou decifrar pacotes.
3. Injetar código no cliente.
4. Usar um cliente de protocolo próprio.

Todas são **invasivas**, provavelmente violam as regras do serviço e não foram pesquisadas nem implementadas.

**O `LiveAdapter` permanece inexistente de propósito.** Hoje, os dados estruturados reais só podem vir de exportações manuais (CSV/JSONL produzidos pela Founder com ferramentas que ela já usa), convertidas para o formato de replay.

## 5. Compatibilidade do cliente atual

- O Live foi reformulado (Quest Chat, novembro de 2023; registro 007) e continua recebendo atualizações. Em 13/10/2026, houve uma cross-server zone e reformulação de classes de anões, segundo a [MassivelyOP](https://massivelyop.com/2026/10/13/lineage-ii-opens-a-cross-server-realm-on-live-revamps-dwarf-classes-on-aden-wolf-and-samurai/).
- Protocolos e IDs de High Five, Classic, Essence e L2J **não devem** ser presumidos válidos no Live NA/Kain. As tabelas de sysmsg usadas no registro 010 continuam "não comprovadas".

## 6. Política e risco

A política operacional da PlayNC para o Lineage II (página coreana: [plaync.com/policy/operation/lineage2](https://www.plaync.com/policy/operation/lineage2)) classifica como violação:

- usar programas que permitem realizar, sem operação do usuário, ações que exigiriam operação direta;
- produzir ou distribuir esses programas;
- modificar o cliente;
- jogar de forma repetitiva e difícil para um humano.

As punições chegam ao banimento permanente da conta, com bloqueio de IP. A versão NA dos termos não foi lida nesta pesquisa e precisa ser conferida pela Founder.

**Consequência para o projeto:** o motor offline, os replays, a simulação e a observação passiva por imagem não enviam nada ao jogo. Qualquer backend que **execute** ações no Live implica risco de punição da conta e depende de uma decisão explícita da Founder.

## 7. Decisões pendentes da Founder

1. Se e como avançar para alguma execução no Live, considerando as regras acima. Até lá, `LiveAdapter` continua não implementado.
2. Autorizar a gravação local de um modelo real da Quest Chat e o teste do `VisualAdapter` no Windows.
3. Definir a fonte de replays reais: quais ferramentas e campos podem ser exportados de forma autorizada e sem dados de terceiros.
