import copy
import logging
from typing import Optional, Union

import discord
from deepdiff import DeepDiff

import db
import main

import utils


class ProjectEditor(discord.ui.View):
    def __init__(self, project: dict, base_interaction: discord.Interaction):
        self.boolean_options = [discord.SelectOption(label="Commit drafts", value='commit_drafts'),
                                discord.SelectOption(label="Is lobby", value='is_lobby'),
                                discord.SelectOption(label="Ensure level name in posts", value='ensure_level'),
                                discord.SelectOption(label="Use contributors file", value='use_contributors_file'),
                                discord.SelectOption(label="Run sync checks", value='do_run_validation'),
                                discord.SelectOption(label="Project enabled", value='enabled')]

        super().__init__(timeout=300)
        self.project = project
        self.project_original = copy.deepcopy(project)
        self.base_interaction = base_interaction
        self.boolean_option_selected: str | None = None
        self.boolean_select = BooleanSelect(self)
        self.project_options_boolean_select = ProjectOptionsBooleanSelect(self)
        self.add_item(self.project_options_boolean_select)
        self.add_item(self.boolean_select)
        self.add_item(EditDetailsButton(self))
        self.add_item(SubmitButton(self))

    @staticmethod
    async def generate_message(project: dict):
        admins = [utils.detailed_user(user=await utils.user_from_id(client, admin)) for admin in project['admins']]
        subdir_display = f"`{project['subdir']}`" if project['subdir'] else "None"
        mods_display = f"`{', '.join([mod for mod in project['mods']])}`" if project['mods'] else "None"
        contributors_file_path_display = f"`{project['contributors_file_path']}`" if project['contributors_file_path'] else "Root"
        excluded_items_display = f"`{', '.join([item for item in project['excluded_items']])}`" if project['excluded_items'] else "None"

        return (f"Editing TAS project **{project['name']}**\n\n"
                f"Repo: `{project['repo']}`\n"
                f"Subdirectory: {subdir_display}\n"
                f"Github installation owner: `{project['installation_owner']}`\n"
                f"Admins: `{', '.join(admins)}` (edit with `/edit_admin`)\n"
                f"Mods: {mods_display} (edit with `/add_mods`)\n"
                f"Excluded items: {excluded_items_display}\n"
                f"Contributors file path: {contributors_file_path_display}\n\n"
                f"Commit drafts: `{project['commit_drafts']}`\n"
                f"Is lobby: `{project['is_lobby']}`\n"
                f"Ensure level name in posts: `{project['ensure_level']}`\n"
                f"Use contributors file: `{project['use_contributors_file']}`\n"
                f"Run sync checks: `{project['do_run_validation']}`\n"
                f"Project enabled: `{project['enabled']}`")

    async def update_message(self):
        await self.base_interaction.edit_original_response(content=await self.generate_message(self.project))


class ProjectOptionsBooleanSelect(discord.ui.Select):
    def __init__(self, editor: ProjectEditor):
        self.editor = editor
        super().__init__(placeholder="Project setting", options=self.editor.boolean_options)

    async def callback(self, interaction: discord.Interaction):
        self.editor.boolean_option_selected = self.values[0]
        await interaction.response.defer()


class BooleanSelect(discord.ui.Select):
    def __init__(self, editor: ProjectEditor):
        self.editor = editor
        options = [discord.SelectOption(label="True", value='t'), discord.SelectOption(label="False", value='f')]
        super().__init__(placeholder="True/false", options=options)

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


class EditDetailsButton(discord.ui.Button):
    def __init__(self, editor: ProjectEditor):
        super().__init__(label="Edit details", style=discord.ButtonStyle.secondary)
        self.editor = editor

    async def callback(self, interaction: discord.Interaction):
        log.info("Opening project details editor modal")
        await interaction.response.send_modal(ProjectEditorModal(self.editor))


class SubmitButton(discord.ui.Button):
    def __init__(self, editor: ProjectEditor):
        super().__init__(label="Submit", style=discord.ButtonStyle.primary)
        self.editor = editor

    async def callback(self, interaction: discord.Interaction):
        project = self.editor.project
        log.info(f"Submitted, changes: {DeepDiff(self.editor.project_original, project, ignore_order=True, verbose_level=2)}")
        db.projects.set(project['project_id'], project)
        await main.edit_pin(client.get_channel(project['project_id']))
        await interaction.response.send_message("Saved.")
        self.editor.stop()


class ProjectEditorModal(discord.ui.Modal, title="Edit project details"):
    def __init__(self, base_editor: ProjectEditor):
        super().__init__()
        self.base_editor = base_editor
        project = base_editor.project

        self.field_name.default = project['name']
        self.field_repo.default = project['repo']
        # self.field_installation_owner.default = project['installation_owner']
        self.field_subdir.default = project['subdir']
        self.field_contributors_file_path.default = project['contributors_file_path']
        self.field_excluded_items.default = ', '.join(project['excluded_items'])

    field_name = discord.ui.TextInput(label="Name")
    field_repo = discord.ui.TextInput(label="Repo")
    field_subdir = discord.ui.TextInput(label="Subdirectory (project root folder in repo)", required=False)
    # field_installation_owner = discord.ui.TextInput(label="Github installation owner")
    field_contributors_file_path = discord.ui.TextInput(label="Contributors file path", required=False)
    field_excluded_items = discord.ui.TextInput(label="Excluded items", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        log.info("Submitted modal")
        project = self.base_editor.project

        project['name'] = self.field_name.value
        project['repo'] = self.field_repo.value
        # project['installation_owner'] = self.field_installation_owner.value
        project['subdir'] = self.field_subdir.value
        project['contributors_file_path'] = self.field_contributors_file_path.value
        project['excluded_items'] = [item.strip() for item in self.field_excluded_items.value.split(',')]

        await interaction.response.defer()
        await self.base_editor.update_message()


client: Optional[discord.Client] = None
log: Union[logging.Logger, utils.LogPlaceholder] = utils.LogPlaceholder()
