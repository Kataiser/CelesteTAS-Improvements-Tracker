import argparse
import asyncio
import os
import re
import subprocess
import time
from typing import List, Optional

import discord
import dotenv
from discord import app_commands

import commands
import db
import main
import project_editor
import spreadsheet
import tasks
import utils
from constants import admin_user_id, slash_command_servers
from utils import plural

intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.message_content = True

client = discord.Client(intents=intents)
command_tree = app_commands.CommandTree(client)

debug = False
safe_mode = False


def start():
    heartbeat = db.misc.get('heartbeat')
    time_since_heartbeat = int(time.time()) - heartbeat['time']

    if time_since_heartbeat < 180 and heartbeat['host_socket'] != utils.cached_hostname():
        log.warning(f"Bot seems to be running on host {heartbeat['host_socket']} ({heartbeat['host']}), "
                    f"last heartbeat was {time_since_heartbeat} seconds ago. Press any key to start anyway.")
        input()

    global debug, projects_startup
    log.info("Bot starting")
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        log.info("DEBUG MODE")

    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    projects_startup = db.projects.dict()
    main.fast_project_ids = set(projects_startup)
    path_caches_size = db.path_caches.size()
    project_logs_size = db.project_logs.size()
    log.info(f"Loaded {len(projects_startup)} project{plural(projects_startup)}, {project_logs_size} project message log{plural(project_logs_size)}, "
             f"and {path_caches_size} path cache{plural(path_caches_size)}")

    if not len(projects_startup) == project_logs_size == path_caches_size:
        log.warning("Project data component lengths are not equal")

    dotenv.load_dotenv()
    bot_token = os.getenv('BOT_TOKEN')

    while True:
        try:
            log.info("Logging in...")
            client.run(bot_token, log_handler=None)
        except Exception as error:
            if isinstance(error, RuntimeError) and str(error) == "Session is closed":
                log.info("Assuming bot has been closed, not restarting")
                tasks.heartbeat(killed=True)
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
    await command_tree.sync()
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

            try:
                set_status = await main.process_improvement_message(message, project)
            except Exception:
                await utils.report_error(client)

            if set_status:
                set_default_status = False

    log.info(f"Finished considering {downtime_message_count} downtime messages")
    db.project_logs.disable_cache()

    if set_default_status:
        await main.set_status()

    tasks.start_tasks()


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    elif not message.guild:
        await commands.handle_direct_dm(message)
        tasks.start_tasks()
    elif message.channel.id in main.fast_project_ids:
        await client.wait_until_ready()
        await main.process_improvement_message(message)
    elif message.guild.id == 403698615446536203:
        return  # 1985
    elif message.channel.id == 1185382846018359346 and message.embeds and ':master' in message.embeds[0].title:
        updating_text = "New commit found, updating and restarting"
        log.info(updating_text)
        await (await utils.user_from_id(client, admin_user_id)).send(updating_text)
        import psutil
        self_process = psutil.Process()
        subprocess.Popen(f'python updater.py {self_process.pid} {self_process.parent().pid}', creationflags=0x00000010)
        time.sleep(60)

    message_search = re_non_ascii.subn('', message.content.lower())[0]
    user_ids = set()

    if re_1984.findall(message_search):
        user_ids.add(219955313334288385)
    elif re_1984_music.findall(message_search):
        message_split = re.findall(r'\w+|[^\w\s]', message_search)

        if 'music' in message_split or 'song' in message_split:
            user_ids.add(219955313334288385)

    if re_1984_hydro.findall(message_search):
        user_ids.add(236760821286436865)

    if re_1984_cabob.findall(message_search):
        user_ids.add(256796503530536970)

    if re_1984_vamp.findall(message_search):
        user_ids.add(234520815658336258)

    if re_1984_cirion.findall(message_search):
        user_ids.add(243310302995677184)

    if re_1984_minty.findall(message_search):
        user_ids.add(1060652582663630919)

    if re_1984_bot.findall(message_search) and message.guild.id != 1334595934495182861:
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

    if not await has_bot_reaction(message, '❌'):
        return

    log.info("Reprocessing message after edit")

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
    join_message = f"Bot has been added to server: {guild.name} (ID = {guild.id}, owner ID = {guild.owner_id})"
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


@app_commands.check(spreadsheet.sj_command_allowed)
async def draft(interaction, map_name: str):
    await spreadsheet.draft(interaction, map_name)


@app_commands.check(spreadsheet.sj_command_allowed)
async def update_progress(interaction, map_name: str, note: str):
    await spreadsheet.update_progress(interaction, map_name, note)


@command_tree.command(description=spreadsheet.progress.__doc__, guilds=slash_command_servers)
async def progress(interaction, map_name: str):
    await spreadsheet.progress(interaction, map_name)


@app_commands.check(spreadsheet.sj_command_allowed)
async def drop(interaction, map_name: str, reason: str):
    await spreadsheet.drop(interaction, map_name, reason)


@app_commands.check(spreadsheet.sj_command_allowed)
async def complete(interaction, map_name: str):
    await spreadsheet.complete(interaction, map_name)


@app_commands.check(spreadsheet.sj_command_allowed)
async def undraft(interaction, map_name: str):
    await spreadsheet.undraft(interaction, map_name)


@command_tree.command(description=spreadsheet.taser_status.__doc__, guilds=slash_command_servers)
async def taser_status(interaction, taser: str):
    await spreadsheet.taser_status(interaction, taser)


@command_tree.command(description="Get bot installation instructions")
@app_commands.dm_only()
async def help(interaction):
    await commands.command_help(interaction)


@command_tree.command(description="Add or edit a project (improvements channel)")
@app_commands.describe(name="The name or ID of the project")
@app_commands.describe(improvements_channel="The channel for posting improvments, drafts, etc.")
@app_commands.describe(repository="Either as OWNER/REPO, or as OWNER/REPO/PROJECT if you have multiple projects in a repo")
@app_commands.describe(account="Your GitHub account name")
@app_commands.describe(commit_drafts="Automatically commit drafts to the root directory")
@app_commands.describe(is_lobby="Whether this channel is for a lobby, which handles file validation differently")
@app_commands.describe(ensure_level="Whether to make sure the level's name is in the message when validating a posted file")
@app_commands.describe(use_contributors_file="Save a Contributors.txt file")
@app_commands.describe(do_sync_check="Do regular sync tests of all your files by actually running the game (highly recommended)")
@app_commands.guild_only()
async def register_project(interaction, name: str, improvements_channel: discord.TextChannel, repository: str, account: str, commit_drafts: bool, is_lobby: bool, ensure_level: bool,
                               use_contributors_file: bool, do_sync_check: bool):
        await commands.command_register_project(interaction, name, improvements_channel, repository, account, commit_drafts, is_lobby, ensure_level, use_contributors_file, do_sync_check)


@command_tree.command(description="Edit a project (improvements channel)")
@app_commands.describe(project_name="The name or ID of your project. If you have multiple improvement channels with the same project name, this will only update the first")
@app_commands.dm_only()
async def edit_project(interaction, project_name: str):
    await commands.command_edit_project(interaction, project_name)


@command_tree.command(description="Link a lobby project to a routing table, so that improvements will automatically be written to it")
@app_commands.describe(project_name="The name or ID of your project")
@app_commands.describe(sheet="The link to your spreadsheet, e.g. https://docs.google.com/spreadsheets/d/1xY9W_fvKyYYz7E-t_t5UXSpqxECUil2mLY7PdB-ifLc")
@app_commands.describe(cell="The location where the 0-0 connection of the table is, e.g. C2, Beginner!C2 or Beginner Lobby!C2")
@app_commands.dm_only()
async def link_lobby_sheet(interaction, project_name: str, sheet: str, cell: str):
    await commands.command_link_lobby_sheet(interaction, project_name, sheet, cell)


@command_tree.command(description="Set the game mods a sync check needs to load")
@app_commands.describe(project_name="The name or ID of your project")
@app_commands.describe(mods="The mod(s) used by your project, separated by spaces (dependencies are automatically handled). Ex: EGCPACK, WinterCollab2021, \"Monika's D-Sides\"")
@app_commands.dm_only()
async def add_mods(interaction, project_name: str, mods: str):
    await commands.command_add_mods(interaction, project_name, mods)


@command_tree.command(description="Rename a file in the repo of a project, recommended over manually committing")
@app_commands.describe(project_name="The name or ID of your project")
@app_commands.describe(filename_before="The current name of the TAS file you want to rename (with .tas)")
@app_commands.describe(filename_after="What you want the TAS file to be renamed to (with .tas)")
@app_commands.dm_only()
async def rename_file(interaction, project_name: str, filename_before: str, filename_after: str):
    await commands.command_rename_file(interaction, project_name, filename_before, filename_after)


@command_tree.command(description="Add or remove admins from a project")
@app_commands.describe(project_name="The name or ID of your project")
@app_commands.guild_only()
async def edit_admins(interaction, project_name: str):
    await commands.command_edit_admins(interaction, project_name)


@command_tree.command(description="Get bot info and status")
@app_commands.dm_only()
async def about(interaction):
    await commands.command_about(interaction)


@command_tree.command(description="Get the info and settings of a project")
@app_commands.describe(project_name="The name or ID of your project")
@app_commands.dm_only()
async def about_project(interaction, project_name: str):
    await commands.command_about_project(interaction, project_name)


@command_tree.command(description="Get the basic info and settings of all projects")
@app_commands.dm_only()
async def projects(interaction):
    await commands.command_projects(interaction)


@command_tree.command(description="List projects you're an admin of")
@app_commands.dm_only()
async def projects_admined(interaction):
    await commands.command_projects_admined(interaction)


@command_tree.command(description="Set your Github account information for commit crediting")
@app_commands.describe(account_name="The username (not display name) of the account")
@app_commands.describe(email="The email of the account. Will NOT be private because of how git works")
@app_commands.dm_only()
async def set_github(interaction, account_name: str, email: str):
    await commands.command_set_github(interaction, account_name, email)


@command_tree.command(description="Remove your Github account information for commit crediting")
@app_commands.dm_only()
async def remove_github(interaction):
    await commands.command_remove_github(interaction)


@command_tree.command(description="Check your Github account information for commit crediting")
@app_commands.dm_only()
async def check_github(interaction):
    await commands.command_check_github(interaction)


@progress.autocomplete('map_name')
async def map_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    return [app_commands.Choice(name=sj_map, value=sj_map) for sj_map in spreadsheet.sj_fuzzy_match(current.lower())]


def remove_project_log(message: discord.Message):
    project_logs = db.project_logs.get(message.channel.id)
    if message.id in project_logs:
        project_logs.remove(message.id)
        db.project_logs.set(message.channel.id, project_logs)


async def has_bot_reaction(message: discord.Message, emoji: str):
    for reaction in message.reactions:
        if reaction.emoji == emoji:
            async for user in reaction.users():
                if user.id == client.user.id:
                    return True
    return False


def share_client(client_: discord.Client):
    commands.client = client_
    project_editor.client = client_
    spreadsheet.client = client_
    main.client = client_
    tasks.client = client_


log = main.create_logger('bot')
share_client(client)
main.safe_mode = safe_mode
projects_startup = Optional[dict]
substrings_1984 = ('kataiser', 'kata', 'kori', 'warm fish', 'jaded', 'psycabob', 'shadowdrop mix', 'cosmic brain', 'pndcm', '<@970375635027525652>')
substrings_1984_music = ('lab', 'psychokinetic', 'pk ', 'superluminary')
substrings_1984_hydro = ('shatter', 'shong', 'shattersong', 'hydro', 'penumbra', 'reversion', 'lightspeed')
re_1984 = re.compile('|'.join(fr'\b{re.escape(sub)}\b' for sub in substrings_1984))
re_1984_music = re.compile('|'.join(fr'\b{re.escape(sub)}\b' for sub in substrings_1984_music))
re_1984_hydro = re.compile('|'.join(fr'\b{re.escape(sub)}\b' for sub in substrings_1984_hydro) + r'|\bss\+(?=\b|\W|$)')
re_1984_cabob = re.compile(r'\bcabob\b')
re_1984_vamp = re.compile(r'\bvamp\b')
re_1984_cirion = re.compile(r'\bsussy\b')
re_1984_minty = re.compile(r'\benigma\b')
re_1984_bot = re.compile(r'\bbot\b|\btracker\b')
re_non_ascii = re.compile(r'[^\x00-\x7F]')

if __name__ == '__main__':
    start()
