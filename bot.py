import argparse
import ctypes
import time
import traceback
from typing import List

import discord

import commands
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
slash_command_servers = [discord.Object(id=970379400887558204)]

debug = False
safe_mode = False


def start():
    global debug
    log.info("Bot starting")
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        print("DEBUG MODE")

    main.projects = utils.load_projects()
    main.load_project_logs()
    main.path_caches = utils.load_path_caches()
    log.info(f"Loaded {len(main.projects)} project{plural(main.projects)}, {len(main.project_logs)} project message log{plural(main.project_logs)}, "
             f"and {len(main.path_caches)} path cache{plural(main.path_caches)}")

    if not len(main.projects) == len(main.project_logs) == len(main.path_caches):
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
    global safe_mode
    log.info(f"Logged in as {client.user}")
    main.login_time = time.time()
    [await command_tree.sync(guild=server) for server in slash_command_servers]
    log.info(f"Servers: {[g.name for g in client.guilds]}")
    await main.handle_game_sync_results()
    main.handle_game_sync_results.start()
    downtime_message_count = 0
    projects_to_scan = main.safe_projects if safe_mode else main.projects

    for improvements_channel_id in projects_to_scan:
        improvements_channel = client.get_channel(improvements_channel_id)

        if not improvements_channel:
            log.error(f"Can't access improvements channel for project {main.projects[improvements_channel_id]['name']}")
            continue

        for message in reversed([m async for m in improvements_channel.history(limit=2 if debug else 20)]):
            downtime_message_count += 1
            await main.process_improvement_message(message)

    log.info(f"Finished considering {downtime_message_count} downtime messages")


@client.event
async def on_message(message: discord.Message):
    await client.wait_until_ready()

    if message.author == client.user:
        return
    elif not message.guild:
        await commands.handle(message)
        return
    elif message.channel.id not in main.projects:
        return

    await main.process_improvement_message(message)


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await client.wait_until_ready()

    if payload.channel_id in main.projects:
        async for message in client.get_channel(payload.channel_id).history(limit=20):
            if message.reference and message.reference.message_id == payload.message_id and message.author == client.user:
                await message.delete()
                log.info(f"Deleted bot reply message in project: {main.projects[payload.channel_id]['name']}")
                break


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await client.wait_until_ready()

    if '⏭' in payload.emoji.name and payload.channel_id in main.projects:
        for project_id in main.projects:
            if payload.message_id in main.project_logs[project_id]:
                message = await client.get_channel(payload.channel_id).fetch_message(payload.message_id)

                if payload.user_id in (message.author.id, *main.projects[project_id]['admins'], 219955313334288385):
                    request_user = await client.fetch_user(payload.user_id)
                    log.info(f"{utils.detailed_user(user=request_user)} has requested committing invalid post")
                    await message.clear_reaction('⏭')
                    await message.reply(f"{request_user.mention} has requested committing invalid post.")
                    await main.process_improvement_message(message, skip_validation=True)

                break


@client.event
async def on_guild_join(guild: discord.Guild):
    join_message = f"Bot has been added to server: {guild.name} ({guild.member_count})"
    log.info(join_message)
    await (await client.fetch_user(219955313334288385)).send(join_message)


@client.event
async def on_guild_remove(guild: discord.Guild):
    remove_message = f"Bot has been removed from a server: {guild.name} ({guild.member_count})"
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


@command_tree.command(description=spreadsheet.draft.__doc__, guilds=slash_command_servers)
@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def draft(interaction, map_name: str):
    await spreadsheet.draft(interaction, map_name)


@command_tree.command(description=spreadsheet.update_progress.__doc__, guilds=slash_command_servers)
@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def update_progress(interaction, map_name: str, note: str):
    await spreadsheet.update_progress(interaction, map_name, note)


@command_tree.command(description=spreadsheet.progress.__doc__, guilds=slash_command_servers)
@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def progress(interaction, map_name: str):
    await spreadsheet.progress(interaction, map_name)


@command_tree.command(description=spreadsheet.drop.__doc__, guilds=slash_command_servers)
@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def drop(interaction, map_name: str, reason: str):
    await spreadsheet.drop(interaction, map_name, reason)


@command_tree.command(description=spreadsheet.complete.__doc__, guilds=slash_command_servers)
@discord.app_commands.check(spreadsheet.sj_command_allowed)
async def complete(interaction, map_name: str):
    await spreadsheet.complete(interaction, map_name)


@draft.autocomplete('map_name')
@update_progress.autocomplete('map_name')
@progress.autocomplete('map_name')
@drop.autocomplete('map_name')
@complete.autocomplete('map_name')
async def map_autocomplete(interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
    return [discord.app_commands.Choice(name=sj_map, value=sj_map) for sj_map in spreadsheet.sj_fuzzy_match(current.lower())]


log, history_log = main.create_loggers('bot.log')
commands.client = client
main.client = client
main.safe_mode = safe_mode

if __name__ == '__main__':
    start()
