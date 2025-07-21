import copy
import logging
from typing import Optional, Union

import cron_validator
import discord
from deepdiff import DeepDiff, Delta

import constants
import db
import main
import tasks
import utils
from utils import plural


class ProjectEditor(discord.ui.View):
    def __init__(self, project: dict, base_interaction: discord.Interaction):
        self.boolean_options = [discord.SelectOption(label="Commit drafts", value='commit_drafts'),
                                discord.SelectOption(label="Is lobby", value='is_lobby'),
                                discord.SelectOption(label="Ensure level name in posts", value='ensure_level'),
                                discord.SelectOption(label="Use contributors file", value='use_contributors_file'),
                                discord.SelectOption(label="Run sync checks", value='do_run_validation'),
                                discord.SelectOption(label="Validate room labels in sync check", value='validate_room_labels'),
                                discord.SelectOption(label="Auto commit any timesave", value='commit_any_time_saved'),
                                discord.SelectOption(label="Project enabled", value='enabled')]

        super().__init__(timeout=600)  # 10 mins
        self.project = project
        self.project_original = copy.deepcopy(project)
        self.base_interaction = base_interaction
        self.boolean_option_selected: str | None = None
        self.boolean_select = BooleanSelect(self)
        self.project_options_boolean_select = ProjectOptionsBooleanSelect(self)
        self.add_item(self.project_options_boolean_select)
        self.add_item(self.boolean_select)

    @discord.ui.button(label="Edit details 1", style=discord.ButtonStyle.secondary, row=2)
    async def edit_details_1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log.info("Opening project details 1 editor modal")
        await interaction.response.send_modal(ProjectEditorModal1(self))

    @discord.ui.button(label="Edit details 2", style=discord.ButtonStyle.secondary, row=2)
    async def edit_details_2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log.info("Opening project details 2 editor modal")
        await interaction.response.send_modal(ProjectEditorModal2(self))

    @discord.ui.button(label="Add disallowed command exemption", style=discord.ButtonStyle.secondary, row=3)
    async def add_disallowed_command_exemption_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log.info("Opening disallowed command exemption modal")
        await interaction.response.send_modal(DisallowedCommandExemptionModal(self))

    @discord.ui.button(label="Save", style=discord.ButtonStyle.primary, row=3)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        deep_diff = DeepDiff(self.project_original, self.project, ignore_order=True, report_repetition=True, verbose_level=2)
        log.info(f"Saving, changes: {deep_diff}")
        changes_made_count = len(deep_diff.pretty().splitlines())

        if changes_made_count:
            project_id = self.project['project_id']
            updated_project = self.project_original + Delta(deep_diff)
            db.projects.set(project_id, updated_project)

        saved_text = f"Saved {changes_made_count} change{plural(changes_made_count)}." if changes_made_count else "No changes made."
        await interaction.response.send_message(saved_text)
        await (await utils.user_from_id(client, constants.admin_user_id)).send(f"`{saved_text}`")

        if changes_made_count:
            tasks.get_crons.cache_clear()
            await main.edit_pin(client.get_channel(project_id))
            main.generate_request_headers(updated_project['installation_owner'])
            main.generate_path_cache(project_id, updated_project)

        self.stop()

    @staticmethod
    async def generate_message(project: dict):
        admins = [(await utils.user_from_id(client, admin)).display_name for admin in project['admins']]
        subdir_display = f"`{project['subdir']}`" if project['subdir'] else "None"
        mods_display = f"`{', '.join([mod for mod in project['mods']])}`" if project['mods'] else "None"
        contributors_file_path_display = f"`{project['contributors_file_path']}`" if project['contributors_file_path'] else "Root"
        excluded_items_display = f"`{', '.join([item for item in project['excluded_items']])}`" if project['excluded_items'] else "None"

        if project['room_suggestion_cron']:
            room_suggestion_display = f"Enabled, runs at `{project['room_suggestion_cron']}` in channel ID `{project['room_suggestion_channel']}`"
        else:
            room_suggestion_display = "Disabled"

        if not project['disallowed_command_exemptions']:
            disallowed_command_exemptions_display = "None"
        else:
            exemption_bullets = []

            for command, argument in project['disallowed_command_exemptions']:
                exemption_bullets.append(f"    - Command: `{command}`, argument: {"None" if argument == '' else f"`{argument}`"}")

            disallowed_command_exemptions_display = f'\n{'\n'.join(exemption_bullets)}'

        return (f"- Details 1\n"
                f"  - Name: `{project['name']}`\n"
                f"  - Repo: `{project['repo']}`\n"
                f"  - Subdirectory: {subdir_display}\n"
                f"  - Excluded items: {excluded_items_display}\n"
                f"  - Contributors file path: {contributors_file_path_display}\n"
                f"  - Admins: `{', '.join(admins)}` (edit with `/edit_admin`)\n"
                f"- Details 2\n"
                f"  - Github installation owner: `{project['installation_owner']}`\n"
                f"  - Mods: {mods_display} (add with `/add_mods` preferably)\n"
                f"  - Lobby sheet cell: {project['lobby_sheet_cell']} (edit with `/link_lobby_sheet`)\n"
                f"  - Disallowed command exemptions: {disallowed_command_exemptions_display}\n"
                f"  - Room suggestions: {room_suggestion_display}\n"
                f"- Settings\n"
                f"  - Commit drafts: `{project['commit_drafts']}`\n"
                f"  - Is lobby: `{project['is_lobby']}`\n"
                f"  - Ensure level name in posts: `{project['ensure_level']}`\n"
                f"  - Use contributors file: `{project['use_contributors_file']}`\n"
                f"  - Run sync checks: `{project['do_run_validation']}`\n"
                f"  - Validate room labels in sync check: `{project['validate_room_labels']}`\n"
                f"  - Auto commit any timesave: `{project['commit_any_time_saved']}`\n"
                f"  - Project enabled: `{project['enabled']}`")

    async def update_message(self):
        await self.base_interaction.edit_original_response(content=await self.generate_message(self.project))


class ProjectOptionsBooleanSelect(discord.ui.Select):
    def __init__(self, editor: ProjectEditor):
        self.editor = editor
        super().__init__(placeholder="Project setting", options=self.editor.boolean_options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.editor.boolean_option_selected = self.values[0]
        await interaction.response.defer()


class BooleanSelect(discord.ui.Select):
    def __init__(self, editor: ProjectEditor):
        self.editor = editor
        options = [discord.SelectOption(label="True", value='t'), discord.SelectOption(label="False", value='f')]
        super().__init__(placeholder="True/false", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0] == 't'

        for option in self.editor.boolean_options:
            if option.value == self.editor.boolean_option_selected:
                self.editor.project[option.value] = chosen
                log.info(f"Changed {option.value} to {chosen}")
                break

        self.editor.boolean_option_selected = None
        await interaction.response.defer()
        await self.editor.update_message()


class ProjectEditorModal1(discord.ui.Modal, title="Edit project details 1"):
    def __init__(self, base_editor: ProjectEditor):
        super().__init__()
        self.base_editor = base_editor
        project = base_editor.project
        self.field_name.default = project['name']
        self.field_repo.default = project['repo']
        self.field_subdir.default = project['subdir']
        self.field_contributors_file_path.default = project['contributors_file_path']
        self.field_excluded_items.default = ', '.join(project['excluded_items'])

    field_name = discord.ui.TextInput(label="Name")
    field_repo = discord.ui.TextInput(label="Repo")
    field_subdir = discord.ui.TextInput(label="Subdirectory (project root folder in repo)", required=False)
    field_contributors_file_path = discord.ui.TextInput(label="Contributors file path", required=False)
    field_excluded_items = discord.ui.TextInput(label="Excluded items", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        log.info("Submitted modal 1")
        project = self.base_editor.project
        project['name'] = self.field_name.value
        project['repo'] = self.field_repo.value
        project['subdir'] = self.field_subdir.value
        project['contributors_file_path'] = self.field_contributors_file_path.value
        project['excluded_items'] = split_items(self.field_excluded_items)
        await interaction.response.defer()
        await self.base_editor.update_message()


class ProjectEditorModal2(discord.ui.Modal, title="Edit project details 2"):
    def __init__(self, base_editor: ProjectEditor):
        super().__init__()
        self.base_editor = base_editor
        project = base_editor.project
        self.field_installation_owner.default = project['installation_owner']
        self.field_mods.default = ', '.join(project['mods'])
        self.field_room_suggestion_cron.default = project['room_suggestion_cron']
        self.field_room_suggestion_channel.default = None if project['room_suggestion_channel'] == 0 else int(project['room_suggestion_channel'])

    field_installation_owner = discord.ui.TextInput(label="Github installation owner")
    field_mods = discord.ui.TextInput(label="Mods (for sync check, filenames without .zip)", required=False)
    field_room_suggestion_cron = discord.ui.TextInput(label="Room suggestion cron (none = disable)", required=False)
    field_room_suggestion_channel = discord.ui.TextInput(label="Room suggestion channel ID", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        room_suggestion_cron = self.field_room_suggestion_cron.value

        try:
            cron_validator.CronValidator.parse(room_suggestion_cron)
        except ValueError:
            log.warning(f"Entered cron \"{room_suggestion_cron}\" is invalid")
            room_suggestion_cron = ''

        try:
            room_suggestion_channel = int(self.field_room_suggestion_channel.value)
        except ValueError:
            room_suggestion_channel = 0

        log.info("Submitted modal 2")
        project = self.base_editor.project
        project['installation_owner'] = self.field_installation_owner.value
        project['mods'] = split_items(self.field_mods)
        project['room_suggestion_cron'] = room_suggestion_cron
        project['room_suggestion_channel'] = room_suggestion_channel
        await interaction.response.defer()
        await self.base_editor.update_message()


class DisallowedCommandExemptionModal(discord.ui.Modal, title="Add disallowed command exemption"):
    def __init__(self, base_editor: ProjectEditor):
        super().__init__()
        self.base_editor = base_editor

    field_command = discord.ui.TextInput(label="Command")
    field_argument = discord.ui.TextInput(label="Argument", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        log.info("Submitted command exemption modal")
        added_command = self.field_command.value.lower()
        added_argument = self.field_argument.value.lower()
        self.base_editor.project['disallowed_command_exemptions'].append([added_command, added_argument])
        await interaction.response.defer()
        await self.base_editor.update_message()


# these are here for organization but actually belongs to /edit_admin, not /edit_project

class AdminEditor(discord.ui.View):
    def __init__(self, project: dict, current_admins: list[discord.User]):
        super().__init__(timeout=600)  # 10 mins
        self.project = project
        self.admin_user_select = AdminUserSelect(current_admins)
        self.add_item(self.admin_user_select)
        self.add_item(AdminSaveButton(self))


class AdminUserSelect(discord.ui.UserSelect):
    def __init__(self, current_admins: list[discord.User]):
        super().__init__(placeholder="Select admin(s)", default_values=current_admins, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class AdminSaveButton(discord.ui.Button):
    def __init__(self, editor: AdminEditor):
        super().__init__(label="Save", style=discord.ButtonStyle.primary)
        self.editor = editor

    async def callback(self, interaction: discord.Interaction):
        project = self.editor.project
        previous_admin_ids = project['admins']
        new_admins_ids = [user.id for user in self.editor.admin_user_select.values]
        deep_diff = DeepDiff(previous_admin_ids, new_admins_ids, ignore_order=True, verbose_level=2)
        log.info(f"Saved, changes: {deep_diff}")
        changes_made_count = len(deep_diff.pretty().splitlines())

        if changes_made_count:
            project['admins'] = new_admins_ids
            db.projects.set(project['project_id'], project)
            await main.edit_pin(client.get_channel(project['project_id']))

        await interaction.response.send_message(f"Saved {changes_made_count} change{plural(changes_made_count)}." if changes_made_count else "Saved, no changes made.", ephemeral=True)
        self.editor.stop()

        for user in self.editor.admin_user_select.values:
            if user.id not in previous_admin_ids and user.id != interaction.user.id:
                await user.send(f"{interaction.user.display_name} has added you as an admin to the \"{project['name']}\" TAS project.")
                log.info(f"DM'd {utils.detailed_user(user=user)} about being added as an admin")

        for user_id in previous_admin_ids:
            if user_id not in new_admins_ids and user_id != interaction.user.id:
                user = await utils.user_from_id(client, user_id)
                await user.send(f"{interaction.user.display_name} has removed you as an admin from the \"{project['name']}\" TAS project.")
                log.info(f"DM'd {utils.detailed_user(user=user)} about being removed as an admin")


def split_items(field: discord.ui.TextInput) -> list[str]:
    return [item.strip() for item in field.value.split(',') if item]


client: Optional[discord.Client] = None
log: Union[logging.Logger, utils.LogPlaceholder] = utils.LogPlaceholder()
