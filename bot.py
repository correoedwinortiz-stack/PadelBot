import os
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# Añadimos la URL de la API de pádel
PADEL_API_URL = "https://fantasy-padel-tour-api.onrender.com/api"


# --- LÓGICA DE LA API DE PÁDEL ---


async def get_padel_rankings(gender: str) -> str:
    """
    Obtiene los rankings de la API de Fantasy Padel Tour y les da formato.
    gender puede ser 'male' o 'female'.
    """
    try:
        # Hacemos la petición a la API para obtener el ranking
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{PADEL_API_URL}/players/{gender}")
            response.raise_for_status()  # Esto dará un error si la petición falla (ej: 404, 500)

            players = response.json()

        # Damos formato al texto que enviaremos al usuario
        # Tomamos solo los primeros 10 jugadores
        top_10_players = players[:10]

        # Creamos el mensaje
        gender_title = "Masculino" if gender == "male" else "Femenino"
        message = f"🏆 **Ranking {gender_title} - Top 10** 🏆\n\n"

        for player in top_10_players:
            # Usamos .get() para evitar errores si un campo no existe
            rank = player.get("ranking", "N/A")
            name = player.get("name", "Sin Nombre")
            points = player.get("points", 0)
            message += f"**{rank}.** {name} - `{points}` pts\n"

        return message

    except httpx.RequestError as e:
        print(f"Error al contactar la API de pádel: {e}")
        return "Lo siento, no pude contactar al proveedor de datos de pádel en este momento. Por favor, inténtalo más tarde."
    except Exception as e:
        print(f"Ocurrió un error inesperado al procesar los rankings: {e}")
        return "Ocurrió un error inesperado al obtener los rankings."


# --- LÓGICA DEL BOT DE TELEGRAM ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador para el comando /start. Muestra el menú principal."""
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

    # Si el usuario viene de presionar un botón, editamos el mensaje. Si no, enviamos uno nuevo.
    if update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_message, reply_markup=reply_markup, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            welcome_message, reply_markup=reply_markup, parse_mode="Markdown"
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador principal para todos los botones."""
    query = update.callback_query
    await query.answer()  # Siempre responde al clic primero

    # Dividimos el callback_data para manejar sub-menús, ej: "rankings_male"
    parts = query.data.split("_")
    command = parts[0]

    if command == "start":
        await start(update, context)

    elif command == "show" and parts[1] == "rankings":
        # El usuario presionó "Rankings", mostramos las opciones de género
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
        # El usuario seleccionó un género (male o female)
        gender = parts[1]

        # Mostramos un mensaje de "Cargando..." mientras obtenemos los datos
        await query.edit_message_text(
            text="🔄 Obteniendo los datos del ranking, por favor espera..."
        )

        # Obtenemos y formateamos los datos
        rankings_text = await get_padel_rankings(gender)

        # Botón para volver al menú de rankings
        keyboard = [[InlineKeyboardButton("« Volver", callback_data="show_rankings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Editamos el mensaje con el resultado final
        await query.edit_message_text(
            text=rankings_text, reply_markup=reply_markup, parse_mode="Markdown"
        )

    else:
        # Para botones aún no implementados
        await query.edit_message_text(
            text=f"La función '{query.data}' se implementará pronto. ¡Gracias por tu paciencia!"
        )
        # Añadimos un botón para volver al menú principal
        keyboard = [[InlineKeyboardButton("« Volver al Menú", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"La función '{query.data}' se implementará pronto. ¡Gracias por tu paciencia!",
            reply_markup=reply_markup,
        )


def main() -> None:
    """Inicia el bot."""
    print("Iniciando bot...")
    if not TELEGRAM_TOKEN:
        print(
            "Error: No se encontró el TELEGRAM_TOKEN. Asegúrate de configurarlo en las variables de entorno."
        )
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("El bot está en línea. Escuchando peticiones...")
    application.run_polling()


if __name__ == "__main__":
    main()
