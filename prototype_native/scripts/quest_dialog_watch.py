"""Script Python do proprio l2-tools. Quests continuam manuais."""


def on_tick(ctx):
    if ctx.state.get("quest_chat_visible") is True:
        ctx.note("Quest Chat visivel: precisa de intervencao manual")
        ctx.propose("pause_for_manual_quest_dialog")
    elif ctx.state.get("quest_chat_visible") is False:
        ctx.note("Quest Chat nao visivel")
    else:
        ctx.note("Visibilidade desconhecida: nao assumir ausencia")
