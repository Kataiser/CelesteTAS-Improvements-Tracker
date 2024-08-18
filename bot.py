import argparse
import re
import subprocess
import time
from typing import List, Optional

import discord

import commands
import db
import main
import spreadsheet
import utils
from constants import admin_user_id, slash_command_servers
from utils import plural

intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.message_content = True

client = discord.Client(intents=intents)
command_tree = discord.app_commands.CommandTree(client)

debug = False
safe_mode = False


def start():
    global debug, projects_startup
    log.info("Bot starting")
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        log.info("DEBUG MODE")

    projects_startup = db.projects.dict()
    main.fast_project_ids = set(projects_startup)
    path_caches_size = db.path_caches.size()
    project_logs_size = db.project_logs.size()
    log.info(f"Loaded {len(projects_startup)} project{plural(projects_startup)}, {project_logs_size} project message log{plural(project_logs_size)}, "
             f"and {path_caches_size} path cache{plural(path_caches_size)}")

    if not len(projects_startup) == project_logs_size == path_caches_size:
        log.warning("Project data component lengths are not equal")

    with open('bot_token', 'r') as bot_token_file:
        bot_token = bot_token_file.read()

    while True:
        try:
            log.info("Logging in...")
            client.run(bot_token, log_handler=None)
        except Exception as error:
            if isinstance(error, RuntimeError) and str(error) == "Session is closed":
                log.info("Assuming bot has been closed, not restarting")
                return
            else:
                utils.log_error()

                if not debug:
                    log.info("Restarting bot in 5 seconds, this can only end well")
                    time.sleep(5)
                else:
                    break


@client.event
async def on_ready():
    log.info(f"Logged in as {client.user}")
    main.login_time = time.time()
    [await command_tree.sync(guild=server) for server in slash_command_servers]
    log.info(f"Servers: {[g.name for g in client.guilds]}")
    downtime_message_count = 0
    projects_to_scan = main.safe_projects if safe_mode else projects_startup
    db.project_logs.enable_cache()
    set_default_status = True

    for improvements_channel_id in reversed(projects_to_scan):
        project = projects_startup[improvements_channel_id]
        improvements_channel = client.get_channel(improvements_channel_id)

        if not improvements_channel or utils.missing_channel_permissions(improvements_channel):
            utils.log_error(f"Can't access improvements channel for project {project['name']}", flash_window=False)
            main.inaccessible_projects.add(improvements_channel_id)
            continue

        if debug:
            history_limit = 2
        elif main.login_time - int(project['last_commit_time']) > 2600000:  # a month
            history_limit = 10
        else:
            history_limit = 20

        for message in reversed([m async for m in improvements_channel.history(limit=history_limit)]):
            downtime_message_count += 1
            set_status = await main.process_improvement_message(message, project)

            if set_status:
                set_default_status = False

    log.info(f"Finished considering {downtime_message_count} downtime messages")
    db.project_logs.disable_cache()

    if set_default_status:
        await main.set_status()

    if not main.handle_game_sync_results.is_running():
        main.handle_game_sync_results.start()

    if not main.handle_no_game_sync_results.is_running():
        main.handle_no_game_sync_results.start()


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    elif not message.guild:
        await client.wait_until_ready()
        await commands.handle(message)
    elif message.channel.id in main.fast_project_ids:
        await client.wait_until_ready()
        await main.process_improvement_message(message)
    elif message.author.id in (438978127973318656, 155149108183695360) or message.channel.id == 403698615446536206:
        return
    elif message.channel.id == 1185382846018359346:
        updating_text = "New commit found, updating and restarting"
        log.info(updating_text)
        await (await utils.user_from_id(client, admin_user_id)).send(updating_text)
        import psutil
        self_process = psutil.Process()
        subprocess.Popen(f'python updater.py {self_process.pid} {self_process.parent().pid}', creationflags=0x00000010)
        time.sleep(60)

    message_lower = message.content.lower()
    user_ids = set()

    if re_1984.findall(message_lower):
        user_ids.add(219955313334288385)
    elif re_1984_music.findall(message_lower):
        message_split = re.findall(r'\w+|[^\w\s]', message_lower)

        if 'music' in message_split or 'song' in message_split:
            user_ids.add(219955313334288385)

    if re_1984_hydro.findall(message_lower):
        user_ids.add(236760821286436865)

    if re_1984_cabob.findall(message_lower):
        user_ids.add(256796503530536970)

    if re_1984_vamp.findall(message_lower):
        user_ids.add(234520815658336258)

    if message.guild and message.guild.id != 403698615446536203 and re_1984_bot.findall(message_lower):
        user_ids.add(219955313334288385)

    if message.reference:
        replied_to_kataiser = False

        if message.reference.resolved:
            replied_to_kataiser = message.reference.resolved.author.id == 219955313334288385
        elif message.reference.cached_message:
            replied_to_kataiser = message.reference.cached_message.author.id == 219955313334288385

        if replied_to_kataiser:
            user_ids.add(219955313334288385)

    for user_id in user_ids:
        if user_id in (message.author.id, *[m.id for m in message.mentions]):
            continue

        dm = f"`{utils.detailed_user(message)}:` \"{message.content}\" {message.jump_url} (#{message.channel.name})"[:1990]
        user = await utils.user_from_id(client, user_id)
        await user.send(dm)
        log.info(f"Sent 1984 DM to {user.display_name}:\n{dm}")


@client.event
async def on_message_edit(old_message: discord.Message, message: discord.Message):
    if message.channel.id not in main.fast_project_ids:
        return

    if not await has_bot_reaction(message, "❌"):
        return

    for reaction in message.reactions:
        await reaction.remove(client.user)

    remove_project_log(message)

    await delete_message_responses(message.channel.id, message.id)
    await on_message(message)


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.channel_id in main.fast_project_ids:
        await client.wait_until_ready()
        await delete_message_responses(payload.channel_id, payload.message_id)


async def delete_message_responses(channel_id: int, message_id: int):
    async for message in client.get_channel(channel_id).history(limit=50):
        if message.reference and message.reference.message_id == message_id and message.author == client.user:
            await message.delete()
            log.info(f"Deleted bot reply message in project: {db.projects.get(channel_id)['name']}")


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if '⏭' in payload.emoji.name and payload.channel_id in main.fast_project_ids:
        await client.wait_until_ready()

        for project_id in main.fast_project_ids:
            if payload.message_id in db.project_logs.get(project_id):
                message = await client.get_channel(payload.channel_id).fetch_message(payload.message_id)
                project = db.projects.get(project_id)

                if payload.user_id in (message.author.id, *project['admins'], admin_user_id):
                    request_user = await utils.user_from_id(client, payload.user_id)
                    log.info(f"{utils.detailed_user(user=request_user)} has requested committing invalid post")
                    await message.clear_reaction('⏭')
                    await message.reply(f"{request_user.mention} has requested committing invalid post.")
                    await main.process_improvement_message(message, project, skip_validation=True)

                break


@client.event
async def on_guild_join(guild: discord.Guild):
    join_message = f"Bot has been added to server: {guild.name} (ID = {guild.id}, owner = {utils.detailed_user(user=guild.owner)})"
    log.info(join_message)
    await (await utils.user_from_id(client, admin_user_id)).send(join_message)

    if guild.id == 1039054819626848276:
        await guild.leave()
        log.info("LEFT THAT ONE jeez")


@client.event
async def on_guild_remove(guild: discord.Guild):
    remove_message = f"Bot has been removed from a server: {guild.name} (ID = {guild.id})"
    log.info(remove_message)
    await (await utils.user_from_id(client, admin_user_id)).send(remove_message)


@client.event
async def on_connect():
    log.info("Connected to Discord")


@client.event
async def on_disconnect():
    log.warning("Disconnected from Discord")


@client.event
async def on_error(*args):
    await utils.report_error(client)


@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def draft(interaction, map_name: str):
    await spreadsheet.draft(interaction, map_name)


@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def update_progress(interaction, map_name: str, note: str):
    await spreadsheet.update_progress(interaction, map_name, note)


@command_tree.command(description=spreadsheet.progress.__doc__, guilds=slash_command_servers)
async def progress(interaction, map_name: str):
    await spreadsheet.progress(interaction, map_name)


@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def drop(interaction, map_name: str, reason: str):
    await spreadsheet.drop(interaction, map_name, reason)


@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def complete(interaction, map_name: str):
    await spreadsheet.complete(interaction, map_name)


@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def undraft(interaction, map_name: str):
    await spreadsheet.undraft(interaction, map_name)


@command_tree.command(description=spreadsheet.taser_status.__doc__, guilds=slash_command_servers)
async def taser_status(interaction, taser: str):
    await spreadsheet.taser_status(interaction, taser)


@progress.autocomplete('map_name')
async def map_autocomplete(interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
    return [discord.app_commands.Choice(name=sj_map, value=sj_map) for sj_map in spreadsheet.sj_fuzzy_match(current.lower())]


def remove_project_log(message: discord.Message):
    project_logs = db.project_logs.get(message.channel.id)
    if message.id in project_logs:
        project_logs.remove(message.id)
        db.project_logs.set(message.channel.id, project_logs)


async def has_bot_reaction(message: discord.Message, emoji: str):
    for reaction in message.reactions:
        if reaction.emoji == '❌':
            async for user in reaction.users():
                if user.id == client.user.id:
                    return True
    return False


def share_client(client_: discord.Client):
    commands.client = client_
    spreadsheet.client = client_
    main.client = client_


log = main.create_logger('bot')
share_client(client)
main.safe_mode = safe_mode
projects_startup = Optional[dict]
substrings_1984 = ('kataiser', 'kata', 'warm fish', 'jaded', 'psycabob', 'shadowdrop mix', 'cosmic brain')
substrings_1984_music = ('lab', 'psychokinetic', 'pk ', 'superluminary')
substrings_1984_hydro = ('shatter', 'shong', 'shattersong', 'hydro', 'penumbra', 'reversion', 'lightspeed')
re_1984 = re.compile('|'.join(fr'\b{re.escape(sub)}\b' for sub in substrings_1984))
re_1984_music = re.compile('|'.join(fr'\b{re.escape(sub)}\b' for sub in substrings_1984_music))
re_1984_hydro = re.compile('|'.join(fr'\b{re.escape(sub)}\b' for sub in substrings_1984_hydro) + r'|\bss\+(?=\b|\W|$)')
re_1984_cabob = re.compile(r'\bcabob\b')
re_1984_vamp = re.compile(r'\bvamp\b')
re_1984_bot = re.compile(r'\bbot\b|\btracker\b')

if __name__ == '__main__':
    start()
