# L2-Tools — prova de conceito de bot com Python nativo

**Nao e um bot funcional para Lineage II Live.** E um motor independente que carrega arquivos Python `.py` e recebe eventos simulados `JSONL`. Nao usa o interpretador Pascal, nem o Adrenaline.

Com Python 3.11 ou superior, na pasta `prototype_native/`, execute:

```powershell
python runner.py --script scripts/quest_dialog_watch.py --events fixtures/quest_states.jsonl
python -m unittest discover -s tests -v
```

### Contrato

- O script define `on_tick(ctx)` e pode importar bibliotecas Python (OpenCV, OCR etc., se instaladas).
- `ctx.state` contem uma observacao da origem configurada. Nesta prova, vem **somente de um arquivo JSONL**, nao do cliente do jogo.
- `ctx.note(texto)` registra uma observacao.
- `ctx.propose(tipo, **parametros)` registra uma intencao em memoria; **nao a executa no jogo**.
- O exemplo de quest pede **intervencao manual** quando a janela e reportada visivel.
- **Scripts executam Python sem isolamento de seguranca.** Carregue somente arquivos confiaveis: eles tem acesso aos recursos da conta Windows.

### Proximos componentes

Precisamos de uma interface `GameAdapter` com captura de estado e execucao de acoes **disponiveis e autorizadas**. O Adrenaline antes fornecia `NpcList`, `CharList`, coordenadas XYZ, movimentos e quest status. Nada disso e automaticamente fornecido por Python. OCR pode identificar parte da UI, mas nao substitui estado estruturado nem caminho confiavel.

Este prototipo **nao** anexa ao jogo, injeta codigo, intercepta pacotes, burla protecoes, move ou ataca personagens.
