import datetime
import functools
import logging
import time
from typing import Optional

import jwt
import requests
import ujson

import utils
from utils import plural


def generate_jwt(min_time: int) -> str:
    current_time = time.time()

    if '_jwt' in tokens:
        time_remaining = tokens['_jwt'][1] - current_time

        if time_remaining > min_time:
            log.info(f"Reused JWT with {round(time_remaining / 60, 1)} mins remaining")
            return tokens['_jwt'][0]

    with open('celestetas-improvements-tracker.2022-05-01.private-key.pem', 'rb') as pem_file:
        private = pem_file.read()

    payload = {'iat': round(current_time - 60),
               'exp': round(current_time + (9.5 * 60)),
               'iss': '196447'}

    generated_jwt = jwt.encode(payload, private, algorithm='RS256')
    log.info("Generated JWT")
    tokens['_jwt'] = (generated_jwt, payload['exp'])
    return generated_jwt


def generate_access_token(installation_owner: str, min_jwt_time: int) -> tuple:
    headers = {'Authorization': f'Bearer {generate_jwt(min_jwt_time)}', 'Accept': 'application/vnd.github.v3+json'}
    installations_saved = installations_file()

    if installation_owner not in installations_file():
        log.info(f"Installation ID not cached for owner \"{installation_owner}\"")
        r = requests.get('https://api.github.com/app/installations', headers=headers)
        utils.handle_potential_request_error(r, 200)
        installations = ujson.loads(r.content)
        log.info(f"Found {len(installations)} installation{plural(installations)}: {[(i['id'], i['account']['login'], i['created_at']) for i in installations]}")
        installations_file.cache_clear()

        for installation in installations:
            installations_saved[installation['account']['login']] = installation['id']

        with open('installations.json', 'w') as installations_json_write:
            ujson.dump(installations_saved, installations_json_write, indent=4)

    installation_id = installations_saved[installation_owner]
    r = requests.post(f'https://api.github.com/app/installations/{installation_id}/access_tokens', headers=headers)
    utils.handle_potential_request_error(r, 201)
    access_token_data = ujson.loads(r.content)
    token_expiration_str = access_token_data['expires_at'][:-1]
    token_expiration = datetime.datetime.fromisoformat(f'{token_expiration_str}+00:00')
    log.info(f"Generated {installation_owner} access token: {access_token_data}")
    return access_token_data['token'], token_expiration.timestamp()


def access_token(installation_owner: str, min_time: int):
    if installation_owner not in tokens:
        tokens[installation_owner] = generate_access_token(installation_owner, min_time)
    else:
        time_remaining = tokens[installation_owner][1] - time.time()

        if time_remaining > min_time:
            log.info(f"Reusing {installation_owner} access token with {round(time_remaining / 60, 1)} mins remaining")
        else:
            tokens[installation_owner] = generate_access_token(installation_owner, min_time)

    return tokens[installation_owner][0]


@functools.cache
def installations_file() -> dict:
    with open('installations.json', 'r') as installations_json_read:
        return ujson.load(installations_json_read)


tokens = {}
log: Optional[logging.Logger] = None


if __name__ == '__main__':
    generate_access_token('Kataiser')
