# Experimento 028: identificação visual da personagem (27/10/2026)

**Branch:** `experimento/l2tools-identidade-visual-028`, empilhada sobre `experimento/l2tools-replay-historico-027` (PR #20). **Sem merge.**

**Origem:** a especificação do Experimento 028, que segue a ordem 027 → 028 → 029 da revisão da pesquisa 026, e o adendo E13 da PR #19.

**Resultado em uma frase:** o L2-Tools lê o nome da própria personagem por OCR local, numa região configurada da janela escolhida pela operadora, e compara esse nome com a associação manual. Isso funciona **com imagens sintéticas e com o OCR real (Tesseract)**. **Ainda não foi verificado com o cliente Lineage II real.**

> Janela descoberta ≠ personagem identificada ≠ estado do jogo observado ≠ capacidade de executar ações.

## Arquitetura

```text
SessionDiscovery (exp. 025, estendido)        ← enumera; nao captura
   └─ DiscoveredSession  session_id (por execucao), started_at do processo, binding (operador), reading (visual)
        │  a operadora seleciona UMA linha e pede "Identificar personagem"
        ▼
WindowClientCapture.grab_frame(regiao)          ← so a janela escolhida, so a regiao
   confere: janela existe · mesmo PID · nao minimizada · visivel · em primeiro plano · regiao nao encoberta
        ▼
Frame (memoria) + RegionOfInterest (fracoes + tamanho de referencia)
        ▼
VisualIdentityProbe  (3 amostras; cada uma: layout → imagem utilizavel → OCR → um unico nome valido)
        ▼
OcrBackend  (TesseractOcr local | ScriptedOcr nos testes)
        ▼
IdentityReading  (lida / inconclusiva / indisponivel; nome, confianca, instante, regiao, origem visual:ocr)
        ▼
IdentityMatcher.compare(leitura, associacao ATUAL, agora)
        ▼
IdentityObservation  compativel / divergente / observada sem associacao / desconhecida / indisponivel
        ▼
SessionsPanel (coluna "Identidade observada", "Confianca", "Lida ha")
```

| Peça | Arquivo | Observação |
| --- | --- | --- |
| `Frame`, `RegionOfInterest`, `FrameSource`, `VisualObserver`, `blank_reason` | `native/l2native/vision/frames.py` | Contrato **reutilizável** para o HP/MP visual (029): nada aqui é específico de OCR |
| `WindowClientCapture.grab_frame` | `native/l2native/vision/capture.py` | Estende a captura do exp. 018/020. Mantém a exigência de **primeiro plano** |
| `OcrBackend`, `TesseractOcr`, `ScriptedOcr`, `NameRules`, normalização | `native/l2native/ocr.py` | Tesseract opcional, local, sem download |
| `VisualIdentityProbe`, `IdentityReading`, `IdentityMatcher`, `IdentityObservation` | `native/l2native/identity.py` | Leitura e comparação separadas |
| Sessão: `session_id`, `started_at`, `reading`, `apply_reading`, `action_gate`, regra sugerida E13, regiões persistidas | `native/l2native/discovery.py` | **Não há** um segundo sistema de sessões |
| Painel: botões, worker, cancelamento, `RegionDialog` | `native/l2native/gui/sessions.py` | — |

O `LiveAdapter` **não foi alterado**: continua não implementado. A identidade observada **não** entra no `GameState` e não conta para o contrato de observação mínima (`docs/contratos/observacao-minima.md`).

## Contrato da identidade observada

**`IdentityReading`** registra o que foi lido:

- `session_id`;
- `status`: `lida`, `inconclusiva` ou `indisponivel`;
- `name`, presente **só** quando a leitura foi `lida`;
- `confidence`: a menor confiança entre as amostras;
- `samples`;
- `observed_at`;
- `region`;
- `source="visual:ocr"`;
- `engine`;
- `reason`.

**`IdentityObservation`** é a comparação com a associação atual:

| Estado | Quando | Libera ações dependentes da identidade? |
| --- | --- | --- |
| `compativel` (MATCH) | Nome lido igual ao rótulo associado, após NFC e sem diferenciar maiúsculas, e leitura recente | Sim (`action_gate`). Hoje não há nenhuma ação Live |
| `divergente` (MISMATCH) | Nome lido com confiança e claramente diferente do rótulo | **Não, bloqueio explícito** |
| `observada, sem associacao` | Nome lido, mas a janela não tem rótulo | Não |
| `desconhecida` (UNKNOWN) | Sem leitura; OCR inconclusivo; nome **parecido** com o rótulo (distância ≤ 2); leitura com mais de 120 s ("identificação antiga; nova leitura necessária"); leitura de outra sessão | Não |
| `indisponivel` (UNAVAILABLE) | Captura, região ou OCR indisponíveis, ou leitura cancelada | Não |

A comparação é recalculada **a cada exibição**, com a associação atual. Por isso, trocar a associação depois de uma leitura muda o estado na hora (por exemplo, compatível → divergente), sem reaproveitar uma conclusão antiga.

### Regras de leitura (conservadoras)

- **Por amostra:** a região precisa conter exatamente **um** texto com formato de nome:
  - 2 a 16 letras ou dígitos Unicode (`NameRules`, ajustável);
  - confiança ≥ `min_confidence` (padrão 0,80).
- **Recusas por amostra:** dois textos com formato de nome tornam a amostra ambígua, mesmo que o segundo tenha confiança baixa. Símbolos soltos (`|`, `-`) são ignorados.
- **Entre amostras:** as 3 amostras precisam ler o **mesmo** nome. Uma amostra fraca ou divergente torna a leitura inconclusiva.
- **Normalização:** NFC, remoção de caracteres invisíveis e espaços nas pontas. **Não** remove acentos e **não** converte largura total em ASCII, porque isso poderia juntar dois nomes diferentes.

### Por que 0,80 (e o que isso não significa)

Medido com o Tesseract 5.5.3 sobre imagens sintéticas (fonte Hershey do OpenCV, texto claro em fundo escuro):

| Imagem | Leitura | Confiança |
| --- | --- | --- |
| Nome nítido | correta | 0,92 |
| Ruído σ = 25 | correta | 0,84 |
| Baixo contraste | correta | 0,96 |
| Dois nomes ("Mira Kael") | duas palavras corretas | 0,95 / 0,96 → **ambígua** |
| Borrado σ = 1,2 | **errada** ("…ea") | 0,44 |
| Fonte pequena | **errada** | 0,00 |
| Recorte pela metade | **errada** | 0,57 |
| Ruído σ = 60 | várias palavras erradas | ≤ 0,47 |

Nessa amostra, todas as leituras corretas ficaram em 0,84 ou mais, e todas as erradas, em 0,57 ou menos. O limiar 0,80 separa os dois grupos, e os testes `TesseractSyntheticTest` fixam esse comportamento.

**Isso não é uma precisão medida no Lineage II.** A fonte, o fundo e a escala do jogo são outros. O limiar é configurável (`probe_options`) e deve ser revisto com capturas locais da Founder.

## Integração com as sessões

| Requisito | Como |
| --- | --- |
| Identidade pertence à sessão correta | A leitura carrega o `session_id` da sessão que a pediu, que **não** é HWND nem PID. `apply_reading` só grava se essa sessão ainda existe |
| Janela fechada invalida a identidade | A sessão sai da lista junto com a leitura. Um resultado que chegar depois é **descartado** ("Resultado descartado: a janela foi fechada ou mudou") |
| HWND/PID reaproveitado não recupera identidade antiga | `WindowInfo.started_at` vem de `GetProcessTimes`. O mesmo HWND+PID com outro executável **ou** outro instante de início vira sessão nova, com outro `session_id` e sem leitura. A captura confere o PID esperado (`JANELA_REUTILIZADA`) |
| Duas janelas com a mesma classe e o mesmo título | São sessões distintas, por HWND+PID+início e `session_id`. Os testes usam dois clientes "Lineage II" idênticos |
| Trocar de personagem exige nova verificação | A leitura vence em 120 s e passa a "identificação antiga; nova leitura necessária". **Uma troca dentro desse prazo não é detectada** (ver limitações) |
| Trocar a associação exige reavaliar | A comparação é recalculada a cada exibição |
| Divergência bloqueia ações futuras | `SessionDiscovery.action_gate(key)` só libera com identidade **compatível** e recente; com divergência, devolve "BLOQUEADO". Nenhuma ação Live existe hoje |
| Clientes abertos antes do L2-Tools | A enumeração não depende da ordem: o primeiro `scan` encontra janelas de processos iniciados antes. É exatamente o contrário do comportamento relatado do Adrenaline (E12) |
| HWND/PID nunca persistidos | `discovery.json` guarda regras, identidades locais e **regiões por regra** (`identity_regions`), e continua recusando `hwnd`/`pid` |

### Regra sugerida E13

`SUGGESTED_RULES` traz `L2.bin` + `L2UnrealWWindowsViewportWindow` + título contendo "Lineage II", com a origem "PR #19, adendo E13 (observação passiva de 27/10/2026)":

- **Não vale sozinha:**
  - a varredura só **avisa** que uma janela casa com ela;
  - "Regras sugeridas…" mostra a origem e o limite ("vale para a versão do cliente observada naquele dia") e só adota a regra com confirmação explícita;
  - a regra adotada guarda a origem `suggested:E13; confirmada pelo operador em AAAA-MM-DD`.
- **Não substitui as regras manuais:** soma-se a elas e pode ser removida.
- **Não associa nada:** uma janela localizada continua sem perfil e sem identidade até a operadora agir.

## Captura

- **Só a região:**
  - `grab_frame(regiao)` pede ao `mss` apenas o retângulo da região dentro da área cliente;
  - a área cliente inteira só é capturada quando a operadora abre "Configurar região do nome…";
  - essa imagem fica só no diálogo e não é salva.
- **Verificações antes de capturar**, cada uma com o seu motivo:

  | Motivo | Situação |
  | --- | --- |
  | `JANELA_INVALIDA` | a janela foi fechada |
  | `JANELA_REUTILIZADA` | o HWND pertence agora a outro PID |
  | `JANELA_MINIMIZADA` | a janela está minimizada |
  | `JANELA_OCULTA` | a janela não está visível |
  | `JOGO_SEM_FOCO` | o jogo não está em primeiro plano |
  | `REGIAO_ENCOBERTA` | centro e cantos da região não pertencem à janela |

- **Sem alternativa silenciosa:** quando uma verificação falha, nada é capturado.
- **Imagem inútil:** imagem vazia, preta (`CAPTURA_PRETA`, típica de tela cheia exclusiva DirectX) ou uniforme vira "indisponível" **antes** do OCR.
- **Coordenadas:** são de tela e de imagem, nunca do mundo do jogo.
- **Captura antiga:** não é reaproveitada. Cada amostra é uma captura nova, e o resultado registra `observed_at`.

## Região de reconhecimento

- Retângulo em **frações** da área cliente, com o **tamanho de referência** (largura × altura) em que foi marcado.
- **Mudança de tamanho ou proporção:** vira `LAYOUT_ALTERADO… revise a região do nome`. O programa não tenta ler no lugar errado.
- **Região menor que 8 px:** é recusada (`REGIAO_PEQUENA`).
- **Persistência:** uma região por regra de descoberta, em `discovery.json`. Clientes da mesma regra compartilham a região. Clientes com layouts diferentes precisam de regras diferentes.
- **O diálogo orienta a marcar o nome da própria personagem** e proíbe usar o chat, o alvo ou a lista de jogadores.

## GUI

- **Botões novos:**
  - **Identificar personagem**;
  - **Cancelar identificação**;
  - **Configurar região do nome…**;
  - **Limpar observação**;
  - **Regras sugeridas…**.
- **Colunas novas:** **Confiança** e **Lida há**.
- **A leitura começa só por clique.** Não há leitura periódica neste incremento.
- **Worker:** uma `threading.Thread` faz o trabalho e entrega o resultado por sinal Qt à thread da interface.
  - A espera entre amostras é interrompida pelo cancelamento.
  - Ao fechar a janela principal, o worker é cancelado e aguardado.
- **Mensagens:**
  - compatível / DIFERENTE do perfil;
  - "OCR inconclusivo. Motivo: …";
  - "Identificação indisponível. Motivo: …", por exemplo `JANELA_MINIMIZADA`, `REGIAO_NAO_CONFIGURADA` ou `TESSERACT_AUSENTE`;
  - "identificação antiga; nova leitura necessária";
  - "Resultado descartado…".
- **Eventos sem o texto lido:**
  - `identity_observed` leva estado, confiança e motivo, sem o nome;
  - existem também `identity_region_set`, `identity_cleared` e `identity_discarded`.
- **Nenhuma identificação inicia script.** A linha nunca aparece como "conectada", e "Telemetria real" continua "indisponível".

## Testes

São 31 novos, num total de 191:

- `tests/test_identity.py`;
- `IdentityGuiTest` em `tests/test_gui.py`.

| Pedido | Teste |
| --- | --- |
| Nome legível · parcialmente ilegível · vazio · ambíguo · semelhante · Unicode · OCR indisponível · captura inválida | `RecognitionTest` (OCR substituível) e `TesseractSyntheticTest` (**Tesseract real**, imagens sintéticas) |
| Compatível · divergente · desconhecida · associação alterada · duas janelas · mesmo título · janela encerrada · ID reutilizado · leitura desatualizada | `AssociationTest` |
| Botão aciona a leitura · resultado na linha certa · GUI responsiva · cancelamento · motivo da falha · mudança de janela invalida · nenhum script começa | `IdentityGuiTest` |
| Regra sugerida só com confirmação; região persistida sem HWND/PID | `SuggestedRuleAndPersistenceTest`, `test_suggested_rule_needs_confirmation` |
| Janela real no Windows (tkinter): minimizada, PID errado, captura da região em primeiro plano | `Win32CaptureTest`. Se o runner não der foco à janela, o teste é **pulado com o motivo**, nunca dado como sucesso |

**CI:** o workflow passou a instalar `tesseract-ocr` (apt, Ubuntu) e `pytesseract`/`mss` (pip, Ubuntu e Windows). É a única alteração de workflow, e ela tem dois motivos:

- o OCR real roda na CI do Ubuntu;
- a captura real roda na CI do Windows.

No Windows, o programa Tesseract não está instalado, e os testes de OCR real ficam pulados com o motivo.

## Limitações observadas

1. **Sem validação no cliente real.** Nenhuma imagem do Lineage II foi usada. A fonte, o contorno, o fundo e a escala do jogo podem exigir outro pré-processamento, outro limiar ou outro idioma do Tesseract. **Não se declara compatibilidade com o Live.**
2. **Não se sabe se existe** um lugar da interface do jogo que mostre **sempre** o nome da própria personagem. Isso fica para a validação 6.5. Se não existir, o resultado do 028 é essa limitação, e o programa não substitui a leitura pelo chat, pelo alvo nem pela lista de jogadores.
3. **Exige primeiro plano**, como a captura do exp. 018. Um cliente em segundo plano fica "indisponível: JOGO_SEM_FOCO". Contornar isso exigiria `PrintWindow` ou captura DWM, que ficaram fora do escopo.
4. **Tela cheia exclusiva (DirectX)** pode devolver imagem preta, que vira `CAPTURA_PRETA`. Não há contorno.
5. **Escala da interface do jogo** sem mudança de resolução **não é detectada**. A região marcada pode passar a ler o lugar errado, e o resultado tende a "inconclusivo", não a um nome falso, mas isso não está provado. A validação 6.14 registra o comportamento.
6. **Troca de personagem em menos de 120 s** sem nova leitura não é detectada. O prazo é configurável (`IdentityMatcher.max_age_s`).
7. **Nomes com caracteres fora de letras e dígitos** precisam de `NameRules` próprio. O Tesseract `eng` não lê bem outros alfabetos.
8. **DPI:** a captura usa coordenadas físicas (o `mss` ativa a percepção de DPI). Escalas de 125% e 150% no Windows não foram testadas por uma pessoa.

## Conferência do adendo E13 (PR #19, versão `e75f47e`, 27/10 12:39)

Consultei a versão mais recente da PR #19 imediatamente antes de publicar, como pedia a especificação. Ela inclui a correção de 12:39 sobre o fim da observação e o `global.gmap`. **Não alterei a PR #19.**

1. **Tabela de evidências E12/E13: persiste.**
   - O cabeçalho tem 3 colunas (`Ref`, `Fonte`, `O que forneceu`).
   - A linha **E12** tem só 2 células: a coluna "O que forneceu" está vazia.
   - A linha **E13** tem 4 células. A 4ª ("Ordem de abertura necessária para o vínculo conta ↔ cliente. Relato, não verificado…") descreve o E12, não o E13. O texto parece ter sido deslocado de uma linha para a outra.
2. **Horários: o fim foi corrigido; o início, não.**
   - **Fim, corrigido:** a versão `e75f47e` descreve o que houve até as 12:38, incluindo a gravação do `global.gmap` às 12:26:31, o que é coerente com o commit das 12:39. A primeira versão (`81c10e0`, commit das 12:24) já declarava "12:18–12:38", antes de as 12:38 chegarem.
   - **Início, persiste:** a linha E13 declara o início às **12:18**, mas a seção 1.3 registra eventos às **12:17:02** (`AB.exe` iniciado) e às **12:17:15** (cliente iniciado, com conexões às portas 13001 e 6600).
   - Instantes de início de processo podem ser lidos depois do fato, mas conexões TCP observadas às 12:17:15 não. Ou a coleta começou antes das 12:18, ou essas conexões foram vistas mais tarde e receberam o horário de início do processo. O documento não diz qual das duas.
3. **Alcance de "o `AB.exe` não teve nenhuma conexão TCP": persiste.** A conclusão "não há intermediário TCP local" vale só para o que o método via:
   - amostras de `Get-NetTCPConnection` a cada 1,5 s, o que deixa de fora conexões curtas entre duas amostras;
   - só TCP, sem UDP;
   - só o PID do `AB.exe`, sem processos filhos ou auxiliares;
   - só a partir do início efetivo da coleta. As conexões da inicialização do bot (12:17:02) podem ter ficado de fora.

Este experimento usa do E13 apenas os três valores da regra **sugerida**. Ele não depende das hipóteses H9 e H11 e não as trata como comprovadas.

## Privacidade e segurança

- **Sem dados reais no repositório:**
  - nenhuma captura real e nenhum nome real;
  - os nomes dos testes ("Seratonina", "Velina", "Arkhen42", "Lyrae") são fictícios, desenhados em imagens sintéticas.
- **Imagens só em memória:** os frames nunca são gravados nem enviados, e os eventos não levam o texto lido.
- **Somente leitura:**
  - o OCR é local;
  - não há leitura de memória, injeção, driver, pacote, clique nem envio de mensagem ao cliente;
  - o Defender não foi tocado.
- **Ferramenta local de desenvolvimento:** para rodar os testes de OCR real, o Tesseract 5.5.3 foi instalado no WSL desta máquina com `dnf`, a partir dos repositórios oficiais do Fedora. Isso não afeta o Windows.

## Decisões da Founder

1. Fazer ou não o merge (#21 → #20 → … → `main`).
2. Executar a seção 6 do checklist com o cliente real e registrar a região usada, sem capturas nem nomes.
3. Decidir se a exigência de primeiro plano fica, ou se a captura de janelas em segundo plano será estudada à parte.
4. Decidir o próximo experimento: 029, HP/MP visual, que pode reutilizar `vision/frames.py`.
