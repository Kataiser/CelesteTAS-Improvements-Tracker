import datetime
import functools
import logging
import re
import time
from ssl import SSLEOFError
from typing import List, Optional, Any

import discord
from fuzzywuzzy import process as fuzzy_process
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import db
import utils
import validation
from utils import plural


class MapRow:
    def __init__(self, map_name: str):
        difficulty = sj_data[map_name][1]
        row = sj_data[map_name][3]
        self.map_name = map_name
        self.data = {'map': '', 'file': '', 'taser': '', 'current_time': '', 'draft_time': '', 'time_saved': '', 'records': '', 'rpf': '', 'improvement_date': ''}
        self.range = f'{difficulty}!A{row}:J{row}'
        self.status_cell = Cell(self, 'status')
        self.taser_cell = Cell(self, 'taser')
        self.current_time_cell = Cell(self, 'current_time')
        self.draft_time_cell = Cell(self, 'draft_time')
        self.time_saved_cell = Cell(self, 'time_saved')
        self.records_cell = Cell(self, 'records')
        self.rpf_cell = Cell(self, 'rpf')
        self.improvement_date_cell = Cell(self, 'improvement_date')
        self.writes = []
        self.changed_data = False
        values = read_sheet(self.range)

        # because values can be too short
        for column_enum in enumerate(self.data):
            i, column = column_enum

            if i < len(values):
                self.data[column] = values[i]

    def write_cell(self, column: str, value: str):
        if value != self.data[column]:
            if column == 'taser' and self.data[column] and value:
                value = f"{self.data[column]}, {value}"

            self.changed_data = True
            self.writes.append(str((column, value)))

        self.data[column] = value

    def update(self):
        if self.changed_data:
            sheet_log = str((self.map_name, self.range, ' '.join(self.writes)))

            try:
                sheet.values().update(spreadsheetId=SHEET_ID, range=self.range, valueInputOption='USER_ENTERED', body={'values': [list(self.data.values())]}).execute()
                db.sheet_writes.set(utils.log_timestamp(), {'status': 'INFO', 'log': sheet_log})
            except HttpError:
                utils.log_error()
                db.sheet_writes.set(utils.log_timestamp(), {'status': 'ERROR', 'log': sheet_log})


class Cell:
    def __init__(self, map_row: MapRow, column: str):
        self.map_row = map_row
        self.column = column

    def value(self) -> str:
        if self.column == 'status':
            return '✅'  # dummy, since the status column was removed
        else:
            return self.map_row.data[self.column]

    def write(self, value: Any):
        self.map_row.write_cell(self.column, str(value))


async def draft(interaction: discord.Interaction, map_name: str):
    """Sign yourself up for drafting a map"""
    update_last_command_used()
    map_name = correct_map_case(map_name)
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} wants to draft \"{map_name}\"")

    if map_name not in sj_data:
        await invalid_map(interaction, map_name)
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.value()
    status = map_row.status_cell.value()
    caller_name = utils.nickname(interaction.user)

    if status in ('❌', '⬇️', '🛠️'):
        if status == '🛠️' and caller_name in marked_taser:
            log.warning("Already marked as drafting by user")
            await interaction.response.send_message(f"You are already marked for drafting **{map_name}**.")
            return

        if status == '🛠️':
            prev_taser_line = f"\nOther TASer{plural(marked_taser.split(', '))}: {marked_taser}"
        elif status == '⬇️':
            prev_taser_line = f"\nPrevious TASer{plural(marked_taser.split(', '))}: {marked_taser}"
        else:
            prev_taser_line = ""

        map_row.status_cell.write('🛠️')
        map_row.taser_cell.write(caller_name)
        map_row.progress_cell.write("")
        map_row.update()
        mapper_line = f"Mappers: {sj_data[map_name][0]}" if '&' in sj_data[map_name][0] else f"Mapper: {sj_data[map_name][0]}"
        await interaction.response.send_message(f"You have been marked for drafting **{map_name}**."
                                                f"{prev_taser_line}"
                                                f"\nTAS file: `{sj_data[map_name][4]}` (get the initial file from the repo)"
                                                f"\n{mapper_line}"
                                                f"\nDifficulty: {sj_data[map_name][1]}"
                                                f"\nDescription (probably): {sj_data[map_name][2]}")
        log.info("Successfully marked for drafting")
    elif status == '✅':
        log.warning("Map already drafted")
        await interaction.response.send_message(f"**{map_name}** has already been drafted by {marked_taser}.")


async def update_progress(interaction: discord.Interaction, map_name: str, note: str):
    """Put a note for how progress is going"""
    update_last_command_used()
    map_name = correct_map_case(map_name)
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is setting progress for \"{map_name}\": \"{note}\"")

    if map_name not in sj_data:
        await invalid_map(interaction, map_name)
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.value()

    if utils.nickname(interaction.user) in marked_taser:
        map_row.progress_cell.write(note)
        map_row.update()
        await interaction.response.send_message(f"Progress note added to **{map_name}**: \"{note}\"")
        log.info("Progress note set")
    else:
        await interaction.response.send_message(f"Can't add note for **{map_name}** since the map is not being drafted by you.")
        log.warning("Progress note not set")


async def progress(interaction: discord.Interaction, map_name: str):
    """Show progress note"""
    update_last_command_used()
    map_name = correct_map_case(map_name)
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is checking progress for \"{map_name}\"")

    if map_name not in sj_data:
        await invalid_map(interaction, map_name)
        return

    map_row = MapRow(map_name)
    status = map_row.status_cell.value()

    if status == '❌':
        log.info("Not yet drafted")
        await interaction.response.send_message(f"❌ The draft for **{map_name}** has not yet been started.", ephemeral=True)
    elif status == '🛠️':
        marked_taser = map_row.taser_cell.value()
        progress_note = map_row.progress_cell.value()

        if progress_note:
            log.info("Draft is WIP with a note")
            await interaction.response.send_message(f"🛠️ The draft for **{map_name}** has been started by {marked_taser}.\nProgress note: \"{progress_note}\"", ephemeral=True)
        else:
            log.info("Draft is WIP without a note")
            await interaction.response.send_message(f"🛠️ The draft for **{map_name}** has been started by {marked_taser}.", ephemeral=True)
    elif status == '⬇️':
        log.info("Draft is dropped")
        marked_taser = map_row.taser_cell.value()
        progress_note = map_row.progress_cell.value()
        drop_reason_formatted = f"Drop reason: \"{progress_note.removeprefix('Drop reason: ')}\""
        await interaction.response.send_message(f"⬇️ The draft for **{map_name}** has been dropped by {marked_taser}.\n{drop_reason_formatted}", ephemeral=True)
    elif status == '✅':
        marked_taser = map_row.taser_cell.value()
        log.info("Draft is finished")
        await interaction.response.send_message(f"✅ The draft for **{map_name}** has been finished by {marked_taser}.", ephemeral=True)
    else:
        log.warning("Unknown draft status")
        await interaction.response.send_message(f"❓ The draft for **{map_name}** is unknown.", ephemeral=True)

    map_row.update()


async def drop(interaction: discord.Interaction, map_name: str, reason: str):
    """Drop a map (stop drafting it after having made progress)"""
    update_last_command_used()
    map_name = correct_map_case(map_name)
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is dropping \"{map_name}\" for reason: \"{reason}\"")

    if map_name not in sj_data:
        await invalid_map(interaction, map_name)
        return

    map_row = MapRow(map_name)
    map_row.status_cell.write('⬇️')
    map_row.progress_cell.write(f"Drop reason: {reason}")
    map_row.update()
    await interaction.response.send_message(f"Dropped **{map_name}**. Make sure to post the file in <#1074148268407275520> if you haven't already (include \"WIP\" in your message)."
                                            f"\nDrop reason: \"{reason}\"")


async def complete(interaction: discord.Interaction, map_name: str):
    """Mark a draft as completed"""
    update_last_command_used()
    map_name = correct_map_case(map_name)
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} has completed \"{map_name}\"")

    if map_name not in sj_data:
        await invalid_map(interaction, map_name)
        return

    map_row = MapRow(map_name)
    marked_taser = map_row.taser_cell.value()
    caller_name = utils.nickname(interaction.user)

    if caller_name in marked_taser or not marked_taser:
        map_row.status_cell.write('✅')
        map_row.progress_cell.write("")
        map_row.update()
        await interaction.response.send_message(f"Completed **{map_name}**. Make sure to post the file in <#1074148268407275520> if you haven't already.")
        log.info("Successfully dropped")
    else:
        log.warning(f"Marked taser is {marked_taser}")
        await interaction.response.send_message(f"**{map_name}** is marked for drafting by {marked_taser}.")


async def undraft(interaction: discord.Interaction, map_name: str):
    """Stop drafting a map without dropping a WIP file"""
    update_last_command_used()
    map_name = correct_map_case(map_name)
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is undrafting \"{map_name}\"")

    if map_name not in sj_data:
        await invalid_map(interaction, map_name)
        return

    map_row = MapRow(map_name)
    map_row.status_cell.write('❌')
    map_row.taser_cell.write("")
    map_row.progress_cell.write("")
    map_row.update()
    await interaction.response.send_message(f"Undrafted **{map_name}**.")


async def taser_status(interaction: discord.Interaction, taser: str):
    """See what maps a TASer is marked for drafting"""
    update_last_command_used()
    log.info(f"Spreadsheet: {utils.detailed_user(user=interaction.user)} is checking status for TASer \"{taser}\"")

    if re_ping.match(taser):
        taser = utils.nickname(await client.fetch_user(int(taser[2:-1])))
        log.info(f"Converted ping to name: {taser}")

    sheet_beg = read_sheet('Beginner!A2:E24', multiple_rows=True)
    sheet_int = read_sheet('Intermediate!A2:E20', multiple_rows=True)
    sheet_adv = read_sheet('Advanced!A2:E27', multiple_rows=True)
    sheet_exp = read_sheet('Expert!A2:E31', multiple_rows=True)
    sheet_gm = read_sheet('Grandmaster!A2:E20', multiple_rows=True)
    combined = sheet_beg + sheet_int + sheet_adv + sheet_exp + sheet_gm
    found_log = []
    found_formatted = []
    taser_lower = taser.lower()

    for row in combined:
        if len(row) > 3 and (taser_lower in row[3].lower().split(', ') if ',' in row[3] else taser_lower == row[3].lower()):
            row_formatted_lines = [f"{row[0]} **{row[1]}**"]

            if ',' in row[3]:
                row_formatted_lines.append(f"Drafters: {row[3]}")

            if len(row) > 4:
                row_formatted_lines.append(f"Progress note: \"{row[4]}\"")

            found_log.append(str(row))
            found_formatted.append('\n'.join(row_formatted_lines))

    if found_log:
        log.info(f"Statuses: {','.join(found_log)}")
        await interaction.response.send_message('\n\n'.join(found_formatted), ephemeral=True)
    else:
        log.info("No statuses found")
        await interaction.response.send_message(f"{taser} is not marked for any drafts.", ephemeral=True)


def update_stats(filename: str, validation_result: validation.ValidationResult, date: Optional[str] = None):
    sj_map = sj_data_filenames[filename]
    log.info(f"Updating spreadsheet stats for {sj_map} ({filename})")
    tas_lines, chaptertime_line = validation_result.sj_data
    recordcount = 0

    map_row = MapRow(sj_map)
    map_row.current_time_cell.write(validation_result.finaltime)
    map_row.improvement_date_cell.write(date if date else datetime.datetime.now().strftime('%Y-%m-%d'))
    draft_time = map_row.draft_time_cell.value()
    new_frames = validation.time_to_frames(validation_result.finaltime)

    if draft_time:
        draft_frames = validation.time_to_frames(draft_time)
        timesave_frames = validation.calculate_time_difference(draft_frames, new_frames)
        percent_saved = (timesave_frames / draft_frames) * 100
        map_row.time_saved_cell.write(f"{timesave_frames}f ({percent_saved:.1f}%)")
    else:
        log.warning("No draft time")

    for line in tas_lines:
        if line.startswith('RecordCount: '):
            recordcount = int(line.partition(':')[2].strip())
            break

    if recordcount:
        map_row.records_cell.write(recordcount)
        map_row.rpf_cell.write(f"{recordcount / new_frames:.3f}")
    else:
        log.warning("Couldn't calculate RPF (no record count)")

    map_row.update()


async def sj_command_allowed(interaction: discord.Interaction) -> bool:
    if interaction.channel_id == 1071151339905753138:  # test channel
        return True

    role_check = [role for role in interaction.user.roles if role.id == 511380746779230240] != []
    channel_check = interaction.channel_id == 1074148152317321316

    if not role_check:
        await interaction.response.send_message("SJ TAS spreadsheet commands can only be run by users with the TASer role.", ephemeral=True)
    elif not channel_check:
        await interaction.response.send_message("SJ TAS spreadsheet commands can only be run in <#1074148152317321316>.", ephemeral=True)

    return role_check and channel_check


@functools.lru_cache(maxsize=512)
def sj_fuzzy_match(search: str) -> List[str]:
    if search:
        fuzzes = fuzzy_process.extract(search, sj_data.keys())
        return [sj_map[0] for sj_map in fuzzes[:25] if sj_map[1] >= 65]
    else:
        return []


@functools.lru_cache(maxsize=512)
def correct_map_case(map_name: str) -> str:
    if map_name in sj_data:
        return map_name

    map_name_lower = map_name.lower()

    for sj_map in sj_data:
        if sj_map.lower() == map_name_lower:
            return sj_map

    return map_name


async def invalid_map(interaction: discord.Interaction, map_name: str):
    log.warning("Not a valid SJ map")
    await interaction.response.send_message(f"**{map_name}** is not a valid SJ map.", ephemeral=True)


def read_sheet(cell_range: str, multiple_rows=False):
    try:
        result = sheet.values().get(spreadsheetId=SHEET_ID, range=cell_range).execute()

        if multiple_rows:
            return result.get('values', [])
        else:
            return result.get('values', [])[0]
    except (HttpError, SSLEOFError):
        utils.log_error()
        raise SheetReadError


def update_last_command_used():
    db.misc.set('last_command_used', f"{int(time.time())} ({datetime.datetime.now().strftime('%c')})")


class SheetReadError(Exception):
    pass


client: Optional[discord.Client] = None
log: Optional[logging.Logger] = None
SHEET_ID = '1yXTxFyIbqxjuzRt7Y8WCojpX2prULcfgiCZm1hWMbjE'
service_json = open('service.json', 'r').read()
print(len(service_json), hash(service_json))
creds = service_account.Credentials.from_service_account_file('service.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
sheet = build('sheets', 'v4', credentials=creds).spreadsheets()
difficulties = ("Beginner", "Intermediate", "Advanced", "Expert", "Grandmaster")
re_ping = re.compile(r'<@\d+>')
sj_data, sj_data_filenames = utils.load_sj_data()
