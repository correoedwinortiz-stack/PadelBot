import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURACIÓN ---
load_dotenv()  # Carga variables del archivo .env
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ No se encontró TELEGRAM_TOKEN. ¿Lo pusiste en .env?")


# --- LÓGICA DEL BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    welcome_message = (
        f"👋 ¡Hola, {user.first_name}! Bienvenido a Punto de Oro Bot 🏆\n\n"
        "Soy tu asistente personal para todo lo relacionado con el mundo del pádel profesional.\n\n"
        "🔹 **Ver Partidos:** Explora los partidos de hoy y los torneos activos.\n"
        "🔹 **Configurar Alertas:** Elige a tus jugadores favoritos y recibe notificaciones en tiempo real.\n\n"
        "👇 Usa el menú de abajo para empezar."
    )

    keyboard = [
        [
            InlineKeyboardButton("🎾 Partidos en Vivo", callback_data="live_matches"),
            InlineKeyboardButton("🔔 Mis Alertas", callback_data="my_alerts"),
        ],
        [
            InlineKeyboardButton("📊 Rankings", callback_data="rankings"),
            InlineKeyboardButton("📅 Calendario", callback_data="calendar"),
        ],
        [InlineKeyboardButton("❓ Ayuda", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.effective_message.reply_text(
        welcome_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=f"Has presionado un botón ({query.data}). ¡Esta función se implementará pronto!"
    )


def main() -> None:
    print("Iniciando bot...")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("El bot está en línea. Presiona Ctrl+C para detenerlo.")
    application.run_polling()


if __name__ == "__main__":
    main()
