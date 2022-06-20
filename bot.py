import argparse
import ctypes
import datetime
import time
import traceback

import discord
import psutil
from discord.ext import tasks

import dm
import game_sync
import main
import utils
from utils import plural, projects

client = discord.Client()
debug = False


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
            nightly.start()
            log.info("Logging in...")
            client.run(bot_token)
        except Exception as error:
            log.error(error)

            if not debug:
                log.info("Restarting bot in 5 seconds, this can only end well")
                time.sleep(5)
            else:
                break


@client.event
async def on_ready():
    log.info(f"Logged in as {client.user}")
    main.login_time = time.time()
    log.info(f"Servers: {[f'{g.name} ({g.member_count})' for g in client.guilds]}")
    downtime_message_count = 0

    for improvements_channel_id in projects:
        improvements_channel = client.get_channel(improvements_channel_id)

        if not improvements_channel:
            log.error(f"Can't access improvements channel for project {projects[improvements_channel_id]['name']}")
            continue

        downtime_messages = await client.get_channel(improvements_channel_id).history(limit=10).flatten()
        downtime_messages.reverse()  # make chronological

        for message in downtime_messages:
            downtime_message_count += 1
            await main.process_improvement_message(message)

    log.info(f"Finished considering {downtime_message_count} downtime messages")

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
        await dm.handle(message)
        return
    elif message.channel.id not in projects:
        return

    await main.process_improvement_message(message)


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await client.wait_until_ready()

    if payload.channel_id in projects:
        past_messages = await client.get_channel(payload.channel_id).history(limit=20).flatten()

        for message in past_messages:
            if message.reference and message.reference.message_id == payload.message_id and message.author == client.user:
                await message.delete()
                log.info(f"Deleted bot reply message in project: {projects[payload.channel_id]['name']}")
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
dm.client = client
game_sync.client = client


if __name__ == '__main__':
    start()
