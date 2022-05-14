import logging
import os
import re
import time
import zipfile
from typing import Optional

import discord
import requests
import yaml

import utils
from utils import plural, projects


async def handle(message: discord.Message):
    log.info(f"Recieved DM from {utils.detailed_user(message)}: \"{message.content}\"")
    command = message.content.partition(' ')[0]

    if command in command_functions:
        await command_functions[command](message)
    else:
        await message.channel.send("Unrecognized command, try `help`")


async def command_help(message: discord.Message):
    """"""

    log.info("Handling 'help' command")
    message_split = message.content.split()

    if len(message_split) > 1 and message_split[1] in command_functions:
        try:
            command_doc = command_functions[message_split[1]].__doc__.replace('\n    ', '\n')
            await message.channel.send(f"```\n{command_doc}```")
        except AttributeError:
            log.error(f"{command_functions[message_split[1]]} has no docstring")
    else:
        response = "Alright, looks you want to add your TAS project to this bot (or are just curious about what the help command says). Awesome! So, steps:" \
                   "\n\n1. Contact Kataiser#4640 that you're adding a new project. Theoretically this process doesn't need him, but realistically it's probably broken and/or janky, " \
                   "and also he'd like to know. Maybe this step can be removed at some point." \
                   "\n2. Register GitHub app with your account and repo (you don't need to be the repo owner, admin permissions are enough): " \
                   "<https://github.com/apps/celestetas-improvements-tracker>" \
                   "\n3. Add bot to your server: <https://discord.com/api/oauth2/authorize?client_id=970375635027525652&permissions=76864&scope=bot>" \
                   "\n4. *Please* disable the View Channels permissions for categories the improvements channel isn't in, as well as other channels in that category. This is because " \
                   "otherwise every message in every server the bot's in will be processed, and since the bot is being hosted on Kataiser's machine, " \
                   "he doesn't want that background CPU usage." \
                   "\n5. Run the `register_project` command, see `help register_project` for parameters." \
                   "\n\nAvailable commands:" \
                   "\n```help\nregister_project\nadd_mods\nadd_category```"

        await message.channel.send(response)


async def command_register_project(message: discord.Message):
    """
    register_project NAME IMPROVEMENTS_CHANNEL_ID REPOSITORY ACCOUNT COMMIT_DRAFTS

      NAME: The name of the project (with underscores instead of spaces), ex: Into_the_Jungle, Strawberry_Jam, Celeste_maingame, Celeste_mindash
      IMPROVEMENTS_CHANNEL_ID: Turn on developer mode in Discord advanced setting, then right click the channel and click Copy ID
      REPOSITORY: Either as OWNER/REPO, or as OWNER/REPO/PROJECT if you have multiple projects in a repo
      ACCOUNT: Your GitHub account name
      COMMIT_DRAFTS: Automatically commit drafts to the root directory, Y or N
    """

    log.info("Handling 'register_project' command")
    message_split = message.content.split()

    if len(message_split) != 6 or not re.match(r'register_project \d+ .+/.+ .+ [YyNn]', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help`")
        return

    log.info("Verifying project")
    await message.channel.send("Verifying...")
    _, name, improvements_channel_id, repo_and_subdir, account, commit_drafts = message_split
    improvements_channel_id = int(improvements_channel_id)
    editing = improvements_channel_id in projects

    if editing:
        log.warning("This project already exists, preserving some settings")
        previous = {'pin': projects[improvements_channel_id]['pin'],
                    'mods': projects[improvements_channel_id]['mods'],
                    'path_cache': projects[improvements_channel_id]['path_cache']}

    # verify improvements channel exists
    improvements_channel = client.get_channel(improvements_channel_id)
    if not improvements_channel:
        error = f"Channel {improvements_channel_id} doesn't exist"
        log.error(error)
        await message.channel.send(error)
        return

    # verify repo exists
    repo_split = repo_and_subdir.split('/')
    repo, subdir = '/'.join(repo_split[:2]), '/'.join(repo_split[2:])
    r = requests.get(f'https://api.github.com/repos/{repo}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        log.error(f"Repo {repo} doesn't seem to publically exist, status code is {r.status_code}")
        await message.channel.send(f"Repo \"{repo}\" doesn't seem to publically exist")
        return

    # verify subdir exists in repo
    if subdir:
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{subdir}', headers={'Accept': 'application/vnd.github.v3+json'})
        if r.status_code != 200 or 'type' in r.json():
            log.error(f"Directory {subdir} doesn't seem to exist in repo {repo}, status code is {r.status_code}")
            await message.channel.send(f"Directory \"{subdir}\" doesn't seem to exist in \"{repo}\"")
            return

    # verify account exists
    r = requests.get(f'https://api.github.com/users/{account}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        log.error(f"GitHub account {account} doesn't seem to exist, status code is {r.status_code}")
        await message.channel.send(f"GitHub account \"{account}\" doesn't seem to exist")
        return

    # verify committing drafts
    if commit_drafts.lower() not in ('y', 'n'):
        log.error(f"Committing drafts is {commit_drafts}, not Y or N")
        await message.channel.send(f"Whether to commit drafts should be Y or N, not {commit_drafts}")
        return

    log.info("Verification successful")

    if editing:
        pinned_message = await utils.edit_pin(improvements_channel)
        await pinned_message.pin()
        log.info("Created pinned message")
    else:
        log.info("Skipped creating pinned message")

    projects[improvements_channel_id] = {'name': name.replace('_', ' '),
                                         'repo': repo,
                                         'installation_owner': account,
                                         'commit_drafts': commit_drafts.lower() == 'y',
                                         'install_time': int(time.time()),
                                         'pin': previous['pin'] if editing else pinned_message.id,
                                         'do_run_validation': False,
                                         'subdir': subdir,
                                         'mods': previous['mods'] if editing else [],
                                         'path_cache': previous['path_cache'] if editing else {}}
    # TODO: handle do_run_validation

    utils.save_projects()
    project_added_log = f"Added project {improvements_channel_id}: {projects[improvements_channel_id]}"
    log.info(project_added_log)
    history_log.info(project_added_log)
    await message.channel.send("Successfully verified and added project! If you want to change your project's settings, "
                               "run the command again and it will overwrite what was there before.")


async def command_add_mods(message: discord.Message):
    """
    add_mods IMPROVEMENTS_CHANNEL_ID MODS

      IMPROVEMENTS_CHANNEL_ID: Turn on developer mode in Discord advanced setting, then right click the channel and click Copy ID
      MODS: The mod(s) used by your project, separated by spaces (dependencies are automatically handled). Ex: EGCPACK, WinterCollab2021, conquerorpeak103
    """

    log.info("Handling 'add_mods' command")
    message_split = message.content.split()

    if len(message_split) < 3 or not re.match(r'add_mods \d+ .+', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help add_mods`")
        return

    project_id = int(message_split.pop(1))
    mods_given = [mod.removesuffix('.zip') for mod in message_split[1:]]
    project_mods = set(projects[project_id]['mods'])
    log.info(f"{len(project_mods)} mod(s) before adding: {project_mods}")
    project_mods = project_mods.union(mods_given)

    for mod_given in mods_given:
        project_mods = project_mods.union(get_mod_dependencies(mod_given))

    log.info(f"{len(project_mods)} mod(s) after adding: {project_mods}")
    projects[project_id]['mods'] = list(project_mods)
    utils.save_projects()
    mods_missing = set()
    installed_mods = [item.removesuffix('.zip') for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]

    for mod in project_mods:
        if mod not in installed_mods:
            mods_missing.add(mod)

    await message.channel.send(f"Project \"{projects[project_id]['name']}\" now has {len(project_mods)} mod{plural(project_mods)} to load for sync testing")

    if mods_missing:
        log.warning(f"Missing {len(mods_missing)} mod(s) from installed: {mods_missing}")
        mods_missing_formatted = '\n'.join(sorted(mods_missing))
        await (await client.fetch_user(219955313334288385)).send(f"hey you need to install some mods for sync testing\n```\n{mods_missing_formatted}```")
        await message.channel.send(f"The following mod(s) are not currently prepared for sync testing (Kataiser has been automatically DM'd about it):\n```\n{mods_missing_formatted}```")


async def command_add_category(message: discord.Message):
    """"""

    await message.channel.send("Not yet implemented")


# TODO: make recursive (if necessary)
def get_mod_dependencies(mod: str) -> list:
    zip_path = f'E:\\Big downloads\\celeste\\Mods\\{mod}.zip'

    if not os.path.isfile(zip_path):
        return []

    with zipfile.ZipFile(zip_path) as mod_zip:
        if zipfile.Path(mod_zip, 'everest.yaml').is_file():
            with mod_zip.open('everest.yaml') as everest_yaml:
                mod_everest = yaml.safe_load(everest_yaml)
        else:
            return []

    return [d['Name'] for d in mod_everest[0]['Dependencies'] if d['Name'] != 'Everest']


command_functions = {'help': command_help, 'register_project': command_register_project, 'add_category': command_add_category, 'add_mods': command_add_mods}
client: Optional[discord.Client] = None
log: Optional[logging.Logger] = None
history_log: Optional[logging.Logger] = None
