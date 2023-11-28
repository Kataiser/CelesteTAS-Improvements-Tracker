import base64
import inspect
import logging
import os
import re
import time
from typing import Optional, List

import discord
import requests
import ujson

import db
import game_sync
import gen_token
import main
import utils
from utils import plural


report_commands = set()
command_functions = {}


async def handle(message: discord.Message):
    log.info(f"Recieved DM from {utils.detailed_user(message)}: \"{message.content}\"")
    command_name = message.content.partition(' ')[0].lower()

    if command_name in command_functions:
        log.info(f"Handling '{command_name}' command")
        await report_command_used(command_name, message)
        await command_functions[command_name](message)
    elif message.content.lower() == 'ok':
        await message.channel.send(message.content)
    else:
        await message.channel.send("Unrecognized command, try `help`")


# haha yep this definitely is code
def command(format_regex: Optional[re.Pattern] = None, report_usage: bool = False):
    def outer(func: callable):
        command_name = func.__name__.removeprefix('command_')
        use_message_split = 'message_split' in inspect.signature(func).parameters

        if report_usage:
            report_commands.add(command_name)

        try:
            command_doc = func.__doc__.replace('\n    ', '\n')
            func.help = f"```\n{command_doc}```"
        except AttributeError:
            utils.log_error(f"{func.__name__} has no docstring")

        async def inner(message: discord.Message):
            message_fixed = re_combine_whitespace.sub(" ", message.content)

            if format_regex and not format_regex.match(message_fixed):
                log.warning("Bad command format")
                await message.channel.send(f"Incorrect command format.\n{func.help}")
                return

            if use_message_split:
                return await func(message, re_command_split.split(message_fixed))
            else:
                return await func(message)

        inner.help = func.help
        command_functions[command_name] = inner
        return inner

    return outer


@command()
async def command_help(message: discord.Message, message_split: List[str]):
    """
    help COMMAND

      Get bot installation instructions, or the info for a command.

      COMMAND: The command to get the parameter info for (optional)
    """

    if len(message_split) > 1 and message_split[1] in command_functions:
        await message.channel.send(command_functions[message_split[1]].help)
    else:
        add_bot_link = discord.utils.oauth_url('970375635027525652', permissions=discord.Permissions(2147560512), scopes=('bot',))
        commands_available = '\n'.join(command_functions)

        response = "Alright, looks you want to add your TAS project to this bot (or are just curious about what the help command says). Awesome! So, steps:" \
                   "\n\n1. Register GitHub app with your account and repo (you likely need to be the repo owner): " \
                   "<https://github.com/apps/celestetas-improvements-tracker>" \
                   f"\n2. Add bot to your server: <{add_bot_link}>" \
                   "\n3. Run the `register_project` command, see `help register_project` for parameters. You can also use this to edit existing projects." \
                   "\n4. (Optional) Add other admins with `edit_admins`, and add mod(s) for sync testing with `add_mods`." \
                   "\n\nAvailable commands:" \
                   f"\n```\n{commands_available}```"

        await message.channel.send(response)


@command(re.compile(r'(?i)register_project .+ \d+ .+/.+ .+ [YN] [YN] [YN] [YN] [YN]'), report_usage=True)
async def command_register_project(message: discord.Message, message_split: List[str]):
    """
    register_project NAME IMPROVEMENTS_CHANNEL_ID REPOSITORY ACCOUNT COMMIT_DRAFTS IS_LOBBY ENSURE_LEVEL DO_SYNC_CHECK

      Add or edit a project (improvements channel).

      NAME: The name of the project (in quotes if needed), ex: "Into the Jungle", "Strawberry Jam", "Celeste maingame", "Celeste mindash"
      IMPROVEMENTS_CHANNEL_ID: Turn on developer mode in Discord advanced settings, then right click the channel and click Copy ID
      REPOSITORY: Either as OWNER/REPO, or as OWNER/REPO/PROJECT if you have multiple projects in a repo
      ACCOUNT: Your GitHub account name
      COMMIT_DRAFTS: Automatically commit drafts to the root directory [Y or N]
      IS_LOBBY: Whether this channel is for a lobby, which handles file validation differently [Y or N]
      ENSURE_LEVEL: Whether to make sure the level's name is in the message when validating a posted file [Y or N]
      USE_CONTRIBUTORS_FILE: Save a Contributors.txt file [Y or N]
      DO_SYNC_CHECK: Do a nightly sync test of all your files by actually running the game (highly recommended) [Y or N]
    """

    log.info("Verifying project")
    await message.channel.send("Verifying...")
    _, name, improvements_channel_id, repo_and_subdir, github_account, commit_drafts, is_lobby, ensure_level, use_contributors_file, do_run_validation = message_split
    improvements_channel_id = int(improvements_channel_id)
    projects = db.projects.dict()
    editing = improvements_channel_id in projects

    if editing:
        if not await is_admin(message, projects[improvements_channel_id]):
            return

        log.info("This project already exists, preserving some keys")
        preserved_keys = ('install_time', 'pin', 'mods', 'last_run_validation', 'admins', 'desyncs', 'filetimes')
        previous = {key: projects[improvements_channel_id][key] for key in preserved_keys}

    # verify improvements channel exists
    improvements_channel = client.get_channel(improvements_channel_id)
    if not improvements_channel:
        error = f"Channel {improvements_channel_id} doesn't exist"
        await utils.report_error(client, error)
        await message.channel.send(error)
        return

    # verify needed permissions in improvements channel
    missing_permissions = utils.missing_channel_permissions(improvements_channel)
    if missing_permissions:
        error = f"Don't have {missing_permissions[0]} permission for #{improvements_channel.name} ({improvements_channel_id})"
        await utils.report_error(client, error)
        await message.channel.send(error)
        return

    # verify github account exists
    r = requests.get(f'https://api.github.com/users/{github_account}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        await utils.report_error(client, f"GitHub account {github_account} doesn't seem to exist, status code is {r.status_code}")
        await message.channel.send(f"GitHub account \"{github_account}\" doesn't seem to exist")
        return

    # verify app is installed
    try:
        main.generate_request_headers(github_account)
    except gen_token.InstallationOwnerMissingError as missing_installation_owner:
        await utils.report_error(client, f"GitHub account {missing_installation_owner} doesn't have the app installed")
        await message.channel.send(f"GitHub account {missing_installation_owner} doesn't have the app installed, please do so here: https://github.com/apps/celestetas-improvements-tracker")
        return

    # verify repo exists
    repo_split = repo_and_subdir.rstrip('/').split('/')
    repo, subdir = '/'.join(repo_split[:2]), '/'.join(repo_split[2:])
    r = requests.get(f'https://api.github.com/repos/{repo}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        await utils.report_error(client, f"Repo {repo} doesn't seem to publically exist, status code is {r.status_code}")
        await message.channel.send(f"Repo \"{repo}\" doesn't seem to publically exist")
        return

    # verify subdir exists in repo
    if subdir:
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{subdir}', headers={'Accept': 'application/vnd.github.v3+json'})
        if r.status_code != 200 or 'type' in ujson.loads(r.content):
            await utils.report_error(client, f"Directory {subdir} doesn't seem to exist in repo {repo}, status code is {r.status_code}")
            await message.channel.send(f"Directory \"{subdir}\" doesn't seem to exist in \"{repo}\"")
            return

    # verify not adding run validation to a lobby
    if do_run_validation.lower() == 'y' and is_lobby.lower() == 'y':
        await utils.report_error(client, "Can't add run validation to a lobby project")
        await message.channel.send("Enabling run validation for a lobby project is not allowed")
        return

    log.info("Verification successful")

    current_time = int(time.time())
    registered_project = {'name': name.replace('"', ''),
                          'repo': repo,
                          'installation_owner': github_account,
                          'admins': (message.author.id,),
                          'install_time': current_time,
                          'commit_drafts': commit_drafts.lower() == 'y',
                          'is_lobby': is_lobby.lower() == 'y',
                          'ensure_level': ensure_level.lower() == 'y',
                          'do_run_validation': do_run_validation.lower() == 'y',
                          'use_contributors_file': use_contributors_file.lower() == 'y',
                          'last_run_validation': None,
                          'pin': None,
                          'subdir': subdir,
                          'mods': [],
                          'desyncs': [],
                          'last_commit_time': current_time,
                          'filetimes': {},
                          'sync_check_timed_out': False}

    if not editing:
        await message.channel.send("Generating path cache...")
        main.generate_path_cache(improvements_channel_id, registered_project)
        pinned_message = await main.edit_pin(improvements_channel, create_from_project=registered_project)
        await pinned_message.pin()
        registered_project['pin'] = pinned_message.id
        db.project_logs.set(improvements_channel_id, [])
    else:
        for previous_key in previous:
            registered_project[previous_key] = previous[previous_key]

        await main.edit_pin(improvements_channel)

    db.projects.set(improvements_channel_id, registered_project)
    main.fast_project_ids.add(improvements_channel_id)
    project_added_log = f"{'Edited' if editing else 'Added'} project {improvements_channel_id}: {registered_project}"
    log.info(project_added_log)
    db.history_log.set(utils.log_timestamp(), project_added_log)

    if editing:
        await message.channel.send("Successfully verified and edited project.")
    else:
        add_mods_text = " Since you are doing sync checking, be sure to add mods (if need be) with the command `add_mods`." if do_run_validation.lower() == 'y' else ""
        await message.channel.send("Successfully verified and added project! If you want to change your project's settings, "
                                   f"run the command again and it will overwrite what was there before.{add_mods_text}")


@command(re.compile(r'(?i)add_mods .+ .+'), report_usage=True)
async def command_add_mods(message: discord.Message, message_split: List[str]):
    """
    add_mods PROJECT_NAME MODS

      Set the game mods a sync check needs to load.

      PROJECT_NAME: The name of your project (in quotes if needed). If you have multiple improvement channels with the same project name, this will update all of them
      MODS: The mod(s) used by your project, separated by spaces (dependencies are automatically handled). Ex: EGCPACK, WinterCollab2021, conquerorpeak103
    """

    project_search_name = message_split[1].replace('"', '')
    project_mods_added = False

    for project in db.projects.get_by_name(project_search_name):
        if not await is_admin(message, project):
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
        db.projects.set(project['project_id'], project)
        mods_missing = set()

        for mod_given in mods_given:
            all_project_mods = project_mods.union(game_sync.get_mod_dependencies(mod_given))

        log.info(f"{len(all_project_mods)} total mod{plural(all_project_mods)}: {all_project_mods}")
        installed_mods = [item.stem for item in game_sync.mods_dir().iterdir() if item.suffix == '.zip']

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


@command(re.compile(r'(?i)rename_file .+ .+\.tas .+\.tas'), report_usage=True)
async def command_rename_file(message: discord.Message, message_split: List[str]):
    """
    rename_file PROJECT_NAME FILENAME_BEFORE FILENAME_AFTER

      Rename a file in the repo of a project. Recommended over manually committing.

      PROJECT_NAME: The name of your project (in quotes if needed). If you have multiple improvement channels with the same project name, this will search in all of them
      FILENAME_BEFORE: The current name of the TAS file you want to rename (with .tas)
      FILENAME_AFTER: What you want the TAS file to be renamed to (with .tas)
    """

    project_search_name = message_split[1].replace('"', '')
    matching_projects = db.projects.get_by_name(project_search_name)
    filename_before, filename_after = message_split[2:]
    renamed_file = False

    if filename_before == filename_after:
        await message.channel.send("what")
        return

    for project in matching_projects:
        main.generate_request_headers(project['installation_owner'])
        path_cache = main.generate_path_cache(project['project_id'])

        if filename_before not in path_cache:
            not_found_text = f"{filename_before} not in project {project['name']}"
            log.warning(not_found_text)
            await message.channel.send(not_found_text)
            return

        renaming_text = f"Renaming `{filename_before}` to `{filename_after}` in project \"{project['name']}\""
        log.info(renaming_text)
        await message.channel.send(renaming_text)
        repo = project['repo']
        file_path = path_cache[filename_before]
        renamed_file = True
        user_github_account = utils.get_user_github_account(message.author.id)

        log.info(f"Downloading {filename_before}")
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=main.headers)
        utils.handle_potential_request_error(r, 200)
        tas_downloaded = base64.b64decode(ujson.loads(r.content)['content'])

        # commit 1: delete old file
        log.info("Performing delete commit")
        data = {'message': f"Renamed {filename_before} to {filename_after} (deleting)", 'sha': main.get_sha(repo, file_path)}
        if user_github_account:
            data['author'] = {'name': user_github_account[0], 'email': user_github_account[1]}
            log.info(f"Setting commit author to {data['author']}")
        r = requests.delete(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=main.headers, data=ujson.dumps(data))
        utils.handle_potential_request_error(r, 200)
        time.sleep(1)  # just to be safe

        # commit 2: create new file (or overwrite)
        log.info("Performing recreate commit")
        file_path_after = file_path.replace(filename_before, filename_after)
        data = {'message': f"Renamed {filename_before} to {filename_after} (creating)", 'content': base64.b64encode(tas_downloaded).decode('UTF8')}
        if filename_after in path_cache:
            data['sha'] = main.get_sha(repo, file_path_after)
            expected_status = 200
        else:
            expected_status = 201
        if user_github_account:
            data['author'] = {'name': user_github_account[0], 'email': user_github_account[1]}
            log.info(f"Setting commit author to {data['author']}")
        r = requests.put(f'https://api.github.com/repos/{repo}/contents/{file_path_after}', headers=main.headers, data=ujson.dumps(data))
        utils.handle_potential_request_error(r, expected_status)

        if r.status_code == expected_status:
            db.path_caches.enable_cache()
            db.path_caches.remove_file(project['project_id'], filename_before)
            db.path_caches.add_file(project['project_id'], filename_after, file_path_after)
            db.path_caches.disable_cache()
            log.info("Rename successful")
            await message.channel.send("Rename successful")
            improvements_channel = client.get_channel(project['project_id'])
            await improvements_channel.send(f"{message.author.mention} renamed `{filename_before}` to `{filename_after}`")
            await main.edit_pin(improvements_channel)
        else:
            await utils.report_error(client, "Rename unsuccessful")
            await message.channel.send("Rename unsuccessful")

    if not matching_projects:
        log.info("Found no matching projects")
        await message.channel.send(f"Found no projects matching that name.")
    elif not renamed_file:
        log.warning("No files renamed")
        await message.channel.send(f"{filename_before} not found in any project named `{project_search_name}`.")


@command(re.compile(r'(?i)edit_admin .+ \d+ [YN]'), report_usage=True)
async def command_edit_admin(message: discord.Message, message_split: List[str]):
    """
    edit_admin PROJECT_NAME ADMIN_ID ADDING

      Add or remove admins from a project.

      PROJECT_NAME: The name of your project (in quotes if needed). If you have multiple improvement channels with the same project name, this will search in all of them
      ADMIN_ID: The Discord ID (not the username, display name, or nickname) of the user you're adding or removing
      ADDING: Y if adding admin, N if removing admin
    """

    project_search_name = message_split[1].replace('"', '')
    matching_projects = db.projects.get_by_name(project_search_name)
    admin_id = int(message_split[2])
    adding = message_split[3].lower() == 'y'

    for project in matching_projects:
        if not await is_admin(message, project):
            continue

        try:
            edited_admin = await client.fetch_user(admin_id)
        except discord.NotFound:
            await utils.report_error(client, f"User {admin_id} not found")
            await message.channel.send(f"User with ID {admin_id} could not be found")
            return

        if adding:
            if admin_id in project['admins']:
                already_admin_text = f"{utils.detailed_user(user=edited_admin)} is already an admin for project \"{project['name']}\"."
                log.warning(already_admin_text)
                await message.channel.send(already_admin_text)
            else:
                project['admins'].append(admin_id)
                db.projects.set(project['project_id'], project)
                added_admin_text = f"Added {utils.detailed_user(user=edited_admin)} as an admin to project \"{project['name']}\"."
                log.info(added_admin_text)
                await message.channel.send(added_admin_text)
                await main.edit_pin(client.get_channel(project['project_id']))

                if edited_admin.id != message.author.id:
                    await edited_admin.send(f"{message.author.global_name} has added you as an admin to the \"{project['name']}\" TAS project.")
        else:
            if admin_id in project['admins']:
                project['admins'].remove(admin_id)
                db.projects.set(project['project_id'], project)
                removed_admin_text = f"Removed {utils.detailed_user(user=edited_admin)} as an admin from project \"{project['name']}\"."
                log.info(removed_admin_text)
                await message.channel.send(removed_admin_text)
                await main.edit_pin(client.get_channel(project['project_id']))

                if edited_admin.id != message.author.id:
                    await edited_admin.send(f"{message.author.global_name} has removed you as an admin from the \"{project['name']}\" TAS project.")
            else:
                not_admin_text = f"{utils.detailed_user(user=edited_admin)} is not an admin for project \"{project['name']}\"."
                log.warning(not_admin_text)
                await message.channel.send(not_admin_text)

    if not matching_projects:
        log.info("Found no matching projects")
        await message.channel.send(f"Found no projects matching that name.")


@command()
async def command_about(message: discord.Message):
    """
    about

      Get bot info and status.

      (No parameters)
    """

    text = "Source: <https://github.com/Kataiser/CelesteTAS-Improvements-Tracker>" \
           "\nProjects (improvement channels): {0}" \
           "\nServers: {1}" \
           "\nGithub installations: {2}" \
           "\nCurrent uptime: {3} hours" \
           "\nCurrent host: {7}" \
           "\nNightly sync check: {4} project{6}" \
           "\nImprovements/drafts processed and committed: {5}"

    sync_checks = 0
    installations = set()

    for project in db.projects.get_all(consistent_read=False):
        installations.add(project['installation_owner'])

        if project['do_run_validation']:
            sync_checks += 1

    if os.path.isfile('host'):
        with open('host', 'r', encoding='UTF8') as host_file:
            host = host_file.read().strip('" \n')
    else:
        host = "Unknown"
        await utils.report_error(client, "Couldn't determine host for about command")

    if main.login_time:
        uptime = round((time.time() - main.login_time) / 3600, 1)
    else:
        uptime = 0.0

    text_out = text.format(main.projects_count(),
                           len(client.guilds),
                           len(installations),
                           uptime,
                           sync_checks,
                           db.history_log.size(),  # techically inaccurate because add/edit project logs but close enough
                           plural(sync_checks),
                           host)

    log.info(text_out)
    await message.channel.send(text_out)


@command(re.compile(r'(?i)about_project .+'))
async def command_about_project(message: discord.Message, message_split: List[str]):
    """
    about_project PROJECT_NAME

      Get the info and settings of a project.

      PROJECT_NAME: The name of your project (in quotes if needed). If you have multiple improvement channels with the same project name, this will show info for all of them
    """

    # message_split = re_command_split.split(message.content)
    project_search_name = message_split[1].replace('"', '')
    matching_projects = db.projects.get_by_name(project_search_name)
    text = "Name: **{0}**" \
           "\nRepo: <{1}>" \
           "\nImprovement channel: <#{2}>" \
           "\nAdmin{12}: {3}" \
           "\nGithub installation owner: {4}" \
           "\nInstall time: <t:{5}>" \
           "\nPin: <{6}>" \
           "\nCommit drafts: `{7}`" \
           "\nIs lobby: `{8}`" \
           "\nEnsure level name in posts: `{9}`" \
           "\nDo sync check: `{10}`" \
           "{11}"

    for project in matching_projects:
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
        admins = [utils.detailed_user(user=await client.fetch_user(admin)) for admin in project['admins']]
        text_out = text.format(project['name'],
                               f'https://github.com/{repo}/tree/HEAD/{subdir}' if subdir else f'https://github.com/{repo}',
                               project['project_id'],
                               ', '.join(admins),
                               project['installation_owner'],
                               project['install_time'],
                               client.get_channel(project['project_id']).get_partial_message(project['pin']).jump_url,
                               project['commit_drafts'],
                               project['is_lobby'],
                               project['ensure_level'],
                               project['do_run_validation'],
                               last_sync_check,
                               plural(admins))

        log.info(text_out)
        await message.channel.send(text_out)

    if not matching_projects:
        log.info("Found no matching projects")
        await message.channel.send(f"Found no projects matching that name.")


@command()
async def command_projects_admined(message: discord.Message):
    """
    projects_admined

      List projects you're an admin of.

      (No parameters)
    """

    projects_admined_names = []

    for project in db.projects.get_all():
        if message.author.id in project['admins']:
            projects_admined_names.append(project['name'])

    log.debug(f"Projects admined: {projects_admined_names}")

    if projects_admined_names:
        await message.channel.send('\n'.join(projects_admined_names))
    else:
        await message.channel.send("You're not an admin of any projects.")


# verify that the user editing the project is an admin (or Kataiser)
async def is_admin(message: discord.Message, project: dict):
    if message.author.id in (*project['admins'], 219955313334288385):
        return True
    else:
        log.warning("Not project admin")
        await message.channel.send("Not allowed, you are not a project admin.")
        return False


# DM Kataiser when an important command is used
async def report_command_used(command_name: str, message: discord.Message):
    try:
        if command_name in report_commands and message.author.id != 219955313334288385:
            await (await client.fetch_user(219955313334288385)).send(f"Handling {command_name} from {utils.detailed_user(message)}: `{message.content}`")
            log.info("Reported command usage to Kataiser")
    except Exception as error:
        utils.log_error(f"Couldn't report command usage to Kataiser: {repr(error)}")


client: Optional[discord.Client] = None
log: Optional[logging.Logger] = None
re_command_split = re.compile(r' (?=(?:[^"]|"[^"]*")*$)')
re_combine_whitespace = re.compile(r'\s+')
