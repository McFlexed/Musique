import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
from collections import deque
import time
import os
import subprocess
result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
FFMPEG_EXEC = result.stdout.strip() or "ffmpeg"

cookies_content = os.environ.get("YOUTUBE_COOKIES", "")
if cookies_content:
    with open("/tmp/cookies.txt", "w") as f:
        f.write(cookies_content)

# ─── Config ───────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN") 

MY_GUILD_ID = 1311010435885174844  # ← remplace par l'ID de ton serveur

# ─── YT-DLP options ───────────────────────────────────────────────────────────
YTDL_OPTIONS_SINGLE = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extractor_args": {"youtube": {"player_client": ["android"]}},
    "cookiefile": "/tmp/cookies.txt",
}

YTDL_OPTIONS_PLAYLIST = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "extract_flat": "in_playlist",
    "extractor_args": {"youtube": {"player_client": ["android"]}},
    "cookiefile": "/tmp/cookies.txt",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# ─── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

# guild_id → {"queue": deque, "voice_client": VoiceClient, "current": dict, "start_time": float, "volume": float}
guild_state: dict[int, dict] = {}


def get_state(guild_id: int) -> dict:
    if guild_id not in guild_state:
        guild_state[guild_id] = {
            "queue": deque(),
            "voice_client": None,
            "current": None,
            "start_time": None,
            "volume": 0.8,
            "player_message": None,
        }
    return guild_state[guild_id]


def fmt_duration(seconds: int) -> str:
    if not seconds:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(elapsed: float, total: float, length: int = 12) -> str:
    if not total:
        return "▬" * length
    filled = int((elapsed / total) * length)
    filled = min(filled, length)
    bar = "▬" * filled + "🔘" + "▬" * (length - filled)
    return bar


# ─── Fetch track info ─────────────────────────────────────────────────────────
async def fetch_single(query: str) -> dict | None:
    loop = asyncio.get_event_loop()
    def _extract():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS_SINGLE) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info
    try:
        return await loop.run_in_executor(None, _extract)
    except Exception as e:
        print(f"Erreur fetch_single: {e}")
        return None


async def fetch_playlist(url: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    def _extract():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS_PLAYLIST) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    try:
        info = await loop.run_in_executor(None, _extract)
    except Exception as e:
        print(f"Erreur fetch_playlist: {e}")
        return []

    tracks = []
    entries = info.get("entries", [info])
    for entry in entries:
        if not entry:
            continue
        tracks.append({
            "title": entry.get("title", "Titre inconnu"),
            "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id','')}",
            "webpage_url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id','')}",
            "duration": entry.get("duration", 0),
            "thumbnail": entry.get("thumbnail"),
            "requester": "",
            "_needs_resolve": True,  # les URLs plates doivent être résolues avant lecture
        })
    return tracks


async def resolve_track(track: dict) -> dict:
    """Résout l'URL de streaming réelle pour une piste extraite à plat depuis une playlist."""
    if not track.get("_needs_resolve"):
        return track
    info = await fetch_single(track["webpage_url"])
    if info:
        track["url"] = info["url"]
        track["title"] = info.get("title", track["title"])
        track["duration"] = info.get("duration", track["duration"])
        track["thumbnail"] = info.get("thumbnail", track["thumbnail"])
        track["_needs_resolve"] = False
    return track


# ─── Player buttons ───────────────────────────────────────────────────────────
class PlayerView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def _vc(self):
        return get_state(self.guild_id)["voice_client"]

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.secondary, custom_id="btn_pause")
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if not vc:
            await interaction.response.send_message("❌ Pas de lecture en cours.", ephemeral=True)
            return
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            button.style = discord.ButtonStyle.success
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.primary, custom_id="btn_skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Piste suivante !", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Rien en cours.", ephemeral=True)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, custom_id="btn_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        vc = state["voice_client"]
        state["queue"].clear()
        state["current"] = None
        if vc:
            vc.stop()
            await vc.disconnect()
            state["voice_client"] = None
        await interaction.response.send_message("⏹️ Musique stoppée.", ephemeral=True)
        await interaction.message.delete()

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, custom_id="btn_vol_down")
    async def btn_vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        vc = state["voice_client"]
        state["volume"] = max(0.0, state["volume"] - 0.1)
        if vc and vc.source:
            vc.source.volume = state["volume"]
        await interaction.response.send_message(f"🔉 Volume : **{int(state['volume']*100)}%**", ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="btn_vol_up")
    async def btn_vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        vc = state["voice_client"]
        state["volume"] = min(1.0, state["volume"] + 0.1)
        if vc and vc.source:
            vc.source.volume = state["volume"]
        await interaction.response.send_message(f"🔊 Volume : **{int(state['volume']*100)}%**", ephemeral=True)


# ─── Build now-playing embed ──────────────────────────────────────────────────
def build_np_embed(state: dict, guild_id: int) -> discord.Embed:
    track = state["current"]
    if not track:
        return discord.Embed(description="Rien en cours.", color=0x5865F2)

    elapsed = time.time() - (state["start_time"] or time.time())
    total = track.get("duration", 0)
    bar = progress_bar(elapsed, total)
    elapsed_fmt = fmt_duration(int(elapsed))
    total_fmt = fmt_duration(total)

    embed = discord.Embed(
        title="🎵 En cours de lecture",
        description=f"**[{track['title']}]({track['webpage_url']})**",
        color=0x5865F2,
    )
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])

    embed.add_field(name="⏱️ Progression", value=f"`{elapsed_fmt}` {bar} `{total_fmt}`", inline=False)
    embed.add_field(name="🔊 Volume", value=f"{int(state['volume']*100)}%", inline=True)
    embed.add_field(name="📋 En attente", value=str(len(state["queue"])), inline=True)
    embed.set_footer(text=f"Demandé par {track.get('requester', '?')}")
    return embed


# ─── Play next ───────────────────────────────────────────────────────────────
async def play_next(guild_id: int):
    state = get_state(guild_id)
    vc: discord.VoiceClient = state["voice_client"]

    if not vc or not vc.is_connected():
        return
    if not state["queue"]:
        state["current"] = None
        state["start_time"] = None
        return

    track = state["queue"].popleft()

    # Résoudre l'URL si piste plate (playlist)
    if track.get("_needs_resolve"):
        track = await resolve_track(track)

    state["current"] = track
    state["start_time"] = time.time()

    source = discord.FFmpegPCMAudio(track["url"], executable=FFMPEG_EXEC, **FFMPEG_OPTIONS)
    source = discord.PCMVolumeTransformer(source, volume=state["volume"])

    def after_playing(error):
        if error:
            print(f"Erreur playback: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

    vc.play(source, after=after_playing)

    # Mettre à jour ou envoyer le message player
    channel = vc.channel.guild.text_channels[0]
    # On cherche le dernier salon texte utilisé
    if state.get("text_channel"):
        channel = state["text_channel"]

    embed = build_np_embed(state, guild_id)
    view = PlayerView(guild_id)

    if state.get("player_message"):
        try:
            await state["player_message"].edit(embed=embed, view=view)
            return
        except Exception:
            pass
    try:
        msg = await channel.send(embed=embed, view=view)
        state["player_message"] = msg
    except Exception as e:
        print(f"Impossible d'envoyer le player: {e}")


# ─── Events ──────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    guild = discord.Object(id=MY_GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"✅ {bot.user} connecté — slash commands synchronisées !")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name="/play"
    ))

# ─── /play ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="play", description="▶️ Joue une vidéo ou playlist YouTube (URL ou recherche)")
@app_commands.describe(query="URL YouTube, lien de playlist, ou mots-clés de recherche")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("❌ Tu dois être dans un salon vocal !")
        return

    channel = interaction.user.voice.channel
    state = get_state(interaction.guild_id)
    state["text_channel"] = interaction.channel
    vc: discord.VoiceClient = state["voice_client"]

    if vc is None or not vc.is_connected():
        vc = await channel.connect()
        state["voice_client"] = vc
    elif vc.channel != channel:
        await vc.move_to(channel)

    is_playing = vc.is_playing() or vc.is_paused()

    # ── Playlist ──
    is_playlist = ("playlist" in query or "&list=" in query or "?list=" in query)
    if is_playlist:
        tracks = await fetch_playlist(query)
        if not tracks:
            await interaction.followup.send("❌ Impossible de charger la playlist.")
            return
        for t in tracks:
            t["requester"] = interaction.user.display_name
            state["queue"].append(t)

        embed = discord.Embed(
            title="📋 Playlist ajoutée",
            description=f"**{len(tracks)} pistes** ajoutées à la queue",
            color=0x5865F2,
        )
        embed.set_footer(text=f"Demandé par {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

        if not is_playing:
            await play_next(interaction.guild_id)
        return

    # ── Titre unique ou recherche ──
    info = await fetch_single(query)
    if not info:
        await interaction.followup.send("❌ Impossible de trouver la vidéo.")
        return

    track = {
        "title": info.get("title", "Titre inconnu"),
        "url": info["url"],
        "webpage_url": info.get("webpage_url", query),
        "duration": info.get("duration", 0),
        "thumbnail": info.get("thumbnail"),
        "requester": interaction.user.display_name,
        "_needs_resolve": False,
    }
    state["queue"].append(track)

    if is_playing:
        embed = discord.Embed(
            title="📋 Ajouté à la queue",
            description=f"**[{track['title']}]({track['webpage_url']})**",
            color=0x5865F2,
        )
        embed.add_field(name="⏱️ Durée", value=fmt_duration(track["duration"]), inline=True)
        embed.add_field(name="Position", value=f"#{len(state['queue'])}", inline=True)
        embed.set_footer(text=f"Demandé par {track['requester']}")
        if track["thumbnail"]:
            embed.set_thumbnail(url=track["thumbnail"])
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send("▶️ Chargement...")
        await play_next(interaction.guild_id)


# ─── /nowplaying ─────────────────────────────────────────────────────────────
@bot.tree.command(name="nowplaying", description="🎵 Affiche la piste en cours avec progression")
async def nowplaying(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    if not state["current"]:
        await interaction.response.send_message("❌ Rien en cours de lecture.", ephemeral=True)
        return
    embed = build_np_embed(state, interaction.guild_id)
    view = PlayerView(interaction.guild_id)
    state["text_channel"] = interaction.channel
    msg = await interaction.response.send_message(embed=embed, view=view)
    state["player_message"] = await interaction.original_response()


# ─── /queue ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="queue", description="📋 Affiche la file d'attente")
async def queue_cmd(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    embed = discord.Embed(title="📋 File d'attente", color=0x5865F2)

    if state["current"]:
        embed.add_field(
            name="▶️ En cours",
            value=f"**[{state['current']['title']}]({state['current']['webpage_url']})**",
            inline=False,
        )
    if state["queue"]:
        lines = []
        for i, t in enumerate(list(state["queue"])[:10], 1):
            dur = fmt_duration(t.get("duration", 0))
            lines.append(f"`{i}.` [{t['title']}]({t['webpage_url']}) `{dur}`")
        embed.add_field(name="Suivantes", value="\n".join(lines), inline=False)
        if len(state["queue"]) > 10:
            embed.set_footer(text=f"… et {len(state['queue']) - 10} pistes de plus")
    elif not state["current"]:
        embed.description = "La queue est vide."

    await interaction.response.send_message(embed=embed)


# ─── /skip ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="skip", description="⏭️ Passe à la piste suivante")
async def skip(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    vc = state["voice_client"]
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message("❌ Rien en cours.", ephemeral=True)
        return
    vc.stop()
    await interaction.response.send_message("⏭️ Piste suivante !")


# ─── /stop ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="stop", description="⏹️ Stoppe et vide la queue")
async def stop(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    vc = state["voice_client"]
    state["queue"].clear()
    state["current"] = None
    if vc:
        vc.stop()
        await vc.disconnect()
        state["voice_client"] = None
    await interaction.response.send_message("⏹️ Musique stoppée et queue vidée.")


# ─── /volume ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="volume", description="🔊 Change le volume (0–100)")
@app_commands.describe(level="Niveau entre 0 et 100")
async def volume(interaction: discord.Interaction, level: int):
    if not 0 <= level <= 100:
        await interaction.response.send_message("❌ Entre 0 et 100.", ephemeral=True)
        return
    state = get_state(interaction.guild_id)
    state["volume"] = level / 100
    vc = state["voice_client"]
    if vc and vc.source:
        vc.source.volume = state["volume"]
    await interaction.response.send_message(f"🔊 Volume : **{level}%**")


# ─── /pause & /resume ────────────────────────────────────────────────────────
@bot.tree.command(name="pause", description="⏸️ Met en pause")
async def pause(interaction: discord.Interaction):
    vc = get_state(interaction.guild_id)["voice_client"]
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Pause.")
    else:
        await interaction.response.send_message("❌ Rien en cours.", ephemeral=True)


@bot.tree.command(name="resume", description="▶️ Reprend la lecture")
async def resume(interaction: discord.Interaction):
    vc = get_state(interaction.guild_id)["voice_client"]
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Reprise !")
    else:
        await interaction.response.send_message("❌ Pas en pause.", ephemeral=True)


# ─── Run ─────────────────────────────────────────────────────────────────────
bot.run(TOKEN)
