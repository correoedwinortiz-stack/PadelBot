import os
from dotenv import load_dotenv
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from datetime import datetime, timedelta, date
import asyncpg
import asyncio


# --- CONFIGURACIÓN ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PADEL_API_KEY = os.getenv("PADEL_API_KEY")

if not TELEGRAM_TOKEN or not PADEL_API_KEY:
    raise ValueError(
        "❌ Asegúrate de que TELEGRAM_TOKEN y PADEL_API_KEY estén en tu archivo .env"
    )

# --- ¡LA CORRECCIÓN DEFINITIVA! ---
# Usamos la URL correcta del panel de control de la API
PADEL_API_URL = "https://en.fantasypadeltour.com/api"


# Variables de caché en memoria
TOURNAMENTS_CACHE = None
TOURNAMENTS_CACHE_TIME = None
CACHE_DURATION = timedelta(minutes=60)  # ⏳ refrescar cada 60 minutos

# --- Caché de partidos en vivo ---
LIVE_MATCHES_CACHE = {}
CACHE_DURATION_MATCHES = timedelta(minutes=2)  # ⏳ refresco cada 2 minutos

# --- Caché de últimos resultados ---
LAST_RESULTS_CACHE = None
LAST_RESULTS_CACHE_TIME = None
CACHE_DURATION_RESULTS = timedelta(minutes=10)  # ⏳ refrescar cada 10 minutos


# --- ALERTAS: DB FAVORITOS ---
# --- DB_PATH = "alertas.db" --- uso en local
DATABASE_URL = os.getenv("DATABASE_URL")


async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    # Crear tablas si no existen
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id BIGINT,
            player_id BIGINT,
            player_name TEXT,
            PRIMARY KEY(user_id, player_id)
        )
    """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notified (
            user_id BIGINT,
            match_id BIGINT,
            status TEXT,
            PRIMARY KEY(user_id, match_id, status)
        )
    """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'active',
            plan TEXT DEFAULT 'premium',
            subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await conn.close()


async def cleanup_notified():
    conn = await asyncpg.connect(DATABASE_URL)
    deleted = await conn.execute(
        """
        DELETE FROM notified
        WHERE created_at < NOW() - INTERVAL '30 days'
    """
    )
    await conn.close()
    print(f"🧹 Limpieza ejecutada: {deleted}")


async def fetch_live_matches_cached(tournament_id: int) -> list:
    """
    Devuelve los partidos de un torneo en vivo, usando caché para evitar
    llamar muchas veces a la API.
    """
    now = datetime.now()

    # Si ya tenemos en caché y sigue siendo válido → usarlo
    if tournament_id in LIVE_MATCHES_CACHE:
        cached_data, cached_time = LIVE_MATCHES_CACHE[tournament_id]
        if (now - cached_time) < CACHE_DURATION_MATCHES:
            print(f"✅ Usando partidos en vivo de torneo {tournament_id} desde caché.")
            return cached_data

    # Si no hay caché o está vencido → refrescar desde la API
    print(f"🔄 Refrescando partidos en vivo del torneo {tournament_id} desde la API...")
    headers = {"Authorization": f"Bearer {PADEL_API_KEY}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PADEL_API_URL}/tournaments/{tournament_id}/matches",
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

        matches = data.get("data", [])
        LIVE_MATCHES_CACHE[tournament_id] = (matches, now)
        return matches

    except Exception as e:
        print(f"⚠️ Error en fetch_live_matches_cached: {e}")
        return []


async def fetch_all_tournaments_cached():
    """
    Devuelve la lista de torneos, usando caché para no saturar la API.
    Se refresca cada CACHE_DURATION minutos.
    """
    global TOURNAMENTS_CACHE, TOURNAMENTS_CACHE_TIME

    now = datetime.now()
    if (
        TOURNAMENTS_CACHE is None
        or not TOURNAMENTS_CACHE_TIME
        or (now - TOURNAMENTS_CACHE_TIME) > CACHE_DURATION
    ):
        print("🔄 Refrescando torneos desde la API (no en caché)...")
        try:
            TOURNAMENTS_CACHE = await fetch_all_tournaments()
            TOURNAMENTS_CACHE_TIME = now
        except Exception as e:
            print(f"⚠️ Error al actualizar torneos: {e}")
            # Si hay error pero tenemos caché previo → devolverlo
            if TOURNAMENTS_CACHE is not None:
                return TOURNAMENTS_CACHE
            else:
                return []
    else:
        print("✅ Usando torneos desde caché.")

    return TOURNAMENTS_CACHE


async def was_notified(user_id: int, match_id: int, status: str) -> bool:
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow(
        "SELECT 1 FROM notified WHERE user_id=$1 AND match_id=$2 AND status=$3",
        user_id,
        match_id,
        status,
    )
    await conn.close()
    return row is not None


async def mark_notified(user_id: int, match_id: int, status: str):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO notified (user_id, match_id, status)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        """,
        user_id,
        match_id,
        status,
    )
    await conn.close()


async def get_favorites(user_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch(
        "SELECT player_id, player_name FROM favorites WHERE user_id=$1", user_id
    )
    await conn.close()
    return [(r["player_id"], r["player_name"]) for r in rows]


async def is_subscriber(user_id: int) -> bool:
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow(
        "SELECT status FROM subscribers WHERE user_id=$1", str(user_id)
    )
    await conn.close()
    return row and row["status"] == "active"


async def add_subscriber(user_id: int, plan="premium"):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO subscribers (user_id, status, plan)
        VALUES ($1, 'active', $2)
        ON CONFLICT (user_id) DO UPDATE SET status='active', plan=$2
    """,
        str(user_id),
        plan,
    )
    await conn.close()


async def remove_favorite(user_id: int, player_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "DELETE FROM favorites WHERE user_id=$1 AND player_id=$2", user_id, player_id
    )
    await conn.close()


# --- FUNCION SEGUIR (reutilizada en flujo con botones) ---
async def seguir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        player_name = update.message.text
    else:
        return

    headers = {"Authorization": f"Bearer {PADEL_API_KEY}", "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{PADEL_API_URL}/players", headers=headers, params={"name": player_name}
        )
        resp.raise_for_status()
        data = resp.json()

    players = data.get("data", [])
    if not players:
        await update.message.reply_text(f"❌ No encontré al jugador '{player_name}'.")
        return

    player = players[0]
    player_id, real_name = player["id"], player["name"]

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO favorites (user_id, player_id, player_name) VALUES ($1, $2, $3)",
            update.effective_user.id,
            player_id,
            real_name,
        )
        await update.message.reply_text(
            f"✅ Ahora sigues a {real_name}. ¡Te avisaré cuando juegue!"
        )
    except asyncpg.UniqueViolationError:
        await update.message.reply_text(f"⚠️ Ya sigues a {real_name}.")
    finally:
        await conn.close()


# --- CAPTURA NOMBRE DEL JUGADOR ---
async def capture_player_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler cuando el usuario escribe el nombre del jugador (ruta 'manual_add').
    - Busca jugadores por nombre en la API.
    - Si hay 0 resultados -> sugiere reintentar o usar el Top10.
    - Si hay 1 resultado -> guarda en favorites (si no estaba ya).
    - Si hay varios -> muestra botones para confirmar (usa callback_data 'follow_<id>').
    """
    # Solo procesamos si estábamos esperando el nombre
    if context.user_data.get("awaiting_player") != "add":
        return

    # Consumimos la señal de "esperando nombre"
    context.user_data.pop("awaiting_player", None)

    player_query = (update.message.text or "").strip()
    if not player_query:
        await update.message.reply_text(
            "❌ No recibí ningún nombre. Escribe el nombre del jugador que quieres seguir."
        )
        return

    headers = {"Authorization": f"Bearer {PADEL_API_KEY}", "Accept": "application/json"}

    # Intento principal: buscar por el texto completo
    players = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{PADEL_API_URL}/players",
                headers=headers,
                params={"name": player_query},
            )
            r.raise_for_status()
            data = r.json() or {}
            players = data.get("data", []) or []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            await update.message.reply_text(
                "⚠️ La API está rate-limited en este momento. Intenta de nuevo en unos minutos."
            )
            return
        await update.message.reply_text(
            "⚠️ Error al consultar la API de jugadores. Intenta de nuevo más tarde."
        )
        return
    except Exception as e:
        await update.message.reply_text(
            "⚠️ No pude contactar la API de jugadores. Intenta de nuevo más tarde."
        )
        return

    # Si no encontró nada, intentamos buscar por fragmentos (primer/última palabra)
    if not players:
        parts = player_query.split()
        tried = set()
        for frag in parts[:1] + parts[-1:]:
            frag = frag.strip()
            if not frag or frag in tried:
                continue
            tried.add(frag)
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(
                        f"{PADEL_API_URL}/players",
                        headers=headers,
                        params={"name": frag},
                    )
                    r.raise_for_status()
                    data = r.json() or {}
                    found = data.get("data", []) or []
                    # añadir nuevos (evitar duplicados por id)
                    for p in found:
                        if p not in players:
                            players.append(p)
                if players:
                    break
            except Exception:
                continue

    # Si sigue sin resultados
    if not players:
        await update.message.reply_text(
            "❌ No encontré jugadores con ese nombre.\n\n"
            "✔️ Opciones:\n"
            " • Reintenta con otra ortografía.\n"
            " • Elige desde el Top10: Menú → 🔔 Mis Alertas → ➕ Seguir jugador → Top10."
        )
        return

    # Si hay exactamente 1 candidato → guardarlo (si no ya existe)
    if len(players) == 1:
        p = players[0]
        player_id = p.get("id")
        player_name_real = p.get("name", "Desconocido")

        # Guardar en la DB (comprobamos si ya lo seguía)
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            existed = await conn.fetchval(
                "SELECT 1 FROM favorites WHERE user_id=$1 AND player_id=$2",
                update.effective_user.id,
                player_id,
            )
            if existed:
                await update.message.reply_text(f"⚠️ Ya sigues a {player_name_real}.")
            else:
                await conn.execute(
                    "INSERT INTO favorites (user_id, player_id, player_name) VALUES ($1, $2, $3)",
                    update.effective_user.id,
                    player_id,
                    player_name_real,
                )
                await update.message.reply_text(
                    f"✅ Ahora sigues a {player_name_real}. ¡Te avisaré cuando juegue!"
                )
            await conn.close()
        except Exception as e:
            # no exponemos stack trace al usuario
            await update.message.reply_text(
                "⚠️ Error al guardar en la base de datos. Intenta de nuevo más tarde."
            )
        return

    # Si hay varios candidatos -> mostrar botón para confirmar (reutiliza 'follow_<id>' que ya manejas)
    # Limitamos a un máximo razonable (ej. 8) para no saturar el teclado
    max_choices = 8
    keyboard = []
    for p in players[:max_choices]:
        pid = p.get("id")
        pname = p.get("name", "Desconocido")
        keyboard.append([InlineKeyboardButton(pname, callback_data=f"follow_{pid}")])

    keyboard.append(
        [InlineKeyboardButton("🔄 Buscar de nuevo", callback_data="alerts_add")]
    )
    keyboard.append([InlineKeyboardButton("« Volver", callback_data="my_alerts")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "He encontrado varios jugadores que podrían coincidir. Selecciona el correcto:",
        reply_markup=reply_markup,
    )


async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    conn = await asyncpg.connect(DATABASE_URL)
    favorites = await conn.fetch(
        "SELECT DISTINCT user_id, player_id, player_name FROM favorites"
    )
    await conn.close()

    if not favorites:
        return

    try:
        headers = {
            "Authorization": f"Bearer {PADEL_API_KEY}",
            "Accept": "application/json",
        }
        tournaments = await fetch_all_tournaments_cached() or []
        live_or_finished = [
            t for t in tournaments if t["status"] in ("live", "finished")
        ]

        async with httpx.AsyncClient() as client:
            for t in live_or_finished:
                r = await client.get(
                    f"{PADEL_API_URL}/tournaments/{t['id']}/matches", headers=headers
                )
                r.raise_for_status()
                matches = r.json().get("data", [])

                for m in matches:
                    status = m.get("status")
                    if status not in ("live", "finished"):
                        continue

                    # ⏳ Filtrar solo partidos recientes
                    played_at = m.get("played_at")
                    if status == "finished" and played_at:
                        try:
                            dt = datetime.strptime(played_at, "%Y-%m-%d").date()
                            if (datetime.now().date() - dt).days > 1:
                                continue
                        except Exception:
                            continue

                    players = m.get("players", {})
                    team1 = " / ".join(p["name"] for p in players.get("team_1", []))
                    team2 = " / ".join(p["name"] for p in players.get("team_2", []))
                    all_players = [p["name"] for team in players.values() for p in team]

                    score = format_match_score(m)
                    duration = m.get("duration", "")
                    round_map = {1: "Final", 2: "Semifinal", 4: "Cuartos", 8: "Octavos"}
                    round_name = round_map.get(
                        m.get("round", 0), f"Ronda {m.get('round', '?')}"
                    )
                    winner = m.get("winner")
                    if winner == "team_1":
                        team1 = f"🏆 {team1}"
                    elif winner == "team_2":
                        team2 = f"🏆 {team2}"

                    # 🔔 Notificar a cada usuario que tenga este jugador
                    for fav in favorites:
                        user_id, pid, pname = (
                            fav["user_id"],
                            fav["player_id"],
                            fav["player_name"],
                        )
                        if pname in all_players:
                            already_notified = await was_notified(
                                user_id, m["id"], status
                            )
                            if already_notified:
                                continue

                            text_status = (
                                "está jugando ahora"
                                if status == "live"
                                else "terminó su partido"
                            )
                            message = (
                                f"🔔 {pname} {text_status} en {t['name']} 🏆\n\n"
                                f"👥 {team1} vs {team2}\n"
                                f"📊 {score}\n"
                                f"📅 {played_at or 'Hoy'}   ⏱️ {duration}\n"
                                f"🔎 {round_name}"
                            )
                            await context.bot.send_message(
                                chat_id=user_id, text=message
                            )
                            await mark_notified(user_id, m["id"], status)

    except Exception as e:
        print(f"Error en check_alerts: {e}")


def format_match_score(match: dict) -> str:
    """
    Devuelve el marcador de un partido en formato legible.
    Ejemplo: "6-1 | 6-4"
    """
    score_list = match.get("score") or []
    if not score_list:
        return "📊 Sin resultado"

    return " | ".join(
        f"{s.get('team_1', '?')}-{s.get('team_2', '?')}" for s in score_list
    )


async def fetch_all_tournaments() -> list:
    """
    Descarga todos los torneos de la API, recorriendo todas las páginas.
    Siempre devuelve una lista (aunque esté vacía).
    """
    headers = {
        "Authorization": f"Bearer {PADEL_API_KEY}",
        "Accept": "application/json",
    }

    tournaments = []
    url = f"{PADEL_API_URL}/tournaments"

    async with httpx.AsyncClient(timeout=15.0) as client:
        while url:
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json() or {}
            except Exception as e:
                print(f"⚠️ Error al pedir torneos: {e}")
                break

            tournaments.extend(data.get("data", []) or [])
            url = data.get("links", {}).get("next")

    return tournaments


async def get_padel_rankings(gender: str) -> str:
    """
    Obtiene el Top 10 de jugadores/as desde la API oficial de Fantasy Padel Tour.

    gender: "male" o "female" (Telegram usa estos en callback_data, los convertimos a men/women)
    """
    # Convertimos de "male"/"female" a "men"/"women"
    if gender == "male":
        api_gender = "men"
    elif gender == "female":
        api_gender = "women"
    else:
        return "⚠️ El género debe ser 'male' o 'female'."

    print(f"Pidiendo rankings para {api_gender} en la API oficial...")

    headers = {
        "Authorization": f"Bearer {PADEL_API_KEY}",
        "Accept": "application/json",
    }
    params = {"category": api_gender, "sort_by": "ranking", "order_by": "asc"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://en.fantasypadeltour.com/api/players",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        players = data.get("data", [])
        if not players:
            return f"No se encontraron jugadores en la categoría '{api_gender}'."

        # Tomamos solo el Top 10
        top_10_players = players[:10]

        gender_title = "Masculino" if api_gender == "men" else "Femenino"
        message = f"🏆 **Ranking {gender_title} - Top 10** 🏆\n\n"
        for player in top_10_players:
            rank = player.get("ranking", "N/A")
            name = player.get("name", "Sin Nombre")
            points = player.get("points", 0)
            nationality = player.get("nationality", "??")
            message += f"**{rank}.** {name} ({nationality}) - `{points}` pts\n"

        print(f"Ranking {api_gender} formateado con éxito.")
        return message

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return "❌ Error de autenticación. Verifica tu API Key."
        return f"⚠️ Error HTTP {e.response.status_code}: {e}"
    except httpx.RequestError as e:
        return f"⚠️ Error de conexión con la API: {e}"
    except Exception as e:
        return f"⚠️ Error inesperado: {e}"


async def get_padel_calendar() -> str:
    """
    Obtiene torneos próximos desde la API (usa la caché central).
    Incluye torneos cuyo estado sea upcoming/scheduled/created o
    aquellos con start_date >= hoy aunque el estado no esté correcto.
    """
    print("📅 Pidiendo calendario de torneos (usando caché)...")

    try:
        tournaments = await fetch_all_tournaments_cached() or []
        # DEBUG: ver los primeros resultados (quita o comenta cuando no lo necesites)
        print(
            f"🔍 DEBUG: recibidos {len(tournaments)} torneos (ejemplos): {tournaments[:5]}"
        )

        today = datetime.now().date()

        upcoming = []
        for t in tournaments:
            status = (t.get("status") or "").lower()
            start_raw = t.get("start_date")
            start_date = None
            if start_raw:
                # la API usa 'YYYY-MM-DD' normalmente; intentamos parsear
                try:
                    start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
                except Exception:
                    # no pudimos parsear -> lo ignoramos como fecha válida
                    start_date = None

            # incluir si estado indica próximo o si la fecha de inicio es hoy o futura
            if status in ("upcoming", "scheduled", "created") or (
                start_date and start_date >= today
            ):
                upcoming.append(t)

        if not upcoming:
            return "📅 No hay torneos próximos publicados en este momento."

        # ordenar por fecha de inicio (si no tiene fecha, lo mandamos al final)
        upcoming_sorted = sorted(
            upcoming, key=lambda x: x.get("start_date") or "9999-12-31"
        )

        message = "📅 **Próximos Torneos de Pádel** 📅\n\n"
        for i, t in enumerate(upcoming_sorted[:10], start=1):
            name = t.get("name", "Sin nombre")
            location = t.get("location") or t.get("venue") or "Lugar desconocido"
            country = t.get("country", "")
            start = t.get("start_date", "¿?")
            end = t.get("end_date", "¿?")
            message += (
                f"**{i}. {name}**\n"
                f"   📍 {location}, {country}\n"
                f"   📅 {start} → {end}\n\n"
            )

        return message

    except Exception as e:
        print(f"⚠️ Error en get_padel_calendar: {e}")
        return "⚠️ No se pudo obtener el calendario en este momento."


# --- FUNCIÓN: TORNEOS EN VIVO ---
async def get_live_tournaments() -> list:
    """
    Devuelve los torneos en vivo o, si no hay, los programados para la fecha de hoy.
    Si no hay ninguno, devuelve lista vacía.
    """
    tournaments = await fetch_all_tournaments_cached()
    today_str = date.today().isoformat()

    # 1) Buscar torneos con partidos en vivo
    live_tournaments = [t for t in tournaments if t.get("status") == "live"]

    if live_tournaments:
        return live_tournaments

    # 2) Si no hay, buscar torneos con partidos programados para hoy
    today_tournaments = [
        t
        for t in tournaments
        if t.get("status") == "scheduled"
        and t.get("scheduled_at", "").startswith(today_str)
    ]

    if today_tournaments:
        return today_tournaments

    # 3) Si tampoco hay, devolvemos vacío
    return []


# --- FUNCIÓN: PARTIDOS EN VIVO DE UN TORNEO ---
async def get_live_matches(tournament_id: int) -> str:
    matches = await fetch_live_matches_cached(int(tournament_id))

    print(f"🔍 DEBUG: Matches recibidos de API para torneo {tournament_id}: {matches}")

    # 🎾 Filtrar partidos en vivo
    live_matches = [
        m
        for m in matches
        if m.get("status") == "live"
        or (m.get("connections", {}).get("live") and m.get("status") == "scheduled")
    ]

    if live_matches:
        message = f"🎾 **Partidos en Vivo - Torneo {tournament_id}** 🎾\n\n"
        for m in live_matches[:10]:
            players_t1 = " / ".join(
                p.get("name", "?") for p in m.get("players", {}).get("team_1", [])
            )
            players_t2 = " / ".join(
                p.get("name", "?") for p in m.get("players", {}).get("team_2", [])
            )
            score = format_match_score(m)
            message += f"👥 {players_t1} vs {players_t2}\n📊 {score}\n⏱️ En juego\n\n"
        return message

    # 📅 Si no hay live, buscar partidos programados para hoy
    today_str = datetime.utcnow().date().isoformat()
    scheduled_today = [
        m
        for m in matches
        if m.get("status") == "scheduled"
        and m.get("scheduled_at", "").startswith(today_str)
    ]

    if scheduled_today:
        message = f"📅 **Partidos Programados Hoy - Torneo {tournament_id}** 📅\n\n"
        for m in scheduled_today[:10]:
            players_t1 = " / ".join(
                p.get("name", "?") for p in m.get("players", {}).get("team_1", [])
            )
            players_t2 = " / ".join(
                p.get("name", "?") for p in m.get("players", {}).get("team_2", [])
            )
            when = m.get("scheduled_at", "Sin hora")
            message += f"👥 {players_t1} vs {players_t2}\n🕒 Programado: {when}\n\n"
        return message

    # 🚫 Nada disponible
    return "⚠️ No hay partidos en curso ni programados para hoy en este torneo."


async def get_last_results(summary_only: bool = True) -> str:
    """
    Obtiene los últimos resultados de torneos finalizados,
    mostrando rivales, marcador, fecha, duración y ronda.
    """
    global LAST_RESULTS_CACHE, LAST_RESULTS_CACHE_TIME

    now = datetime.now()
    if (
        LAST_RESULTS_CACHE
        and LAST_RESULTS_CACHE_TIME
        and (now - LAST_RESULTS_CACHE_TIME) < CACHE_DURATION_RESULTS
    ):
        print("✅ Usando últimos resultados desde caché.")
        return LAST_RESULTS_CACHE

    headers = {
        "Authorization": f"Bearer {PADEL_API_KEY}",
        "Accept": "application/json",
    }

    try:
        tournaments = await fetch_all_tournaments_cached() or []
        finished = [t for t in tournaments if t.get("status") == "finished"]

        if not finished:
            return "⚠️ No hay torneos finalizados en la API."

        last_tournament = finished[-1]
        t_id, t_name = last_tournament["id"], last_tournament["name"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PADEL_API_URL}/tournaments/{t_id}/matches",
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            matches_data = resp.json()

        matches = matches_data.get("data", []) or []
        finished_matches = [m for m in matches if m.get("status") == "finished"]

        if not finished_matches:
            return f"📜 Últimos Resultados - {t_name}\n\n(No hay partidos finalizados todavía)"

        # Ordenar por ronda (Final primero)
        finished_matches.sort(key=lambda m: m.get("round", 99))

        resultados = []
        for m in finished_matches[:5]:  # máximo 5 partidos
            players = m.get("players", {})
            team1 = " / ".join(p.get("name", "?") for p in players.get("team_1", []))
            team2 = " / ".join(p.get("name", "?") for p in players.get("team_2", []))

            # marcador
            score = format_match_score(m)

            # fecha y duración
            played_at = m.get("played_at", "¿?")
            duration = m.get("duration", "")

            # ronda
            round_map = {1: "Final", 2: "Semifinal", 4: "Cuartos", 8: "Octavos"}
            round_name = round_map.get(
                m.get("round", 0), f"Ronda {m.get('round', '?')}"
            )

            # ganador
            winner = m.get("winner")
            if winner == "team_1":
                team1 = f"🏆 {team1}"
            elif winner == "team_2":
                team2 = f"🏆 {team2}"

            resultados.append(
                f"👥 {team1} vs {team2}\n"
                f"📊 {score}\n"
                f"📅 {played_at}   ⏱️ {duration}\n"
                f"🔎 {round_name}"
            )

        message = f"📜 **Últimos Resultados - {t_name}** 📜\n\n" + "\n\n".join(
            resultados
        )

        # Guardar en caché
        LAST_RESULTS_CACHE = message
        LAST_RESULTS_CACHE_TIME = now

        return message

    except Exception as e:
        print(f"Error en get_last_results: {e}")
        return "⚠️ Error al obtener últimos resultados."


# --- LÓGICA DEL BOT (sin cambios) ---
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
        [InlineKeyboardButton("📜 Últimos Resultados", callback_data="last_results")],
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


# --- HANDLER DE BOTONES ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    command = parts[0]

    # --- MENÚ PRINCIPAL ---
    if command == "start":
        await start(update, context)

    # --- SUBMENÚ ALERTAS ---
    elif command == "my" and parts[1] == "alerts":
        keyboard = [
            [InlineKeyboardButton("➕ Seguir jugador", callback_data="alerts_add")],
            [InlineKeyboardButton("📋 Ver seguidos", callback_data="alerts_list")],
            [InlineKeyboardButton("« Volver", callback_data="start")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🔔 Gestión de Alertas", reply_markup=reply_markup
        )

    elif command == "alerts":
        if parts[1] == "add":
            # Menú con 2 opciones
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🏆 Top 10 Masculino", callback_data="choose_male"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏆 Top 10 Femenino", callback_data="choose_female"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✍️ Ingresar nombre manualmente", callback_data="manual_add"
                    )
                ],
                [InlineKeyboardButton("« Volver", callback_data="my_alerts")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "🔎 ¿Cómo quieres elegir al jugador?",
                reply_markup=reply_markup,
            )
        elif parts[1] == "list":
            favorites = await get_favorites(query.from_user.id)
            if not favorites:
                await query.edit_message_text("📭 No estás siguiendo a ningún jugador.")
                return

            keyboard = []
            for pid, pname in favorites:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"❌ {pname}", callback_data=f"unfollow_{pid}"
                        )
                    ]
                )
            keyboard.append(
                [InlineKeyboardButton("« Volver", callback_data="my_alerts")]
            )

            reply_markup = InlineKeyboardMarkup(keyboard)
            text = "📋 Jugadores que sigues:\n\n" + "\n".join(
                [f"✅ {pname}" for _, pname in favorites]
            )
            await query.edit_message_text(text, reply_markup=reply_markup)

    elif command == "choose":
        gender = parts[1]  # male o female
        headers = {
            "Authorization": f"Bearer {PADEL_API_KEY}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PADEL_API_URL}/players",
                headers=headers,
                params={
                    "category": "men" if gender == "male" else "women",
                    "sort_by": "ranking",
                    "order_by": "asc",
                },
            )
            players = resp.json().get("data", [])[:10]

        keyboard = []
        for p in players:
            keyboard.append(
                [InlineKeyboardButton(p["name"], callback_data=f"follow_{p['id']}")]
            )
        keyboard.append([InlineKeyboardButton("« Volver", callback_data="alerts_add")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Elige un jugador del Top 10 {'Masculino' if gender=='male' else 'Femenino'}:",
            reply_markup=reply_markup,
        )

    elif command == "follow":
        player_id = int(parts[1])
        headers = {
            "Authorization": f"Bearer {PADEL_API_KEY}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PADEL_API_URL}/players/{player_id}", headers=headers
            )
            player = resp.json()

        player_name = player.get("name", "Desconocido")
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "INSERT INTO favorites (user_id, player_id, player_name) VALUES ($1, $2, $3)",
                query.from_user.id,
                player_id,
                player_name,
            )
            await query.edit_message_text(f"✅ Ahora sigues a {player_name}.")
        except asyncpg.UniqueViolationError:
            await query.edit_message_text(f"⚠️ Ya sigues a {player_name}.")
        finally:
            await conn.close()

    # --- OPCIÓN MANUAL (como ya lo tenías) ---
    elif command == "manual":
        if parts[1] == "add":
            await query.edit_message_text(
                "✍️ Escribe el nombre del jugador que quieres seguir:"
            )
            context.user_data["awaiting_player"] = "add"

    elif command == "unfollow":
        player_id = parts[1]
        remove_favorite(query.from_user.id, player_id)
        await query.edit_message_text("❌ Jugador eliminado de tus alertas.")

    # --- RANKINGS ---
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
        keyboard = [
            [
                InlineKeyboardButton(
                    "« Volver a Rankings", callback_data="show_rankings"
                )
            ],
            [InlineKeyboardButton("« Volver al Menú Principal", callback_data="start")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=rankings_text, reply_markup=reply_markup, parse_mode="Markdown"
        )

    # --- CALENDARIO ---
    elif command == "calendar":
        await query.edit_message_text(
            text="🔄 Obteniendo calendario de torneos, por favor espera..."
        )
        calendar_text = await get_padel_calendar()
        keyboard = [
            [InlineKeyboardButton("« Volver al Menú Principal", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=calendar_text, reply_markup=reply_markup, parse_mode="Markdown"
        )

    # --- PARTIDOS EN VIVO ---
    elif command == "live":
        live_tournaments = await get_live_tournaments()
        if not live_tournaments:
            await query.edit_message_text("🚫 No hay torneos en curso en este momento.")
            return

        keyboard = []
        for t in live_tournaments:
            keyboard.append(
                [InlineKeyboardButton(t["name"], callback_data=f"matches_{t['id']}")]
            )
        keyboard.append(
            [InlineKeyboardButton("« Volver al Menú", callback_data="start")]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Selecciona un torneo en vivo:", reply_markup=reply_markup
        )

    elif command == "matches":
        tournament_id = parts[1]
        await query.edit_message_text("🔄 Obteniendo partidos en vivo...")
        matches_text = await get_live_matches(tournament_id)
        keyboard = [[InlineKeyboardButton("« Volver", callback_data="live_matches")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=matches_text, reply_markup=reply_markup, parse_mode="Markdown"
        )

    # --- RESULTADOS ---
    elif command == "last":
        await query.edit_message_text("📜 Obteniendo últimos resultados...")
        results_text = await get_last_results(summary_only=True)
        keyboard = [
            [InlineKeyboardButton("📜 Ver todos", callback_data="all_results")],
            [InlineKeyboardButton("« Volver al Menú", callback_data="start")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=results_text, reply_markup=reply_markup, parse_mode="Markdown"
        )

    elif command == "all":
        await query.edit_message_text("📜 Obteniendo todos los resultados...")
        results_text = await get_last_results(summary_only=False)
        keyboard = [[InlineKeyboardButton("« Volver al Menú", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=results_text, reply_markup=reply_markup, parse_mode="Markdown"
        )

    # --- DEFAULT ---
    else:
        keyboard = [[InlineKeyboardButton("« Volver al Menú", callback_data="start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"La función '{query.data}' se implementará pronto. ¡Gracias por tu paciencia! 😊",
            reply_markup=reply_markup,
        )


def main():
    print("Iniciando bot con alertas...")

    # Crear aplicación
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers principales
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Job de alertas
    job_queue = application.job_queue
    job_queue.run_repeating(check_alerts, interval=60, first=10)

    print("El bot está en línea. Presiona Ctrl+C para detenerlo.")

    # ✅ async polling correcto
    application.run_polling()


if __name__ == "__main__":
    main()
