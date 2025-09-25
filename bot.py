from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


import os
import httpx
import asyncio
from flask import Flask
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURACIÓN ---
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
    except httpx.RequestError as e:
        print(f"Error al contactar la API de pádel: {e}")
        return "Lo siento, no pude contactar al proveedor de datos de pádel en este momento."
    except Exception as e:
        print(f"Ocurrió un error inesperado al procesar los rankings: {e}")
        return "Ocurrió un error inesperado al obtener los rankings."


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


# --- NUEVA PARTE: EL SERVIDOR WEB FALSO ---
app = Flask(__name__)


@app.route("/")
def index():
    # Esta es la "página principal" que Render verá.
    # Simplemente devuelve un mensaje para confirmar que el servidor está vivo.
    return "El servidor web está activo, pero el bot se ejecuta en segundo plano."


def run_flask_app():
    # Ejecuta el servidor Flask en el puerto que Render nos asigne.
    # Render asigna el puerto a través de la variable de entorno PORT.
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# --- FUNCIÓN PRINCIPAL MODIFICADA ---
def main() -> None:
    """Inicia el bot y el servidor web."""
    print("Iniciando bot...")
    if not TELEGRAM_TOKEN:
        print("Error: No se encontró el TELEGRAM_TOKEN.")
        return

    # Inicia el servidor Flask en un hilo separado
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    print("Servidor web falso iniciado en un hilo.")

    # Configura y ejecuta el bot de Telegram
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("El bot está en línea. Escuchando peticiones...")
    # Usamos run_polling en el hilo principal
    application.run_polling()


if __name__ == "__main__":
    main()
