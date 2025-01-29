import atexit
import dataclasses
import decimal
import enum
import json
import os
from operator import itemgetter
from typing import Union, Any

import boto3
import fastjsonschema
import ujson
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer


class Table:
    def __init__(self, table_name: str, primary_key: str):
        self.table_name = table_name
        self.table_full_name = f'CelesteTAS-Improvement-Tracker_{self.table_name}'
        self.primary_key = primary_key
        self.caching = False
        self.cache = {}

    def get(self, key: Union[str, int], consistent_read: bool = True, keep_primary_key: bool = True) -> Any:
        if self.caching and key in self.cache:
            return self.cache[key]

        key_type = 'S' if isinstance(key, str) else 'N'
        actual_consistent_read = False if always_inconsistent_read else consistent_read
        item = dynamodb_client.get_item(TableName=self.table_full_name, Key={self.primary_key: {key_type: str(key)}}, ConsistentRead=actual_consistent_read)

        if 'Item' in item:
            item_deserialized = deserializer.deserialize({'M': item['Item']})
        else:
            raise DBKeyError(f"'{key}' not found in table '{self.table_full_name}'")

        if '_value' in item_deserialized:
            result = item_deserialized['_value']
        else:
            result = item_deserialized

        if self.caching:
            self.cache[key] = result

        if not keep_primary_key:
            del result[self.primary_key]
            # I'd prefer it if this was the default, but that would require refactoring

        return result

    def set(self, key: Union[str, int], value: Any, get_previous: bool = False) -> Any:
        if not writes_enabled:
            return

        added_primary = False

        if isinstance(value, dict):
            if self.primary_key not in value:
                value[self.primary_key] = key
                added_primary = True
        else:
            value = {self.primary_key: key, '_value': value}

        return_values = 'ALL_OLD' if get_previous else 'NONE'
        response = dynamodb_client.put_item(TableName=self.table_full_name, Item=serializer.serialize(value)['M'], ReturnValues=return_values)

        if added_primary:
            del value[self.primary_key]

        if get_previous:
            prev_values = deserializer.deserialize({'M': response['Attributes']})

            if '_value' in prev_values:
                return prev_values['_value']
            else:
                return prev_values

    def get_all(self, consistent_read: bool = True) -> list:
        actual_consistent_read = False if always_inconsistent_read else consistent_read
        items = dynamodb_client.scan(TableName=self.table_full_name, ConsistentRead=actual_consistent_read)
        return [deserializer.deserialize({'M': item}) for item in items['Items']]

    def dict(self, consistent_read: bool = True) -> dict:
        items_list = self.get_all(consistent_read)
        items_dict = {}

        for item in items_list:
            key = item[self.primary_key]
            value = item['_value'] if '_value' in item else item
            items_dict[int(key) if isinstance(key, decimal.Decimal) else key] = value

        return items_dict

    def delete_item(self, key: Union[str, int]):
        if not writes_enabled:
            return

        key_type = 'S' if isinstance(key, str) else 'N'
        dynamodb_client.delete_item(TableName=self.table_full_name, Key={self.primary_key: {key_type: str(key)}})

    def metadata(self) -> dict:
        return dynamodb_client.describe_table(TableName=self.table_full_name)

    def size(self, consistent_read: bool = True) -> int:
        if consistent_read:
            return dynamodb_client.scan(TableName=self.table_full_name, Select='COUNT', ConsistentRead=True)['Count']
        else:
            return dynamodb_client.describe_table(TableName=self.table_full_name)['Table']['ItemCount']

    def enable_cache(self):
        self.caching = True
        self.cache = {}

    def disable_cache(self):
        self.caching = False
        self.cache = {}


class PathCaches(Table):
    def get(self, *args, **kwargs) -> dict:
        result = super().get(*args, **kwargs)

        if 'project_id' in result:
            del result['project_id']

        return dict(sorted(result.items(), key=itemgetter(1)))

    def add_file(self, project_id: int, filename: str, file_path: str):
        path_cache = self.get(project_id)
        path_cache[filename] = file_path
        self.set(project_id, path_cache)

    def remove_file(self, project_id: int, filename: str):
        path_cache = self.get(project_id)
        del path_cache[filename]
        self.set(project_id, path_cache)


class Projects(Table):
    def __init__(self, table_name: str, primary_key: str):
        super().__init__(table_name, primary_key)

        with open('project_schema.json', 'r') as projects_schema_file:
            self.validate_project_schema = fastjsonschema.compile(ujson.load(projects_schema_file))

    def set(self, project_id: Union[str, int], project: dict, get_previous: bool = False) -> Any:
        self.validate_project_schema(project, get_previous)
        return super().set(project_id, project)

    def get_all(self, consistent_read: bool = True) -> list:
        projects_list = [p for p in super().get_all(consistent_read) if p['enabled']]

        for project_unvalidated in projects_list:
            self.validate_project_schema(project_unvalidated)

        return projects_list

    def get_by_name_or_id(self, name_or_id: Union[str, int], consistent_read: bool = True) -> list:
        # gave ID
        if isinstance(name_or_id, int) or name_or_id.isdigit():
            try:
                return [self.get(int(name_or_id), consistent_read)]
            except DBKeyError:
                return []

        all_projects = self.get_all(consistent_read)
        name_lower = name_or_id.lower()
        projects_selected = []

        for project in all_projects:
            if project['name'].lower() == name_lower:
                projects_selected.append(project)

        return projects_selected


def add_project_key(key: str, value: Any):
    # update project_schema.json first
    projects_ = projects.dict()

    for project_id in projects_:
        print(projects_[project_id]['name'])

        if key in projects_[project_id]:
            print(f"\tAlready exists: {projects_[project_id][key]}")
        else:
            projects_[project_id][key] = value
            projects.set(project_id, projects_[project_id])

    print(f"Added `{key}: {value}` to {len(projects_)} projects, be sure to update command_register_project")


class SyncResultType(enum.StrEnum):
    NORMAL = enum.auto()
    MAINGAME_COMMIT = enum.auto()
    AUTO_DISABLE = enum.auto()
    REPORTED_ERROR = enum.auto()


@dataclasses.dataclass
class SyncResult:
    type: SyncResultType
    data: dict
    receipt_handle: str

    def __str__(self) -> str:
        return f"SyncResult type={str(self.type).upper()} data={self.data}"


def send_sync_result(result_type: SyncResultType, data: dict):
    if writes_enabled:
        payload = {'type': str(result_type), 'data': data}
        sqs_client.send_message(QueueUrl=sqs_queue_url, MessageBody=ujson.dumps(payload, ensure_ascii=False), MessageGroupId=str(result_type))


def get_sync_results() -> list[SyncResult]:
    results = []
    response = sqs_client.receive_message(QueueUrl=sqs_queue_url, MaxNumberOfMessages=10)

    if 'Messages' not in response and response['ResponseMetadata']['HTTPStatusCode'] == 200:
        return results

    for message in response['Messages']:
        body = json.loads(message['Body'])
        results.append(SyncResult(type=SyncResultType(body['type']),
                                  data=body['data'],
                                  receipt_handle=message['ReceiptHandle']))

    return results


def delete_sync_result(sync_result: SyncResult):
    if writes_enabled:
        sqs_client.delete_message(QueueUrl=sqs_queue_url, ReceiptHandle=sync_result.receipt_handle)
        del sync_result


class DBKeyError(Exception):
    pass


githubs = Table('githubs', 'discord_id')
history_log = Table('history_log', 'timestamp')
installations = Table('installations', 'github_username')
project_logs = Table('project_logs', 'project_id')
sheet_writes = Table('sheet_writes', 'timestamp')
logs = Table('logs', 'time')
misc = Table('misc', 'key')
contributors = Table('contributors', 'project_id')
sid_caches = Table('sid_caches', 'project_id')
tokens = Table('tokens', 'installation_owner')
projects = Projects('projects', 'project_id')
path_caches = PathCaches('path_caches', 'project_id')

dynamodb_client = boto3.client('dynamodb')
sqs_client = boto3.client('sqs')
sqs_queue_url = sqs_client.get_queue_url(QueueName='CelesteTAS-Improvement-Tracker_sync_results.fifo')['QueueUrl']
atexit.register(dynamodb_client.close)
serializer = TypeSerializer()
deserializer = TypeDeserializer()
always_inconsistent_read = False
writes_enabled = True

if __name__ == '__main__':
    print(projects.metadata())

    with open('sync\\projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = ujson.load(projects_json)
        projects_fixed = {int(k): projects_loaded[k] for k in projects_loaded}

    for project_id in projects_fixed:
        projects.set(project_id, projects_fixed[project_id])

    print(projects.get(970380662907482142))
    print(path_caches.metadata())

    with open('sync\\path_caches.json', 'r', encoding='UTF8') as path_caches_json:
        path_caches_loaded = ujson.load(path_caches_json)

    for project_id in path_caches_loaded:
        path_caches.set(int(project_id), path_caches_loaded[project_id])

    print(path_caches.get(970380662907482142))
    print(project_logs.metadata())

    for project_log_name in os.listdir('sync\\project_logs'):
        with open(f'sync\\project_logs\\{project_log_name}', 'r', encoding='UTF8') as project_log:
            project_log_loaded = ujson.load(project_log)

        project_logs.set(int(project_log_name.removesuffix('.json')), project_log_loaded)

    print(project_logs.get(970380662907482142))
    print(dynamodb_client.describe_table(TableName='CelesteTAS-Improvement-Tracker_installations'))
    print(installations.metadata())

    with open('sync\\installations.json', 'r', encoding='UTF8') as installations_json:
        installations_loaded = ujson.load(installations_json)

    for github_username in installations_loaded:
        installations.set(github_username, installations_loaded[github_username])

    print(installations.get('Kataiser'))
    print(githubs.metadata())

    with open('sync\\githubs.json', 'r', encoding='UTF8') as githubs_json:
        githubs_loaded = ujson.load(githubs_json)

    for discord_id in githubs_loaded:
        installations.set(int(discord_id), githubs_loaded[discord_id])

    print(githubs.get(219955313334288385))
    print(sheet_writes.metadata())

    with open('sync\\sheet_writes.log', 'r', encoding='UTF8') as sheet_writes_file:
        for line in sheet_writes_file:
            if line.startswith('2023-02-09'):
                continue

            line_partitioned = line.partition(': ')
            line_partitioned2 = line_partitioned[0].rpartition(':')
            timestamp = line_partitioned2[0]
            status = line_partitioned2[2]
            data = eval(line_partitioned[2][:-1])
            sheet_writes.set(timestamp, {'status': status, 'log': data})

    print(sheet_writes.get('2023-07-07 07:31:27,264'))
    print(history_log.metadata())

    with open('sync\\history.log', 'r', encoding='UTF8') as history_log_file:
        for line in history_log_file:
            line_partitioned = line.partition(': ')
            timestamp = line_partitioned[0].replace(':history', '').rpartition(':')[0]
            history_log.set(timestamp, line_partitioned[2][:-1])

    print(history_log.get('2023-03-06 20:31:44,890'))
    print(sid_caches.metadata())

    with open('sid_caches.json', 'r', encoding='UTF8') as sid_caches_json:
        sid_caches_loaded = ujson.load(sid_caches_json)

    for project_id in sid_caches_loaded:
        sid_caches.set(int(project_id), sid_caches_loaded[project_id])

    print(sid_caches.get(1180581916529922188))
    dynamodb_client.close()
