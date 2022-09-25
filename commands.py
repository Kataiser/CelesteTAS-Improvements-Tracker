import base64
import json
import logging
import os
import re
import time
from typing import Optional

import discord
import requests
import win32api

import game_sync
import main
import utils
from utils import plural, projects


async def handle(message: discord.Message):
    log.info(f"Recieved DM from {utils.detailed_user(message)}: \"{message.content}\"")
    command = message.content.partition(' ')[0].lower()

    if command in command_functions:
        log.info(f"Handling '{command}' command")
        await command_functions[command](message)
    else:
        await message.channel.send("Unrecognized command, try `help`")


async def command_help(message: discord.Message):
    """
    help COMMAND

      COMMAND: The command to get the parameter info for (optional)
    """

    message_split = message.content.split()

    if len(message_split) > 1 and message_split[1] in command_functions:
        try:
            command_doc = command_functions[message_split[1]].__doc__.replace('\n    ', '\n')
            await message.channel.send(f"```\n{command_doc}```")
        except AttributeError:
            log.error(f"{command_functions[message_split[1]]} has no docstring")
    else:
        add_bot_link = discord.utils.oauth_url('970375635027525652', permissions=discord.Permissions(76864))
        commands_available = '\n'.join(command_functions)

        response = "Alright, looks you want to add your TAS project to this bot (or are just curious about what the help command says). Awesome! So, steps:" \
                   "\n\n1. Tell Kataiser that you're adding a new project. Theoretically this process doesn't need him, but realistically it's probably broken and/or janky, " \
                   "and also he'd like to know. Maybe this step can be removed at some point." \
                   "\n2. Register GitHub app with your account and repo (you don't need to be the repo owner, admin permissions are enough): " \
                   "<https://github.com/apps/celestetas-improvements-tracker>" \
                   f"\n3. Add bot to your server: <{add_bot_link}>" \
                   "\n4. Disable the View Channels permissions for all channels that are not the improvements channel. This is because otherwise every message in every server the bot's in " \
                   "will be processed, and since the bot is being hosted on Kataiser's machine, he doesn't want that background CPU usage." \
                   "\n5. Run the `register_project` command, see `help register_project` for parameters." \
                   "\n\nAvailable commands:" \
                   f"\n```\n{commands_available}```"

        await message.channel.send(response)


async def command_register_project(message: discord.Message):
    """
    register_project NAME IMPROVEMENTS_CHANNEL_ID REPOSITORY ACCOUNT COMMIT_DRAFTS IS_LOBBY ENSURE_LEVEL DO_SYNC_CHECK

      NAME: The name of the project (in quotes), ex: "Into the Jungle", "Strawberry Jam", "Celeste maingame", "Celeste mindash"
      IMPROVEMENTS_CHANNEL_ID: Turn on developer mode in Discord advanced settings, then right click the channel and click Copy ID
      REPOSITORY: Either as OWNER/REPO, or as OWNER/REPO/PROJECT if you have multiple projects in a repo
      ACCOUNT: Your GitHub account name
      COMMIT_DRAFTS: Automatically commit drafts to the root directory [Y or N]
      IS_LOBBY: Whether this channel is for a lobby, which handles file validation differently [Y or N]
      ENSURE_LEVEL: Whether to make sure the level's name is in the message when validating a posted file [Y or N]
      DO_SYNC_CHECK: Do a nightly sync test of all your files by actually running the game on Kataiser's PC (recommended) [Y or N]
    """

    message_split = re_command_split.split(message.content)

    if len(message_split) != 9 or not re.match(r'(?i)register_project .+ \d+ .+/.+ .+ [YN] [YN] [YN] [YN]', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help register_project`")
        return

    log.info("Verifying project")
    await message.channel.send("Verifying...")
    _, name, improvements_channel_id, repo_and_subdir, github_account, commit_drafts, is_lobby, ensure_level, do_run_validation = message_split
    improvements_channel_id = int(improvements_channel_id)
    editing = improvements_channel_id in projects

    if editing:
        if await not_admin(message, improvements_channel_id):
            return

        log.warning("This project already exists, preserving some keys")
        previous = {'pin': projects[improvements_channel_id]['pin'],
                    'mods': projects[improvements_channel_id]['mods'],
                    'last_run_validation': projects[improvements_channel_id]['last_run_validation']}

    # verify improvements channel exists
    improvements_channel = client.get_channel(improvements_channel_id)
    if not improvements_channel:
        error = f"Channel {improvements_channel_id} doesn't exist"
        log.error(error)
        await message.channel.send(error)
        return

    # verify needed permissions in improvements channel
    improvements_channel_permissions = improvements_channel.permissions_for(improvements_channel.guild.me)
    permissions_needed = {'View Channel': improvements_channel_permissions.read_messages,
                          'Send Messages': improvements_channel_permissions.send_messages,
                          'Read Messages': improvements_channel_permissions.read_messages,
                          'Read Message History': improvements_channel_permissions.read_message_history,
                          'Add Reactions': improvements_channel_permissions.add_reactions}

    for permission in permissions_needed:
        if not permissions_needed[permission]:
            error = f"Don't have {permission} permission for #{improvements_channel.name} ({improvements_channel_id})"
            log.error(error)
            await message.channel.send(error)
            return

    # verify github account exists
    r = requests.get(f'https://api.github.com/users/{github_account}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        log.error(f"GitHub account {github_account} doesn't seem to exist, status code is {r.status_code}")
        await message.channel.send(f"GitHub account \"{github_account}\" doesn't seem to exist")
        return

    # verify repo exists
    repo_split = repo_and_subdir.rstrip('/').split('/')
    repo, subdir = '/'.join(repo_split[:2]), '/'.join(repo_split[2:])
    main.generate_request_headers(github_account)
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

    # verify not adding run validation to a lobby
    if do_run_validation.lower() == 'y' and is_lobby.lower() == 'y':
        log.error("Can't add run validation to a lobby project")
        await message.channel.send("Enabling run validation for a lobby project is not allowed")
        return

    log.info("Verification successful")

    current_time = int(time.time())
    projects[improvements_channel_id] = {'name': name.replace('"', ''),
                                         'repo': repo,
                                         'installation_owner': github_account,
                                         'admin': message.author.id,
                                         'install_time': current_time,
                                         'commit_drafts': commit_drafts.lower() == 'y',
                                         'is_lobby': is_lobby.lower() == 'y',
                                         'ensure_level': ensure_level.lower() == 'y',
                                         'do_run_validation': do_run_validation.lower() == 'y',
                                         'last_run_validation': None,
                                         'pin': None,
                                         'subdir': subdir,
                                         'mods': [],
                                         'desyncs': [],
                                         'last_commit_time': current_time}

    if not editing:
        await message.channel.send("Generating path cache...")
        main.generate_path_cache(improvements_channel_id)
        pinned_message = await main.edit_pin(improvements_channel, create=True)
        await pinned_message.pin()
        projects[improvements_channel_id]['pin'] = pinned_message.id
        main.project_logs[improvements_channel_id] = []
    else:
        log.info("Skipped creating pinned message")

        for previous_key in previous:
            projects[improvements_channel_id][previous_key] = previous[previous_key]

    utils.save_projects()
    project_added_log = f"{'Edited' if editing else 'Added'} project {improvements_channel_id}: {projects[improvements_channel_id]}"
    log.info(project_added_log)
    history_log.info(project_added_log)

    if editing:
        await message.channel.send("Successfully verified and edited project.")
    else:
        add_mods_text = " Since you are doing sync checking, be sure to add mods (if need be) with the command `add_mods`."  if do_run_validation.lower() == 'y' else ""
        await message.channel.send("Successfully verified and added project! If you want to change your project's settings, "
                                   f"run the command again and it will overwrite what was there before.{add_mods_text}")


async def command_add_mods(message: discord.Message):
    """
    add_mods PROJECT_NAME MODS

      PROJECT_NAME: The name of your project (in quotes). If you have multiple improvement channels with the same project name, this will update all of them
      MODS: The mod(s) used by your project, separated by spaces (dependencies are automatically handled). Ex: EGCPACK, WinterCollab2021, conquerorpeak103
    """

    message_split = re_command_split.split(message.content)

    if len(message_split) < 3 or not re.match(r'(?i)add_mods .+ .+', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help add_mods`")
        return

    project_search_name = message_split[1].replace('"', '').lower()
    project_mods_added = False

    for project_id in projects:
        project = projects[project_id]

        if project['name'].lower() != project_search_name:
            continue
        elif await not_admin(message, project_id):
            break
        elif not project['do_run_validation']:
            log.warning(f"Trying to add mods to project: {project['name']}, but run validation is disabled")
            await message.channel.send(f"Project \"{project['name']}\" has sync checking disabled")
            continue

        log.info(f"Adding mods for project: {project['name']}")
        project_mods_added = True
        mods_given = [mod.removesuffix('.zip') for mod in message_split[2:]]
        project_mods = set(project['mods'])
        log.info(f"{len(project_mods)} mod{plural(project_mods)} before adding: {project_mods}")
        project_mods = project_mods.union(mods_given)
        log.info(f"{len(project_mods)} mod{plural(project_mods)} after adding: {project_mods}")
        project['mods'] = list(project_mods)
        utils.save_projects()
        mods_missing = set()

        for mod_given in mods_given:
            all_project_mods = project_mods.union(game_sync.get_mod_dependencies(mod_given))

        log.info(f"{len(all_project_mods)} total mod{plural(all_project_mods)}: {all_project_mods}")
        installed_mods = [item.removesuffix('.zip') for item in os.listdir(r'E:\Big downloads\celeste\Mods') if item.endswith('.zip')]

        for mod in all_project_mods:
            if mod not in installed_mods:
                mods_missing.add(mod)

        await message.channel.send(f"Project \"{project['name']}\" now has {len(all_project_mods)} mod{plural(all_project_mods)} to load for sync testing")

        if mods_missing:
            log.warning(f"Missing {len(mods_missing)} mod(s) from installed: {mods_missing}")
            mods_missing_formatted = '\n'.join(sorted(mods_missing))
            await (await client.fetch_user(219955313334288385)).send(f"hey you need to install some mods for sync testing\n```\n{mods_missing_formatted}```")
            await message.channel.send(f"The following mod(s) are not currently prepared for sync testing (Kataiser has been automatically DM'd about it):\n```\n{mods_missing_formatted}```")

    if not project_mods_added:
        log.warning(f"No projects found matching: {project_search_name}")
        await message.channel.send("No projects (with sync checking enabled) matching that name found")


async def command_add_category(message: discord.Message):
    """Not yet implemented"""

    await message.channel.send("Not yet implemented")


async def command_run_sync_check(message: discord.Message):
    """
    run_sync_check PROJECT_NAME

      PROJECT_NAME: The name of your project (in quotes). If you have multiple improvement channels with the same project name, this will run it for all of them
    """

    message_split = re_command_split.split(message.content)

    if len(message_split) != 2 or not re.match(r'(?i)run_sync_check .+', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help run_sync_check`")
        return

    project_search_name = message_split[1].replace('"', '').lower()
    matched_projects = False
    idle_time = (win32api.GetTickCount() - win32api.GetLastInputInfo()) / 1000
    log.info(f"Idle time: {idle_time} seconds")

    if idle_time < 720 and message.author.id != 219955313334288385:  # 12 mins
        await message.channel.send("Kataiser is not currently AFK, so maybe running the game on his PC is not a great idea right now")
        return

    for project_id in projects:
        project = projects[project_id]

        if project['name'].lower() != project_search_name:
            continue
        elif await not_admin(message, project_id):
            break

        matched_projects = True

        if not project['do_run_validation']:
            log.warning(f"Trying to do run validation for project: {project['name']}, but it's disabled")
            await message.channel.send(f"Project \"{project['name']}\" has sync checking disabled")
            continue

        if not main.path_caches[project_id]:
            main.generate_path_cache(project_id)

        if not main.path_caches[project_id]:
            log.warning(f"Trying to do run validation for project: {project['name']}, but it has no files")
            await message.channel.send(f"Project \"{project['name']}\" seems to have no files to sync check")
            continue

        await message.channel.send(f"Running sync check for project \"{project['name']}\"...")

        try:
            await game_sync.sync_test(project_id, message.channel)
        except Exception:
            await game_sync.close_game()
            game_sync.post_cleanup()
            raise

    if not matched_projects:
        log.warning(f"No projects found matching: {project_search_name}")
        await message.channel.send("No projects (with sync checking enabled) matching that name found")


async def command_run_sync_checks(message: discord.Message):
    """
    run_sync_checks

      (No parameters)
    """

    await game_sync.run_syncs(message.channel)


async def command_rename_file(message: discord.Message):
    """
    rename_file PROJECT_NAME FILENAME_BEFORE FILENAME_AFTER

      PROJECT_NAME: The name of your project (in quotes). If you have multiple improvement channels with the same project name, this will search in all of them
      FILENAME_BEFORE: The current name of the TAS file you want to rename (with .tas)
      FILENAME_AFTER: What you want the TAS file to be renamed to (with .tas)
    """

    message_split = re_command_split.split(message.content)

    if len(message_split) != 4 or not re.match(r'(?i)rename_file .+ .+\.tas .+\.tas', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help rename_file`")
        return

    project_search_name = message_split[1].replace('"', '').lower()
    filename_before, filename_after = message_split[2:]
    renamed_file = False

    if filename_before == filename_after:
        await message.channel.send("what")
        return

    for project_id in projects:
        project = projects[project_id]

        if project['name'].lower() != project_search_name:
            continue

        main.generate_request_headers(project['installation_owner'])
        main.generate_path_cache(project_id)

        if filename_before not in main.path_caches[project_id]:
            log.warning(f"{filename_before} not in project: {project['name']}")

        renaming_text = f"Renaming {filename_before} to {filename_after} in project \"{project['name']}\""
        log.info(renaming_text)
        await message.channel.send(renaming_text)
        repo = project['repo']
        file_path = main.path_caches[project_id][filename_before]
        renamed_file = True
        user_github_account = main.get_user_github_account(message.author.id)

        log.info(f"Downloading {filename_before}")
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=main.headers)
        utils.handle_potential_request_error(r, 200)
        tas_downloaded = base64.b64decode(r.json()['content'])

        # commit 1: delete old file
        log.info("Performing delete commit")
        data = {'message': f"Renamed {filename_before} to {filename_after} (deleting)", 'sha': main.get_sha(repo, file_path)}
        if user_github_account:
            data['author'] = {'name': user_github_account[0], 'email': user_github_account[1]}
            log.info(f"Setting commit author to {data['author']}")
        r = requests.delete(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=main.headers, data=json.dumps(data))
        utils.handle_potential_request_error(r, 200)
        time.sleep(1)  # just to be safe

        # commit 2: create new file
        log.info("Performing recreate commit")
        file_path = file_path.replace(filename_before, filename_after)
        data = {'message': f"Renamed {filename_before} to {filename_after} (creating)", 'content': base64.b64encode(tas_downloaded).decode('UTF8')}
        if user_github_account:
            data['author'] = {'name': user_github_account[0], 'email': user_github_account[1]}
            log.info(f"Setting commit author to {data['author']}")
        r = requests.put(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=main.headers, data=json.dumps(data))
        utils.handle_potential_request_error(r, 201)

        del main.path_caches[project_id][filename_before]
        main.path_caches[project_id][filename_after] = file_path
        utils.save_path_caches()
        log.info("Rename successful")
        await message.channel.send("Rename successful")
        improvements_channel = client.get_channel(project_id)
        await improvements_channel.send(f"{message.author.mention} renamed `{filename_before}` to `{filename_after}`")
        await main.edit_pin(improvements_channel)

    if not renamed_file:
        log.warning("No files renamed")
        await message.channel.send(f"{filename_before} not found in any project named {project_search_name}")


async def command_about(message: discord.Message):
    """
    about

      (No parameters)
    """

    text = "Source: <https://github.com/Kataiser/CelesteTAS-Improvements-Tracker>" \
           "\nProjects (improvement channels): {0}" \
           "\nServers: {1}" \
           "\nGithub installations: {2}" \
           "\nCurrent uptime: {3} hours" \
           "\nNightly sync check: {4} project{6}" \
           "\nImprovements/drafts processed and committed: {5}"

    sync_checks = 0
    installations = set()

    for project_id in projects:
        installations.add(projects[project_id]['installation_owner'])

        if projects[project_id]['do_run_validation']:
            sync_checks += 1

    with open('history.log', 'r') as history_log_file:
        commits_made = len([line for line in history_log_file if 'Added project' not in line and 'Edited project' not in line])

    text_out = text.format(len(projects),
                           len(client.guilds),
                           len(installations),
                           round((time.time() - main.login_time) / 3600, 1),
                           sync_checks,
                           commits_made,
                           plural(sync_checks))

    log.info(text_out)
    await message.channel.send(text_out)


async def command_about_project(message: discord.Message):
    """
    about PROJECT_NAME

      PROJECT_NAME: The name of your project (in quotes). If you have multiple improvement channels with the same project name, this will show info for all of them
    """

    message_split = re_command_split.split(message.content)

    if len(message_split) != 2 or not re.match(r'(?i)about_project .+', message.content):
        log.warning("Bad command format")
        await message.channel.send("Incorrect command format, see `help about_project`")
        return

    project_search_name = message_split[1].replace('"', '').lower()
    found_matching_project = False
    text = "Name: **{0}**" \
           "\nRepo: <{1}>" \
           "\nImprovement channel: <#{2}>" \
           "\nAdmin: {3}" \
           "\nGithub installation owner: {4}" \
           "\nInstall time: <t:{5}>" \
           "\nPin: <{6}>" \
           "\nCommit drafts: `{7}`" \
           "\nIs lobby: `{8}`" \
           "\nEnsure level name in posts: `{9}`" \
           "\nDo sync check: `{10}`" \
           "{11}"

    for project_id in projects:
        project = projects[project_id]

        if project['name'].lower() != project_search_name:
            continue

        if project['do_run_validation']:
            last_run = project['last_run_validation']

            if last_run:
                last_sync_check = f"\nLast sync check: <t:{last_run}>"
            else:
                last_sync_check = "\nLast sync check: `Not yet run`"
        else:
            last_sync_check = ""

        repo = project['repo']
        subdir = project['subdir']
        admin = await client.fetch_user(project['admin'])
        text_out = text.format(project['name'],
                               f'https://github.com/{repo}/tree/master/{subdir}' if subdir else f'https://github.com/{repo}',
                               project_id,
                               utils.detailed_user(user=admin),
                               project['installation_owner'],
                               project['install_time'],
                               client.get_channel(project_id).get_partial_message(project['pin']).jump_url,
                               project['commit_drafts'],
                               project['is_lobby'],
                               project['ensure_level'],
                               project['do_run_validation'],
                               last_sync_check)

        log.info(text_out)
        await message.channel.send(text_out)
        found_matching_project = True

    if not found_matching_project:
        log.info("Found no matching projects")
        await message.channel.send(f"Found no projects matching that name")


# verify that the user editing the project is the admin (or Kataiser)
async def not_admin(message: discord.Message, improvements_channel_id: int):
    if message.author.id in (projects[improvements_channel_id]['admin'], 219955313334288385):
        return False
    else:
        log.warning("Not project admin")
        await message.channel.send("Not allowed, you are not the project admin")
        return True


client: Optional[discord.Client] = None
log: Optional[logging.Logger] = None
history_log: Optional[logging.Logger] = None
re_command_split = re.compile(r' (?=(?:[^"]|"[^"]*")*$)')

command_functions = {'help': command_help,
                     'about': command_about,
                     'about_project': command_about_project,
                     'register_project': command_register_project,
                     'rename_file': command_rename_file,
                     'add_mods': command_add_mods,
                     'run_sync_check': command_run_sync_check,
                     'run_sync_checks': command_run_sync_checks}
