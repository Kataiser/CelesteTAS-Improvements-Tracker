import argparse
import ctypes
import time
import traceback
from typing import List

import discord

import commands
import db
import main
import spreadsheet
import utils
from utils import plural

intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.message_content = True

client = discord.Client(intents=intents)
command_tree = discord.app_commands.CommandTree(client)
slash_command_servers = [discord.Object(id=403698615446536203), discord.Object(id=970379400887558204)]

debug = False
safe_mode = False


def start():
    global debug, projects_startup
    log.info("Bot starting")
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        print("DEBUG MODE")

    projects_startup = db.projects.dict()
    main.fast_project_ids = set(projects_startup)
    path_caches_size = db.path_caches.size()
    project_logs_size = db.project_logs.size()
    log.info(f"Loaded {len(projects_startup)} project{plural(projects_startup)}, {project_logs_size} project message log{plural(project_logs_size)}, "
             f"and {path_caches_size} path cache{plural(path_caches_size)}")

    if not len(projects_startup) == project_logs_size == path_caches_size:
        log.critical("Project data component lengths are not equal, exiting")
        return

    with open('bot_token', 'r') as bot_token_file:
        bot_token = bot_token_file.read()

    while True:
        try:
            log.info("Logging in...")
            client.run(bot_token, log_handler=None)
        except Exception as error:
            log.error(error)

            if not debug:
                log.info("Restarting bot in 5 seconds, this can only end well")
                time.sleep(5)
            else:
                break


@client.event
async def on_ready():
    global safe_mode, projects_startup
    log.info(f"Logged in as {client.user}")
    main.login_time = time.time()
    [await command_tree.sync(guild=server) for server in slash_command_servers]
    log.info(f"Servers: {[g.name for g in client.guilds]}")
    main.handle_game_sync_results.start()
    downtime_message_count = 0
    projects_to_scan = main.safe_projects if safe_mode else projects_startup
    db.project_logs.enable_cache()
    set_default_status = True

    for improvements_channel_id in reversed(projects_to_scan):
        project = projects_startup[improvements_channel_id]
        improvements_channel = client.get_channel(improvements_channel_id)

        if not improvements_channel or utils.missing_channel_permissions(improvements_channel):
            log.error(f"Can't access improvements channel for project {project['name']}")
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
    elif message.author.id in (438978127973318656, 155149108183695360, 219955313334288385):
        return

    message_lower = message.content.lower()
    substrings_1984_found = [s for s in substrings_1984 if s in message_lower]
    substrings_1984_music_found = [s for s in substrings_1984_music if s in message_lower]

    if substrings_1984_found or (substrings_1984_music_found and ('music' in message_lower or ('song' in message_lower and 'shatter' not in message_lower))):
        await (await client.fetch_user(219955313334288385)).send(f"`{utils.detailed_user(message)}:` \"{message.content}\" {message.jump_url} {message.channel.jump_url}"[:1990])


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.channel_id in main.fast_project_ids:
        await client.wait_until_ready()

        async for message in client.get_channel(payload.channel_id).history(limit=50):
            if message.reference and message.reference.message_id == payload.message_id and message.author == client.user:
                await message.delete()
                log.info(f"Deleted bot reply message in project: {db.projects.get(payload.channel_id)['name']}")
                break


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if '⏭' in payload.emoji.name and payload.channel_id in main.fast_project_ids:
        await client.wait_until_ready()

        for project_id in main.fast_project_ids:
            if payload.message_id in db.project_logs.get(project_id):
                message = await client.get_channel(payload.channel_id).fetch_message(payload.message_id)
                project = db.projects.get(project_id)

                if payload.user_id in (message.author.id, *project['admins'], 219955313334288385):
                    request_user = await client.fetch_user(payload.user_id)
                    log.info(f"{utils.detailed_user(user=request_user)} has requested committing invalid post")
                    await message.clear_reaction('⏭')
                    await message.reply(f"{request_user.mention} has requested committing invalid post.")
                    await main.process_improvement_message(message, project, skip_validation=True)

                break


@client.event
async def on_guild_join(guild: discord.Guild):
    join_message = f"Bot has been added to server: {guild.name}"
    log.info(join_message)
    await (await client.fetch_user(219955313334288385)).send(join_message)


@client.event
async def on_guild_remove(guild: discord.Guild):
    remove_message = f"Bot has been removed from a server: {guild.name}"
    log.info(remove_message)
    await (await client.fetch_user(219955313334288385)).send(remove_message)


@client.event
async def on_connect():
    log.info("Connected to Discord")


@client.event
async def on_disconnect():
    log.warning("Disconnected from Discord")


@client.event
async def on_error(*args):
    error = traceback.format_exc()
    log.error(error)
    ctypes.windll.user32.FlashWindow(ctypes.windll.kernel32.GetConsoleWindow(), True)

    if not debug:
        await (await client.fetch_user(219955313334288385)).send(f"```\n{error[-1990:]}```")


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


log = main.create_logger('bot.log')
commands.client = client
spreadsheet.client = client
main.client = client
main.safe_mode = safe_mode
projects_startup = None
substrings_1984 = ('kataiser', 'warm fish', 'jaded', 'psycabob', 'shadowdrop', 'cosmic brain')
substrings_1984_music = ('lab ', 'psychokinetic', 'pk ', 'superluminary')

if __name__ == '__main__':
    start()
