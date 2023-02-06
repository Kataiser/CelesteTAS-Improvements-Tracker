import functools
import logging
from typing import List, Optional

import discord
import ujson
from fuzzywuzzy import process as fuzzy_process
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import utils


class MapRow:
    def __init__(self, map_name: str):
        difficulty = sj_data[map_name][1]
        column = sj_data[map_name][3]
        self.status_cell = Cell(self, difficulty, "status", column)
        self.taser_cell = Cell(self, difficulty, "marked taser", column)
        self.progress_cell = Cell(self, difficulty, "progress note", column)
        self.writes = []

    def __del__(self):
        sheet_writes.info(' '.join(self.writes))


class Cell:
    def __init__(self, map_row: MapRow, difficulty: str, cell_type: str, column: int):
        cell_types = {"status": 'A', "marked taser": 'D', "progress note": 'E'}
        self.position = f'{difficulty}!{cell_types[cell_type]}{column}'
        self.map_row = map_row
        self.cell_type = cell_type

    def read(self) -> Optional[str]:
        try:
            result = sheet.values().get(spreadsheetId=sheet_id, range=self.position).execute()
        except HttpError as error:
            log.error(repr(error))
            return

        values = result.get('values', [])

        if values:
            return values[0][0]
        else:
            return ''

    def write(self, data: str):
        try:
            sheet.values().update(spreadsheetId=sheet_id, range=self.position, valueInputOption='RAW', body={'values': [[data]]}).execute()
            successful = True
        except HttpError as error:
            log.error(repr(error))
            successful = False

        self.map_row.writes.append(str((self.cell_type, data, successful)))


async def draft(interaction: discord.Interaction, map_name: str):
    """Sign yourself up for drafting a map"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} wants to draft \"{map_name}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.read()
    status = map_row.status_cell.read()

    if status == '❌' or (status == '⬇️' and marked_taser == interaction.user.name):
        map_row.status_cell.write('🛠️')
        map_row.taser_cell.write(interaction.user.name)
        log.info("Successfully marked for drafting")
        await interaction.response.send_message(f"You have been marked for drafting **{map_name}**."
                                                f"\nTAS file: {sj_data[map_name][4]}"
                                                f"\nMapper: {sj_data[map_name][0]}"
                                                f"\nDifficulty: {sj_data[map_name][1]}"
                                                f"\nDescription: {sj_data[map_name][2]}")
    elif status == '🛠️':
        if marked_taser == interaction.user.name:
            log.warning("Already marked as drafting by user")
            await interaction.response.send_message(f"You are already marked for drafting **{map_name}**.")
        else:
            log.warning(f"Already marked as drafting by {marked_taser}")
            await interaction.response.send_message(f"**{map_name}** is already marked for drafting by {marked_taser}.")
    elif status == '⬇️':
        log.warning(f"Marked as dropped by {marked_taser}")
        await interaction.response.send_message(f"**{map_name}** is marked as dropped by {marked_taser}.")
    elif status == '✅':
        log.warning("Map already drafted")
        await interaction.response.send_message(f"**{map_name}** has already been drafted by {marked_taser}.")

    del map_row


async def update_progress(interaction: discord.Interaction, map_name: str, note: str):
    """Put a note for how progress is going"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is setting progress for \"{map_name}\": \"{note}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.read()

    if marked_taser == interaction.user.name:
        map_row.progress_cell.write(note)
        await interaction.response.send_message(f"Progress note added to **{map_name}**: \"{note}\"")
        log.info("Progress note set")
    else:
        await interaction.response.send_message(f"Can't add note for **{map_name}** since the map is not being drafted by you.")
        log.warning("Progress note not set")

    del map_row


async def progress(interaction: discord.Interaction, map_name: str):
    """Show progress note"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is checking progress for \"{map_name}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    status = map_row.status_cell.read()

    if status == '❌':
        log.info("Not yet drafted")
        await interaction.response.send_message(f"❌ The draft for **{map_name}** has not yet been started.")
    elif status == '🛠️':
        marked_taser = map_row.taser_cell.read()
        progress_note = map_row.progress_cell.read()

        if progress_note:
            log.info("Draft is WIP with a note")
            await interaction.response.send_message(f"🛠️ The draft for **{map_name}** has been started by {marked_taser}.\nProgress note: \"{progress_note}\"")
        else:
            log.info("Draft is WIP without a note")
            await interaction.response.send_message(f"🛠️ The draft for **{map_name}** has been started by {marked_taser}.")
    elif status == '⬇️':
        log.info("Draft is dropped")
        marked_taser = map_row.taser_cell.read()
        progress_note = map_row.progress_cell.read()
        drop_reason_formatted = f"Drop reason: \"{progress_note.removeprefix('Drop reason: ')}\""
        await interaction.response.send_message(f"⬇️ The draft for **{map_name}** has been dropped by {marked_taser}.\n{drop_reason_formatted}")
    elif status == '✅':
        marked_taser = map_row.taser_cell.read()
        log.info("Draft is finished")
        await interaction.response.send_message(f"✅ The draft for **{map_name}** has been finished by {marked_taser}.")
    else:
        log.warning("Unknown draft status")
        await interaction.response.send_message(f"❓ The draft for **{map_name}** is unknown.")

    del map_row


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
    await interaction.response.send_message(f"Dropped **{map_name}**. Make sure to post the file.\nDrop reason: \"{reason}\"")
    del map_row


async def complete(interaction: discord.Interaction, map_name: str):
    """Mark a draft as completed"""
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} has completed \"{map_name}\"")

    if map_name not in sj_data:
        log.warning("Not a valid SJ map")
        await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.")
        return

    map_row = MapRow(map_name)
    map_row.status_cell.write('✅')
    map_row.progress_cell.write('')
    await interaction.response.send_message(f"Completed **{map_name}**. Make sure to post the file.")
    log.info("Successfully dropped")
    del map_row


async def sj_command_allowed(interaction: discord.Interaction) -> bool:
    return True

    role_check = [role for role in interaction.user.roles if role.id == 511380746779230240] != []
    channel_check = interaction.channel_id == 0

    if not role_check:
        await interaction.response.send_message("SJ TAS commands can only be run by users with the TASer role.")
    elif not channel_check:
        await interaction.response.send_message("SJ TAS commands can only be run in #.")

    return role_check and channel_check


@functools.lru_cache(maxsize=256)
def sj_fuzzy_match(search: str) -> List[str]:
    return []

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
