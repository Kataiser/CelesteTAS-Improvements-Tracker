import atexit
import copy
import os
from operator import itemgetter
from typing import Union, Any

import boto3
import ujson
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer


class Table:
    def __init__(self, table_name: str, primary_key: str):
        self.table_name = table_name
        self.primary_key = primary_key
        self.caching = False
        self.cache = {}

    def get(self, key: Union[str, int], consistent_read: bool = True) -> Any:
        if self.caching and key in self.cache:
            return self.cache[key]

        key_type = 'S' if isinstance(key, str) else 'N'
        item = client.get_item(TableName=f'CelesteTAS-Improvement-Tracker_{self.table_name}', Key={self.primary_key: {key_type: str(key)}}, ConsistentRead=consistent_read)

        if 'Item' in item:
            item_deserialized = deserializer.deserialize({'M': item['Item']})
        else:
            raise DBKeyError(f"'{key}' not found in table 'CelesteTAS-Improvement-Tracker_{self.table_name}'")

        if '_value' in item_deserialized:
            result = item_deserialized['_value']
        else:
            result = item_deserialized

        if self.caching:
            self.cache[key] = result

        return result

    def set(self, key: Union[str, int], value: Any):
        if isinstance(value, dict):
            item = copy.copy(value)
            item[self.primary_key] = key
        else:
            item = {self.primary_key: key, '_value': value}

        client.put_item(TableName=f'CelesteTAS-Improvement-Tracker_{self.table_name}', Item=serializer.serialize(item)['M'])

    def get_all(self, consistent_read: bool = True) -> list:
        items = client.scan(TableName=f'CelesteTAS-Improvement-Tracker_{self.table_name}', ConsistentRead=consistent_read)
        return [deserializer.deserialize({'M': item}) for item in items['Items']]

    def metadata(self) -> dict:
        return client.describe_table(TableName=f'CelesteTAS-Improvement-Tracker_{self.table_name}')

    def size(self) -> int:
        return self.metadata()['Table']['ItemCount']

    def enable_cache(self):
        self.caching = True
        self.cache = {}

    def disable_cache(self):
        self.caching = False
        self.cache = {}


class PathCaches(Table):
    def get(self, key: int, consistent_read: bool = True) -> dict:
        result = super().get(key, consistent_read)

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
    def dict(self, consistent_read: bool = True) -> dict:
        return {int(item[self.primary_key]): item for item in self.get_all(consistent_read)}

    def get_by_name(self, name: str, consistent_read: bool = True) -> dict:
        all_projects = self.get_all(consistent_read)
        name_lower = name.lower()
        projects_selected = []

        for project in all_projects:
            if project['name'].lower() == name_lower:
                projects_selected.append(project)

        return projects_selected


class DBKeyError(Exception):
    pass


githubs = Table('githubs', 'discord_id')
history_log = Table('history_log', 'timestamp')
installations = Table('installations', 'github_username')
project_logs = Table('project_logs', 'project_id')
sheet_writes = Table('sheet_writes', 'timestamp')
logs = Table('logs', 'time')
projects = Projects('projects', 'project_id')
path_caches = PathCaches('path_caches', 'project_id')

client = boto3.client('dynamodb')
atexit.register(client.close)
serializer = TypeSerializer()
deserializer = TypeDeserializer()

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
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_installations'))
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
    client.close()
