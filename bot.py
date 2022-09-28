import argparse
import ctypes
import datetime
import time
import traceback

import discord
import psutil
from discord.ext import tasks

import commands
import game_sync
import main
import utils
from utils import plural, projects


intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.message_content = True

client = discord.Client(intents=intents)
debug = False
safe_mode = False


def start():
    global debug
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        print("DEBUG MODE")

    main.load_project_logs()
    utils.load_path_caches()
    log.info(f"Loaded {len(projects)} project{plural(projects)}, {len(main.project_logs)} project message log{plural(main.project_logs)}, "
             f"and {len(main.path_caches)} path cache{plural(main.path_caches)}")

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
    log.info(f"Servers: {[f'{g.name} ({g.member_count})' for g in client.guilds]}")
    downtime_message_count = 0
    projects_to_scan = main.safe_projects if safe_mode else projects

    for improvements_channel_id in projects_to_scan:
        improvements_channel = client.get_channel(improvements_channel_id)

        if not improvements_channel:
            log.error(f"Can't access improvements channel for project {projects[improvements_channel_id]['name']}")
            continue

        for message in reversed([m async for m in improvements_channel.history(limit=2 if debug else 10)]):
            downtime_message_count += 1
            await main.process_improvement_message(message)

    log.info(f"Finished considering {downtime_message_count} downtime messages")

    try:
        nightly.start()
    except RuntimeError as error:
        if str(error) == "Task is already launched and is not completed.":
            log.warning("Skipped starting nightly task")
        else:
            raise

    if not debug:
        self_process = psutil.Process()
        self_process.nice(psutil.IDLE_PRIORITY_CLASS)
        self_process.ionice(psutil.IOPRIO_VERYLOW)
        log.info("Set process priorities")
    else:
        log.info("Skipped setting priorities")


@client.event
async def on_message(message: discord.Message):
    await client.wait_until_ready()

    if message.author == client.user:
        return
    elif not message.guild:
        await commands.handle(message)
        return
    elif message.channel.id not in projects:
        return

    await main.process_improvement_message(message)


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await client.wait_until_ready()

    if payload.channel_id in projects:
        async for message in client.get_channel(payload.channel_id).history(limit=20):
            if message.reference and message.reference.message_id == payload.message_id and message.author == client.user:
                await message.delete()
                log.info(f"Deleted bot reply message in project: {projects[payload.channel_id]['name']}")
                break


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await client.wait_until_ready()

    if payload.emoji.name == '⏭' and payload.channel_id in projects:
        for project_id in projects:
            if payload.message_id in main.project_logs[project_id]:
                message = await client.get_channel(payload.channel_id).fetch_message(payload.message_id)

                if payload.user_id in (message.author.id, projects[project_id]['admin'], 219955313334288385):
                    request_user = await client.fetch_user(payload.user_id)
                    log.info(f"{utils.detailed_user(user=request_user)} has requested committing invalid post")
                    await message.clear_reaction('⏭')
                    await message.reply(f"{request_user.mention} has requested committing invalid post")
                    await main.process_improvement_message(message, skip_validation=True)

                break


@tasks.loop(hours=2)
async def nightly():
    if datetime.datetime.now().hour in (4, 5):
        await game_sync.run_syncs()


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


log, history_log = main.create_loggers()
commands.client = client
game_sync.client = client
main.safe_mode = safe_mode


if __name__ == '__main__':
    start()
