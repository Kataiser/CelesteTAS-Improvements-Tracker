import functools
import logging
from typing import List, Optional

import discord
import ujson
from fuzzywuzzy import process as fuzzy_process
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import main
import utils


class MapRow:
    def __init__(self, map_name: str):
        difficulty = sj_data[map_name][1]
        row = sj_data[map_name][3]
        self.map_name = map_name
        self.data = {"status": '', "map": '', "file": '', "taser": '', "progress": ''}
        self.range = f'{difficulty}!A{row}:E{row}'
        self.status_cell = Cell(self, "status")
        self.taser_cell = Cell(self, "taser")
        self.progress_cell = Cell(self, "progress")
        self.writes = []
        self.changed_data = False

        try:
            result = sheet.values().get(spreadsheetId=sheet_id, range=self.range).execute()
            values = result.get('values', [])[0]
        except HttpError as error:
            log.error(repr(error))
            return

        # because values can be too short
        for column_enum in enumerate(self.data):
            i, column = column_enum

            if i < len(values):
                self.data[column] = values[i]

    def write_cell(self, column: str, value: str):
        if value != self.data[column]:
            self.changed_data = True
            self.writes.append(str((column, value)))

        self.data[column] = value

    def update(self):
        if self.changed_data:
            sheet_log = str((self.map_name, self.range, ' '.join(self.writes)))

            try:
                sheet.values().update(spreadsheetId=sheet_id, range=self.range, valueInputOption='RAW', body={'values': [list(self.data.values())]}).execute()
                sheet_writes.info(sheet_log)
            except HttpError as error:
                log.error(repr(error))
                sheet_writes.error(sheet_log)


class Cell:
    def __init__(self, map_row: MapRow, column: str):
        self.map_row = map_row
        self.column = column

    def value(self) -> str:
        return self.map_row.data[self.column]

    def write(self, value: str):
        self.map_row.write_cell(self.column, value)


async def draft(interaction: discord.Interaction, map_name: str):
    """Sign yourself up for drafting a map"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} wants to draft \"{map_name}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.value()
    status = map_row.status_cell.value()
    caller_name = main.nickname(interaction.user)

    if status in ('❌', '⬇️'):
        map_row.status_cell.write('🛠️')
        map_row.taser_cell.write(caller_name)
        map_row.update()
        await interaction.response.send_message(f"You have been marked for drafting **{map_name}**."
                                                f"\nTAS file: {sj_data[map_name][4]}"
                                                f"\nMapper: {sj_data[map_name][0]}"
                                                f"\nDifficulty: {sj_data[map_name][1]}"
                                                f"\nDescription (probably): {sj_data[map_name][2]}")
        log.info("Successfully marked for drafting")
    elif status == '🛠️':
        if marked_taser == caller_name:
            log.warning("Already marked as drafting by user")
            await interaction.response.send_message(f"You are already marked for drafting **{map_name}**.")
        else:
            log.warning(f"Already marked as drafting by {marked_taser}")
            await interaction.response.send_message(f"**{map_name}** is already marked for drafting by {marked_taser}.")
    elif status == '✅':
        log.warning("Map already drafted")
        await interaction.response.send_message(f"**{map_name}** has already been drafted by {marked_taser}.")


async def update_progress(interaction: discord.Interaction, map_name: str, note: str):
    """Put a note for how progress is going"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is setting progress for \"{map_name}\": \"{note}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.value()

    if marked_taser == main.nickname(interaction.user):
        map_row.progress_cell.write(note)
        map_row.update()
        await interaction.response.send_message(f"Progress note added to **{map_name}**: \"{note}\"")
        log.info("Progress note set")
    else:
        await interaction.response.send_message(f"Can't add note for **{map_name}** since the map is not being drafted by you.")
        log.warning("Progress note not set")


async def progress(interaction: discord.Interaction, map_name: str):
    """Show progress note"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is checking progress for \"{map_name}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    status = map_row.status_cell.value()

    if status == '❌':
        log.info("Not yet drafted")
        await interaction.response.send_message(f"❌ The draft for **{map_name}** has not yet been started.")
    elif status == '🛠️':
        marked_taser = map_row.taser_cell.value()
        progress_note = map_row.progress_cell.value()

        if progress_note:
            log.info("Draft is WIP with a note")
            await interaction.response.send_message(f"🛠️ The draft for **{map_name}** has been started by {marked_taser}.\nProgress note: \"{progress_note}\"")
        else:
            log.info("Draft is WIP without a note")
            await interaction.response.send_message(f"🛠️ The draft for **{map_name}** has been started by {marked_taser}.")
    elif status == '⬇️':
        log.info("Draft is dropped")
        marked_taser = map_row.taser_cell.value()
        progress_note = map_row.progress_cell.value()
        drop_reason_formatted = f"Drop reason: \"{progress_note.removeprefix('Drop reason: ')}\""
        await interaction.response.send_message(f"⬇️ The draft for **{map_name}** has been dropped by {marked_taser}.\n{drop_reason_formatted}")
    elif status == '✅':
        marked_taser = map_row.taser_cell.value()
        log.info("Draft is finished")
        await interaction.response.send_message(f"✅ The draft for **{map_name}** has been finished by {marked_taser}.")
    else:
        log.warning("Unknown draft status")
        await interaction.response.send_message(f"❓ The draft for **{map_name}** is unknown.")

    map_row.update()


async def drop(interaction: discord.Interaction, map_name: str, reason: str):
    """Drop a map (stop drafting it)"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is dropping \"{map_name}\" for reason: \"{reason}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    map_row.status_cell.write('⬇️')
    map_row.progress_cell.write(f"Drop reason: {reason}")
    map_row.update()
    await interaction.response.send_message(f"Dropped **{map_name}**. Make sure to post the file.\nDrop reason: \"{reason}\"")


async def complete(interaction: discord.Interaction, map_name: str):
    """Mark a draft as completed"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} has completed \"{map_name}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.value()
    caller_name = main.nickname(interaction.user)

    if marked_taser == caller_name:
        map_row.status_cell.write('✅')
        map_row.progress_cell.write('')
        map_row.update()
        await interaction.response.send_message(f"Completed **{map_name}**. Make sure to post the file.")
        log.info("Successfully dropped")
    else:
        log.warning(f"Marked taser is {marked_taser}")
        await interaction.response.send_message(f"**{map_name}** is marked for drafting by {marked_taser}.")


async def sj_command_allowed(interaction: discord.Interaction) -> bool:
    if interaction.channel_id == 1071151339905753138:  # test channel
        return True

    role_check = [role for role in interaction.user.roles if role.id == 511380746779230240] != []
    channel_check = interaction.channel_id == 1074148152317321316

    if not role_check:
        await interaction.response.send_message("SJ TAS commands can only be run by users with the TASer role.")
    elif not channel_check:
        await interaction.response.send_message("SJ TAS commands can only be run in <#1074148152317321316>.")

    return role_check and channel_check


@functools.lru_cache(maxsize=256)
def sj_fuzzy_match(search: str) -> List[str]:
    if search:
        fuzzes = fuzzy_process.extract(search, sj_data.keys())
        return [sj_map[0] for sj_map in fuzzes[:25] if sj_map[1] >= 65]
    else:
        return []


log: Optional[logging.Logger] = None
sheet_writes: Optional[logging.Logger] = None
creds = service_account.Credentials.from_service_account_file('service.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
sheet = build('sheets', 'v4', credentials=creds).spreadsheets()
difficulties = ("Beginner", "Intermediate", "Advanced", "Expert", "Grandmaster")

with open('sj.json', 'r', encoding='UTF8') as sj_file:
    sj_data: dict = ujson.load(sj_file)

with open('sheet_id', 'r') as sheet_id_file:
    sheet_id: str = sheet_id_file.read()
