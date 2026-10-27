"""Script A - Observador de missoes (evolucao de prototype_native/scripts/quest_dialog_watch.py).

Escopo vigente: aceitar/entregar missoes e MANUAL. Ao ver a janela de missao,
pede pausa; o motor so retoma quando a janela for observada fechada (ou o
operador retomar). Visibilidade desconhecida nao e tratada como ausencia.
"""


def on_start(bot):
    bot.memory["pausas"] = 0


def on_tick(bot):
    dialog = bot.perception.state.quest_dialog
    if dialog.is_true():
        bot.log("janela de missao visivel: aguardando intervencao manual",
                source=dialog.source, confidence=dialog.confidence)
        bot.pause_for_manual_interaction("janela de missao visivel: aceitar/entregar manualmente")
    elif not dialog.known:
        bot.log("visibilidade da janela de missao desconhecida: nada presumido", detail=dialog.note)


def on_resume(bot):
    bot.memory["pausas"] += 1
    bot.log(f"retomado apos intervencao manual #{bot.memory['pausas']}")
