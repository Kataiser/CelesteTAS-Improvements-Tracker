import copy
import os
from typing import Union, Any

import boto3
import ujson
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer


def get(table: str, key: Union[str, int], consistent_read = True) -> Any:
    key_type = 'S' if isinstance(key, str) else 'N'
    item = client.get_item(TableName=f'CelesteTAS-Improvement-Tracker_{table}', Key={table_primaries[table]: {key_type: str(key)}}, ConsistentRead=consistent_read)
    item_deserialized = deserializer.deserialize({'M': item['Item']})

    if '_value' in item_deserialized:
        return item_deserialized['_value']
    else:
        return item_deserialized


def set(table: str, key: Union[str, int], value: Any):
    if isinstance(value, dict):
        item = copy.copy(value)
        item[table_primaries[table]] = key
    else:
        item = {table_primaries[table]: key, '_value': value}

    client.put_item(TableName=f'CelesteTAS-Improvement-Tracker_{table}', Item=serializer.serialize(item)['M'])


client = boto3.client('dynamodb')
serializer = TypeSerializer()
deserializer = TypeDeserializer()
table_primaries = {'projects': 'project_id', 'githubs': 'discord_id', 'history_log': 'timestamp', 'installations': 'github_username', 'path_caches': 'project_id',
                   'project_logs': 'project_id', 'sheet_writes': 'timestamp'}

if __name__ == '__main__':
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_projects'))

    with open('sync\\projects.json', 'r', encoding='UTF8') as projects_json:
        projects_loaded = ujson.load(projects_json)
        projects_fixed = {int(k): projects_loaded[k] for k in projects_loaded}

    for project_id in projects_fixed:
        set('projects', project_id, projects_fixed[project_id])

    print(get('projects', 970380662907482142))
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_project_logs'))

    for project_log_name in os.listdir('sync\\project_logs'):
        with open(f'sync\\project_logs\\{project_log_name}', 'r', encoding='UTF8') as project_log:
            project_log_loaded = ujson.load(project_log)

        set('project_logs', int(project_log_name.removesuffix('.json')), project_log_loaded)

    print(get('project_logs', 970380662907482142))
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_path_caches'))

    with open('sync\\path_caches.json', 'r', encoding='UTF8') as path_caches_json:
        path_caches = ujson.load(path_caches_json)

    for project_id in path_caches:
        set('path_caches', int(project_id), path_caches[project_id])

    print(get('path_caches', 970380662907482142))
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_installations'))

    with open('sync\\installations.json', 'r', encoding='UTF8') as installations_json:
        installations = ujson.load(installations_json)

    for github_username in installations:
        set('installations', github_username, installations[github_username])

    print(get('installations', 'Kataiser'))
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_githubs'))

    with open('sync\\githubs.json', 'r', encoding='UTF8') as githubs_json:
        githubs = ujson.load(githubs_json)

    for discord_id in githubs:
        set('githubs', int(discord_id), githubs[discord_id])

    print(get('githubs', 219955313334288385))
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_sheet_writes'))

    with open('sync\\sheet_writes.log', 'r', encoding='UTF8') as history_log:
        for line in history_log:
            if line.startswith('2023-02-09'):
                continue

            line_partitioned = line.partition(': ')
            line_partitioned2 = line_partitioned[0].rpartition(':')
            timestamp = line_partitioned2[0]
            status = line_partitioned2[2]
            data = eval(line_partitioned[2][:-1])
            set('sheet_writes', timestamp, {'status': status, 'log': data})

    print(get('sheet_writes', '2023-07-07 07:31:27,264'))
    print(client.describe_table(TableName='CelesteTAS-Improvement-Tracker_history_log'))

    with open('sync\\history.log', 'r', encoding='UTF8') as history_log:
        for line in history_log:
            line_partitioned = line.partition(': ')
            timestamp = line_partitioned[0].replace(':history', '').rpartition(':')[0]
            set('history_log', timestamp, line_partitioned[2][:-1])

    print(get('history_log', '2023-03-06 20:31:44,890'))
    client.close()
