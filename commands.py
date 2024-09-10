import base64
import logging
import os
import pprint
import re
import subprocess
import time
import webbrowser
from operator import itemgetter
from typing import Optional, Union, Literal

import discord
import requests
import strip_markdown
import ujson

import db
import game_sync
import gen_token
import main
import spreadsheet
import utils
from constants import admin_user_id
from utils import plural

kataiser_commands = {}


def command(report_usage: bool = False, slow_start: bool = False):
    def outer(func: callable):
        command_name = func.__name__.removeprefix('command_')

        async def inner(interaction: discord.Interaction, *args):
            if report_usage and interaction.user.id != admin_user_id:
                try:
                    await (await utils.user_from_id(client, admin_user_id)).send(f"Handling /{command_name} from {utils.detailed_user(user=interaction.user)}: ```"
                                                                                 f"\n{pprint.pformat(interaction.data)}```")
                    log.info("Reported command usage to Kataiser")
                except Exception as error:
                    utils.log_error(f"Couldn't report command usage to Kataiser: {repr(error)}")

            if slow_start:
                await interaction.response.defer()

            interaction.extras['report_usage'] = report_usage
            return await func(interaction, *args)

        return inner

    return outer


def kataiser_command():
    def outer(func: callable):
        kataiser_commands[func.__name__.removeprefix('command_')] = func
        return func

    return outer


@command()
async def command_help(interaction: discord.Interaction):
    add_bot_link = discord.utils.oauth_url('970375635027525652', permissions=discord.Permissions(2147560512), scopes=('bot',))
    kataiser_commands_formatted = ""

    if interaction.user.id == 219955313334288385:
        kataiser_commands_formatted = f"\n\n{', '.join(sorted([f'`{c}`' for c in kataiser_commands]))}"

    response = "Alright, looks you want to add your TAS project to this bot (or are just curious about what the help command says). Awesome! So, steps:" \
               "\n\n1. Register GitHub app with your account and repo (you likely need to be the repo owner): " \
               "<https://github.com/apps/celestetas-improvements-tracker>" \
               f"\n2. Add bot to your server: <{add_bot_link}>" \
               "\n3. Run the `/register_project` command. You can also use this to edit existing projects." \
               "\n4. (Optional) Add other admins with `/edit_admins`, and add mod(s) for sync testing with `/add_mods`." \
               "\n\nThere are many other commands available, to assist with managing projects and more. Many commands are only visible and available in bot DMs, with the exception of "\
               f"`/register_project` and `/edit_admin`.{kataiser_commands_formatted}"

    await respond(interaction, response)


@command(report_usage=True)
async def command_register_project(interaction: discord.Interaction, name: str, improvements_channel: discord.TextChannel, repo_and_subdir: str, github_account: str, commit_drafts: bool,
                                   is_lobby: bool, ensure_level: bool, use_contributors_file: bool, do_sync_check: bool):
    log.info("Verifying project")
    await respond(interaction, "Verifying...")
    projects = db.projects.dict()
    editing = improvements_channel.id in projects

    if editing:
        if not await is_admin(interaction, projects[improvements_channel.id]):
            return

        log.info("This project already exists, preserving some keys")
        preserved_keys = ('install_time', 'pin', 'mods', 'last_run_validation', 'admins', 'desyncs', 'filetimes', 'last_commit_time', 'excluded_items',
                          'sync_environment_state', 'contributors_file_path', 'disallowed_command_exemptions')
        previous = {key: projects[improvements_channel.id][key] for key in preserved_keys}

    # safeguard for celestecord
    if improvements_channel.guild.id == 403698615446536203 and (interaction.user.id not in projects[1074148268407275520]['admins'] or improvements_channel.id != 1074148268407275520):
        no = "no"
        await utils.report_error(client, no)
        await respond(interaction, no)
        return

    # verify needed permissions in improvements channel
    missing_permissions = utils.missing_channel_permissions(improvements_channel)
    if missing_permissions:
        error = f"Don't have {missing_permissions[0]} permission for #{improvements_channel.name} ({improvements_channel.id})."
        await utils.report_error(client, error)
        await respond(interaction, error)
        return

    # verify github account exists
    r = requests.get(f'https://api.github.com/users/{github_account}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        await utils.report_error(client, f"GitHub account {github_account} doesn't seem to exist, status code is {r.status_code}")
        await respond(interaction, f"GitHub account \"{github_account}\" doesn't seem to exist.")
        return

    # verify app is installed
    try:
        main.generate_request_headers(github_account)
    except gen_token.InstallationOwnerMissingError as missing_installation_owner:
        await utils.report_error(client, f"GitHub account {missing_installation_owner} doesn't have the app installed")
        await respond(interaction, f"GitHub account {missing_installation_owner} doesn't have the app installed, please do so here: https://github.com/apps/celestetas-improvements-tracker")
        return

    # verify repo exists
    if '/' not in repo_and_subdir:
        await utils.report_error(client, f"Repo {repo_and_subdir} isn't fully qualified")
        await respond(interaction, f"Repo \"{repo_and_subdir}\" isn't fully qualified, must be OWNER/REPO or OWNER/REPO/PROJECT")
        return
    repo_split = repo_and_subdir.rstrip('/').split('/')
    repo, subdir = '/'.join(repo_split[:2]), '/'.join(repo_split[2:])
    r = requests.get(f'https://api.github.com/repos/{repo}', headers={'Accept': 'application/vnd.github.v3+json'})
    if r.status_code != 200:
        await utils.report_error(client, f"Repo {repo} doesn't seem to publically exist, status code is {r.status_code}")
        await respond(interaction, f"Repo \"{repo}\" doesn't seem to publically exist.")
        return

    # verify subdir exists in repo
    if subdir:
        r = requests.get(f'https://api.github.com/repos/{repo}/contents/{subdir}', headers={'Accept': 'application/vnd.github.v3+json'})
        if r.status_code != 200 or 'type' in ujson.loads(r.content):
            await utils.report_error(client, f"Directory {subdir} doesn't seem to exist in repo {repo}, status code is {r.status_code}")
            await respond(interaction, f"Directory \"{subdir}\" doesn't seem to exist in \"{repo}\".")
            return

    # verify installation can access repo
    r = requests.get(f'https://api.github.com/installation/repositories', headers=main.headers)
    accessible_repos = [i['full_name'] for i in ujson.loads(r.content)['repositories']]
    if repo not in accessible_repos:
        await utils.report_error(client, f"Repo {repo} not in accessible to installation: {accessible_repos}")
        await respond(interaction, f"Github app instllation cannot access the repo.")
        return

    # verify not adding run sync check to a lobby
    if do_sync_check and is_lobby:
        await utils.report_error(client, "Can't add sync check to a lobby project")
        await respond(interaction, "Enabling sync check for a lobby project is not allowed.")
        return

    log.info("Verification successful")

    current_time = int(time.time())
    registered_project = {'name': strip_markdown.strip_markdown(name.replace('"', '')),
                          'repo': repo,
                          'installation_owner': github_account,
                          'admins': (interaction.user.id,),
                          'install_time': current_time,
                          'commit_drafts': commit_drafts,
                          'is_lobby': is_lobby,
                          'lobby_sheet_cell': None,
                          'ensure_level': ensure_level,
                          'do_run_validation': do_sync_check,
                          'use_contributors_file': use_contributors_file,
                          'contributors_file_path': '',
                          'last_run_validation': None,
                          'pin': None,
                          'subdir': subdir,
                          'mods': [],
                          'desyncs': [],
                          'last_commit_time': current_time,
                          'filetimes': {},
                          'sync_check_timed_out': False,
                          'excluded_items': [],
                          'sync_environment_state': {'host': None, 'last_commit_time': None, 'everest_version': None, 'mod_versions': {}},
                          'enabled': True,
                          'disallowed_command_exemptions': []}

    if not editing:
        # await respond(interaction, "Generating path cache...")
        main.generate_path_cache(improvements_channel.id, registered_project)
        pinned_message = await main.edit_pin(improvements_channel, create_from_project=registered_project)
        await pinned_message.pin()
        registered_project['pin'] = pinned_message.id
        db.project_logs.set(improvements_channel.id, [])
    else:
        for previous_key in previous:
            registered_project[previous_key] = previous[previous_key]

        await main.edit_pin(improvements_channel)

    db.projects.set(improvements_channel.id, registered_project)
    main.fast_project_ids.add(improvements_channel.id)
    project_added_log = f"{'Edited' if editing else 'Added'} project {improvements_channel.id}: {registered_project}"
    log.info(project_added_log)
    db.history_log.set(utils.log_timestamp(), project_added_log)

    if editing:
        await respond(interaction, "Successfully verified and edited project.")
    else:
        add_mods_text = " Since you are doing sync checking, be sure to add mods (if need be) with the command `/add_mods`." if do_sync_check.lower() == 'y' else ""
        lobby_sheet_text = " If you want to automatically update lobby connection times on a google sheet, run `/link_lobby_sheet`." if registered_project['is_lobby'] else ""
        await respond(interaction, "Successfully verified and added project! If you want to change your project's settings, "
                                   f"run the command again and it will overwrite what was there before.{add_mods_text}{lobby_sheet_text}")


@command(report_usage=True, slow_start=True)
async def command_link_lobby_sheet(interaction: discord.Interaction, project_name: str, sheet: str, cell: str):
    sheet_match = re_google_sheet_id.match(sheet)

    if not sheet_match:
        log.warning(f"Invalid sheet: \"{sheet}\"")
        await respond(interaction, f"Could not understand google sheet link \"{sheet}\", try pasting only the ID part after https://docs.google.com/spreadsheets/d/")

    spreadsheet_id = sheet_match[1]
    lobby_sheet_cell = f"{spreadsheet_id}/{cell}"
    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
    projects = db.projects.get_by_name_or_id(project_name)

    if not projects:
        log.warning(f"No projects found matching: {project_name}")
        await respond(interaction, "No projects (with sync checking enabled) matching that name or ID found.")

    for project in projects:
        if not await is_admin(interaction, project):
            break
        if not project['is_lobby']:
            log.warning(f"Trying to link sheet to non-lobby project {project['name']}")
            await respond(interaction, "Cannot link sheet to a non-lobby project")
            continue

        try:
            spreadsheet.check_write_permission(spreadsheet_id, cell)
        except Exception as error:
            await utils.report_error(client,
                                     f"Cannot write to '{spreadsheet_url}' '{cell}'. Missing write access? Error message: {error}")
            await respond(interaction, f"Bot can't write to to `{spreadsheet_url}` at '{cell}'. Make sure to invite `{spreadsheet.service_account_email}` to the sheet.")
            return

        project['lobby_sheet_cell'] = lobby_sheet_cell
        db.projects.set(project['project_id'], project)

        await respond(interaction, f"Project \"{project['name']}\" is now linked to {spreadsheet_url} {cell}")


@command(report_usage=True, slow_start=True)
async def command_add_mods(interaction: discord.Interaction, project_name: str, mods: str):
    project_mods_added = False

    for project in db.projects.get_by_name_or_id(project_name):
        if not await is_admin(interaction, project):
            break
        elif not (project['do_run_validation'] or project['sync_check_timed_out']):
            log.warning(f"Trying to add mods to project: {project['name']}, but run validation is disabled")
            await respond(interaction, f"Project \"{project['name']}\" has sync checking disabled.")
            continue

        log.info(f"Adding mods for project: {project['name']}")
        project_mods_added = True
        mods_given = [mod.replace('"', '').removesuffix('.zip') for mod in re_command_split.split(mods)]
        project_mods = set(project['mods'])
        log.info(f"{len(project_mods)} mod{plural(project_mods)} before adding: {project_mods}")
        project_mods = project_mods.union(mods_given)
        log.info(f"{len(project_mods)} mod{plural(project_mods)} after adding: {project_mods}")
        project['mods'] = list(project_mods)
        db.projects.set(project['project_id'], project)
        mods_missing = set()
        dependencies = set()
        game_sync.get_mod_dependencies.cache_clear()

        for mod_given in mods_given:
            dependencies |= game_sync.get_mod_dependencies(mod_given)
            all_project_mods = project_mods.union(dependencies)

        log.info(f"{len(all_project_mods)} total mod{plural(all_project_mods)}: {all_project_mods}")
        installed_mods = [item.stem for item in game_sync.mods_dir().iterdir() if item.suffix == '.zip']

        for mod in all_project_mods:
            if mod not in installed_mods:
                mods_missing.add(mod)

        dependency_text = 'dependency' if len(dependencies) == 1 else 'dependencies'
        await respond(interaction, f"Project \"{project['name']}\" now has {len(mods_given)} mod{plural(mods_given)} "
                                                f"(plus {len(dependencies)} {dependency_text}) to load for sync testing.")

        if mods_missing:
            log.warning(f"Missing {len(mods_missing)} mod(s) from installed: {mods_missing}")
            mods_missing_formatted = '\n'.join(sorted(mods_missing))
            await (await utils.user_from_id(client, admin_user_id)).send(f"hey you need to install some mods for sync testing\n```\n{mods_missing_formatted}```")
            await respond(interaction,
                           f"The following mod(s) are not currently prepared for sync testing (Kataiser has been automatically DM'd about it):\n```\n{mods_missing_formatted}```")

    if not project_mods_added:
        log.warning(f"No projects found matching: {project_name}")
        await respond(interaction, "No projects (with sync checking enabled) matching that name or ID found.")


@command(report_usage=True, slow_start=True)
async def command_rename_file(interaction: discord.Interaction, project_name: str, filename_before: str, filename_after: str):
    matching_projects = db.projects.get_by_name_or_id(project_name)
    renamed_file = False

    if filename_before == filename_after:
        await respond(interaction, "what")
        return

    for project in matching_projects:
        main.generate_request_headers(project['installation_owner'])
        path_cache = main.generate_path_cache(project['project_id'])

        if filename_before not in path_cache:
            not_found_text = f"{filename_before} not in project {project['name']}."
            log.warning(not_found_text)
            await respond(interaction, not_found_text)
            return

        renaming_text = f"Renaming `{filename_before}` to `{filename_after}` in project \"{project['name']}\"."
        log.info(renaming_text)
        await respond(interaction, renaming_text)
        repo = project['repo']
        file_path = path_cache[filename_before]
        renamed_file = True
        user_github_account = utils.get_user_github_account(interaction.user.id)

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
        data = {'message': f"Renamed {filename_before} to {filename_after} (creating)", 'content': base64.b64encode(tas_downloaded).decode('UTF8')}
        if filename_after in path_cache:
            file_path_after = path_cache[filename_after]
            log.info(f"Overwriting, file should already exist at {file_path_after}")
            data['sha'] = main.get_sha(repo, file_path_after)
            expected_status = 200
        else:
            file_path_after = file_path.replace(filename_before, filename_after)
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
            await respond(interaction, "Rename successful.")
            improvements_channel = client.get_channel(project['project_id'])
            await improvements_channel.send(f"{interaction.user.mention} renamed `{filename_before}` to `{filename_after}`")
            await main.edit_pin(improvements_channel)
        else:
            await utils.report_error(client, "Rename unsuccessful")
            await respond(interaction, "Rename unsuccessful.")

    if not matching_projects:
        log.info("Found no matching projects")
        await respond(interaction, f"Found no projects matching that name or ID.")
    elif not renamed_file:
        log.warning("No files renamed")
        await respond(interaction, f"{filename_before} not found in any project named `{project_name}`.")


@command(report_usage=True, slow_start=True)
async def command_edit_admin(interaction: discord.Interaction, project_name: str, edited_admin: discord.User, edit: Literal["Add", "Remove"]):
    matching_projects = db.projects.get_by_name_or_id(project_name)
    adding = edit == "Add"

    for project in matching_projects:
        if not await is_admin(interaction, project):
            continue

        if adding:
            if edited_admin.id in project['admins']:
                already_admin_text = f"{utils.detailed_user(user=edited_admin)} is already an admin for project \"{project['name']}\"."
                log.warning(already_admin_text)
                await respond(interaction, already_admin_text)
            else:
                project['admins'].append(edited_admin.id)
                db.projects.set(project['project_id'], project)
                added_admin_text = f"Added {utils.detailed_user(user=edited_admin)} as an admin to project \"{project['name']}\"."
                log.info(added_admin_text)
                await respond(interaction, added_admin_text)
                await main.edit_pin(client.get_channel(project['project_id']))

                if edited_admin.id != interaction.user.id:
                    await edited_admin.send(f"{interaction.user.global_name} has added you as an admin to the \"{project['name']}\" TAS project.")
        else:
            if edited_admin.id in project['admins']:
                project['admins'].remove(edited_admin.id)
                db.projects.set(project['project_id'], project)
                removed_admin_text = f"Removed {utils.detailed_user(user=edited_admin)} as an admin from project \"{project['name']}\"."
                log.info(removed_admin_text)
                await respond(interaction, removed_admin_text)
                await main.edit_pin(client.get_channel(project['project_id']))

                if edited_admin.id != interaction.user.id:
                    await edited_admin.send(f"{interaction.user.global_name} has removed you as an admin from the \"{project['name']}\" TAS project.")
            else:
                not_admin_text = f"{utils.detailed_user(user=edited_admin)} is not an admin for project \"{project['name']}\"."
                log.warning(not_admin_text)
                await respond(interaction, not_admin_text)

    if not matching_projects:
        log.info("Found no matching projects")
        await respond(interaction, f"Found no projects matching that name or ID.")


@command()
async def command_about(interaction: discord.Interaction):
    text = "Source: <https://github.com/Kataiser/CelesteTAS-Improvements-Tracker>" \
           "\nProjects (improvement channels): {0}" \
           "\nServers: {1}" \
           "\nGithub installations: {2}" \
           "\nBot uptime: {3} hours" \
           "\nHost uptime: {8} days" \
           "\nCurrent host: {7}" \
           "\nSync checks: {4} project{6}" \
           "\nImprovements/drafts processed and committed: {5}"

    sync_checks = 0
    installations = set()
    import psutil
    host_uptime = round((time.time() - psutil.boot_time()) / 86400, 1)

    for project in db.projects.get_all(consistent_read=False):
        installations.add(project['installation_owner'])

        if project['do_run_validation']:
            sync_checks += 1

    if main.login_time:
        bot_uptime = round((time.time() - main.login_time) / 3600, 1)
    else:
        bot_uptime = 0.0

    text_out = text.format(main.projects_count(),
                           len(client.guilds),
                           len(installations),
                           bot_uptime,
                           sync_checks,
                           db.history_log.size(False),  # techically inaccurate because add/edit project logs but close enough
                           plural(sync_checks),
                           utils.host(),
                           host_uptime)

    log.info(text_out)
    await respond(interaction, text_out)


@command(slow_start=True)
async def command_about_project(interaction: discord.Interaction, project_name: str):
    matching_projects = db.projects.get_by_name_or_id(project_name)
    text = "Name: **{0}**" \
           "\nRepo: <{1}>" \
           "\nImprovement channel: <#{2}>" \
           "\nAdmin{12}: {3}" \
           "\nGithub installation owner: {4}" \
           "\nInstall time: <t:{5}>" \
           "\nPin: <{6}>" \
           "\nCommits drafts: `{7}`" \
           "\nIs lobby: `{8}`" \
           "\nEnsures level name in posts: `{9}`" \
           "\nDoes sync check: `{10}`" \
           "\nUses contributors file: `{13}`" \
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
        admins = [utils.detailed_user(user=await utils.user_from_id(client, admin)) for admin in project['admins']]
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
                               plural(admins),
                               project['use_contributors_file'])

        log.info(text_out)
        await respond(interaction, text_out)

    if not matching_projects:
        log.info("Found no matching projects")
        await respond(interaction, f"Found no projects matching that name or ID.")


@command(slow_start=True)
async def command_projects(interaction: discord.Interaction):
    projects = sorted(db.projects.get_all(), key=itemgetter('last_commit_time'), reverse=True)
    project_texts = ["Sorted by most recently improved."]
    project_texts_length = len(project_texts[0])

    for project in projects:
        if project['project_id'] in main.inaccessible_projects:
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
        admins = [utils.detailed_user(user=await utils.user_from_id(client, admin)) for admin in project['admins']]
        repo_url = f'https://github.com/{repo}/tree/HEAD/{subdir}' if subdir else f'https://github.com/{repo}'

        spreadsheet_line = ""
        if project['lobby_sheet_cell']:
            spreadsheet_id, _, cell = project['lobby_sheet_cell'].partition('/')
            spreadsheet_url = f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}'
            spreadsheet_line = f"\nLobby Sheet: [google sheet]({spreadsheet_url}) {cell}"

        text = f"**{project['name']}**" \
               f"\nRepo: <{repo_url}>" \
               f"\nImprovement channel: <#{project['project_id']}>" \
               f"\nAdmin{plural(admins)}: {', '.join(admins)}" \
               f"\nIs lobby: `{project['is_lobby']}`" + \
               spreadsheet_line + \
               f"\nDoes sync check: `{project['do_run_validation']}`" \
               f"{last_sync_check}"

        if project_texts_length + len(text) > 1950:
            log.info(f"Sending {len(project_texts)} project texts")
            await respond(interaction, '\n\n'.join(project_texts))
            project_texts = []
            project_texts_length = 0
            time.sleep(1)

        project_texts.append(text)
        project_texts_length += len(text)

    if project_texts:
        log.info(f"Sending {len(project_texts)} remaining project texts")
        await respond(interaction, '\n\n'.join(project_texts))


@command(slow_start=True)
async def command_projects_admined(interaction: discord.Interaction):
    projects_admined_names = []

    for project in db.projects.get_all():
        if interaction.user.id in project['admins']:
            projects_admined_names.append(project['name'])

    projects_admined_names.sort()
    log.info(f"Projects admined: {projects_admined_names}")

    if projects_admined_names:
        await respond(interaction, '\n'.join(projects_admined_names))
    else:
        await respond(interaction, "You're not an admin of any projects.")


@kataiser_command()
async def command_log(message: discord.Message):
    log.handlers[0].flush()
    time.sleep(0.2)

    with open('bot.log', 'rb') as bot_log:
        log_bytes = bot_log.read()
        bot_log.seek(0)
        end_text = log_bytes[-1000:].decode('UTF8')
        await message.channel.send(f'`{end_text}`', file=discord.File(bot_log, filename=utils.saved_log_name('bot')))


@kataiser_command()
async def command_sync_log(message: discord.Message):
    with open('game_sync.log', 'rb') as sync_log:
        log_bytes = sync_log.read()
        sync_log.seek(0)
        end_text = log_bytes[-1000:].decode('UTF8')
        await message.channel.send(f'`{end_text}`', file=discord.File(sync_log, filename=utils.saved_log_name('game_sync')))


@kataiser_command()
async def command_die(message: discord.Message):
    await message.channel.send("https://cdn.discordapp.com/attachments/972366104204812338/1179648390649360454/wqovpsazm7z61.png")
    raise SystemExit("guess I'll die")


@kataiser_command()
async def command_run_cmd(message: discord.Message):
    process = subprocess.Popen(message.content.partition(' ')[2], creationflags=0x00000010)
    await message.channel.send(f"`{process.pid}`")


@kataiser_command()
async def command_install_mod(message: discord.Message):
    install_log = subprocess.check_output(f'mons mods add itch {message.content.partition(' ')[2]} --force')
    await message.channel.send(install_log.decode('UTF8'))


@kataiser_command()
async def command_send_file(message: discord.Message):
    assert message.attachments
    given_path = message.content.partition(' ')[2].strip('"')

    if given_path:
        assert os.path.isdir(given_path)

    for file in message.attachments:
        full_path = os.path.join(given_path, file.filename)
        await file.save(full_path)
        await message.channel.send(f"Saved to {full_path}")
        log.info(f"Saved to {full_path}")


@kataiser_command()
async def command_get_file(message: discord.Message):
    given_path = message.content.partition(' ')[2].strip('"')
    assert given_path
    assert os.path.isfile(given_path)
    await message.channel.send('_ _', file=discord.File(given_path))
    log.info("Sent file")


@kataiser_command()
async def command_echo(message: discord.Message):
    channel_id, _, message_text = message.content[5:].partition(' ')
    sent_message = await client.get_channel(int(channel_id)).send(message_text)
    await message.channel.send(sent_message.jump_url)


@kataiser_command()
async def command_echo_reply(message: discord.Message):
    message_full_id, _, message_text = message.content[11:].partition(' ')
    channel_id, message_id = message_full_id.split('-')
    sent_message = await client.get_channel(int(channel_id)).get_partial_message(int(message_id)).reply(message_text)
    await message.channel.send(sent_message.jump_url)


@kataiser_command()
async def command_open_url(message: discord.Message):
    url = message.content.split()[1]
    await message.channel.send(webbrowser.open(url))


@kataiser_command()
async def command_restart_tasks(message: discord.Message):
    await message.channel.send(main.start_tasks())


async def handle_direct_dm(message: discord.Message):
    if message.content.lower() in ('ok', 'hi', 'hello'):
        await message.channel.send(message.content)
        return

    command_name = message.content.partition(' ')[0].lower()

    if command_name in kataiser_commands:
        if message.author.id == admin_user_id:
            await kataiser_commands[command_name](message)
        else:
            await message.channel.send("Not allowed, you are not Kataiser.")
    else:
        await message.channel.send("DM commands are now slash commands, run `/help` for more info.")


# verify that the user editing the project is an admin (or Kataiser)
async def is_admin(interaction: discord.Interaction, project: dict):
    if interaction.user.id in (*project['admins'], admin_user_id):
        return True
    else:
        log.warning("Not project admin")
        await respond(interaction, "Not allowed, you are not an admin for this project.")
        return False


async def respond(interaction: discord.Interaction, message: str):
    if not interaction.response.is_done():
        await interaction.response.send_message(message)
    else:
        await interaction.followup.send(message)

    if interaction.extras['report_usage'] and interaction.user.id != admin_user_id:
        await (await utils.user_from_id(client, admin_user_id)).send(f"`{message}`")


client: Optional[discord.Client] = None
log: Union[logging.Logger, utils.LogPlaceholder] = utils.LogPlaceholder()
re_command_split = re.compile(r' (?=(?:[^"]|"[^"]*")*$)')
re_google_sheet_id = re.compile(r'(?:https://docs.google.com/spreadsheets/d/)?([0-9a-zA-Z-_]+).*')
