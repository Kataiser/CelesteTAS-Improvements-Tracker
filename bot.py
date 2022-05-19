import argparse
import logging
import sys
import time
import traceback
from typing import Optional

import discord
import psutil

import dm
import game_sync
import gen_token
import main
import utils
import validation
from utils import plural

client = discord.Client()
debug = False


def bot():
    global debug, projects
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help="Debug mode", default=False)
    debug = parser.parse_args().debug

    if debug:
        print("DEBUG MODE")

    utils.load_projects()
    projects = utils.projects
    main.projects = projects
    main.load_project_logs()
    log.info(f"Loaded {len(projects)} project{plural(projects)} and {len(main.project_logs)} project message log{plural(main.project_logs)}")

    with open('bot_token', 'r') as bot_token_file:
        bot_token = bot_token_file.read()

    while True:
        try:
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
    downtime_message_count = 0

    if not debug:
        self_process = psutil.Process()
        self_process.nice(psutil.IDLE_PRIORITY_CLASS)
        self_process.ionice(psutil.IOPRIO_VERYLOW)
        log.info("Set process priorities")
    else:
        log.info("Skipped setting priorities")

    for improvements_channel in projects:
        downtime_messages = await client.get_channel(improvements_channel).history(limit=20).flatten()
        downtime_messages.reverse()  # make chronological

        for message in downtime_messages:
            downtime_message_count += 1
            await main.process_improvement_message(message)

    log.info(f"Finished considering {downtime_message_count} downtime messages")


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
async def on_connect():
    log.info("Connected to Discord")


@client.event
async def on_disconnect():
    log.error("Disconnected from Discord")


@client.event
async def on_error(event: str):
    error = traceback.format_exc()
    log.error(error)


def create_loggers() -> (logging.Logger, logging.Logger):
    logger = logging.getLogger('bot')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(filename='bot.log', encoding='UTF8', mode='w')
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler(sys.stdout))

    history = logging.getLogger('history')
    history.setLevel(logging.DEBUG)
    handler = logging.FileHandler(filename='history.log', encoding='UTF8', mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    history.addHandler(handler)

    main.log = logger
    gen_token.log = logger
    validation.log = logger
    utils.log = logger
    dm.log = logger
    game_sync.log = logger
    main.history_log = history
    dm.history_log = history

    return logger, history


log, history_log = create_loggers()
projects: Optional[dict] = None
dm.client = client


if __name__ == '__main__':
    bot()
