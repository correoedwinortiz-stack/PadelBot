from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


from aiohttp import web  # Usamos aiohttp en lugar de Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURACIÓN (sin cambios ) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PADEL_API_URL = "https://fantasy-padel-tour-api.onrender.com/api"


# --- LÓGICA DE LA API DE PÁDEL (sin cambios ) ---
async def get_padel_rankings(gender: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{PADEL_API_URL}/players/{gender}")
            response.raise_for_status()
            players = response.json()
        top_10_players = players[:10]
        gender_title = "Masculino" if gender == "male" else "Femenino"
        message = f"🏆 **Ranking {gender_title} - Top 10** 🏆\n\n"
        for player in top_10_players:
            rank = player.get("ranking", "N/A")
            name = player.get("name", "Sin Nombre")
            points = player.get("points", 0)
            message += f"**{rank}.** {name} - `{points}` pts\n"
        return message
    except Exception as e:
        print(f"Error al obtener rankings: {e}")
        return "Lo siento, ocurrió un error al obtener los rankings."


# --- LÓGICA DEL BOT DE TELEGRAM (sin cambios) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_message = (
        f"👋 ¡Hola, {user.first_name}! Bienvenido a Punto de Oro Bot 🏆\n\n"
        "Soy tu asistente personal para todo lo relacionado con el mundo del pádel profesional.\n\n"
        "👇 Usa el menú de abajo para empezar."
    )
    keyboard = [
        [
            InlineKeyboardButton("🎾 Partidos en Vivo", callback_data="live_matches"),
            InlineKeyboardButton("🔔 Mis Alertas", callback_data="my_alerts"),
        ],
        [
            InlineKeyboardButton("📊 Rankings", callback_data="show_rankings"),
            InlineKeyboardButton("📅 Calendario", callback_data="calendar"),
        ],
        [InlineKeyboardButton("❓ Ayuda", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_message, reply_markup=reply_markup, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            welcome_message, reply_markup=reply_markup, parse_mode="Markdown"
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    command = parts[0]
    if command == "start":
        await start(update, context)
    elif command == "show" and parts[1] == "rankings":
        keyboard = [
            [
                InlineKeyboardButton("🚹 Masculino", callback_data="rankings_male"),
                InlineKeyboardButton("🚺 Femenino", callback_data="rankings_female"),
            ],
            [InlineKeyboardButton("« Volver al Menú", callback_data="start")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="Selecciona el ranking que quieres ver:", reply_markup=reply_markup
        )
    elif command == "rankings":
        gender = parts[1]
        await query.edit_message_text(
            text="🔄 Obteniendo los datos del ranking, por favor espera..."
        )
        rankings_text = await get_padel_rankings(gender)
        keyboard = [[InlineKeyboardButton("« Volver", callback_data="show_rankings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=rankings_text, reply_markup=reply_markup, parse_mode="Markdown"
        )
    else:
        keyboard = [[InlineKeyboardButton("« Volver al Menú", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"La función '{query.data}' se implementará pronto.",
            reply_markup=reply_markup,
        )


# --- NUEVA ESTRUCTURA PRINCIPAL ASÍNCRONA ---


async def main():
    """Configura e inicia el bot y el servidor web de forma concurrente."""

    # --- Configuración del Bot de Telegram ---
    if not TELEGRAM_TOKEN:
        print("Error: No se encontró el TELEGRAM_TOKEN.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # --- Configuración del Servidor Web aiohttp ---
    app = web.Application()

    # Ruta principal que Render verificará
    async def health_check(request):
        return web.Response(text="El bot está activo y escuchando.")

    app.router.add_get("/", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    # Render nos da el puerto a través de la variable de entorno PORT
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)

    # --- Ejecución Concurrente ---
    print("Iniciando servidor web y bot de Telegram...")

    # Inicia el bot (sin bloquear) y el servidor web
    await asyncio.gather(application.run_polling(), site.start())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot detenido.")
